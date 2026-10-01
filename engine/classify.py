"""Label candidates with the NavAtlas taxonomy via headless Claude (`claude -p`).

Reads atlases/<id>/data/candidates.jsonl (prefilter == keep), skips uids already present in
atlases/<id>/data/labels/labels.jsonl, sends the rest in batches, validates every returned
object against taxonomy.CODES and appends good rows. Incremental and
restart-safe: kill it any time and re-run.

  ./atlas classify <id> --smoke 20 --batch 20 --workers 1   # one small batch
  ./atlas classify <id> --workers 6                          # everything unlabelled
  ./atlas classify <id> --redo-low --model opus              # second opinion on low-confidence rows
  ./atlas classify <id> --print-prompt                       # show the rendered system prompt
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from engine import ctx, limits, taxonomy
from engine.ctx import CANDS, LABELS

SYSTEM = (
    (Path(__file__).parent / "prompts" / "classify_system.md").read_text()
    .replace("{ROLE}", ctx.D.CLASSIFY_ROLE.strip())
    .replace("{RULES}", ctx.D.CLASSIFY_RULES.strip())
    .replace("{TAXONOMY}", taxonomy.prompt_block())
)
LOG_DIR = LABELS.parent / "logs"
PRESCREENED = LABELS.parent / "prescreen_keep.txt"  # uids the cheap pass judged in scope (full pass pending)
LOCK = threading.Lock()
ABSTRACT_CHARS = 1600


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def render_batch(batch: list[dict]) -> str:
    parts = [f"Label these {len(batch)} papers. Return a JSON array of {len(batch)} objects in the same order.\n"]
    for c in batch:
        ab = (c.get("abstract") or "(no abstract available)")[:ABSTRACT_CHARS]
        extra = f" | comment: {c['comment'][:160]}" if c.get("comment") else ""
        parts.append(f"### id: {c['uid']}\nyear: {c.get('year')}{extra}\ntitle: {c['title']}\nabstract: {ab}\n")
    return "\n".join(parts)


def call_claude(prompt: str, model: str) -> str:
    cmd = [
        "claude", "-p", "--model", model,
        "--system-prompt", SYSTEM,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--strict-mcp-config",
    ]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=900, cwd="/tmp")
    if r.returncode != 0:
        raise RuntimeError(f"claude exit {r.returncode}: {r.stderr[-500:]} {r.stdout[-500:]}")
    env = json.loads(r.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude error: {str(env)[:500]}")
    return env.get("result", "")


def parse_array(text: str) -> list[dict]:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0]
    i, j = s.find("["), s.rfind("]")
    return json.loads(s[i : j + 1])


def validate(o: dict) -> dict:
    C = taxonomy.CODES
    out = {
        "uid": str(o.get("id", "")).strip(),
        "scope": o.get("scope") if o.get("scope") in C["scope"] else None,
        "tasks": [t for t in o.get("tasks") or [] if t in C["tasks"]],
        "settings": [t for t in o.get("settings") or [] if t in C["settings"]],
        "paradigm": o.get("paradigm") if o.get("paradigm") in C["paradigm"] else "none",
        "contrib": [t for t in o.get("contrib") or [] if t in C["contrib"]] or ["method"],
        "traits": [t for t in o.get("traits") or [] if t in C["traits"]],
        "benchmarks": [str(b).strip() for b in o.get("benchmarks") or [] if str(b).strip()],
        "name": (o.get("name") or None),
        "tldr": (o.get("tldr") or "").strip(),
        "conf": o.get("conf") if o.get("conf") in ("high", "low") else "low",
    }
    if out["scope"] is None:
        raise ValueError(f"bad scope {o.get('scope')!r}")
    return out


def run_batch(batch: list[dict], model: str, idx: int) -> tuple[int, int]:
    prompt = render_batch(batch)
    want = {c["uid"] for c in batch}
    attempt = 0
    while attempt < 3:
        limits.wait_if_paused()
        try:
            t0 = time.time()
            text = call_claude(prompt, model)
            arr = parse_array(text)
            good = []
            for o in arr:
                try:
                    v = validate(o)
                except Exception:
                    continue
                if v["uid"] in want:
                    v.update(model=model, labeled_at=now())
                    good.append(v)
            with LOCK:
                with LABELS.open("a") as f:
                    for v in good:
                        f.write(json.dumps(v, ensure_ascii=False) + "\n")
            print(f"  batch {idx}: {len(good)}/{len(batch)} ok in {time.time()-t0:.0f}s", flush=True)
            return len(good), len(batch)
        except Exception as ex:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / f"fail-{idx}-{attempt}.txt").write_text(f"{ex!r}\n")
            if limits.pause_for(str(ex)):
                continue  # not counted as an attempt: wait for the limit to reset, then retry
            print(f"  batch {idx} attempt {attempt+1} failed: {str(ex)[:200]}", flush=True)
            attempt += 1
            time.sleep(20 * attempt)
    return 0, len(batch)


PRESCREEN_SYSTEM = """{role}

Cheap first pass: decide ONLY the scope of each paper from its title and abstract.
{scopes}

