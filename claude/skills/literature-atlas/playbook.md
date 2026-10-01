# Playbook: Literature Atlas

**What it is**: one git repository (SurveyAtlas) holds several "literature atlases". Each atlas covers one research field, and all of them work the same way:
harvest → filter and classify → venues/BibTeX → searchable website → weekly incremental updates. All atlases share one engine
and are served by one website (the hub, default port 8668); the site can switch atlases, set the default atlas and adjust its appearance.

**Where the repo is**: `readlink -f ~/.claude/skills/literature-atlas` points to `<repo>/claude/skills/literature-atlas`,
so the repo root is three levels above that path. It is typically a clone of https://github.com/billzhao1030/SurveyAtlas.
**Reference implementation**: `atlases/navatlas/` (embodied navigation, completed 2026-09-29: 20k candidates → 2.2k core, 108/108 landmark papers recalled).

---

## 0. The one-line request to Claude

> "Use literature-atlas to build an atlas for <field>, with the id <id>."

**Ask the user only three things first**:
1. Field boundaries: what counts as core / adjacent / parked (held for later) / out.
2. The axis the survey should tell its story along, i.e. the timeline of paradigms.
3. Whether this atlas should become the website's default atlas.

Do not ask about the port: every atlas is mounted on the same hub. Everything else has a default.

---

## 1. Repository layout

```
atlas                 CLI: list / new / update / add / build / <step> / ask / export / serve / start / stop / snapshot / restore / cron
install.sh            one-command setup on a new machine (see §7)
engine/               generic pipeline, no field-specific content
  ctx.py              ATLAS=<id> → paths + loads atlases/<id>/atlas.py
  harvest_arxiv.py  harvest_openalex.py  merge.py  classify.py  resolve_venues.py  venues.py
  build.py  digest.py  recall_check.py  taxonomy.py  prompts/classify_system.md (generic template)
  ask.py  export.py   Q&A with citations · static read-only export
hub/serve.py          web server: /api/atlases  /api/settings  /api/marks?atlas=  /api/ask  /a/<id>/<file>
hub/site/             single-page app: atlas switcher · Library · Map · Timeline · Benchmarks · Surveys · Ask · Pipeline · settings panel
settings.json         site settings: default atlas, theme, background, accent colour, card density (edited on the web page, stored here, can be committed)
atlases/<id>/
  atlas.py            ★ field definition, the only file you need to write: META, queries, prefilter regexes, taxonomy, classifier rules, landmark papers, UI config
  seeds.txt           papers force-included by arXiv id (bypass the prefilter)
  overrides.json      manual corrections {uid: {scope, paradigm, tasks, venue, venue_year, …}}
  surveys/<sid>/      optional survey outlines outside atlas.py (survey.py with a dict SURVEY) + the survey's LaTeX workspace
  snapshot/           compressed data snapshot (committed to git, used to move machines)
  data/               runtime data (gitignored): raw/ labels/ meta/ candidates.jsonl reading/ marks.json logs/
  public/             build output (gitignored): papers/excluded/taxonomy/stats/meta.json, atlas.bib
atlases/_template/    template copied by `./atlas new` (DOMAIN_READY = False)
claude/               deployed into ~/.claude: this playbook, this skill, the CLAUDE.md snippet
```

**Pipeline**
```
arXiv API (N queries + title safety net + seeds.txt) ─┐
OpenAlex title/abstract search (papers not on arXiv)  ─┴→ merge: dedupe by arXiv id / normalized title + rule-based prefilter
  → classify: headless `claude -p --model sonnet`, 40 papers/batch, labels by the taxonomy and validates them
  → classify --redo-low --model opus: re-judges only the low-confidence borderline cases
  → resolve: Semantic Scholar batch (venue / DOI / citation count) + CrossRef (the publisher's official BibTeX)
  → build: dedupe and merge → public/*.json + atlas.bib → shown on the hub
update (cron weekly, `--all`) · add (add single papers by hand) · recall (landmark paper recall) · digest (weekly list of new papers)
```

