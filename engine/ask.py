"""Ask the atlas a question in plain language.

Retrieves the most relevant papers (title, abstract, TL;DR and the deep-reading notes) plus, when the question
names a benchmark, the best comparable results on it, and lets headless Claude answer from that context only,
citing papers by id. Without the `claude` CLI (or with --no-llm) it returns the retrieved papers.

  ./atlas ask <id> "which training-free methods lead R2R-CE?"  [--k 24] [--model sonnet] [--no-llm] [--json]
  ATLAS=<id> python3 -m engine.ask "question"

The hub calls answer() for POST /api/ask.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

STOP = set("""a an the of in on for to and or with without by from at as is are was were be been being it its this that
these those which what who whom whose how why when where do does did can could should would will may might than then
there their they them we our you your i me my about into over under between across via vs versus using use used based
any all some most more less much many few each other such not no yes paper papers work works method methods approach
approaches model models recent latest best state art sota""".split())
TOKEN = re.compile(r"[a-z0-9][a-z0-9+.-]*[a-z0-9+]|[a-z0-9]")
FIELDS = (("n", 3.0), ("t", 2.0), ("tl", 1.5), ("bm", 1.5), ("ab", 1.0), ("ki", 1.2), ("ins", 0.8), ("pr", 0.6))
YEAR_RE = re.compile(r"\b(after|since|from|before|until|in|during|post|pre)[- ]?((?:19|20)\d\d)\b"
                     r"|\b((?:19|20)\d\d)\s*(?:-|–|to|and)\s*((?:19|20)\d\d)\b", re.I)
BROAD = re.compile(r"\b(chang\w*|trend\w*|evolv\w*|evolution|history|overview|landscape|progress|open (?:problems?|questions?|challenges?)"
                   r"|challenges?|future|directions?|gaps?|state of the (?:art|field)|summar\w*|surveys?|main approaches|paradigms?|lines of work)\b", re.I)
ASK_VERSION = 4  # bump when retrieval or the prompt changes: cached answers are not reused across versions
_LOCK = threading.Lock()
_INDEX: dict[str, dict] = {}


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


def _reading(rdir: Path, uid: str) -> dict:
    try:
        return json.loads((rdir / f"{uid.replace('/', '_').replace(':', '_')}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _text(v) -> str:
    return " ".join(map(str, v)) if isinstance(v, list) else str(v or "")


def index(adir: Path) -> dict:
    """BM25 index over the built papers.json + reading notes; rebuilt when papers.json changes."""
    pj = adir / "public" / "papers.json"
    key = f"{adir}:{pj.stat().st_mtime}"
    with _LOCK:
        if key in _INDEX:
            return _INDEX[key]
        papers = [p for p in json.loads(pj.read_text("utf-8")) if p.get("sc") not in ("out",)]
        rdir = adir / "data" / "reading"
        docs, df = [], Counter()
        for p in papers:
            rd = _reading(rdir, p["id"]) if p.get("r") else {}
            p["_ki"], p["_ins"], p["_res"] = _text(rd.get("key_idea")), _text(rd.get("insight")), _text(rd.get("results"))
            src = {"n": p.get("n") or "", "t": p.get("t", ""), "tl": p.get("tl") or "", "bm": " ".join(p.get("bm") or []),
                   "ab": p.get("ab") or "", "ki": p["_ki"], "ins": p["_ins"], "pr": _text(rd.get("problem"))}
            tf: Counter = Counter()
            for f, w in FIELDS:
                for t in tokens(src[f]):
                    tf[t] += w
            docs.append(tf)
            df.update(tf.keys())
        avg = sum(sum(d.values()) for d in docs) / max(1, len(docs))
        names: dict = {}
        for p in papers:
            if p.get("n") and len(p["n"]) >= 3:
                names.setdefault(p["n"].lower(), []).append(p)
        _INDEX.clear()
        _INDEX[key] = idx = {"papers": papers, "docs": docs, "df": df, "avg": avg, "n": len(docs), "names": names}
        return idx


def year_range(q: str) -> tuple[int, int] | None:
    """'after 2023' → (2024, 9999), 'since 2024' → (2024, 9999), 'before 2020' → (0, 2019), 'in 2025' → (2025, 2025),
    '2019-2021' → (2019, 2021)."""
    m = YEAR_RE.search(q)
    if not m:
        return None
    if m.group(3):
        a, b = sorted((int(m.group(3)), int(m.group(4))))
        return a, b
    w, y = m.group(1).lower(), int(m.group(2))
    return {"after": (y + 1, 9999), "post": (y + 1, 9999), "since": (y, 9999), "from": (y, 9999), "before": (0, y - 1),
            "pre": (0, y - 1), "until": (0, y)}.get(w, (y, y))


def named(adir: Path, q: str) -> list[dict]:
    """Papers whose method / benchmark name the question spells out ("NaVid", "R2R", "GOAT-Bench"), most cited first."""
    idx, ql = index(adir), f" {q.lower()} "
    hits = []
    for name, ps in idx["names"].items():
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", ql):
            hits += ps
    # the paper that introduced a benchmark the question names (R2R → "Vision-and-Language Navigation: ... Room-to-Room")
    return sorted({p["id"]: p for p in hits}.values(), key=lambda p: -(p.get("c") or 0))[:5]


def search(adir: Path, q: str, k: int = 24) -> list[dict]:
    idx = index(adir)
    qt = [t for t in tokens(q) if not re.fullmatch(r"(19|20)\d\d", t)]
    yr = year_range(q)
    pinned = named(adir, q)
    if not qt:
        return pinned
    # keep multi-word names ("vln-ce", "goat-bench") and their parts
    qt += [x for t in qt if "-" in t for x in t.split("-") if x and x not in STOP]
    n, avg, out = idx["n"], idx["avg"], []
    idf = {t: math.log(1 + (n - idx["df"].get(t, 0) + 0.5) / (idx["df"].get(t, 0) + 0.5)) for t in set(qt)}
    for p, d in zip(idx["papers"], idx["docs"]):
        if yr and not (yr[0] <= (p.get("y") or 0) <= yr[1]):
            continue
        dl = sum(d.values())
        s = sum(idf[t] * d[t] * 2.2 / (d[t] + 1.2 * (0.25 + 0.75 * dl / avg)) for t in set(qt) if t in d)
        if s <= 0:
            continue
        s *= 1.0 + 0.06 * math.log1p(p.get("c") or 0) + (0.15 if p.get("sc") == "core" else 0) + (0.1 if p.get("r") else 0)
        out.append((s, p))
    out.sort(key=lambda x: -x[0])
    ids = {p["id"] for p in pinned}
    return (pinned + [p for _, p in out if p["id"] not in ids])[:k]


def label(p: dict) -> str:
    """'Name: Title', without repeating a name the title already starts with."""
    n, t = p.get("n") or "", p.get("t") or ""
    return t if not n or t.lower().startswith(n.lower()) else f"{n}: {t}"


_DOMAINS: dict = {}


def _domain(adir: Path):
    """The atlas's own field file (atlases/<id>/atlas.py), loaded without engine.ctx so the hub can call Ask for any atlas."""
    if adir not in _DOMAINS:
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"ask_domain_{adir.name}", adir / "atlas.py")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001  (a broken field file must not break Ask)
            mod = None
        _DOMAINS[adir] = mod
    return _DOMAINS[adir]


