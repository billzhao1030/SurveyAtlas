"""Merge harvest channels into one candidate table: atlases/<id>/data/candidates.jsonl.

uid = arXiv id when the paper has one, else "oa:<OpenAlex id>". OpenAlex rows
are folded into the arXiv row with the same id or the same normalised title;
what's left is venue-only work (no arXiv preprint).

A cheap rule prefilter (regexes from the atlas's domain file) tags rows that can't be in the field
(`prefilter: "drop:<why>"`) so the classifier never sees them.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from engine import ctx
from engine.ctx import CANDS as OUT, RAW_ARXIV, RAW_OA

D = ctx.D
RELEVANCE_RE = D.RELEVANCE_RE        # must appear somewhere in title + abstract
OA_STRICT_RE = D.OA_STRICT_RE        # OpenAlex-only rows need a strict field phrase…
OA_STRICT_CS_RE = D.OA_STRICT_CS_RE  # …or a case-sensitive field acronym
OK_CATS = getattr(D, "OK_CATS", ("cs.", "eess.", "stat.ML"))
MIN_YEAR = ctx.meta("min_year", 2010)
BAD_OA_TYPES = {"dataset", "paratext", "peer-review", "erratum", "editorial", "retraction", "supplementary-materials", "libguides", "other", "letter", "grant"}


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:120]


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def main() -> None:
    arx = {r["id"]: r for r in load(RAW_ARXIV)}
    oa = load(RAW_OA)
    by_title = {norm_title(r["title"]): aid for aid, r in arx.items()}

    cands: dict[str, dict] = {}
    for aid, r in arx.items():
        cands[aid] = {
            "uid": aid,
            "arxiv_id": aid,
            "title": r["title"],
            "abstract": r["abstract"],
            "authors": r["authors"],
            "date": (r.get("published") or "")[:10],
            "year": int((r.get("published") or "0000")[:4]),
            "categories": r.get("categories", []),
            "comment": r.get("comment"),
            "journal_ref": r.get("journal_ref"),
            "doi": r.get("doi"),
            "sources": ["arxiv"],
            "first_seen": (r.get("first_seen") or "")[:10],
            "queries": list(r.get("queries", [])),
            "oa_ids": [],
        }

    n_fold, n_new = 0, 0
    for o in oa:
        key = o.get("arxiv_id")
        if key and key not in cands:
            # arXiv id known to OpenAlex but not hit by our arXiv queries.
            # Keep as an arXiv-keyed row (abstract from OpenAlex).
            pass
        tgt = cands.get(key) if key else None
        if tgt is None:
            tid = by_title.get(norm_title(o["title"]))
            tgt = cands.get(tid) if tid else None
        if tgt is not None:
            n_fold += 1
            if o["oa_id"] not in tgt["oa_ids"]:
                tgt["oa_ids"].append(o["oa_id"])
            if "openalex" not in tgt["sources"]:
                tgt["sources"].append("openalex")
            tgt["queries"] += [f"oa:{q}" for q in o["queries"] if f"oa:{q}" not in tgt["queries"]]
            continue
        uid = key or f"oa:{o['oa_id']}"
        if uid in cands:  # second OpenAlex record for the same venue-only paper
            cands[uid]["oa_ids"].append(o["oa_id"])
            continue
        n_new += 1
        cands[uid] = {
            "uid": uid,
            "arxiv_id": key,
            "title": o["title"],
            "abstract": o.get("abstract") or "",
            "authors": o.get("authors", []),
            "date": o.get("date") or "",
            "year": o.get("year") or 0,
            "categories": [],
            "comment": None,
            "journal_ref": None,
            "doi": o.get("doi"),
            "sources": ["openalex"],
            "oa_type": o.get("type"),
            "queries": [f"oa:{q}" for q in o["queries"]],
            "oa_ids": [o["oa_id"]],
        }
        by_title[norm_title(o["title"])] = uid

    # Title-level dedup among OpenAlex-only rows (conference + journal copies).
    seen: dict[str, str] = {}
    for uid in list(cands):
        if not uid.startswith("oa:"):
            continue
        nt = norm_title(cands[uid]["title"])
        if nt in seen:
            keep = cands[seen[nt]]
            keep["oa_ids"] += cands[uid]["oa_ids"]
            del cands[uid]
        else:
            seen[nt] = uid

    stats = {"keep": 0}
    for c in cands.values():
        text = f"{c['title']} {c['abstract']}"
        why = None
        if not c["title"]:
            why = "no-title"
        elif not RELEVANCE_RE.search(text):
            why = "no-nav-term"
        elif c.get("oa_type") in BAD_OA_TYPES or re.match(r"(dataset|supplementary|data) (from|for) ", c["title"], re.I):
            why = "oa-type"
        elif c["sources"] == ["openalex"] and not (OA_STRICT_RE.search(text) or OA_STRICT_CS_RE.search(text)):
            why = "oa-weak"
        elif c["categories"] and not any(cat.startswith(OK_CATS) for cat in c["categories"]):
            why = "category"
        elif not c["abstract"] and c["sources"] == ["openalex"] and c["year"] and c["year"] < 2015:
            why = "old-no-abstract"
        elif c["year"] and c["year"] < MIN_YEAR:
            why = f"pre-{MIN_YEAR}"
        if "seed" in c["queries"]:  # hand-picked ids always reach the classifier
            why = None
        c["prefilter"] = f"drop:{why}" if why else "keep"
        stats[c["prefilter"]] = stats.get(c["prefilter"], 0) + 1

    with OUT.open("w") as f:
        for c in sorted(cands.values(), key=lambda r: (r["date"] or "", r["uid"]), reverse=True):
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"arxiv={len(arx)} openalex={len(oa)} folded={n_fold} oa_only={n_new} -> candidates={len(cands)}")
    print("prefilter:", stats)


if __name__ == "__main__":
    main()
