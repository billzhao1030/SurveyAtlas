"""Audit the results that decide the rankings: each top row is re-checked against the paper's own text.

  ./atlas audit <id> <survey id>            audit the top rows of every table / figure benchmark in the survey
  ./atlas audit <id> <survey id> --top 20   rows per benchmark (default 15; one row per paper, ranked by the key metric)

The automatic pipeline (deep reading → comparability judge) is good on average but the head of a ranking is
exactly where a mis-assigned benchmark, a hidden privileged input or a subset silently wins. For every row
that is currently among the top N comparable results of a survey benchmark, Opus reads the row next to an
excerpt of the paper (the table the evidence points to) and answers: is the benchmark / split right, is the
number the paper's own method on the full split, and is the setting standard for this benchmark?
Verdicts are cached in data/meta/audit.json (keyed by paper + benchmark + split + method + value);
`build` copies them into leaderboard.json (audit_std / audit_note / audit_ok) and the survey tables and
figures honour them (a row whose number or benchmark is wrong is dropped).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import ctx, fulltext, limits

CACHE = ctx.META_DIR / "audit.json"
_lock = threading.Lock()

SYSTEM = """You audit one reported result for a results table in a survey of {field}. You get the row as it was
extracted from the paper, the benchmark's standard protocol, and an excerpt of the paper around the table
the row came from. Check it against the excerpt, strictly:

1. number_ok: does the excerpt show this value for this method on this benchmark and split? (false if the
   value belongs to another benchmark/split/method, or the excerpt contradicts it; null if the excerpt does
   not contain the table)
2. bench_ok: is it really the named benchmark and split (e.g. R2R-CE = VLN-CE continuous R2R, not discrete
   R2R; HM3D-ObjNav = Habitat ObjectNav on HM3D val, not a re-discretised or custom episode set)?
3. full_split: is it evaluated on the full official split (not a sampled subset)?
4. standard: is the setting the benchmark's standard protocol, directly comparable to other papers? Extra
   training data is fine. Not standard: pre-exploration / pre-built maps of test scenes, beam search or
   several attempts, oracle / ground-truth information the protocol does not provide, test-time feedback,
   ensembles, changed success radius / step budget / action space / sensors, custom simulators.

Protocol notes: {protocols}

