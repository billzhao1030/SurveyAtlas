"""Resolve which VERSION of a benchmark a result row used, when the domain declares that a benchmark name hides
incompatible versions (BENCH_VARIANTS in atlases/<id>/atlas.py) and the domain's own rule (bench_variant) could
not tell from the row text.

  ./atlas variants <id>        one headless Claude call per unresolved row, with an excerpt of the paper

Cached in data/meta/variants.json (row key -> variant name or "unknown"); build applies the cache after the
domain rule. Rows that stay unknown keep the generic name and are left out of rankings.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import audit, ctx, limits

CACHE = ctx.META_DIR / "variants.json"
PCACHE = ctx.META_DIR / "protocols.json"   # subset rows -> shared evaluation protocol (SUBSET_PROTOCOLS) or "other"
_lock = threading.Lock()

SYSTEM = """Decide which version of the benchmark "{bench}" a reported result was evaluated on.
Versions:
{versions}
Use the paper excerpt (tables, captions, experimental setup) and the row. Answer "unknown" if the text does not
let you decide. Return ONLY JSON: {{"variant": "<one of: {names}, unknown>", "why": "<= 15 words"}}"""


def load(f: Path = CACHE) -> dict:
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


PSYSTEM = """A result was evaluated on a SUBSET of the benchmark "{bench}". Several papers share the same subsets;
decide whether this result used one of these shared subsets:
{protocols}
Use the paper excerpt and the row's evaluation note. Answer "other" if it is a different or unspecified subset.
Return ONLY JSON: {{"protocol": "<one of: {names}, other>", "why": "<= 15 words"}}"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()
    spec = getattr(ctx.D, "BENCH_VARIANTS", {})
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    papers = {p["id"]: p for p in json.loads((ctx.PUBLIC / "papers.json").read_text())}
    cache = load()
    todo = [r for r in board if r["bench"] in spec and audit.key(r) not in cache]
    print(f"variants: {len(todo)} rows with an unresolved version ({len(cache)} cached)", flush=True)
    env = {**os.environ, "PATH": os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])}

    def run(r: dict) -> None:
        vs = spec[r["bench"]]
        system = SYSTEM.format(bench=r["bench"], versions="\n".join(f"- {k}: {v}" for k, v in vs.items()), names=", ".join(vs))
        p = papers.get(r["id"], {})
        user = (f"Paper: {p.get('t')} ({p.get('d') or p.get('y')})\nROW: method={r.get('method')} split={r['split']} "
                f"eval note={r.get('eval_note')} extra={r.get('extra')} metrics={json.dumps(r['m'])} evidence={r.get('ev')}\n\n"
                f"<<<EXCERPT\n{audit.excerpt(p, r, 6000)}\nEXCERPT>>>")
        for attempt in range(4):
            limits.wait_if_paused()
            out = None
            try:
                out = subprocess.run(["claude", "-p", "--model", a.model, "--system-prompt", system, "--output-format", "json",
                                      "--no-session-persistence", "--tools", "", "--strict-mcp-config"],
                                     input=user, capture_output=True, text=True, timeout=600, cwd="/tmp", env=env)
                envl = json.loads(out.stdout)
                if envl.get("is_error") or out.returncode:
                    raise RuntimeError(str(envl.get("result") or envl)[:300])
                txt = envl.get("result", "")
                v = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                name = v.get("variant") if v.get("variant") in vs else "unknown"
                with _lock:
                    cache[audit.key(r)] = {"variant": name, "why": str(v.get("why", ""))[:120]}
                    tmp = CACHE.with_suffix(".tmp")
                    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
                    os.replace(tmp, CACHE)
                return
            except Exception as ex:
                if not limits.pause_for(f"{ex} {(out.stdout[-300:] if out else '')}"):
                    print(f"  failed: {str(ex)[:120]}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(run, todo))
    from collections import Counter
    print("done:", dict(Counter(v["variant"] for v in cache.values())), flush=True)

    # shared subset protocols
    pspec = getattr(ctx.D, "SUBSET_PROTOCOLS", {})
    pcache = load(PCACHE)
    ptodo = [r for r in board if r["bench"] in pspec and r["eval_set"] == "subset" and audit.key(r) not in pcache]
    print(f"protocols: {len(ptodo)} subset rows to tag ({len(pcache)} cached)", flush=True)

    def prun(r: dict) -> None:
        ps = pspec[r["bench"]]
        system = PSYSTEM.format(bench=r["bench"], protocols="\n".join(f"- {k}: {v}" for k, v in ps.items()), names=", ".join(ps))
        p = papers.get(r["id"], {})
        user = (f"Paper: {p.get('t')}\nROW: method={r.get('method')} split={r['split']} eval note={r.get('eval_note')} "
                f"metrics={json.dumps(r['m'])} evidence={r.get('ev')}\n\n<<<EXCERPT\n{audit.excerpt(p, r, 5000)}\nEXCERPT>>>")
        for attempt in range(4):
            limits.wait_if_paused()
            out = None
            try:
                out = subprocess.run(["claude", "-p", "--model", a.model, "--system-prompt", system, "--output-format", "json",
                                      "--no-session-persistence", "--tools", "", "--strict-mcp-config"],
                                     input=user, capture_output=True, text=True, timeout=600, cwd="/tmp", env=env)
                envl = json.loads(out.stdout)
                if envl.get("is_error") or out.returncode:
                    raise RuntimeError(str(envl.get("result") or envl)[:300])
                txt = envl.get("result", "")
                v = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                name = v.get("protocol") if v.get("protocol") in ps else "other"
                with _lock:
                    pcache[audit.key(r)] = {"protocol": name, "why": str(v.get("why", ""))[:120]}
                    tmp = PCACHE.with_suffix(".tmp")
                    tmp.write_text(json.dumps(pcache, ensure_ascii=False, indent=0))
                    os.replace(tmp, PCACHE)
                return
            except Exception as ex:
                if not limits.pause_for(f"{ex} {(out.stdout[-300:] if out else '')}"):
                    print(f"  failed: {str(ex)[:120]}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(prun, ptodo))
    print("protocols done:", dict(Counter(v["protocol"] for v in pcache.values())), flush=True)


if __name__ == "__main__":
    main()