Rules: when in doubt between in-scope and out, answer the in-scope label (a later, stronger pass decides).
Return ONLY a JSON array: [{{"id": "...", "scope": "<code>"}}, ...] in input order."""


def prescreen(todo: list[dict], model: str, workers: int) -> list[dict]:
    """Haiku scope-only pass; papers judged `out` are written as out-labels, the rest returned."""
    system = PRESCREEN_SYSTEM.format(role=ctx.D.CLASSIFY_ROLE.strip(),
                                     scopes="\n".join(f"- {c}: {d}" for c, _, d in taxonomy.SCOPES))
    keep: list[dict] = []
    lock = threading.Lock()

    def run(batch: list[dict]) -> None:
        user = "\n".join(f"### id: {c['uid']}\ntitle: {c['title']}\nabstract: {(c.get('abstract') or '')[:700]}\n" for c in batch)
        got, attempt = {}, 0
        while attempt < 3:
            limits.wait_if_paused()
            out = ""
            try:
                cmd = ["claude", "-p", "--model", model, "--system-prompt", system, "--output-format", "json",
                       "--no-session-persistence", "--tools", "", "--strict-mcp-config"]
                r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=600, cwd="/tmp")
                out = r.stdout[-300:] + r.stderr[-300:]
                env = json.loads(r.stdout)
                if env.get("is_error") or r.returncode:
                    raise RuntimeError(str(env.get("result") or env)[:400])
                txt = env.get("result", "")
                arr = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
                got = {o.get("id"): o.get("scope") for o in arr}
                break
            except Exception as ex:
                if limits.pause_for(f"{ex} {out}"):
                    continue
                attempt += 1
                time.sleep(15 * attempt)
        outs = []
        with lock:
            judged_in = [c["uid"] for c in batch if got.get(c["uid"]) not in (None, "out")]
            if judged_in:  # remembered, so a restarted run does not prescreen them again
                with PRESCREENED.open("a") as f:
                    f.write("".join(u + "\n" for u in judged_in))
            for c in batch:
                if got.get(c["uid"]) == "out":
                    outs.append({"uid": c["uid"], "scope": "out", "tasks": [], "settings": [], "paradigm": "none", "contrib": ["method"],
                                 "traits": [], "benchmarks": [], "name": None, "tldr": "", "conf": "low",
                                 "model": f"{model}-prescreen", "labeled_at": now()})
                else:
                    keep.append(c)  # in scope, unsure, or failed → full pass
            with LABELS.open("a") as f:
                for o in outs:
                    f.write(json.dumps(o, ensure_ascii=False) + "\n")
        print(f"  prescreen: {len(batch) - len(outs)}/{len(batch)} kept", flush=True)

    batches = [todo[i: i + 80] for i in range(0, len(todo), 80)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run, batches))
    print(f"prescreen done: {len(keep)}/{len(todo)} go to the full pass", flush=True)
    return keep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=ctx.meta("classify_model", "sonnet"))
    ap.add_argument("--print-prompt", action="store_true", help="show the rendered system prompt and exit")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--smoke", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="max papers this run")
    ap.add_argument("--redo-low", action="store_true")
    ap.add_argument("--prescreen", metavar="MODEL", help="cheap scope-only first pass (e.g. haiku) before full labelling; for very large fields")
    ap.add_argument("--redo-paradigm", nargs="+", metavar="CODE", help="re-label papers currently labelled with these paradigm codes (e.g. after a taxonomy change)")
    args = ap.parse_args()
    if args.print_prompt:
        print(SYSTEM)
        return
    ctx.require_ready()

    LABELS.parent.mkdir(parents=True, exist_ok=True)
    cands = [c for c in load_jsonl(CANDS) if c.get("prefilter") == "keep"]
    labels = {}
    for r in load_jsonl(LABELS):
        labels[r["uid"]] = r  # last write wins
    if args.redo_paradigm:
        todo = [c for c in cands if labels.get(c["uid"], {}).get("paradigm") in set(args.redo_paradigm)]
    elif args.redo_low:
        # borderline papers worth a second look: low confidence, has a real
        # abstract, and is either kept or at least mentions navigation in the title
        todo = [c for c in cands
                if labels.get(c["uid"], {}).get("conf") == "low"
                and not labels[c["uid"]].get("model", "").startswith(args.model)
                and len(c.get("abstract") or "") > 300
                and (labels[c["uid"]]["scope"] != "out" or "navig" in c["title"].lower())]
    else:
        todo = [c for c in cands if c["uid"] not in labels]
    if args.smoke:
        todo = todo[: args.smoke]
    if args.limit:
        todo = todo[: args.limit]
    print(f"candidates(keep)={len(cands)} labelled={len(labels)} todo={len(todo)} model={args.model}", flush=True)
    if args.prescreen and not (args.redo_low or args.redo_paradigm):
        seen = set(PRESCREENED.read_text().split()) if PRESCREENED.exists() else set()
        done_ps = [c for c in todo if c["uid"] in seen]
        todo = done_ps + prescreen([c for c in todo if c["uid"] not in seen], args.prescreen, args.workers)
        if done_ps:
            print(f"  ({len(done_ps)} already passed the prescreen in an earlier run)", flush=True)
    batches = [todo[i : i + args.batch] for i in range(0, len(todo), args.batch)]
    ok = tot = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_batch, b, args.model, i) for i, b in enumerate(batches)]
        for f in as_completed(futs):
            a, b = f.result()
            ok += a
            tot += b
    print(f"done: {ok}/{tot} labelled", flush=True)


if __name__ == "__main__":
    main()
