"""Stage 2 — deep reading: one headless Claude call per paper, full text in, structured notes out.

  ./atlas read <id>                       read every in-scope paper not yet read by the CURRENT method version
                                          (older readings are redone; a paused run resumes where it stopped)
  ./atlas read <id> --limit 20            a slice
  ./atlas read <id> --scopes core         only these scopes (e.g. core first, adjacent later)
  ./atlas read <id> --ids 2402.15852 ...  specific papers (re-read with --force)
  ./atlas read <id> --redo-unverified     re-read (with Opus) papers whose numbers failed verification
  ./atlas read <id> --status              progress only
  ./atlas read <id> --anytime             ignore the reading window (local.json "read_window": "23:30-08:00")
  ./atlas read <id> --links               find arXiv versions / open-access PDFs for papers without an arXiv id

Each atlas reads with its own prompt, atlases/<id>/read_system.md (benchmark conventions, metric keys and setting
fields differ per field); engine/prompts/read_system.md is only the fallback for an atlas without one.
Writes atlases/<id>/data/reading/<uid>.json (schema: the atlas's prompt);
`./atlas build` embeds it into the paper page and the results leaderboards.

Careful by design: full text (tables always kept), a quoted "evidence" for every result row, and
an automatic check that every reported number actually occurs in the paper's text (`verify`).
Landmark / most-cited papers are read with Opus, the rest with Sonnet. Usage-limit errors make
the loop sleep until the limit resets and then continue — it is meant to run for days.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from engine import ctx, fulltext, limits, taxonomy

READING = ctx.READING
LOG = ctx.LOGS / "read.log"
PROMPT_F = ctx.ADIR / "read_system.md" if (ctx.ADIR / "read_system.md").exists() else Path(__file__).parent / "prompts" / "read_system.md"
PROMPT = PROMPT_F.read_text()
PROMPT_ID = f"{ctx.ATLAS_ID if PROMPT_F.parent == ctx.ADIR else 'default'}:{hashlib.sha1(PROMPT.encode()).hexdigest()[:8]}"
SYSTEM = (PROMPT.replace("{FIELD}", ctx.meta("field", ctx.ATLAS_ID))
          .replace("{BENCHMARKS}", ", ".join(b for names in taxonomy.BENCHMARKS.values() for b in names)))
# Bump when the reading method / schema changes: papers read by an older version are read again.
READ_VERSION = "deepread-v1 (2026-09-29): full text + insight + evidence-checked numbers"
BUDGET = 150_000  # chars of paper text sent (~40k tokens); longer papers lose their appendix first
_log_lock = threading.Lock()
ANYTIME = False  # --anytime: ignore local.json "read_window"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"{datetime.now():%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with _log_lock:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")


def rfile(uid: str) -> Path:
    return READING / f"{uid.replace('/', '_').replace(':', '_')}.json"


def prepare(text: str, source: str) -> str:
    text = fulltext.clean(text, source)
    if source == "abstract" or len(text) <= BUDGET:
        return text
    # drop the appendix first; if still too long fall back to head + all tables + tail
    m = re.search(r"\\appendix|\n\s*(Appendix|APPENDIX|Supplementary Material)\s*\n", text)
    if m and m.start() > 20_000:
        body, app = text[: m.start()], text[m.start():]
        tables = "\n\n".join(x.group(0) for x in fulltext.TABLE_RE.finditer(app))
        cand = body + ("\n\n=== APPENDIX TABLES ===\n" + tables if tables else "")
        if len(cand) <= BUDGET:
            return cand
        text = body
    return fulltext.condense(text, source, BUDGET)


NUM_RE = re.compile(r"(?<![\w.])(\d{1,4}(?:\.\d+)?)(?![\w.])")


def number_pool(text: str) -> set[float]:
    pool = set()
    text = re.sub(r"(\\[a-zA-Z]+)(\d)", r"\1 \2", text)  # "\bf54.0" / "\textbf54" → keep the number
    for m in NUM_RE.finditer(text.replace(",", "")):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        pool.update({round(v, 2), round(v * 100, 2)})  # 0.571 printed ↔ 57.1 reported
    return pool


def verify(numbers: list[dict], pool: set[float]) -> dict:
    total = ok = 0
    for row in numbers:
        good = 0
        for v in (row.get("metrics") or {}).values():
            try:
                f = round(float(v), 2)
            except (TypeError, ValueError):
                continue
            total += 1
            if f in pool or round(f, 1) in pool or float(int(f)) == f and f in pool:
                ok += 1
                good += 1
        n = len(row.get("metrics") or {})
        row["verified"] = (good == n) if n else None
    return {"metrics": total, "verified": ok, "rows_unverified": sum(1 for r in numbers if r.get("verified") is False)}


def call_claude(user: str, model: str) -> dict:
    cmd = ["claude", "-p", "--model", model, "--system-prompt", SYSTEM, "--output-format", "json",
           "--no-session-persistence", "--tools", "", "--strict-mcp-config"]
    env = {**os.environ, "PATH": os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])}
    r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=1800, cwd="/tmp", env=env)
    try:
        envl = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"exit {r.returncode}: {(r.stderr or r.stdout)[-400:]}")
    if envl.get("is_error") or r.returncode:
        raise RuntimeError(str(envl.get("result") or envl)[:400])
    s = envl.get("result", "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(s[s.find("{"): s.rfind("}") + 1])


def read_one(p: dict, model: str, patient: bool = True) -> str:
    try:
        return _read_one(p, model, patient)
    except Exception as ex:  # one broken paper must never stop the run
        log(f"  {p['id']} crashed: {type(ex).__name__}: {str(ex)[:160]}")
        return "failed"


def _read_one(p: dict, model: str, patient: bool) -> str:
    if not ANYTIME:
        limits.wait_for_window(log)  # local.json "read_window": only start new papers inside that window
    try:
        text, source = fulltext.get_text(p, patient)
    except fulltext.FetchLater:
        return "later"  # download dropped / throttled: retried at the end of the run
    body = prepare(text, source)
    head = (f"Paper id: {p['id']}\nTitle: {p['t']}\nYear: {p.get('y')}  Venue: {p.get('v') or 'arXiv preprint'}\n"
            f"Authors: {', '.join((p.get('a') or [])[:8])}\nText source: {source} ({len(body):,} chars)\n\n"
            f"<<<PAPER\n{body}\nPAPER>>>")
    attempt = 0
    while True:
        limits.wait_if_paused()
        try:
            data = call_claude(head, model)
            break
        except Exception as ex:
            if limits.pause_for(str(ex), log):
                continue  # not counted as an attempt
            attempt += 1
            log(f"  {p['id']} attempt {attempt} failed: {str(ex)[:160]}")
            if attempt >= 6:
                return "failed"
            time.sleep(20 * attempt)
    data.setdefault("numbers", [])
    pool = number_pool(text) if source != "abstract" else None
    data["verify"] = verify(data["numbers"], pool) if pool else {"metrics": 0, "verified": 0, "rows_unverified": 0}
    if pool and isinstance(data.get("own_tasks"), list) and data["own_tasks"]:  # results on the paper's own task suites
        data["verify_own"] = verify([r for r in data["own_tasks"] if isinstance(r, dict)], pool)
    data.update(read_at=now(), model=model, source=source, chars=len(body), version=READ_VERSION, prompt=PROMPT_ID)
    if source == "abstract" and not patient:
        data["abstract_reason"] = "no full text after repeated download attempts"
    READING.mkdir(parents=True, exist_ok=True)
    tmp = rfile(p["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, rfile(p["id"]))
    v = data["verify"]
    log(f"✓ {p['id']} [{model}/{source}] {p.get('n') or p['t'][:50]} — {len(data['numbers'])} result rows, "
        f"{v['verified']}/{v['metrics']} numbers verified")
    return "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--anytime", action="store_true", help="ignore the reading window in local.json (\"read_window\": \"23:30-08:00\")")
    ap.add_argument("--scopes", nargs="*", help="read only papers in these scopes (default: every scope not in read_skip_scopes)")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--opus-top", type=int, default=ctx.meta("read_opus_top", 300), help="read the N most-cited + landmark papers with Opus")
    ap.add_argument("--redo-unverified", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reverify", action="store_true", help="re-run the number check of every existing reading against its cached text (no model calls)")
    ap.add_argument("--links", action="store_true", help="only look up full texts for papers without an arXiv id "
                    "(incl. the slow arXiv title search), then exit; a running reader picks them up on restart")
    a = ap.parse_args()
    global ANYTIME
    ANYTIME = a.anytime

    papers = json.loads((ctx.PUBLIC / "papers.json").read_text())
    from engine import shared
    shared.sync_readings(papers)  # show other atlases' notes now; with its own prompt this atlas still reads them itself
    skip = set(ctx.meta("read_skip_scopes", []))
    scope = [p for p in papers if p["sc"] not in skip]
    order = {"core": 0, "adjacent": 1}
    scope.sort(key=lambda p: (order.get(p["sc"], 2), -(p.get("c") or 0), -(p.get("y") or 0)))
    landmarks = {x[0] for x in getattr(ctx.D, "LANDMARKS", [])}
    opus = landmarks | {p["id"] for p in sorted(scope, key=lambda p: -(p.get("c") or 0))[: a.opus_top]}

    if a.reverify:
        n = changed = 0
        for p in scope:
            f = rfile(p["id"])
            if not f.exists():
                continue
            d = json.loads(f.read_text())
            if d.get("source") == "abstract" or not d.get("numbers"):
                continue
            try:
                text, _ = fulltext.get_text(p, False)
            except Exception:
                continue
            before = d.get("verify", {}).get("verified")
            d["verify"] = verify(d["numbers"], number_pool(text))
            n += 1
            if d["verify"]["verified"] != before:
                changed += 1
                f.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        print(f"re-verified {n} readings; {changed} changed")
        return
    if a.links:
        fulltext.resolve_links(scope, log, title_search=True)
        return
    if not a.status:
        fulltext.resolve_links(scope, log)
    L = fulltext.links()
    ax_of = {p["id"]: p.get("ax") for p in scope}

    def current(uid: str) -> bool:  # read by THIS method version (older / other readings get redone)
        f = rfile(uid)
        if not f.exists():
            return False
        try:
            d = json.loads(f.read_text())
        except ValueError:
            return False
        lk = L.get(uid) or {}
        if d.get("source") == "abstract" and (lk.get("ax") or lk.get("pdf")) and lk.get("at", "") > d.get("read_at", ""):
            return False  # read from the abstract only, but a full text has been found since
        if d.get("source") == "abstract" and ax_of.get(uid) and not d.get("abstract_reason"):
            return False  # has an arXiv id but the download failed that time: try again
        if d.get("shared_from") and PROMPT_F.parent == ctx.ADIR:
            return False  # a copy from another atlas: shown until this atlas reads the paper with its own prompt
        return d.get("version") == READ_VERSION

    done = {p["id"] for p in scope if current(p["id"])}
    if a.status:
        ab = sum(1 for p in scope if p["id"] in done and json.loads(rfile(p["id"]).read_text()).get("source") == "abstract")
        print(f"abstract-only readings: {ab}")
        by = {}
        for p in scope:
            by.setdefault(p["sc"], [0, 0])[0] += 1
            by[p["sc"]][1] += p["id"] in done
        print(" · ".join(f"{k}: {v[1]}/{v[0]} read" for k, v in by.items()), f"(skipped scopes: {', '.join(skip) or 'none'})")
        return

    if a.ids:
        todo = [p for p in papers if p["id"] in set(a.ids)]
        todo = [p for p in todo if a.force or p["id"] not in done]
    elif a.redo_unverified:
        todo = []
        for p in scope:
            f = rfile(p["id"])
            if f.exists():
                d = json.loads(f.read_text())
                if d.get("verify", {}).get("rows_unverified") and d.get("model") != "opus":
                    todo.append(p)
        opus |= {p["id"] for p in todo}
    else:
        todo = [p for p in scope if p["id"] not in done]
        # papers with no known full text go last: `--links` (arXiv title search) may still find one
        todo.sort(key=lambda p: not (p.get("ax") or (L.get(p["id"]) or {}).get("ax") or (L.get(p["id"]) or {}).get("pdf")))
    if a.scopes and not a.ids:
        todo = [p for p in todo if p["sc"] in set(a.scopes)]
    if a.limit:
        todo = todo[: a.limit]

    if not a.ids:  # a full run is exclusive; reading named papers next to it is safe (different papers)
        lockf = open(ctx.DATA / ".read.lock", "w")
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("another reader is already running for this atlas (use --ids to read specific papers next to it)")
    log(f"== read {ctx.ATLAS_ID}: {len(todo)} to read ({len(done)} already done){' in ' + '+'.join(a.scopes) if a.scopes else ''}, "
        f"workers={a.workers}, opus for {len(opus & {p['id'] for p in todo})}, prompt {PROMPT_ID}"
        + ("" if a.anytime or not limits.window() else f", window {json.loads(limits.LOCAL.read_text())['read_window']}"))
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(lambda p: read_one(p, "opus" if p["id"] in opus else a.model), todo))
        # papers whose download was dropped: a few more rounds later; the last one settles for PDF / abstract
        for rnd in range(3):
            later = [p for p, r in zip(todo, res) if r == "later"]
            if not later:
                break
            log(f"== {len(later)} papers had download problems; retrying in 10 min (round {rnd + 1}/3)")
            time.sleep(600)
            again = list(ex.map(lambda p: read_one(p, "opus" if p["id"] in opus else a.model, patient=rnd < 2), later))
            back = dict(zip((p["id"] for p in later), again))
            res = [back.get(p["id"], r) for p, r in zip(todo, res)]
    log(f"== done: {res.count('ok')} read, {res.count('failed')} failed, {res.count('later')} still without a download")


if __name__ == "__main__":
    main()
