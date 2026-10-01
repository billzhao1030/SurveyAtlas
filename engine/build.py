"""Assemble everything into the website's data files.

inputs : atlases/<id>/data/{candidates.jsonl, labels/, raw/openalex.jsonl, meta/, reading/}
         atlases/<id>/overrides.json (optional)
outputs: atlases/<id>/public/papers.json    in-scope library (every scope except "out"), full records
                             excluded.json  out-of-scope + prefilter drops, slim (for auditing recall)
                             taxonomy.json  taxonomy for legends / facets
                             stats.json     pipeline funnel + query hit counts + build time
                             meta.json      title / UI config / counts — read by the hub's atlas list
                             atlas.bib      BibTeX for every in-scope paper
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from engine import ctx, taxonomy
from engine.ctx import CANDS, LABELS, META_DIR, OVERRIDES, PUBLIC, RAW_OA, READING
from engine.venues import label as venue_label
from engine.venues import parse_venue

ARXIV_QUERIES = ctx.D.ARXIV_QUERIES
SITE_DATA = PUBLIC
DBLP = META_DIR / "dblp.jsonl"
S2 = META_DIR / "s2.jsonl"
CROSSREF = META_DIR / "crossref_bib.jsonl"
BIB_OUT = PUBLIC / "atlas.bib"
BULK_DATE = ctx.meta("bulk_date", "")  # initial import; papers first seen after this are "new"

BOOKTITLE = {
    "CVPR": "Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)",
    "ICCV": "Proceedings of the IEEE/CVF International Conference on Computer Vision (ICCV)",
    "ECCV": "European Conference on Computer Vision (ECCV)",
    "NeurIPS": "Advances in Neural Information Processing Systems (NeurIPS)",
    "ICLR": "International Conference on Learning Representations (ICLR)",
    "ICML": "International Conference on Machine Learning (ICML)",
    "AAAI": "Proceedings of the AAAI Conference on Artificial Intelligence (AAAI)",
    "IJCAI": "Proceedings of the International Joint Conference on Artificial Intelligence (IJCAI)",
    "ACL": "Proceedings of the Annual Meeting of the Association for Computational Linguistics (ACL)",
    "EMNLP": "Proceedings of the Conference on Empirical Methods in Natural Language Processing (EMNLP)",
    "NAACL": "Proceedings of the Conference of the North American Chapter of the Association for Computational Linguistics (NAACL)",
    "EACL": "Proceedings of the Conference of the European Chapter of the Association for Computational Linguistics (EACL)",
    "COLING": "Proceedings of the International Conference on Computational Linguistics (COLING)",
    "CoRL": "Conference on Robot Learning (CoRL)",
    "RSS": "Robotics: Science and Systems (RSS)",
    "ICRA": "IEEE International Conference on Robotics and Automation (ICRA)",
    "IROS": "IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    "ACM MM": "Proceedings of the ACM International Conference on Multimedia (ACM MM)",
    "WACV": "Proceedings of the IEEE/CVF Winter Conference on Applications of Computer Vision (WACV)",
    "BMVC": "British Machine Vision Conference (BMVC)",
    "3DV": "International Conference on 3D Vision (3DV)",
    "ICASSP": "IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)",
}
JOURNAL = {
    "RA-L": "IEEE Robotics and Automation Letters",
    "T-RO": "IEEE Transactions on Robotics",
    "TPAMI": "IEEE Transactions on Pattern Analysis and Machine Intelligence",
    "IJCV": "International Journal of Computer Vision",
    "TMLR": "Transactions on Machine Learning Research",
    "TNNLS": "IEEE Transactions on Neural Networks and Learning Systems",
    "TCSVT": "IEEE Transactions on Circuits and Systems for Video Technology",
    "TIP": "IEEE Transactions on Image Processing",
    "TMM": "IEEE Transactions on Multimedia",
    "Science Robotics": "Science Robotics",
    "Nature MI": "Nature Machine Intelligence",
}
STOP = {"a", "an", "the", "on", "of", "for", "to", "in", "and", "with", "towards", "toward", "via", "is", "are", "do", "does", "can", "what", "how", "when", "learning", "from"}


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def cite_key(authors: list[str], year: int, title: str, used: set[str]) -> str:
    last = ascii_fold(authors[0].split()[-1] if authors else "anon").lower()
    last = re.sub(r"[^a-z]", "", last) or "anon"
    words = [w for w in re.findall(r"[a-z0-9]+", ascii_fold(title).lower()) if w not in STOP]
    key = f"{last}{year or ''}{words[0] if words else 'paper'}"
    base, i = key, 0
    while key in used:
        i += 1
        key = base + chr(ord("a") + i)
    used.add(key)
    return key


def bib_escape(s: str) -> str:
    return s.replace("&", r"\&").replace("%", r"\%").replace("#", r"\#")


def make_bib(key: str, p: dict) -> str:
    authors = " and ".join(p["a"]) or "Anonymous"
    title = "{" + bib_escape(p["t"]) + "}"
    v = p.get("vinfo")
    if v and v.get("kind") != "workshop" and v.get("venue") in BOOKTITLE and v.get("year"):
        return (f"@inproceedings{{{key},\n  title     = {title},\n  author    = {{{authors}}},\n"
                f"  booktitle = {{{BOOKTITLE[v['venue']]}}},\n  year      = {{{v['year']}}}\n}}")
    if v and v.get("venue") in JOURNAL and v.get("year"):
        return (f"@article{{{key},\n  title   = {title},\n  author  = {{{authors}}},\n"
                f"  journal = {{{JOURNAL[v['venue']]}}},\n  year    = {{{v['year']}}}\n}}")
    if p.get("ax"):
        return (f"@article{{{key},\n  title   = {title},\n  author  = {{{authors}}},\n"
                f"  journal = {{arXiv preprint arXiv:{p['ax']}}},\n  year    = {{{p['y']}}}\n}}")
    return (f"@misc{{{key},\n  title  = {title},\n  author = {{{authors}}},\n  year   = {{{p['y']}}}"
            + (f",\n  doi    = {{{p['doi']}}}" if p.get("doi") else "") + "\n}")


def pretty_bib(raw: str, key: str) -> str:
    """Re-key a one-line publisher BibTeX entry and put one field per line."""
    m = re.match(r"\s*@(\w+)\s*\{\s*[^,]*,(.*)\}\s*$", raw, re.S)
    if not m:
        return raw
    kind, body = m.group(1).lower(), m.group(2)
    fields, depth, cur = [], 0, ""
    for ch in body:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        fields.append(cur)
    out = []
    for f in fields:
        if "=" not in f:
            continue
        k, v = f.split("=", 1)
        k = k.strip().lower()
        if k in ("url", "month", "issn", "publisher", "collection", "series"):
            continue
        out.append(f"  {k:<9} = {v.strip()}")
    return f"@{kind}{{{key},\n" + ",\n".join(out) + "\n}"


def last_names(authors: list[str]) -> set[str]:
    return {re.sub(r"[^a-z]", "", ascii_fold(a.split()[-1]).lower()) for a in authors if a.strip()} - {""}


def find_duplicates(rows: list[tuple[dict, dict]]) -> dict[str, str]:
    """rows = [(candidate, label)] in scope. Returns {dup_uid: keeper_uid}.

    A venue-only (OpenAlex) row is a duplicate of an arXiv row when they share
    the method name or a near-identical title AND at least two author surnames
    (or the first author) and are within two years of each other."""
    from difflib import SequenceMatcher

    arx = [(c, l) for c, l in rows if c.get("arxiv_id")]
    by_name: dict[str, list] = {}
    for c, l in arx:
        n = (l.get("name") or "").lower().strip()
        if len(n) >= 3:
            by_name.setdefault(n, []).append(c)
    dup = {}
    # the full-text resolver (engine/fulltext.py) may have found the arXiv twin of a venue-only row directly
    try:
        linked = {u: e["ax"] for u, e in json.loads((ctx.META_DIR / "fulltext_links.json").read_text()).items() if e.get("ax")}
    except (OSError, ValueError):
        linked = {}
    arx_ids = {c["arxiv_id"]: c["uid"] for c, _ in arx}
    for c, l in rows:
        if c.get("arxiv_id"):
            continue
        if linked.get(c["uid"]) in arx_ids:
            dup[c["uid"]] = arx_ids[linked[c["uid"]]]
            continue
        n = (l.get("name") or "").lower().strip()
        pool = by_name.get(n, []) if len(n) >= 3 else []
        nt = re.sub(r"[^a-z0-9 ]", "", c["title"].lower())
        if not pool:
            pool = [a for a, _ in arx if abs((a.get("year") or 0) - (c.get("year") or 0)) <= 2
                    and SequenceMatcher(None, nt, re.sub(r"[^a-z0-9 ]", "", a["title"].lower())).ratio() > 0.9]
        la = last_names(c.get("authors", []))
        for a in pool:
            lb = last_names(a.get("authors", []))
            same_first = c.get("authors") and a.get("authors") and last_names(c["authors"][:1]) == last_names(a["authors"][:1])
            if abs((a.get("year") or 0) - (c.get("year") or 0)) <= 2 and (len(la & lb) >= 2 or same_first):
                dup[c["uid"]] = a["uid"]
                break
    return dup


# Month (1-12) after which an arXiv posting most likely targets NEXT year's
# edition (submission deadline has passed). Used only when no better source.
NEXT_YEAR_AFTER = {"AAAI": 5, "ICLR": 9, "CVPR": 11, "ICRA": 9, "IROS": 11, "RSS": 12, "ICML": 12,
                   "ACL": 10, "NAACL": 10, "WACV": 7, "IJCAI": 12, "ECCV": 12, "ICCV": 12}


def venue_year(vinfo: dict, c: dict, r2: dict, cr: dict, vsrc: str) -> tuple[int | None, str]:
    """Best estimate of the publication year of the venue version."""
    key = r2.get("dblp") or ""
    if key and not key.startswith("journals/corr"):
        m = re.search(r"(\d{2})[a-z]?$", key)
        if m:
            return 2000 + int(m.group(1)), "dblp-key"
    m = re.search(r"^10\.1109/[A-Za-z\-]+\d*\.(20\d\d)\.", r2.get("doi") or "")
    if m:
        return int(m.group(1)), "doi"
    if cr.get("bibtex"):
        m = re.search(r"year\s*=\s*\{?(\d{4})", cr["bibtex"])
        if m:
            return int(m.group(1)), "crossref"
    if vsrc == "arxiv-comment" and vinfo.get("year"):
        return vinfo["year"], "comment"
    cv = parse_venue(c.get("comment"))
    if cv and cv.get("venue") == vinfo.get("venue") and cv.get("year"):
        return cv["year"], "comment"
    d = c.get("date") or ""
    if len(d) >= 7 and vinfo.get("venue") in NEXT_YEAR_AFTER:
        y, mth = int(d[:4]), int(d[5:7])
        return (y + 1 if mth >= NEXT_YEAR_AFTER[vinfo["venue"]] else y), "calendar"
    return vinfo.get("year"), "source"


def main() -> None:
    if (PUBLIC / "papers.json").exists():  # notes other atlases already have for the same papers (engine/shared.py)
        from engine import shared
        shared.sync_readings(json.loads((PUBLIC / "papers.json").read_text()))
    cands = load_jsonl(CANDS)
    labels: dict[str, dict] = {}
    for r in load_jsonl(LABELS):
        labels[r["uid"]] = r
    overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    oa = {r["oa_id"]: r for r in load_jsonl(RAW_OA)}
    dblp = {r["uid"]: r for r in load_jsonl(DBLP)}
    s2 = {r["uid"]: r for r in load_jsonl(S2)}
    crossref = {r["uid"]: r for r in load_jsonl(CROSSREF)}
    from engine import affil as affil_mod
    affil = affil_mod.load()  # industry authorship ({uid: {"co": [...]}}), see engine/affil.py

    # duplicate pass: fold venue-only copies into their arXiv twin
    def lab_of(uid):
        lab = dict(labels.get(uid) or {})
        lab.update(overrides.get(uid, {}))
        return lab
    in_scope = [(c, lab_of(c["uid"])) for c in cands
                if c.get("prefilter", "keep") == "keep" and lab_of(c["uid"]).get("scope") not in (None, "out")]
    dups = find_duplicates(in_scope)
    by_uid = {c["uid"]: c for c in cands}
    for d, keep in dups.items():
        k = by_uid[keep]
        k["oa_ids"] = list(dict.fromkeys(k.get("oa_ids", []) + by_uid[d].get("oa_ids", [])))
        if by_uid[d].get("doi") and not k.get("doi_pub"):
            k["doi_pub"] = by_uid[d]["doi"]

    papers, excluded = [], []
    readings: dict[str, dict] = {}
    funnel = Counter()
    # cite keys are persistent (data/meta/cite_keys.json): once a paper has a key it keeps it, so survey
    # LaTeX never breaks when papers are added / folded or a venue year is corrected
    KEYS_F = ctx.META_DIR / "cite_keys.json"
    try:
        stored_keys: dict[str, str] = json.loads(KEYS_F.read_text())
    except (OSError, ValueError):
        stored_keys = {}
    live = {c["uid"] for c in cands}
    used_keys: set[str] = {k for u, k in stored_keys.items() if u in live}
    bib_lines = []
    for c in cands:
        funnel["harvested"] += 1
        uid = c["uid"]
        if c.get("prefilter", "keep") != "keep":
            funnel["prefilter_drop"] += 1
            excluded.append({"id": uid, "t": c["title"], "y": c.get("year"), "why": c["prefilter"]})
            continue
        lab = dict(labels.get(uid) or {})
        if uid in overrides:
            lab.update(overrides[uid])
            lab["override"] = True
        if not lab:
            funnel["unlabelled"] += 1
            continue
        if lab.get("paradigm") and lab["paradigm"] not in taxonomy.CODES["paradigm"]:
            # taxonomy changed since this label was written: map through the domain's aliases
            new_pd, extra_traits = getattr(ctx.D, "PARADIGM_ALIASES", {}).get(lab["paradigm"], ("none", []))
            lab["paradigm"] = new_pd
            lab["traits"] = list(dict.fromkeys(list(lab.get("traits", [])) + list(extra_traits)))
        if uid in dups:
            funnel["duplicate"] += 1
            excluded.append({"id": uid, "t": c["title"], "y": c.get("year"), "why": f"dup-of:{dups[uid]}"})
            continue
        funnel[f"scope_{lab['scope']}"] += 1
        if lab["scope"] == "out":
            excluded.append({"id": uid, "t": c["title"], "y": c.get("year"), "why": "llm:out", "tl": lab.get("tldr", "")})
            continue

        # ── venue: DBLP > Semantic Scholar > arXiv comment/journal_ref > OpenAlex location ──
        vinfo, vsrc = None, None
        d = dblp.get(uid)
        r2 = s2.get(uid) or {}
        if d and d.get("venue"):
            vinfo, vsrc = {"venue": d["venue"], "year": d.get("year"), "kind": d.get("kind", "main")}, "dblp"
        elif r2.get("venue_ok"):
            vinfo = dict(r2["canon"])
            vinfo["year"] = vinfo.get("year") or r2.get("year")
            vsrc = "semantic-scholar"
        if not vinfo:
            vinfo = parse_venue(c.get("comment")) or parse_venue(c.get("journal_ref"))
            vsrc = "arxiv-comment" if vinfo else None
        cited = r2.get("cites") or 0
        for oid in c.get("oa_ids", []):
            o = oa.get(oid)
            if not o:
                continue
            cited = max(cited, o.get("cited_by") or 0)
            if not vinfo:
                for loc in o.get("venues", []):
                    v = parse_venue(loc["name"])
                    if v:
                        v["year"] = v.get("year") or o.get("year")
                        vinfo, vsrc = v, "openalex"
                        break
        if not vinfo:
            # long-tail venue (journal / smaller conference): keep its name as-is
            name, yr = None, None
            has_pub = bool(r2.get("doi")) or (bool(r2.get("dblp")) and not r2["dblp"].startswith("journals/corr"))
            if has_pub and r2.get("venue") and r2.get("pv_type") in ("journal", "conference") and not re.search(r"arxiv|biorxiv|ssrn|preprint", r2["venue"], re.I):
                name, yr = r2["venue"], r2.get("year")
            else:
                for oid in c.get("oa_ids", []):
                    for loc in (oa.get(oid) or {}).get("venues", []):
                        if not re.search(r"arxiv|lecture notes|preprint|zenodo|figshare|research square", loc["name"], re.I):
                            name, yr = loc["name"], (oa.get(oid) or {}).get("year")
                            break
                    if name:
                        break
            if name:
                name = re.sub(r"^\d{4}\s+", "", name).strip()
                vinfo, vsrc = {"venue": name[:60], "year": yr or c.get("year"), "kind": "other"}, "semantic-scholar" if r2.get("venue") == name else "openalex"
        if vinfo and vinfo.get("kind") != "other":
            vinfo["year"], vinfo["ysrc"] = venue_year(vinfo, c, r2, crossref.get(uid) or {}, vsrc)
        if lab.get("venue"):  # hand override: {"venue": "RSS", "venue_year": 2025, "venue_kind": "main"}
            vinfo, vsrc = {"venue": lab["venue"], "year": lab.get("venue_year") or c.get("year"), "kind": lab.get("venue_kind", "main")}, "override"
        if vinfo and not vinfo.get("year"):
            vinfo["year"] = c.get("year")

        p = {
            "id": uid,
            "ax": c.get("arxiv_id"),
            "t": c["title"],
            "n": lab.get("name"),
            "a": c.get("authors", [])[:30],
            "y": c.get("year"),
            "d": c.get("date"),
            "ab": c.get("abstract", ""),
            "tl": lab.get("tldr", ""),
            "sc": lab["scope"],
            "tk": lab.get("tasks", []),
            "st": lab.get("settings", []),
            "pd": lab.get("paradigm", "none"),
            "ct": lab.get("contrib", []),
            "tr": lab.get("traits", []),
            "bm": lab.get("benchmarks", []),
            "cf": lab.get("conf", "high"),
            "v": venue_label(vinfo),
            "vn": vinfo["venue"] if vinfo else None,
            "vk": vinfo.get("kind") if vinfo else None,
            "vs": vsrc,
            "c": cited,
            "co": lab["co"] if "co" in lab else (affil.get(uid) or {}).get("co", []),
            "doi": r2.get("doi") or c.get("doi_pub") or c.get("doi"),
            "src": c.get("sources", []),
            "q": [q for q in c.get("queries", [])],
            "cm": c.get("comment"),
            "fs": c.get("first_seen") or BULK_DATE,
            "vinfo": vinfo,
        }
        key = stored_keys.get(uid) or cite_key(p["a"], p["y"], p["t"], used_keys)
        stored_keys[uid] = key
        p["key"] = key
        cr = crossref.get(uid) or {}
        if d and d.get("bibtex"):
            bib = re.sub(r"^@(\w+)\{[^,]+,", lambda m: f"@{m.group(1)}{{{key},", d["bibtex"].strip(), count=1)
            p["bs"] = "dblp"
        elif cr.get("bibtex"):
            bib = pretty_bib(cr["bibtex"], key)
            p["bs"] = "crossref"
        else:
            bib = make_bib(key, p)
            p["bs"] = "auto-venue" if (vinfo and vinfo.get("kind") != "workshop" and (vinfo["venue"] in BOOKTITLE or vinfo["venue"] in JOURNAL)) else ("arxiv" if p["ax"] else "misc")
        p["bib"] = bib
        rd_file = READING / f"{uid.replace('/', '_').replace(':', '_')}.json"
        if rd_file.exists():  # deep reading: flag + searchable gist here; full notes served on demand
            try:
                rd = json.loads(rd_file.read_text())
                p["r"] = 1
                p["rk"] = f"{rd.get('key_idea', '')} {rd.get('insight', '')}"[:700]
                if rd.get("summary"):
                    p["sm"] = rd["summary"][:400]
                if rd.get("summary_zh"):  # notes written before summaries were in English (dropped by ./atlas export)
                    p["zh"] = rd["summary_zh"][:400]
                readings[uid] = rd
            except ValueError:
                pass
        del p["vinfo"]
        bib_lines.append(bib)
        papers.append(p)

    papers.sort(key=lambda p: (p["d"] or "", p["id"]), reverse=True)
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "papers.json").write_text(json.dumps(papers, ensure_ascii=False, separators=(",", ":")))
    (SITE_DATA / "excluded.json").write_text(json.dumps(excluded, ensure_ascii=False, separators=(",", ":")))
    (SITE_DATA / "taxonomy.json").write_text(json.dumps(taxonomy.as_json(), ensure_ascii=False, indent=1))
    BIB_OUT.write_text("\n\n".join(bib_lines) + "\n")

    qhits = Counter(q for c in cands for q in c.get("queries", []))
    in_scope_q = Counter(q for p in papers if p["sc"] == "core" for q in p["q"])
    stats = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "funnel": dict(funnel),
        "n_papers": len(papers),
        "n_core": sum(p["sc"] == "core" for p in papers),
        "n_venue": sum(bool(p["v"]) for p in papers),
        "n_dblp": sum(p["bs"] == "dblp" for p in papers),
        "n_bib_official": sum(p["bs"] in ("dblp", "crossref") for p in papers),
        "n_bib_venue": sum(p["bs"] in ("dblp", "crossref", "auto-venue") for p in papers),
        "venue_src": dict(Counter(p["vs"] or "none" for p in papers if p["sc"] == "core")),
        "queries": [
            {"key": k, "query": q, "hits": qhits.get(k, 0), "core": in_scope_q.get(k, 0), "src": "arxiv"} for k, q, _ in ARXIV_QUERIES
        ] + [
            {"key": k[3:], "query": "(OpenAlex)", "hits": v, "core": in_scope_q.get(k, 0), "src": "openalex"}
            for k, v in sorted(qhits.items()) if k.startswith("oa:")
        ],
    }
    (SITE_DATA / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))

    # leaderboard.json: every result row from deep reading, with what is needed to compare fairly
    board = []
    byid = {p["id"]: p for p in papers}
    from engine import audit, comparable
    variant = getattr(ctx.D, "bench_variant", None)  # domain hook: split a benchmark name into incompatible versions
    variants_spec = getattr(ctx.D, "BENCH_VARIANTS", {})
    bench_alias = getattr(ctx.D, "BENCH_ALIASES", {})
    metric_alias = getattr(ctx.D, "METRIC_ALIASES", {})
    from engine import variants as variants_mod
    vcache = variants_mod.load()
    pcache = variants_mod.load(variants_mod.PCACHE)
    cmp = comparable.load()
    aud = audit.load()
    for uid, rd in readings.items():
        p = byid.get(uid)
        if not p or overrides.get(uid, {}).get("results") is False:  # hand-marked: numbers are not this paper's own navigation results
            continue
        st = rd.get("setting") or {}
        for row in rd.get("numbers") or []:
            if not row.get("metrics"):
                continue
            board.append({
                "id": uid, "n": p.get("n"), "t": p["t"], "y": p["y"], "v": p.get("v"), "pd": p["pd"], "sc": p["sc"], "co": p.get("co") or [],
                "bench": re.sub(r"^other:\s*", "", str(row.get("bench", ""))), "split": row.get("split", ""),
                "eval_set": row.get("eval_set", "full"), "eval_note": row.get("eval_set_note", ""),
                "method": row.get("method", ""), "zs": row.get("zero_shot"), "backbone": row.get("backbone") or st.get("backbone"),
                "open": st.get("backbone_open"), "size": st.get("model_size"), "learned": st.get("learned"),
                "obs": st.get("observation"), "act": st.get("action_space"), "priv": st.get("privileged"),
                "extra": row.get("extra", ""), "m": row["metrics"], "ok": row.get("verified"), "ev": row.get("evidence", ""),
            })
            b = board[-1]
            b["bench"] = bench_alias.get(b["bench"], b["bench"])
            if variant:
                b["bench"] = variant(b, p) or b["bench"]
            if b["bench"] in variants_spec:  # still generic: the LLM resolver may know (engine/variants.py)
                vv = vcache.get(audit.key(b), {}).get("variant")
                if vv and vv != "unknown":
                    b["bench"] = vv
            pv = pcache.get(audit.key(b), {}).get("protocol")
            if pv and pv != "other":
                b["protocol"] = pv  # a subset shared by several papers (SUBSET_PROTOCOLS)
            for a_, c_ in metric_alias.get(b["bench"], {}).items():  # after all renames: e.g. s-SR is GOAT-Bench's SR
                if a_ in b["m"] and c_ not in b["m"]:
                    b["m"][c_] = b["m"].pop(a_)
            v = cmp.get(comparable.key(b["bench"], (b["priv"] or "").strip(), (b["extra"] or "").strip()))
            if v:
                b["cmp"], b["cmp_why"] = v["std"], v["why"]
            audit.apply(b, aud)
            hand = (overrides.get(uid, {}).get("nonstandard") or {}).get(f"{b['bench']}|{b['split']}")
            if hand:  # hand correction after reading the paper (overrides.json)
                b["audit_std"], b["audit_note"] = False, hand
    KEYS_F.parent.mkdir(parents=True, exist_ok=True)
    KEYS_F.write_text(json.dumps(stored_keys, ensure_ascii=False, indent=0))
    (SITE_DATA / "leaderboard.json").write_text(json.dumps(board, ensure_ascii=False, separators=(",", ":")))

    # surveys.json: outline trees + assigned papers + writing progress (see engine/survey.py)
    surveys = []
    for sv in ctx.surveys():
        asg = {}
        af = ctx.DATA / "surveys" / sv["id"] / "assign.jsonl"
        if af.exists():
            for line in af.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r["id"] in byid:
                        asg[r["id"]] = {k: r.get(k) for k in ("leaf", "alt", "threads", "role", "why")}
        sdir = ctx.ADIR / "surveys" / sv["id"]
        secs = {f.stem: len(f.read_text()) for f in (sdir / "sections").glob("*.tex")} if (sdir / "sections").exists() else {}
        from engine import survey as survey_mod
        surveys.append({**{k: v for k, v in sv.items()}, "assign": asg,
                        "files": {"sections": secs, "order": survey_mod.writable(sv),
                                  "pdfs": [{**v, "bytes": (sdir / v["file"]).stat().st_size, "mtime": datetime.fromtimestamp((sdir / v["file"]).stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
                                           for v in sv.get("versions", [{"file": "main.pdf", "label": "PDF"}]) if (sdir / v["file"]).exists()],
                                  "pdf": (sdir / "main.pdf").exists(), "main": (sdir / "main.tex").exists()}})
    (SITE_DATA / "surveys.json").write_text(json.dumps(surveys, ensure_ascii=False, separators=(",", ":")))

    # meta.json: what the hub needs to list / switch atlases without loading papers
    core = [p for p in papers if p["sc"] == "core"]
    by_year = Counter(p["y"] for p in core if p.get("y"))
    meta = {
        **getattr(ctx.D, "META", {}),
        "id": ctx.ATLAS_ID,
        "built_at": stats["built_at"],
        "n_papers": len(papers),
        "n_core": len(core),
        "n_venue_core": sum(bool(p["v"]) for p in core),
        "n_read": len(readings),
        "n_results": len(board),
        "surveys": [{"id": s["id"], "short": s.get("short", s["title"])} for s in ctx.surveys()],
        "by_year": {str(y): by_year[y] for y in sorted(by_year) if y >= ctx.meta("timeline_start", 2017) - 3},
        "ui": getattr(ctx.D, "UI", {}),
        "domain_ready": bool(getattr(ctx.D, "DOMAIN_READY", False)),
    }
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps({k: stats[k] for k in ("funnel", "n_papers", "n_core", "n_venue", "n_bib_official", "n_bib_venue", "venue_src")}, indent=1))


if __name__ == "__main__":
    main()
