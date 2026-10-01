"""Recall sanity check: are the field's landmark papers in the library?

Each entry is (arXiv id, short name). Prints where each one ended up:
in-scope with its labels, excluded (and why), or never harvested at all.
Missing ones should be added to atlases/<id>/seeds.txt (harvested by id) and the
query set revisited.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from engine import ctx
from engine.ctx import CANDS, LABELS

LANDMARKS = ctx.D.LANDMARKS  # (arXiv id, short name) — defined in the atlas's domain file


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def main() -> None:
    cands = {c["uid"]: c for c in load_jsonl(CANDS)}
    labels = {}
    for r in load_jsonl(LABELS):
        labels[r["uid"]] = r
    ok = 0
    missing = []
    for aid, name in LANDMARKS:
        c = cands.get(aid)
        if not c:
            print(f"  MISSING   {aid:12s} {name}")
            missing.append(aid)
            continue
        if c.get("prefilter") != "keep":
            print(f"  PREFILTER {aid:12s} {name:28s} {c['prefilter']}")
            continue
        l = labels.get(aid)
        if not l:
            print(f"  UNLABELED {aid:12s} {name}")
            continue
        flag = "ok " if l["scope"] in ("core", "adjacent") else "OUT"
        ok += l["scope"] in ("core", "adjacent")
        print(f"  {flag}       {aid:12s} {name:28s} {l['scope']:8s} {l['paradigm']:11s} {','.join(l['tasks'])}")
    print(f"\nin library: {ok}/{len(LANDMARKS)}")
    if missing:
        print(f"add to atlases/{ctx.ATLAS_ID}/seeds.txt:", " ".join(missing))


if __name__ == "__main__":
    main()
