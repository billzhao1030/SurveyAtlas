"""Resolve published venues, DOIs, citation counts and official BibTeX.

1. Semantic Scholar batch API (500 ids / request) for every in-scope paper:
   venue, year, DOI, citationCount  → atlases/<id>/data/meta/s2.jsonl
2. CrossRef content negotiation for papers whose S2/OpenAlex DOI is a real
   publisher DOI (IEEE, Springer, ACM, AAAI, ...): the publisher's BibTeX
   → atlases/<id>/data/meta/crossref_bib.jsonl
Venues without DOIs (NeurIPS, ICLR, CoRL, RSS, ICML) get a canonical
@inproceedings from build.py.

Incremental: papers resolved in the last --fresh-days with a venue are skipped;
papers still without a venue are re-asked (they may have been accepted since).

(DBLP would be ideal but sits behind an anti-bot wall for scripted clients.)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from engine import ctx
from engine.ctx import CANDS, LABELS, META_DIR
from engine.venues import parse_venue

S2_OUT = META_DIR / "s2.jsonl"
CR_OUT = META_DIR / "crossref_bib.jsonl"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = "title,venue,year,publicationVenue,externalIds,citationCount,publicationTypes,publicationDate"
UA = ctx.UA


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def s2_batch(ids: list[str]) -> list[dict | None]:
    backoff = 5
    for attempt in range(10):
        try:
            r = requests.post(S2_API, params={"fields": S2_FIELDS}, json={"ids": ids}, headers=UA, timeout=120)
            if r.status_code == 200:
                return r.json()
            print(f"  s2 http {r.status_code}, retry {attempt+1}", flush=True)
        except Exception as ex:
            print(f"  s2 error {ex!r}", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 90)
    raise RuntimeError("S2 batch failed")


def crossref_bib(doi: str) -> str | None:
    url = f"https://api.crossref.org/works/{doi}/transform/application/x-bibtex"
    for attempt in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            if r.status_code == 200 and r.text.strip().startswith("@"):
                return r.text.strip()
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh-days", type=int, default=21)
    ap.add_argument("--scopes", default="core,adjacent,aerial")
    args = ap.parse_args()
    scopes = set(args.scopes.split(","))

    META_DIR.mkdir(parents=True, exist_ok=True)
    labels = {}
    for r in load_jsonl(LABELS):
        labels[r["uid"]] = r
    cands = {c["uid"]: c for c in load_jsonl(CANDS) if c.get("prefilter") == "keep"}
    want = [u for u, l in labels.items() if l["scope"] in scopes and u in cands]

    s2 = {r["uid"]: r for r in load_jsonl(S2_OUT)}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.fresh_days)).isoformat()
    todo = [u for u in want if u not in s2 or (not s2[u].get("venue_ok") and s2[u]["at"] < cutoff)]
    print(f"in-scope={len(want)} s2-cached={len(s2)} todo={len(todo)}", flush=True)

    def s2_id(u: str) -> str | None:
        c = cands[u]
        if c.get("arxiv_id"):
            return f"ARXIV:{c['arxiv_id']}"
        if c.get("doi"):
            return f"DOI:{c['doi']}"
        return None

    todo = [u for u in todo if s2_id(u)]
    for i in range(0, len(todo), 450):
        chunk = todo[i : i + 450]
        res = s2_batch([s2_id(u) for u in chunk])
        ts = now()
        for u, r in zip(chunk, res):
            row = {"uid": u, "at": ts, "found": r is not None}
            if r:
                ext = r.get("externalIds") or {}
                pv = r.get("publicationVenue") or {}
                venue_txt = " | ".join(x for x in [r.get("venue"), pv.get("name"), *(pv.get("alternate_names") or [])[:3]] if x)
                doi = ext.get("DOI")
                row.update(
                    s2id=r.get("paperId"), title=r.get("title"), venue=r.get("venue"), pv_name=pv.get("name"),
                    pv_type=pv.get("type"), year=r.get("year"), date=r.get("publicationDate"),
                    doi=doi if doi and "arxiv" not in doi.lower() else None, dblp=ext.get("DBLP"),
                    acl=ext.get("ACL"), cites=r.get("citationCount") or 0, types=r.get("publicationTypes"),
                )
                v = parse_venue(r.get("venue")) or parse_venue(pv.get("name"))
                row["venue_ok"] = bool(v)
                if v:
                    row["canon"] = v
            s2[u] = row
        with S2_OUT.open("w") as f:
            for r in s2.values():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  s2 {i+len(chunk)}/{len(todo)}  (venue found so far: {sum(1 for r in s2.values() if r.get('venue_ok'))})", flush=True)
        time.sleep(1.5)

    # ── CrossRef BibTeX for publisher DOIs ──
    cr = {r["uid"]: r for r in load_jsonl(CR_OUT)}
    jobs = []
    for u in want:
        r = s2.get(u) or {}
        doi = r.get("doi") if r.get("venue_ok") else None
        if not doi:
            c = cands[u]
            if c.get("doi") and "arxiv" not in c["doi"].lower() and not c.get("arxiv_id"):
                doi = c["doi"]
        if doi and (u not in cr or cr[u].get("doi") != doi):
            jobs.append((u, doi))
    print(f"crossref bibtex todo={len(jobs)}", flush=True)

    def work(job):
        u, doi = job
        return u, doi, crossref_bib(doi)

    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for u, doi, bib in ex.map(work, jobs):
            cr[u] = {"uid": u, "doi": doi, "bibtex": bib, "at": now()}
            done += 1
            if done % 100 == 0:
                print(f"  crossref {done}/{len(jobs)}", flush=True)
    with CR_OUT.open("w") as f:
        for r in cr.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"done: s2={len(s2)} venue_ok={sum(1 for r in s2.values() if r.get('venue_ok'))} crossref_bib={sum(1 for r in cr.values() if r.get('bibtex'))}")


if __name__ == "__main__":
    main()