Return ONLY JSON: {{"number_ok": true|false|null, "bench_ok": true|false, "full_split": true|false,
"standard": true|false, "note": "<= 25 words: what is wrong, or 'ok'"}}"""


def key(r: dict) -> str:
    sig = f"{r['id']}\x1f{r['bench']}\x1f{r['split']}\x1f{r.get('method', '')}\x1f{json.dumps(r['m'], sort_keys=True)}"
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def apply(r: dict, cache: dict) -> dict:
    """Copy a cached verdict onto a leaderboard row (audit_ok: number and benchmark right; audit_std: comparable)."""
    v = cache.get(key(r))
    if v:
        r["audit_ok"] = v["number_ok"] is not False and v["bench_ok"]
        r["audit_std"] = v["standard"] and v["full_split"]
        r["audit_note"] = v["note"]
    return r


def excerpt(p: dict, r: dict, width: int = 7000) -> str:
    """The part of the paper where the row's key value appears (preferably inside a table)."""
    try:
        text, _ = fulltext.get_text(p)
    except Exception:
        return ""
    text = fulltext.clean(text, "latex") if "\\begin" in text else text
    vals = [str(v) for v in r["m"].values()][:3]
    hits = []
    for v in vals:
        for m in re.finditer(r"(?<![\d.])" + re.escape(v) + r"(?![\d])", text):
            hits.append(m.start())
    if not hits:
        ev = (r.get("ev") or "")[:40]
        i = text.find(ev.split(":")[0]) if ev else -1
        hits = [i] if i >= 0 else []
    if not hits:
        return text[:width]
    # the densest cluster of hits = the table
    hits.sort()
    best = max(hits, key=lambda h: sum(1 for x in hits if abs(x - h) < 2500))
    a = max(0, best - width // 2)
    return text[a: a + width]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sv = next((s for s in ctx.surveys() if s["id"] == a.sid), None)
    if not sv:
        raise SystemExit(f"no survey {a.sid}")
    from engine.survey import nonstandard
    benches = {(b, sp, k) for b, sp, k, *_ in sv.get("tables", [])} | {tuple(x) for x in sv.get("figures", [])}
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    papers = {p["id"]: p for p in json.loads((ctx.PUBLIC / "papers.json").read_text())}
    cache = {} if a.force else load()
    for rnd in range(5):  # dropping a bad row lets the next one into the top N: repeat until stable
        for r in board:
            apply(r, cache)
        todo = select(board, benches, a.top, cache, nonstandard)
        if not todo:
            break
        audit_rows(todo, papers, cache, a, rnd)
    bad = sum(1 for v in cache.values() if v["number_ok"] is False or not v["bench_ok"] or not v["standard"] or not v["full_split"])
    print(f"done: {len(cache)} rows audited, {bad} with a problem", flush=True)


def select(board: list, benches: set, top: int, cache: dict, nonstandard) -> list:
    todo = []
    for bench, split, k in sorted(benches):
        best: dict[str, tuple] = {}
        for r in board:
            if r["bench"] != bench or r["split"] != split or r["eval_set"] != "full" or k not in r["m"]:
                continue
            if r.get("audit_ok") is False or nonstandard(r):  # nonstandard() honours audit_std
                continue
            try:
                v = float(r["m"][k])
            except (TypeError, ValueError):
                continue
            if r["id"] not in best or v > best[r["id"]][0]:
                best[r["id"]] = (v, r)
        best_rows = sorted(best.values(), key=lambda x: -x[0])[:top]
        todo += [r for _, r in best_rows if key(r) not in cache]
        # every row that set a record over time (the figure's best-so-far line) is audited too
        run = -1.0
        for v, r in sorted(best.values(), key=lambda x: (x[1]["y"], -x[0])):
            if v > run:
                run = v
                if key(r) not in cache and r not in todo:
                    todo.append(r)
    return todo


def audit_rows(todo: list, papers: dict, cache: dict, a, rnd: int) -> None:
    protocols = "\n".join(f"- {b}: {t}" for b, t in getattr(ctx.D, "BENCH_PROTOCOLS", {}).items())
    system = SYSTEM.format(field=ctx.meta("field", ctx.ATLAS_ID), protocols=protocols)
    print(f"audit {a.sid} round {rnd + 1}: {len(todo)} rows to check (top {a.top} per benchmark, {len(cache)} cached)", flush=True)
    env = {**os.environ, "PATH": os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])}

    def run(r: dict) -> None:
        p = papers.get(r["id"], {})
        user = (f"Paper: {p.get('t')} ({p.get('y')}, {p.get('v') or 'arXiv'})\n\nROW\nbenchmark: {r['bench']}\nsplit: {r['split']}\n"
                f"eval set: {r['eval_set']} {r.get('eval_note') or ''}\nmethod: {r.get('method')}\nbackbone: {r.get('backbone')}\n"
                f"zero-shot: {r.get('zs')}\nobservation: {r.get('obs')}\naction space: {r.get('act')}\nprivileged: {r.get('priv')}\n"
                f"extra: {r.get('extra')}\nmetrics: {json.dumps(r['m'])}\nevidence given by the extractor: {r.get('ev')}\n\n"
                f"<<<EXCERPT\n{excerpt(p, r)}\nEXCERPT>>>")
        attempt = 0
        while attempt < 3:
            limits.wait_if_paused()
            out = None
            try:
                out = subprocess.run(["claude", "-p", "--model", a.model, "--system-prompt", system, "--output-format", "json",
                                      "--no-session-persistence", "--tools", "", "--strict-mcp-config"],
                                     input=user, capture_output=True, text=True, timeout=900, cwd="/tmp", env=env)
                envl = json.loads(out.stdout)
                if envl.get("is_error") or out.returncode:
                    raise RuntimeError(str(envl.get("result") or envl)[:300])
                txt = envl.get("result", "")
                v = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                with _lock:
                    cache[key(r)] = {"number_ok": v.get("number_ok"), "bench_ok": bool(v.get("bench_ok", True)),
                                     "full_split": bool(v.get("full_split", True)), "standard": bool(v.get("standard", True)),
                                     "note": str(v.get("note", ""))[:200], "model": a.model}
                    tmp = CACHE.with_suffix(".tmp")
                    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
                    os.replace(tmp, CACHE)
                flag = "ok" if cache[key(r)]["note"].lower().startswith("ok") else cache[key(r)]["note"]
                print(f"  {r['bench']:12s} {r.get('n') or r['t'][:20]:22s} {r['m'].get('SR', '')!s:6s} {flag}", flush=True)
                return
            except Exception as ex:
                if limits.pause_for(f"{ex} {(out.stdout[-300:] if out else '')}"):
                    continue
                attempt += 1
                print(f"  audit failed ({attempt}): {str(ex)[:120]}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(run, todo))


if __name__ == "__main__":
    main()
