"""Second harvest channel: OpenAlex title/abstract search.

Why: arXiv search misses venue-only papers (no preprint) and is rate-limited;
OpenAlex also carries venue locations and citation counts. Output rows are
keyed by OpenAlex work id and carry `arxiv_id` when any location points at
arXiv, so build.py can merge them with the arXiv channel.

  ./atlas harvest-oa <id>
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

from engine import ctx

OUT = ctx.RAW_OA
API = "https://api.openalex.org/works"
SELECT = "id,doi,title,publication_year,publication_date,type,primary_location,locations,ids,cited_by_count,authorships,abstract_inverted_index"

OA_KEY = ctx._local().get("openalex_api_key", "")  # optional free key: https://openalex.org (avoids anonymous throttling)
QUERIES = ctx.D.OPENALEX_QUERIES  # stemmed + case-insensitive: keep them narrow


def abstract_from_index(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos))


ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+/[0-9]{7})", re.I)
ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})", re.I)


def slim(w: dict, qkey: str) -> dict:
    arxiv_id = None
    venues = []
    for loc in w.get("locations") or []:
        url = loc.get("landing_page_url") or ""
        m = ARXIV_RE.search(url) or ARXIV_DOI_RE.search(url)
        if m:
            arxiv_id = arxiv_id or m.group(1)
        src = loc.get("source") or {}
        if src.get("display_name") and src.get("type") in ("conference", "journal", "book series"):
            venues.append({"name": src["display_name"], "type": src.get("type"), "url": url})
    if not arxiv_id and w.get("doi"):
        m = ARXIV_DOI_RE.search(w["doi"])
        if m:
            arxiv_id = m.group(1)
    return {
        "oa_id": w["id"].rsplit("/", 1)[-1],
        "arxiv_id": arxiv_id,
        "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None,
        "title": re.sub(r"\s+", " ", w.get("title") or "").strip(),
        "year": w.get("publication_year"),
        "date": w.get("publication_date"),
        "type": w.get("type"),
        "venues": venues,
        "cited_by": w.get("cited_by_count", 0),
        "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])][:40],
        "abstract": abstract_from_index(w.get("abstract_inverted_index")),
        "queries": [qkey],
    }


def main() -> None:
    ctx.require_ready()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            r = json.loads(line)
            rows[r["oa_id"]] = r
    failed: list[str] = []

    def save_rows() -> None:
        with OUT.open("w") as f:
            for r in rows.values():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    for key, q in QUERIES:
        cursor, n = "*", 0
        while cursor:
            params = {
                "filter": f"title_and_abstract.search:{q}",
                "per-page": 200,
                "cursor": cursor,
                "select": SELECT,
            }
            if ctx.CONTACT:
                params["mailto"] = ctx.CONTACT  # polite pool
            if OA_KEY:
                params["api_key"] = OA_KEY
            for attempt in range(30):
                try:
                    r = requests.get(API, params=params, timeout=60)
                    if r.status_code == 200:
                        break
                    if attempt % 5 == 0:
                        print(f"  http {r.status_code}: {r.text[:160]}", flush=True)
                    if re.search(r"insufficient budget|daily budget", r.text, re.I):
                        save_rows()
                        sys.exit("OpenAlex: the free daily budget for this IP is used up (resets at midnight UTC). "
                                 "Rerun `./atlas harvest-oa <id>` after the reset, or put a free key in local.json "
                                 "as \"openalex_api_key\" (https://openalex.org). Finished queries are kept.")
                    # anonymous search is throttled under load; the message says how long to wait
                    m = re.search(r"retry in (\d+)\s*s", r.text)
                    time.sleep(int(m.group(1)) + 3 if m else min(10 * (attempt + 1), 300))
                    continue
                except Exception as ex:
                    print(f"  error {ex!r}", flush=True)
                time.sleep(min(10 * (attempt + 1), 300))
            else:
                print(f"  giving up on query {key} for now (rerun harvest-oa later; finished queries are kept)", flush=True)
                failed.append(key)
                break
            d = r.json()
            for w in d["results"]:
                s = slim(w, key)
                if s["oa_id"] in rows:
                    if key not in rows[s["oa_id"]]["queries"]:
                        rows[s["oa_id"]]["queries"].append(key)
                    s["queries"] = rows[s["oa_id"]]["queries"]
                rows[s["oa_id"]] = s
                n += 1
            cursor = d["meta"].get("next_cursor") if d["results"] else None
            time.sleep(0.3)
        if key not in failed:
            print(f"[{key:10s}] count={d['meta']['count']:5d} fetched={n:5d} | total={len(rows)}", flush=True)
        save_rows()
    print("done", len(rows) , f"— {len(failed)} queries failed: {', '.join(failed)}" if failed else "")


if __name__ == "__main__":
    main()