**Invariants**
- `atlases/<id>/atlas.py` is the single source of every label in its field: it generates the classifier prompt and the website legends/filters, and is used to validate LLM output.
  No field-specific content may be written in engine/ or hub/.
- `data/raw|labels|meta` are append-only and can be re-run incrementally; never wipe them and start over. In `labels.jsonl` the last write for a uid wins.
- Manual corrections go in `overrides.json`; do not edit labels directly.
- Only one process at a time may write an atlas's labels: `update` / `add` both take a flock lock.
- The generic taxonomy axis names are fixed: scope (must include `core` and `out`), tasks, settings, paradigm (with `none` last),
  contrib, traits, benchmarks. The website renders by these field names.
- Colours are assigned by paradigm **order** (`--cat-1…8` is a colour sequence that passed a CVD check); never hard-code a colour per name.

---

## 2. Creating a new atlas (each step: what to do · checkpoint)

### Step 0 · Scaffold + Claude draft
On the web page: go to `#/atlases`, fill in the title and description in the New atlas card → Create → Draft with Claude.
Command line:
```bash
./atlas new agentatlas --title AgentAtlas --subtitle "Embodied Agent Literature Atlas" --field "Embodied Agents"
./atlas draft agentatlas --brief "what counts as this field / what does not / the survey storyline / key benchmarks"   # Opus by default, a few minutes
```
`draft` has headless Claude write a complete `atlas.py` following this playbook and the NavAtlas example (write access is limited to that atlas's directory);
points that need a human decision are written to `DRAFT_NOTES.md`. It is only a **draft**: the checks in Steps 1–5 still apply, especially scope, paradigms and landmark paper ids.
(Measured: Sonnet took about 4 minutes and produced 59 queries, 9 tasks, 7 paradigms and 68 landmarks, and listed 9 decisions to confirm.)
After editing, self-check with `./atlas check <id>` and unlock with `./atlas ready <id>` (or click Check / Mark ready on the web page).

### Step 1 · Settle scope and taxonomy (most important; agree on it with the user first)
- **SCOPES**:
  - `core`: the field is the paper's subject.
  - `adjacent`: related but secondary; kept, and shown dimmed on the website.
  - `<parked>`: may be included later, e.g. aerial in NavAtlas.
  - `out`: excluded.
  Give every tier **concrete positive and negative examples**; the LLM's boundary calls rest entirely on this text.
- **TASKS** (multi-select): the task families a paper addresses.
- **PARADIGMS** (single-select, `(code, label, line, definition)`): the survey's storyline. `line` is used for grouping, e.g. learned / training-free.
  Write the criteria as **testable questions**, e.g. "Were the decision model's weights trained on data from this field?" or "Is the control flow a hard-coded pipeline, or is it decided by the model?".
- **SETTINGS / CONTRIBS / TRAITS / BENCHMARKS**: use canonical benchmark names; `other:<Name>` is allowed.
- **CLASSIFY_ROLE / CLASSIFY_RULES**: a role sentence + decision rules for each field. When done, read the full rendered prompt with
  `./atlas classify <id> --print-prompt`.

### Step 2 · Write queries (aim for recall first; leave precision to the classifier)
About 40–60 `ARXIV_QUERIES`, in several layers:
1. Spelling variants of the field's phrases (with/without hyphens, abbreviations).
2. **Benchmark / dataset / simulator names**: many abstracts mention only the benchmark, not the name of the field.
3. Subtask names; method family × field terms.
4. **Title safety net**: `(ti:X OR ti:variant) AND (cat:cs.CV OR cat:cs.RO OR cat:cs.AI OR cat:cs.LG OR cat:cs.CL)`.
   NavAtlas used it to recover papers such as NoMaD whose abstracts use unusual wording.
5. `seeds.txt`: papers such as platforms and datasets whose abstracts never mention the field's terms.

**Probe the counts before writing queries for real**: for each query read `totalResults` with `max_results=1`, at least 4 seconds apart.
`OPENALEX_QUERIES` should be narrower than the arXiv ones. Also write `RELEVANCE_RE`, `OA_STRICT_RE` and `OA_STRICT_CS_RE` (abbreviations case-sensitive).
Once everything is filled in, set `DOMAIN_READY = True`.

### Step 3 · Harvest + merge
```bash
./atlas harvest <id>          # runs in the background; backs off automatically on 429
./atlas harvest-oa <id>
./atlas merge <id>
```
Check: look at the prefilter statistics; randomly sample 30 titles that are "OpenAlex-only and kept" and look for junk (see §4).

### Step 4 · Classify
1. `./atlas classify <id> --smoke 20 --batch 20 --workers 1`: **show these 20 labels to the user**, tune the rules, then continue.
2. `./atlas classify <id> --workers 6`: the full run, in the background.
3. `./atlas classify <id> --redo-low --model opus --workers 4 --batch 30`: Opus re-judges only the borderline cases. In NavAtlas about 25% were changed, all of them reasonably.
4. Spot-check both ends: papers judged out whose titles carry a strong field signal (to find misses), and 30 random core papers (to check precision).

### Step 5 · Recall check
Put 60–100 must-have papers in `LANDMARKS`, chosen in layers: foundational work, early training paradigms, systems from the last two years, platforms and datasets, and papers with unusual wording.
**Verify every id against its arXiv title**; never write ids from memory. Run `./atlas recall <id>`.
Add the misses to `seeds.txt`, and go back to find out why they were missed: is a query missing? Were they removed by the prefilter? If an older paper list exists, cross-check against it.

### Step 6 · Venues / BibTeX
```bash
./atlas resolve <id>
./atlas build <id>
```
- Venue source priority: Semantic Scholar → arXiv comment ("Accepted to …") → OpenAlex.
- The venue year is corrected separately, in this priority: last two digits of the DBLP key > year in an IEEE DOI > CrossRef year > comment > inference from the submission calendar.
- Check about 20 papers whose venues you know; write wrong ones into `overrides.json` in the form `{"<id>": {"venue": "RSS", "venue_year": 2025}}`.

### Step 7 · Go live
- In `UI` write: `task_groups` (row groups on the Map page), `paradigm_lines`, `paradigm_note`, `timeline_intro`,
  `presets`, `surveys` (each section is a live query), `roadmap`.
- Run `./atlas build <id>` and refresh the site. A new atlas appears automatically in the top-left switcher and on the `#/atlases` page; to make it the default, choose it in the settings panel.
- Check every page with screenshots (commands in §4), and also look at the mobile layout at 390px width.
- Once the data is stable, run `./atlas snapshot <id> --with-raw` and commit it to git (see §7).
- Install a single cron entry, `./atlas update --all`; new atlases are included automatically (provided `DOMAIN_READY=True`).

---

## 2b. Deep reading, results tables, survey workspace (Stages 2–5)

- **Deep reading** `./atlas read <id>` (engine/read.py + each atlas's own `atlases/<id>/read_system.md`):
  - Each atlas has its own reading prompt: setting fields, benchmark split names and metric keys are written per field (NavAtlas's SR/SPL do not apply to
    manipulation or agents). Before a new atlas starts reading, write its prompt by adapting `atlases/embodied/read_system.md` or `atlases/agentic/read_system.md`;
    `engine/prompts/read_system.md` is only the fallback. Never edit one atlas's prompt for another; every note records `prompt: <atlas>:<hash>`.
  - For large atlases read core first: `./atlas read <id> --scopes core`; adjacent can come later.
  - Multiple accounts: when the Claude Code login changes (e.g. an account switcher rewrites `~/.claude/.credentials.json`), a read that is waiting for a usage reset resumes immediately.
  - Reading window (optional): `"read_window": "23:30-08:00"` in `local.json` lets long runs start new papers only inside that window, for example to leave daytime usage free.
    Outside the window, papers already in progress finish, then the process waits in place and resumes by itself when the window opens (edits to the file take effect immediately; `--anytime` ignores the window).
  - One headless Claude call per paper, with the full text as input. The full text comes from the atlas's own cache or an existing local full-text cache (local.json `fulltext_cache`), the arXiv LaTeX source, or the PDF text.
  - The main LaTeX file is chosen by scoring "length after expanding `\input`"; files named like rebuttal / supplementary are down-weighted; a `\title` that matches the paper title earns a bonus.
    Do **not** use the rule "the largest file containing `\documentclass`": in NaVid's source bundle the rebuttal is larger than the paper.
  - Expansion must handle `\input` `\include` `\subfile` `\import` `\subimport`. A cleaned "full text" under 6,000 characters counts as incomplete and must fall back to the PDF.
    AuxRN's body is entirely inside `\subfile`, and only 653 characters were read before this was fixed.
  - Papers without an arXiv id (about 20%): `fulltext.resolve_links` checks S2's externalIds.ArXiv and openAccessPdf, then OpenAlex's locations;
    `--links` adds an arXiv title search and looks for the PDF by title on the CVF (CVPR / ICCV / WACV) and ECVA (ECCV) open-access pages. Each of the three steps records separately that "the server responded", so a step that was rate-limited is retried next time.
    The results are cached in `data/meta/fulltext_links.json`. A paper read from its abstract only is re-read automatically once its full text is found later.
    Publishers with anti-scraping measures (MDPI, some institutional repositories that return 403) are not circumvented.
  - Output fields: problem / motivation / key_idea / insight / method / setting / numbers / limitations / lineage (builds_on / compares_to) / summary.
    setting covers the backbone, whether the code is open source, observations, action space and privileged information.
    `summary` is a short English summary; older notes may carry `summary_zh` instead, which `./atlas export` drops.
  - Each numbers row records: benchmark, split, eval_set (full or subset + a note), method, backbone, zero-shot or not, extra conditions, metrics, evidence.
  - The program automatically checks that every number appears verbatim in the source text (`verify`).
  - Landmark papers and the top-N most-cited papers are read with Opus. On hitting a usage limit, reading pauses until the limit resets.
  - `READ_VERSION` marks the version of the reading method; when the method changes, everything is re-read.
- **Results tables**: build gathers all numbers into `public/leaderboard.json`; the Benchmarks page can sort and filter them.
- **Survey workspace**: write the outline tree in `SURVEYS` in `atlas.py`, or one outline per survey in `atlases/<id>/surveys/<sid>/survey.py` (a dict `SURVEY`),
  so a manuscript can stay private while the atlas is shared. Time is the main axis, methods are grouped within each era, and Part III follows problem threads across all eras.
  Workflow: `./atlas survey <id> <sid> init` → `assign` (ideally after deep reading is done, so the assignment can use the notes) → `packs` (writing material for each chapter)
  → writing → `pdf`. The matching web page is `#/<id>/survey/<sid>`.
- **Q&A**: `./atlas ask <id> "question"` answers from the atlas and cites papers (engine/ask.py; also the hub's Ask tab and `POST /api/ask`).
- **Large fields**: `./atlas harvest <id> --probe` first shows the hit count of each query; `./atlas update <id> --full --prescreen haiku` first has Haiku judge scope only, keeps the uncertain ones, then runs the detailed classification.

## 3. Day-to-day operations

| Task | Command |
|---|---|
| Saw a new paper and want it in right away | `./atlas add <id> 2510.12345 https://arxiv.org/abs/2509.99999` (about 1 minute; prints the classification) |
| Incremental refresh now | `./atlas update <id>`; all atlases: `./atlas update --all` |
| Full refresh (recommended once a quarter) | `./atlas update <id> --full` |
| Fix a label | edit `atlases/<id>/overrides.json`, then `./atlas build <id>` |
| Change an atlas's display name / description / id | pencil icon on its card on the web page; or `./atlas meta <id> --title …`, `./atlas rename <old> <new>` |
| One-step backup | `./atlas backup` (snapshots of all atlases + commit + push), or Back up now in the website settings |
| See what was added this week | `atlases/<id>/data/logs/new-<date>.md`; on the website, click "Recently added" in the Library |
| Ask the atlas a question | `./atlas ask <id> "question"` (the answer cites papers); or the Ask tab on the hub |
| Publish a read-only copy | `./atlas export <id> <dir>`: a static copy of the site for GitHub Pages or any web host |
| Start / stop the website | `./atlas start` / `./atlas stop` / `./atlas status`; foreground: `./atlas serve`. The hub binds to 127.0.0.1 unless `--host` or local.json `"host"` is set |
| Scheduled updates | `./atlas cron install` (weekly by default, Sunday 05:13; `--daily` for daily) / `./atlas cron show` / `./atlas cron remove` |
| Change the default atlas, theme, background, accent colour | the gear icon at the top right of the site, or `#/settings`; stored in `settings.json` at the repo root |

---

## 4. Pitfalls already hit (all fixed; follow these for new fields)

**arXiv**
- After a run of requests it returns 429, with no Retry-After. Use exponential backoff (10→180 seconds, 12 attempts), and do not probe counts while a harvest is running.
- Mixed AND/OR must be parenthesized.
- Search is stemmed: `abs:agentic` equals `agent` (4k+ results), and `ti:navigate` equals `navigation`.
- Phrase searches undercount; pair them with broad `all:` queries and the title safety net.
- When a single query has more than about 10k results, only empty pages come back after result 10,000. harvest automatically splits such a query into date windows by submission year (and by month if a window is still too big) and fetches each one.
  A failed query only skips itself; the failed keys are listed at the end, and `--only` reruns them.
- Download full texts from `export.arxiv.org`: the main `arxiv.org` site drops the connection after 1–2 MB. Downloads resume with Range requests.

**OpenAlex**
- It matches stemmed full text, case-insensitively: `REVERIE` matches "reverie" in humanities papers.
  So OpenAlex-only rows must match the strict regexes, and abbreviations are always matched case-sensitively.
- Drop records whose type is `dataset / paratext / peer-review / erratum / editorial / other`, and records whose title starts with "Dataset from the paper …".
- Every response carries `cost_usd` (about $0.001 per call); keep the number of calls to a few hundred.
- Anonymous calls get two kinds of 429:
  - Throttling under high load, with a "retry in Ns" hint; just wait as instructed.
  - The free daily budget for this IP is used up (Insufficient budget), which resets only at midnight UTC. In that case harvest-oa stops immediately and says so.
  Building two atlases from scratch back to back easily uses up the daily budget. Put a free `openalex_api_key` in `local.json`, or wait for the budget to reset, then run `./atlas harvest-oa <id>` followed by merge and classify to fill in incrementally.

**Venues and BibTeX**
- DBLP puts an Anubis anti-bot wall in front of scripted clients, on all three mirrors. Use S2 batch instead (500 ids per call, occasional 429, just back off)
  + CrossRef (`/works/<doi>/transform/application/x-bibtex`).
- S2's `year` is the year of **first appearance**, i.e. the arXiv year, which turns NavGPT into "AAAI 2023". See Step 6 for the year correction.
- S2 can attach the wrong long-tail venue: it attached NaVILA to MDPI's journal "Robotics". Trust it only when there is a publisher DOI or a non-CoRR DBLP key.
- The last two digits of a non-CoRR DBLP key are the venue year: `conf/aaai/ZhouHW24` → 2024.

**Dedup and filtering**
- When the journal or conference version changes the title, duplicates appear. Merge on "method name + ≥2 shared author surnames + year gap ≤2".
- Manual seeds must bypass the rule-based prefilter (the abstracts of ALFRED, Gibson and Matterport3D do not contain "navigation"); seeds that are already in the atlas must also get the `seed` flag retroactively.

**Claude CLI and concurrency**
- Classifier call: `claude -p --model sonnet --system-prompt … --output-format json --no-session-persistence --tools "" --strict-mcp-config`,
  with the cwd set to `/tmp`. Every returned code must be validated against the taxonomy.
- In the cron environment PATH must include `~/.local/bin`, or `claude` is not found. `./atlas` already adds it.
- `pkill -f 'xxx'` or `pgrep -f` matches the shell command line it runs in (killing itself, or waiting forever for itself to exit); use PIDs.
- Two classify processes appending to labels.jsonl at the same time interleave and corrupt lines, so they must run one after the other. The flock lock guarantees this.

**Website**
- Horizontal overflow on phones: on narrow screens grid columns must be `minmax(0,1fr)`, not `1fr`.
- Categorical palettes must pass the dataviz validation script (`validate_palette.js` must be run from a `{"type":"module"}` directory). A colour order chosen by meaning failed the CVD check.
- The day of the initial import (`META.bulk_date`) must be excluded from the NEW badges and the weekly digest.
- Screenshot check: `google-chrome --headless=new --screenshot=out.png --window-size=1440,1900 --virtual-time-budget=9000 "http://localhost:8668/#/<id>/map"`;
  for console errors: `--dump-dom --enable-logging=stderr`. The settings panel can be opened directly at `#/settings`.

---

## 5. Scale and timings (measured on NavAtlas, Claude Max subscription)

| Step | Volume | Time |
|---|---|---|
| arXiv harvest | 55 queries → 9.8k papers (including 429 backoff) | ~25 min |
| OpenAlex | 16 queries → 14k records (most are filtered out) | ~5 min |
| Sonnet classification | 10.9k papers, 40 per batch, 6 workers, ~27 s per batch | ~35 min |
| Opus re-judging | 1.7k papers, 30 per batch, 4 workers, ~150 s per batch | ~40 min |
| S2 + CrossRef | 3.3k + 1.3k | ~10 min |
| Weekly incremental update | last 62 days | ~5 min |

**Fields that are too large** (e.g. General LLM Agent, possibly tens of thousands of papers since 2023): first run a cheap "in scope or not" prescreen with `--model haiku`,
then give full Sonnet labels only to the papers that pass; also narrow the queries by category or title.

---

## 6. Starter designs for target fields (agree on them with the user before starting)

> These three fields overlap a lot. Recommendation: **build Embodied Agent on its own** (mostly robotics venues, complementary to NavAtlas);
> **merge General Agent + Agent Harness into one atlas**, so the same papers are not classified twice.
>
> **Harness is not a paradigm of its own** (decided 2026-09-29): an agent harness is a kind of agentic system, since its contribution lies in "control flow outside the model".
> Record the paradigm as `agentic` and add a `Harness` trait to distinguish it. NavAtlas does exactly this (`PARADIGM_ALIASES` maps the old labels).
> Landmark papers are listed by name only; look up and verify their arXiv ids.

### A. Embodied Agent (suggested id: `agentatlas`)
- **core**: agents driven by foundation models (LLM/VLM/VLA) that perceive and act in real or simulated embodied environments.
- **adjacent**: pure low-level control or RL policy learning that still evaluates the agent.
- **parked**: navigation (already covered by NavAtlas).
- **out**: pure NLP, pure vision.
- **TASKS**: manipulation, mobile manipulation, long-horizon household tasks, open world / games (Minecraft), EQA / embodied reasoning, human-robot interaction, navigation.
- **PARADIGMS**: LLM-as-planner → code-as-policy → end-to-end VLA → hierarchical (VLM planner + low-level policy) → agentic
  (skill library / reflection / tools; harness / self-evolving as the `Harness` trait under agentic). There are also two lines, learned and training-free.
- **Query seeds**: `abs:"embodied agent"`, `abs:"vision-language-action"`, `abs:"code as policies"`,
  `abs:"task planning" AND abs:"large language model" AND abs:robot`, `abs:"skill library"`, `abs:Minecraft AND abs:agent`,
  `abs:"embodied reasoning"`, `abs:"world model" AND abs:embodied`, every benchmark name, and the safety net
  `(ti:robot OR ti:embodied) AND (ti:language OR ti:agent OR ti:LLM)`.
- **BENCHMARKS**: ALFRED, BEHAVIOR-1K, VirtualHome, CALVIN, LIBERO, RLBench, SimplerEnv, EmbodiedBench, MineDojo.
- **Landmarks**: SayCan, Inner Monologue, Code as Policies, ProgPrompt, Voyager, VoxPoser, PaLM-E, RT-2, OpenVLA, π0.

### B. General LLM Agent + Harness (suggested id: `llmagent`)
- **Note the scope inversion**: web / GUI / OS "navigation" is out in NavAtlas but **core** here.
- **core**: LLM-centred agents that act over multiple steps (tool calling, planning, memory, multi-agent, web/GUI/OS/mobile, software engineering, research agents),
  plus their benchmarks, safety and evaluation.
- **adjacent**: LLM work that only reasons and does not act.
- **out**: embodied robotics (belongs to A).
- **TASKS**: tool use, web, GUI/OS/mobile, SWE / coding, research / data, multi-agent, memory, evaluation, safety.
- **PARADIGMS**: prompting (ReAct / Reflexion) → agent fine-tuning / RL for agents → multi-agent frameworks → self-evolving / automated workflow search.
  **Harness / scaffold** (the orchestration layer outside the model: control loop, context engineering, verification, tool interfaces) belongs on the agentic side by the adopted definition;
  it is not a paradigm of its own but is marked with the `Harness` trait. Criterion: "Does the contribution mainly live in the weights, or in the orchestration layer outside the weights?"
- **Query seeds**: `abs:"LLM agent" OR abs:"language agent" OR abs:"autonomous agent"`, `abs:"tool use" AND abs:"large language model"`,
  `abs:"web agent" OR abs:"GUI agent" OR abs:"computer use"`, `abs:"coding agent" OR abs:"software engineering agent"`,
  `abs:scaffold AND abs:agent`, `abs:harness AND abs:agent`, `abs:"context engineering"`, `abs:"agentic workflow"`,
  `abs:"self-improving" AND abs:agent`, every benchmark name.
- **BENCHMARKS**: SWE-bench, WebArena, OSWorld, GAIA, AgentBench, τ-bench, Mind2Web, ToolBench, BFCL, AppWorld, Terminal-Bench.
- **Scale**: this is the largest field; it must be prescreened with Haiku first as in §5, and consider restricting it to 2023 onward and to cs.CL/cs.AI/cs.SE.

---

## 7. Moving machines / portability (rebuilding the process, not just reproducing the results)

This repository carries everything: the code plus the data snapshots. A new machine needs only a clone and `./install.sh --cron --start`.
The details:

**Kept in git**: the code, `atlases/<id>/{atlas.py, seeds.txt, overrides.json, snapshot/}`, `settings.json`, `claude/`.
**Not in git**: `data/` and `public/` (both can be restored from a snapshot or rebuilt), and `local.json` (contact email and other per-machine options).

```bash
# Old machine: when the data has changed, snapshot it and push
./atlas snapshot navatlas --with-raw        # ~15 MB; if only labels/notes changed you can leave out --with-raw (~1.5 MB)
git add -A && git commit -m "snapshot" && git push

# New machine (use your own fork if it carries your snapshots)
git clone https://github.com/billzhao1030/SurveyAtlas.git && cd SurveyAtlas
./install.sh --cron --start
# In order: checks python3/requests/claude → writes local.json → symlinks claude/ into ~/.claude
# (skill, playbook, CLAUDE.md snippet) → restores each atlas from its snapshot and builds it → installs cron → starts the site on 8668
claude            # log in to Claude Code once (classification needs it)
```

- The gz files in a snapshot are deterministic (mtime=0): if the data has not changed, there is no git diff.
- After restoring a snapshot taken without raw, run `./atlas update <id> --full` to harvest again; **papers that already have labels are not reclassified** (labels are cached by uid).
- The entries in `~/.claude` are symlinks, so editing the playbook or the skill in the repo takes effect in both places; just commit.


## Caches included in snapshots (2026-09-30)

Besides labels and venues, `./atlas snapshot` also packs these caches that cost many tokens to produce: deep-reading notes (reading.tar.gz), survey assignments (surveys.tar.gz), stable citation keys (cite_keys.json, which the survey LaTeX depends on), protocol judgements (comparable.json), audits (audit.json), benchmark variants (variants.json), full-text links (fulltext_links.json) and prescreen results. After `./atlas restore <id>` on a new machine, none of these need to be rerun.