def board_rows(adir: Path, q: str, limit: int = 12, per_paradigm: int = 6) -> list[dict]:
    """Best full-split results on a benchmark the question names (longest match wins): the overall top `limit`
    plus the top `per_paradigm` of every paradigm, so questions about one family are answerable too."""
    f = adir / "public" / "leaderboard.json"
    if not f.exists():
        return []
    rows = json.loads(f.read_text("utf-8"))
    norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower().replace("objectnav", "objnav").replace("object navigation", "objnav"))
    qn = norm(q)
    counts = Counter(r.get("bench") for r in rows if r.get("bench"))
    names = sorted(counts, key=len, reverse=True)
    ql = f" {q.lower()} "
    hit = next((b for b in names if re.search(rf"(?<![a-z0-9-]){re.escape(b.lower())}(?![a-z0-9-])", ql)), None)
    dom = _domain(adir)
    al = {**getattr(dom, "BENCH_ALIASES", {}), **getattr(dom, "ASK_ALIASES", {})}  # "VLN-CE" -> R2R-CE, "Instance-ImageNav" -> HM3D-IIN
    via = next((a for a in sorted(al, key=lambda a: len(norm(a)), reverse=True) if norm(a) in qn and al[a] in counts), None)
    if via and (not hit or (len(norm(via)), counts[al[via]]) > (len(norm(hit)), counts[hit])):  # longer match wins, then the bigger board
        hit = al[via]
    if not hit:  # spelled differently: a version the question names wins; otherwise take the variant with most results
        exact = [b for b in names if len(norm(b)) >= 4 and norm(b) in qn]
        cands = exact or [b for b in names if len(norm(b)) >= 4 and re.sub(r"v\d$", "", norm(b)) in qn]
        hit = max(cands, key=lambda b: (len(norm(b)) if exact else len(re.sub(r"v\d$", "", norm(b))), counts[b]), default=None)
    if not hit:
        return []
    from engine.rowrules import _pct, nonstandard, usable  # the rules the leaderboards use: audit verdicts override the judge
    moved = {r["id"] for r in rows if r.get("bench") == hit and r.get("audit_std") is False}
    rs = [r for r in rows if r.get("bench") == hit and r.get("eval_set") != "subset" and usable(r)]
    splits = Counter(r.get("split") for r in rs)
    named = [sp for sp in splits if sp and re.search(r"(?<![a-z0-9])" + "[- ]?".join(map(re.escape, sp.lower().split("-"))) + r"(?![a-z0-9])", q.lower())]
    split = max(named, key=len) if named else (splits.most_common(1)[0][0] if splits else None)  # the split the question names wins
    rs = [r for r in rs if split and r.get("split") == split]
    metric = next((m for m in ("SR", "SPL", "GP", "RGS", "Success") if any(m in (r.get("m") or {}) for r in rs)), None)
    if not metric:
        return []

    def val(r):
        try:
            return _pct(metric, float(str((r.get("m") or {}).get(metric)).rstrip("%"))) or 0.0
        except ValueError:
            return -1.0
    best = {}
    for r in sorted(rs, key=val, reverse=True):
        best.setdefault((r["id"], r.get("method")), r)
    ranked = list(best.values())
    keep = ranked[:limit]
    for pd in dict.fromkeys(r.get("pd") for r in ranked):
        keep += [r for r in ranked if r.get("pd") == pd][:per_paradigm]
    keep = sorted({id(r): r for r in keep}.values(), key=val, reverse=True)
    return [{"id": r["id"], "method": r.get("method"), "bench": hit, "split": r.get("split"), "metric": metric,
             "m": r.get("m"), "year": r.get("y"), "pd": r.get("pd"), "zero_shot": r.get("zs"),
             "comparable": not (nonstandard(r) or (r["id"] in moved and "audit_std" not in r)),
             "why": "" if not nonstandard(r) else (r.get("audit_note") if r.get("audit_std") is False else r.get("cmp_why")) or ""}
            for r in keep]


