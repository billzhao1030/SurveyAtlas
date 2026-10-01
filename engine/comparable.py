"""Comparability of reported results: was each result obtained under the benchmark's STANDARD protocol?

  ./atlas comparable <id>          judge every new (benchmark, privileged info, extra conditions) combination

Keyword rules cannot decide this: "ground-truth depth" is standard on R2R, "GT object boxes" are part of
REVERIE, but pre-exploring the test scenes or getting oracle feedback is not. A headless Claude call judges
each distinct combination against the protocol notes in the domain file (BENCH_PROTOCOLS) and the verdict
is cached in data/meta/comparable.json. `build` writes it into leaderboard.json as cmp / cmp_why; the
site's leaderboard and the survey tables use it (keyword rule only as a fallback).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import ctx, limits

CACHE = ctx.META_DIR / "comparable.json"
_lock = threading.Lock()

SYSTEM = """You audit results tables for a survey of {field}. For each reported result decide whether it was
obtained under the benchmark's STANDARD evaluation protocol, i.e. directly comparable to other papers' numbers
on that benchmark and split.

Standard protocol notes per benchmark (information the protocol itself provides is NOT a deviation):
{protocols}

General rules:
- Comparable (std=true): extra TRAINING data or pretraining of any kind (augmentation, synthetic data, web data,
  bigger models), different backbones, zero-shot use of foundation models, a single run of the agent.
- Not comparable (std=false): exploring or mapping the TEST environments before the episode (pre-exploration),
  beam search or picking among several trajectories, test-time feedback from humans / oracles / ground truth,
  privileged test-time information the protocol does not provide (GT semantic maps, GT object locations,
  GT goal position, oracle stop, oracle waypoints where the protocol has none), ensembles, test-time
  augmentation, a changed success radius / step budget / action space / sensor suite.
- IGNORE which episodes were evaluated (episode counts, scenes, subsets, splits): that is recorded separately
  and is not part of this judgement.
- If the text says the condition was only used in training or only in an ablation that is not this row,
  it is comparable. When unsure, say std=true.

Input: numbered items "i | benchmark | privileged info at test time | extra conditions".
Return ONLY a JSON array: [{{"i": 0, "std": true, "why": "<= 10 words"}}, ...] covering every item."""


def key(bench: str, priv: str, extra: str) -> str:
    return hashlib.sha1(f"{bench}\x1f{priv}\x1f{extra}".encode()).hexdigest()[:16]


def load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--batch", type=int, default=60)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    protocols = getattr(ctx.D, "BENCH_PROTOCOLS", {})
    system = SYSTEM.format(field=ctx.meta("field", ctx.ATLAS_ID),
                           protocols="\n".join(f"- {b}: {t}" for b, t in protocols.items()) or "- (none given)")
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    cache = load()
    todo = {}
    for r in board:
        b, pv, ex = r["bench"], (r.get("priv") or "").strip(), (r.get("extra") or "").strip()
        k = key(b, pv, ex)
        if k not in cache and k not in todo:
            todo[k] = (b, pv, ex)
    items = list(todo.items())
    print(f"comparability: {len(items)} new combinations to judge ({len(cache)} cached)", flush=True)
    env = {**os.environ, "PATH": os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])}

    def run(chunk: list) -> None:
        user = "\n".join(f"{i} | {b} | {pv[:300] or 'none'} | {ex[:300] or 'none'}" for i, (_, (b, pv, ex)) in enumerate(chunk))
        attempt = 0
        while attempt < 3:
            limits.wait_if_paused()
            r = None
            try:
                r = subprocess.run(["claude", "-p", "--model", a.model, "--system-prompt", system, "--output-format", "json",
                                    "--no-session-persistence", "--tools", "", "--strict-mcp-config"],
                                   input=user, capture_output=True, text=True, timeout=900, cwd="/tmp", env=env)
                envl = json.loads(r.stdout)
                if envl.get("is_error") or r.returncode:
                    raise RuntimeError(str(envl.get("result") or envl)[:300])
                txt = envl.get("result", "")
                arr = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
                got = {int(o["i"]): o for o in arr if isinstance(o, dict) and "i" in o}
                with _lock:
                    for i, (k, _) in enumerate(chunk):
                        if i in got:
                            cache[k] = {"std": bool(got[i].get("std", True)), "why": str(got[i].get("why", ""))[:120]}
                    tmp = CACHE.with_suffix(".tmp")
                    tmp.write_text(json.dumps(cache, ensure_ascii=False))
                    os.replace(tmp, CACHE)
                print(f"  judged {len(got)}/{len(chunk)}", flush=True)
                return
            except Exception as ex:
                if limits.pause_for(f"{ex} {(r.stdout[-300:] if r else '')}"):
                    continue
                attempt += 1
                print(f"  attempt {attempt} failed: {str(ex)[:160]}", flush=True)

    chunks = [items[i: i + a.batch] for i in range(0, len(items), a.batch)]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(run, chunks))
    n = sum(1 for v in cache.values() if not v["std"])
    print(f"done: {len(cache)} combinations judged, {n} not comparable", flush=True)


if __name__ == "__main__":
    main()
