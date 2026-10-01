"""Digest of papers that entered the library since a date (default: today).

Writes atlases/<id>/data/logs/new-<date>.md grouped by task, and prints a one-line summary.
Used at the end of update.sh so each weekly run leaves a readable "what's new".

  ./atlas digest <id> --since 2026-10-04
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from engine import ctx
from engine.ctx import LOGS, PUBLIC


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=date.today().isoformat())
    a = ap.parse_args()
    papers = json.loads((PUBLIC / "papers.json").read_text())
    tax = json.loads((PUBLIC / "taxonomy.json").read_text())
    tlab = {t["code"]: t["label"] for t in tax["tasks"]}
    plab = {p["code"]: p["label"] for p in tax["paradigms"]}
    bulk = ctx.meta("bulk_date", "")  # initial import day — never "new"
    new = [p for p in papers if (p.get("fs") or "") >= a.since and (p.get("fs") or "") > bulk and p["sc"] in ("core", "adjacent")]
    new.sort(key=lambda p: (p["sc"] != "core", p.get("d") or ""), reverse=False)

    by_task = defaultdict(list)
    for p in new:
        by_task[tlab.get(p["tk"][0], p["tk"][0]) if p["tk"] else "Other"].append(p)
    lines = [f"# {ctx.meta('title', ctx.ATLAS_ID)} — new since {a.since}", "",
             f"{sum(p['sc'] == 'core' for p in new)} core + {sum(p['sc'] == 'adjacent' for p in new)} adjacent papers.", ""]
    for task, ps in sorted(by_task.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"## {task} ({len(ps)})")
        for p in ps:
            name = f"**{p['n']}** — " if p.get("n") else ""
            link = f"https://arxiv.org/abs/{p['ax']}" if p.get("ax") else ""
            lines.append(f"- {name}{p['t']} ({plab.get(p['pd'], p['pd'])}{', ' + p['v'] if p.get('v') else ''}) {link}")
            if p.get("tl"):
                lines.append(f"  - {p['tl']}")
        lines.append("")
    out = LOGS / f"new-{a.since}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"digest: {len(new)} new in-scope papers since {a.since} → {out}")


if __name__ == "__main__":
    main()