def context_block(papers: list[dict], rows: list[dict]) -> str:
    out = []
    for p in papers:
        head = f"[{p['id']}] {label(p)} ({p.get('vn') or 'preprint'} {p.get('y')}; " \
               f"paradigm {p.get('pd')}; tasks {', '.join(p.get('tk') or []) or '-'}; citations {p.get('c') or 0})"
        body = p["_ki"] or p.get("tl") or ""
        extra = (" Insight: " + p["_ins"][:500]) if p["_ins"] else ""
        res = (" Results: " + p["_res"][:500]) if p["_res"] else (" Abstract: " + (p.get("ab") or "")[:700] if not p["_ki"] else "")
        out.append(f"{head}\n{body}{extra}{res}")
    if rows:
        out.append("Best reported full-split results on " + rows[0]["bench"] + " " + str(rows[0]["split"]) + " by " + rows[0]["metric"] +
                   " (as reported by each paper; 'comparable' = no protocol deviation found):\n" +
                   "\n".join(f"- [{r['id']}] {r['method']} ({r['year']}, {r['pd']}{', zero-shot' if r['zero_shot'] else ''}"
                             f"{'' if r['comparable'] is not False else ', NOT directly comparable' + (': ' + r['why'][:120] if r.get('why') else '')}): "
                             + ", ".join(f"{k} {v}" for k, v in (r["m"] or {}).items()) for r in rows))
    return "\n\n".join(out)


