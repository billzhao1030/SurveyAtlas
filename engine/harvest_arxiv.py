"""Harvest candidate papers from the arXiv API.

Runs every query in config.ARXIV_QUERIES, pages through all hits, and merges
them into data/raw/arxiv.jsonl keyed by base arXiv id. Incremental: re-running
adds new papers, refreshes metadata (newer version / comment / journal_ref) and
appends newly matching query keys; nothing is ever dropped.

  ./atlas harvest <id>                    # all queries of the atlas + seeds
  ./atlas harvest <id> --only objnav      # one query key
  ./atlas harvest <id> --since 2026-06    # stop paging a query once results are older (cron mode)
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from engine import ctx
from engine.ctx import RAW_ARXIV, SEEDS

ARXIV_QUERIES = ctx.D.ARXIV_QUERIES
ARXIV_DELAY_S = 3.2   # arXiv asks for >= 3 s between calls
ARXIV_PAGE = 500

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}
API = "https://export.arxiv.org/api/query"
ID_RE = re.compile(r"abs/([^v]+?)(v(\d+))?$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_entry(e: ET.Element) -> dict:
    raw_id = e.findtext("a:id", default="", namespaces=NS)
    m = ID_RE.search(raw_id)
    aid, ver = (m.group(1), int(m.group(3) or 1)) if m else (raw_id, 1)
    prim = e.find("arxiv:primary_category", NS)
    return {
        "id": aid,
        "version": ver,
        "title": clean(e.findtext("a:title", namespaces=NS)),
        "abstract": clean(e.findtext("a:summary", namespaces=NS)),
        "authors": [clean(a.findtext("a:name", namespaces=NS)) for a in e.findall("a:author", NS)],
        "published": e.findtext("a:published", namespaces=NS),
        "updated": e.findtext("a:updated", namespaces=NS),
        "categories": [c.get("term") for c in e.findall("a:category", NS)],
        "primary_category": prim.get("term") if prim is not None else None,
        "comment": clean(e.findtext("arxiv:comment", namespaces=NS)) or None,
        "journal_ref": clean(e.findtext("arxiv:journal_ref", namespaces=NS)) or None,
        "doi": clean(e.findtext("arxiv:doi", namespaces=NS)) or None,
    }


def fetch_page(query: str, start: int, size: int, id_list: str = "") -> tuple[int, list[dict]]:
    params = {
        "search_query": query,
        "id_list": id_list,
        "start": start,
        "max_results": size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    backoff = 10
    for attempt in range(12):
        try:
            r = requests.get(url, timeout=90)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                total = int(root.findtext("os:totalResults", default="0", namespaces=NS))
                entries = [parse_entry(e) for e in root.findall("a:entry", NS)]
                # arXiv occasionally returns an empty page mid-stream; retry those.
                if entries or start >= total:
                    return total, entries
            print(f"    http {r.status_code} / empty page, retry {attempt+1}", flush=True)
        except Exception as ex:  # network / XML hiccup
            print(f"    error {ex!r}, retry {attempt+1}", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 180)
    raise RuntimeError(f"giving up on {query!r} start={start}")


def load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if RAW_ARXIV.exists():
        for line in RAW_ARXIV.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r
    return out


def save(papers: dict[str, dict]) -> None:
    RAW_ARXIV.parent.mkdir(parents=True, exist_ok=True)
    tmp = RAW_ARXIV.with_suffix(".tmp")
    with tmp.open("w") as f:
        for p in sorted(papers.values(), key=lambda r: r["id"]):
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    tmp.replace(RAW_ARXIV)


def merge(papers: dict[str, dict], rec: dict, qkey: str, ts: str) -> bool:
    old = papers.get(rec["id"])
    if old is None:
        rec["queries"] = [qkey]
        rec["first_seen"] = ts
        papers[rec["id"]] = rec
        return True
    if qkey not in old["queries"]:
        old["queries"].append(qkey)
    if rec["version"] >= old.get("version", 1):
        keep = {k: old[k] for k in ("queries", "first_seen")}
        old.update(rec)
        old.update(keep)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="query keys to run")
    ap.add_argument("--since", help="YYYY-MM; stop paging once results are older")
    ap.add_argument("--seeds-only", action="store_true")
    ap.add_argument("--probe", action="store_true", help="only print each query's totalResults (no harvesting)")
    args = ap.parse_args()
    if not args.probe:
        ctx.require_ready()

    if args.probe:  # size check before a first harvest (playbook step 2)
        tot = 0
        for key, query, _ in [q for q in ARXIV_QUERIES if not args.only or q[0] in args.only]:
            n, _ = fetch_page(query, 0, 1)
            tot += n
            print(f"{n:7d}  {key:14s} {query[:90]}", flush=True)
            time.sleep(ARXIV_DELAY_S + 0.8)
        print(f"{tot:7d}  total hits (before de-duplication)")
        return
    papers = load()
    print(f"loaded {len(papers)} existing papers", flush=True)
    if SEEDS.exists():
        seeds = [l.split("#")[0].strip() for l in SEEDS.read_text().splitlines()]
        for x in seeds:  # already harvested: just tag it so merge never prefilters it out
            if x in papers and "seed" not in papers[x]["queries"]:
                papers[x]["queries"].append("seed")
        seeds = [x for x in seeds if x and x not in papers]
        for i in range(0, len(seeds), 100):
            _, entries = fetch_page("", 0, 100, id_list=",".join(seeds[i : i + 100]))
            for rec in entries:
                merge(papers, rec, "seed", now())
            time.sleep(ARXIV_DELAY_S)
        print(f"[seed        ] fetched {len(seeds)} seed ids | corpus={len(papers)}", flush=True)
        save(papers)
    if args.seeds_only:
        return
    queries = [q for q in ARXIV_QUERIES if not args.only or q[0] in args.only]
    ts = now()
    failed: list[str] = []
    for key, query, _ in queries:
        try:
            total, n_seen, n_new = harvest_query(papers, key, query, ts, args.since)
        except RuntimeError as ex:  # one broken query must not cost the others
            print(f"[{key:12s}] FAILED ({ex}) — kept what was fetched; rerun with --only {key}", flush=True)
            failed.append(key)
            save(papers)
            continue
        print(f"[{key:12s}] total={total:5d} seen={n_seen:5d} new={n_new:5d} | corpus={len(papers)}", flush=True)
        save(papers)
    print(f"done: {len(papers)} papers in {RAW_ARXIV}", flush=True)
    if failed:
        sys.exit(f"{len(failed)} queries failed: {' '.join(failed)}")


# arXiv's API stops returning entries beyond ~10,000 results for one query; bigger queries are
# split into submission-date windows (years, then months) and harvested window by window.
ARXIV_MAX = 9500


def harvest_query(papers: dict, key: str, query: str, ts: str, since: str | None, window: tuple[str, str] | None = None) -> tuple[int, int, int]:
    q = f"({query}) AND submittedDate:[{window[0]}0000 TO {window[1]}2359]" if window else query
    total, entries = fetch_page(q, 0, ARXIV_PAGE)
    time.sleep(ARXIV_DELAY_S)
    if total > ARXIV_MAX and (window is None or window[0][4:] == "0101" and window[1][4:] == "1231"):
        if window is None:  # → years
            first = max(1991, int(since[:4]) if since else ctx.meta("min_year", 2010))
            subs = [(f"{y}0101", f"{y}1231") for y in range(first, datetime.now().year + 1)]
        else:  # a year still too big → months
            y = window[0][:4]
            subs = [(f"{y}{m:02d}01", f"{y}{m:02d}{calendar.monthrange(int(y), m)[1]:02d}") for m in range(1, 13)]
        print(f"    {key}: {total} results{' in ' + window[0][:4] if window else ''} > {ARXIV_MAX}: "
              f"splitting into {len(subs)} date windows", flush=True)
        agg = [0, 0, 0]
        for w in subs:
            t, a, b = harvest_query(papers, key, query, ts, since, w)
            agg = [agg[0] + t, agg[1] + a, agg[2] + b]
        return agg[0], agg[1], agg[2]
    start, n_new, n_seen = 0, 0, 0
    while True:
        stop = False
        for rec in entries:
            if since and rec["published"][:7] < since:
                stop = True
                break
            n_seen += 1
            n_new += merge(papers, rec, key, ts)
        start += ARXIV_PAGE
        if stop or start >= total or not entries or start >= ARXIV_MAX + ARXIV_PAGE:
            break
        total, entries = fetch_page(q, start, ARXIV_PAGE)
        time.sleep(ARXIV_DELAY_S)
    return total, n_seen, n_new


if __name__ == "__main__":
    main()
