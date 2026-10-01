# CLAUDE.md — SurveyAtlas repo

Multi-field literature atlas system: `./atlas` CLI + `engine/` pipeline + `hub/` website (:8668) + one domain file per field in `atlases/<id>/atlas.py`. Full procedure and pitfalls: `claude/skills/literature-atlas/playbook.md` — read it before building or changing an atlas.

## Invariants

- Field-specific content (queries, regexes, taxonomy codes, classifier rules, landmarks, UI labels/outlines) lives ONLY in `atlases/<id>/atlas.py`. `engine/` and `hub/` must stay generic — if you catch yourself typing "navigation" there, it belongs in a domain file.
- Engine steps run as `ATLAS=<id> python3 -m engine.<step>` from the repo root; use `./atlas <step> <id>`.
- `atlases/<id>/data/{raw,labels,meta}` are append/merge-only incremental caches (labels cost Claude tokens). Never truncate them; `labels.jsonl` is last-write-wins per uid.
- Hand corrections → `atlases/<id>/overrides.json`. Stars / reading status / notes typed on the site → `data/marks.json` (never overwrite from a pipeline step; included in snapshots).
- One writer per atlas: `./atlas update|add` hold a flock on `data/.update.lock`. Don't run `engine.classify` by hand while an update runs.
- Taxonomy axis names are fixed (scope with `core`+`out`, tasks, settings, paradigm ending in `none`, contrib, traits, benchmarks) — the hub renders by these names. Paradigm colours come from ORDER (`--cat-1…8`, CVD-validated); never hard-code a colour per paradigm name.
- `data/` and `public/` are gitignored; portability is `./atlas snapshot <id> [--with-raw]` (deterministic gz, commit it) and `./atlas restore <id>`.
- `claude/` is deployed into `~/.claude` by `install.sh` as symlinks + a marked CLAUDE.md block. Edit the files here, not the symlink targets' copies.
- Classification uses headless `claude -p` (no API key needed; the user's Claude Code login). Keep batches ≤ 40 and workers ≤ 6; Opus only for `--redo-low`.

## Stage 2 (deep reading) contract

Every atlas reads with its OWN prompt `atlases/<id>/read_system.md` (setting fields, benchmark split names and
metric keys differ per field; never edit one atlas's prompt for another). `engine/prompts/read_system.md` is only the
fallback. Each reading records `prompt: "<atlas>:<sha1[:8]>"`. `./atlas read <id> --scopes core` reads one scope first.
One file per paper at `atlases/<id>/data/reading/<uid with / and : replaced by _>.json`: problem / motivation / key_idea / insight / method / setting / numbers
(bench, split, eval_set full|subset + note, method, zero_shot, backbone, extra, metrics, evidence) / results /
limitations / builds_on / compares_to / summary (+ atlas-specific: own_tasks = non-shared evaluations such as
real-robot suites, verified into `verify_own`; resource = benchmark / dataset card), plus verify / read_at / model /
source / chars / version / prompt. Usage-limit pauses end as soon as the Claude login changes (e.g. an account switcher).
Optional reading window: local.json `"read_window": "23:30-08:00"` — `read`
starts new papers only inside it (in-flight ones finish, the run idles and resumes by itself; edit live; `--anytime` overrides).
`READ_VERSION` in engine/read.py gates re-reading. `build` embeds notes into papers.json and all numbers into
leaderboard.json; `engine/survey.py` turns them into writer packs and LaTeX tables.