def overview(adir: Path, q: str) -> tuple[str, set]:
    """For broad questions (trends, open problems, what changed): papers per year by paradigm, the most-cited papers
    of each paradigm, and the surveys, within the year range the question asks about."""
    if not BROAD.search(q):
        return "", set()
    idx = index(adir)
    yr = year_range(q) or (0, 9999)
    core = [p for p in idx["papers"] if p.get("sc") == "core" and yr[0] <= (p.get("y") or 0) <= yr[1]]
    if not core:
        return "", set()
    pds = list(dict.fromkeys(p.get("pd") for p in sorted(core, key=lambda p: p.get("y") or 0)))
    years = sorted({p["y"] for p in core if p.get("y")})[-10:]
    lines = ["Field overview (core papers" + (f", {yr[0] if yr[0] else '…'}–{yr[1] if yr[1] < 9999 else 'now'}" if yr != (0, 9999) else "") + "):",
             "Papers per year by paradigm: " + "; ".join(f"{y}: " + ", ".join(f"{pd} {sum(1 for p in core if p.get('y') == y and p.get('pd') == pd)}"
                                                                         for pd in pds if any(p.get('y') == y and p.get('pd') == pd for p in core)) for y in years)]
    ids = set()
    for pd in pds:
        top = sorted((p for p in core if p.get("pd") == pd), key=lambda p: -(p.get("c") or 0))[:4]
        lines.append(f"Most cited {pd}: " + "; ".join(f"[{p['id']}] {p.get('n') or p['t'][:60]} ({p.get('y')}, {p.get('c') or 0} cites)" for p in top))
        ids |= {p["id"] for p in top}
    recent = sorted((p for p in core if (p.get("y") or 0) >= max(years) - 1), key=lambda p: -(p.get("c") or 0))[:8]
    lines.append("Most cited recent papers: " + "; ".join(f"[{p['id']}] {p.get('n') or p['t'][:60]} ({p.get('y')}, {p.get('pd')})" for p in recent))
    surveys = sorted((p for p in core if "survey" in (p.get("ct") or [])), key=lambda p: -(p.get("c") or 0))[:5]
    if surveys:
        lines.append("Surveys: " + "; ".join(f"[{p['id']}] {p['t'][:90]} ({p.get('y')})" for p in surveys))
    ids |= {p["id"] for p in recent} | {p["id"] for p in surveys}
    return "\n".join(lines), ids


def system_prompt(meta: dict) -> str:
    return (f"You answer questions about {meta.get('field') or meta.get('title') or 'a research field'} using a curated "
            f"literature atlas ({meta.get('title', '')}). Use ONLY the context provided: paper entries and, when present, "
            "a table of reported benchmark results. Cite every paper you rely on inline with its bracketed id exactly as given, "
            "e.g. [2304.03047]. Never invent papers, ids or numbers. Say plainly when the context does not answer the question. "
            "Prefer specific methods, numbers and years over generalities. Answer in English Markdown, at most about 250 words "
            "unless the question asks for a list or a comparison.")


