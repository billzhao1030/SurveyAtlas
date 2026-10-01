---
name: literature-atlas
description: Build, update or extend a literature atlas — a curated, classified, searchable paper library for writing surveys (arXiv + OpenAlex harvest → headless-Claude taxonomy labels → venue/BibTeX → multi-atlas website on port 8668 with weekly cron). Use when the user wants a new field's paper library ("build a literature library/atlas for X", "new atlas", "embodied agent atlas", "general agent / harness literature"), to add papers to an atlas ("add this paper to NavAtlas", "atlas add"), refresh one ("update the atlas"), change the atlas website (default atlas, background, settings), or set the system up on a new machine ("deploy atlas on a new machine", "install atlas").
---

# literature-atlas

The system lives in a git repo. Find it first:

```bash
REPO=$(cd "$(readlink -f ~/.claude/skills/literature-atlas)/../../.." && pwd); echo "$REPO"
```

(Typically a clone of https://github.com/billzhao1030/SurveyAtlas.) Then **read `$REPO/claude/skills/literature-atlas/playbook.md`** — it has the architecture, the invariants, the step-by-step for a new field, the API pitfalls that already cost real time (arXiv 429 + stemming, OpenAlex "reverie", DBLP bot wall, Semantic Scholar year, seeds vs prefilter, `pgrep -f` self-match), timings, and starter designs for Embodied Agent / General Agent / Harness.

## Quick map

| Request | Do |
|---|---|
| New field | Ask the 3 questions in playbook §0 → `./atlas new <id> --title …` → fill `atlases/<id>/atlas.py` (worked example: `atlases/navatlas/atlas.py`) → playbook §2 steps |
| Add papers now | `./atlas add <id> <arXiv ids or URLs>` |
| Refresh | `./atlas update <id>` · all: `./atlas update --all` · full re-harvest: `--full` |
| Fix a label / venue | `atlases/<id>/overrides.json` → `./atlas build <id>` |
| Website | `./atlas start|stop|status` (port 8668); look + default atlas via the gear icon or `#/settings` (stored in `$REPO/settings.json`) |
| Weekly updates | `./atlas cron install|show|remove` |
| New machine | `git clone … && ./install.sh --cron --start` (playbook §7) |
| Before switching machines | `./atlas snapshot <id> --with-raw` → commit + push |

Rules: everything field-specific goes in `atlases/<id>/atlas.py` — never into `engine/` or `hub/`. Never truncate `data/raw|labels|meta` (incremental caches; labels cost tokens). Only one process may write an atlas's labels at a time (the CLI locks). Smoke-classify 20 papers and show the user before a full run.