def _claude(system: str, prompt: str, model: str, timeout: int = 240) -> str:
    cmd = ["claude", "-p", "--model", model, "--system-prompt", system, "--output-format", "json",
           "--no-session-persistence", "--tools", "", "--strict-mcp-config"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd="/tmp")
    if r.returncode != 0:
        raise RuntimeError(f"claude exit {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
    env = json.loads(r.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude error: {str(env.get('result'))[:300]}")
    return env.get("result", "")


def answer(adir: Path, q: str, k: int = 24, model: str = "sonnet", llm: bool = True) -> dict:
    q = q.strip()[:500]
    t0 = time.time()
    papers = search(adir, q, k)
    rows = board_rows(adir, q)
    if rows:  # the leading entries' own notes, so the answer can say what they rely on
        by = {p["id"]: p for p in index(adir)["papers"]}
        have = {p["id"] for p in papers}
        lead = [by[r["id"]] for r in rows[:8] if r["id"] in by and r["id"] not in have]
        papers = papers[:max(8, k - len(lead))] + list({p["id"]: p for p in lead}.values())
    ov, ov_ids = overview(adir, q)
    meta = json.loads((adir / "public" / "meta.json").read_text("utf-8"))
    cited_ids = {p["id"] for p in papers} | {r["id"] for r in rows} | ov_ids
    out = {"q": q, "papers": [{"id": p["id"], "n": p.get("n"), "t": p["t"], "label": label(p), "y": p.get("y"), "v": p.get("vn")} for p in papers],
           "board": rows, "answer": None, "model": None}
    if llm and papers and shutil.which("claude"):
        cache = adir / "data" / "ask_cache.jsonl"
        h = hashlib.sha1(f"{ASK_VERSION}|{q.lower()}|{k}|{model}|{(adir / 'public' / 'papers.json').stat().st_mtime}".encode()).hexdigest()[:16]
        hit = None
        if cache.exists():
            for line in cache.read_text("utf-8").splitlines():
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("h") == h:
                    hit = o
        if hit:
            out.update(answer=hit["answer"], model=hit["model"], cached=True)
        else:
            prompt = f"Question: {q}\n\nContext:\n\n{(ov + chr(10) + chr(10)) if ov else ''}{context_block(papers, rows)}"
            text = _claude(system_prompt(meta), prompt, model)
            text = re.sub(r"\[([^\]\s]{3,40})\]", lambda m: m.group(0) if m.group(1) in cited_ids else m.group(1), text)
            out.update(answer=text, model=model)
            cache.parent.mkdir(parents=True, exist_ok=True)
            with open(cache, "a", encoding="utf-8") as fo:
                fo.write(json.dumps({"h": h, "q": q, "answer": text, "model": model, "t": int(time.time())}) + "\n")
    out["secs"] = round(time.time() - t0, 1)
    return out


def main() -> None:
    from engine import ctx
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="+")
    ap.add_argument("--k", type=int, default=24, help="papers given to the model (default 24)")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--no-llm", action="store_true", help="only list the retrieved papers")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not (ctx.PUBLIC / "papers.json").exists():
        sys.exit(f"atlas '{ctx.ATLAS_ID}' is not built yet: ./atlas build {ctx.ATLAS_ID}")
    res = answer(ctx.ADIR, " ".join(a.question), a.k, a.model, llm=not a.no_llm)
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return
    if res["answer"]:
        print(res["answer"].strip() + "\n")
    elif not a.no_llm:
        print("(no answer: the `claude` CLI was not found or nothing matched; showing the retrieved papers)\n")
    print("Papers in context:" if res["answer"] else "Most relevant papers:")
    for p in res["papers"]:
        print(f"  [{p['id']}] {p['label']} ({p['v'] or 'preprint'} {p['y']})")


if __name__ == "__main__":
    main()
