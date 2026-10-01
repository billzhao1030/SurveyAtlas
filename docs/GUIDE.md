# SurveyAtlas guide

> SurveyAtlas is a set of **living literature surveys**. Each atlas is the paper library of one research field.
> Papers are harvested from arXiv and OpenAlex, deduplicated, classified into a taxonomy by headless Claude, given venues and BibTeX, deep-read into structured notes, and updated on a schedule.
> **NavAtlas** (embodied navigation and vision-and-language navigation) is the worked example.

## Quick start <!-- #start -->

1. Run `./atlas start` and open `http://localhost:8668/`.
2. Click the atlas name at the top left and pick an atlas from the menu (NavAtlas by default).
3. Type keywords in the **Library** search box (press `/` to jump to it), then tick filters on the left.
4. Click any paper card. A panel opens on the right with the abstract, labels and BibTeX (one-click copy). You can star the paper and write notes.
5. Ask a question in plain language on the **Ask** tab.
6. For the big picture, use **Map** (task × paradigm matrix) and **Timeline** (year by year).

## Pages <!-- #pages -->

| Page | What it is for |
|---|---|
| **Library** | Search and filter papers. Export BibTeX, `\cite{}` or CSV. The page you will use most |
| **Ask** | Ask a question in plain language. The answer comes from the atlas only and cites its papers |
| **Map** | Heat matrix of tasks × method paradigms. Dark cells are mature directions, empty cells are open ones. Click a cell to see its papers |
| **Timeline** | Papers per paradigm per year, growth of each task, and the most-cited papers of each year |
| **Benchmarks** | **Results table**: the numbers each paper reports, taken from deep reading. Sort by any metric. Filter by full split or subset, zero-shot or trained, open or closed models, and use of privileged information. Below it: which papers evaluate on which benchmark |
| **Surveys** | Survey projects. Each has its own page: outline tree, the papers in each section, reading progress, LaTeX and PDF. Other candidate outlines are listed below |
| **Pipeline** | Where the data comes from: the harvest funnel, how well each query works, the excluded papers (search here if you suspect a paper is missing), and the **Update now** button |
| **Atlases** (click the logo at the top left) | Overview of all atlases: create, rename, update. The log of background jobs is at the bottom |

## Search and filters <!-- #search -->

**Filter bar** (above the results):

- **Years**: drag the two handles of the range slider over the papers-per-year histogram, click a bar to keep one year, or pick a quick span (All, last three years, this year, 2020–23, up to 2019).
- **Authors**: type two or more letters of a name and pick from the suggestions (each shows the paper count and active years). With one author selected, a profile card shows their papers, first-author papers, citations, years active, paradigms, most-cited paper, frequent co-authors (click one to switch to them) and venues.
- **Venue**: pick one venue, or switch between All, Published and Preprints.

The bar and the sidebar share the same filters, and every filter is kept in the page URL, so a filtered view can be shared as a link.

**Keywords**: with several words, a paper must contain all of them. The search covers title, method name, authors, TL;DR, abstract, benchmarks, venue and the deep-reading notes.

**Advanced syntax** (mix freely with plain keywords):

| Syntax | Meaning |
|---|---|
| `task:ObjectNav` | Filter by task (code or part of the name) |
| `bench:R2R-CE` | Evaluated on this benchmark |
| `pd:agentic` | Filter by method paradigm |
| `year:>=2024` / `year:2019-2021` / `year:2023` | Filter by year |
| `venue:CVPR` | Filter by conference or journal |
| `author:anderson` | Filter by author |
| `org:google` | Has an author from this industry lab |
| `name:NaVid` | Filter by method short name |
| `"exact phrase"` | Exact phrase |
| `-aerial` / `-bench:R2R` | Exclude |

**Filter sidebar**: several choices in one group mean OR (for example VLN or ObjectNav). Different groups combine with AND.

- **Scope**: only core papers by default. Tick Adjacent for neighbouring directions, or Aerial for drones.
- **Paradigm**: in NavAtlas there are two lines. The training line goes task-specific training → large-scale pretraining → fine-tuned foundation models. The training-free line goes zero-shot pipelines → agentic.
  Agent harnesses (self-evolving systems that write code or skills) count as agentic and carry a **Harness** technique tag. To see only these, tick Harness under Technique, or click the preset **Harness only**.
- **Year**: a histogram. Click bars to select years; several are allowed.
- **Author**: collapsed by default. Shows the top 8 authors and has a search box for the rest.
- **Venue**, **Benchmark** and **Industry authors** each have their own search box.
- **Deep reading**: show only papers that have been deep-read, or only those that have not.

**Tips**

- Paradigm, task and benchmark tags on a card are clickable. A click adds them to the filters.
- A yellow `check` tag means the classifier was unsure about the scope. It is worth a manual look.
- The address bar keeps the current search and filters. Bookmark it or send the link to someone.
- Sort options: Relevance / newest, Newest, Oldest, Most cited, Recently added, Title A–Z, First author A–Z.

## Ask the atlas <!-- #ask -->

The **Ask** tab sits between Library and Map. Type a question in plain language, for example "which training-free methods lead R2R-CE?".

- Claude on the hub machine answers from the atlas only: titles, abstracts, deep-reading notes and reported benchmark results.
- Cited papers appear as numbered chips. Click a chip to open the paper. A numbered reference list follows the answer.
- If the question names a benchmark, a table of the best reported full-split results on it is attached.
- Each question has a shareable link: `#/<atlas>/ask?q=...`.
- Answers are cached in `atlases/<id>/data/ask_cache.jsonl`. The same question returns at once until the atlas is rebuilt.
- On other devices, answering needs the admin key (see [Use it from other devices](#/guide/devices)) or a hub started with `ATLAS_ASK_OPEN=1`. Without either, the page lists the most relevant papers.
- On a static copy (see [Publish a read-only copy](#/guide/publish)), papers are matched in the browser and no answer is written.

From the command line: `./atlas ask <id> "question"`. Add `--no-llm` to list the matching papers only. Answering needs the `claude` CLI.

## Papers, marks and exports <!-- #paper -->

The paper panel contains:

- **Authors**: each name is a link that filters the Library by that author.
- **Links**: arXiv, PDF, DOI, Semantic Scholar, Google Scholar, DBLP, and a GitHub code search.
- **My marks**: star, reading status (queued / reading / read / skip) and notes. Notes save automatically on the hub, and **everyone who opens the site sees the same copy**.
- **BibTeX**, with its source noted below it:
  - Publisher: from CrossRef.
  - Generated in the venue's style: for venues without DOIs, such as NeurIPS, ICLR and CoRL.
  - arXiv preprint: no published version found yet. Every update checks again and upgrades the entry once the paper is accepted.
- **TL;DR**: the one-line summary from classification. Every paper has one.
- **Deep reading**: papers read in full show a short English summary (2–3 sentences), the problem, motivation, key idea, method, insight, and the experimental setting (backbone, open source or not, observations, action space, privileged information).
  They also list every number the paper reports. Each number names its benchmark, split, full set or subset, and model.
  **✓ in text** means the number was found verbatim in the paper. Hover to see where.
  Older notes may have no summary; the TL;DR is still shown.
  Deep-read papers carry a `deep-read` tag in the list.

**Bulk export** from the Library toolbar acts on the current filtered list:

- `BibTeX` downloads all entries.
- `\cite` copies all citation keys.
- `CSV` downloads a spreadsheet with the columns id, name, title, authors, year, venue, citations, scope, paradigm, tasks, benchmarks, tldr, url, bibkey.

Citation keys follow a fixed pattern: first author's surname + arXiv year + first title word. They do not change when the paper is later published.

**Starred only**: tick ★ Starred in the toolbar.

## Staying up to date <!-- #update -->

- **Scheduled**: `./atlas cron install` updates every atlas weekly (Sunday 05:13). Use `./atlas cron install --daily` to update every day at 05:13 instead.
  New papers carry a **new** tag for 10 days. Sort the Library by Recently added to see them.
- **By hand**: click **Update now** on the Pipeline page or on the atlas card on the Atlases page, or run `./atlas update --all`. An incremental update takes about 5 minutes.
- **Add one paper now**: on the hub machine, run `./atlas add navatlas 2510.12345`. It takes about a minute and prints where the paper was classified. An arXiv URL works too.

## Create a new atlas <!-- #new -->

Example: an Embodied Agent atlas.

1. Open the **Atlases** page: click the logo at the top left, or click **+ New atlas** in the atlas menu.
2. Fill in the **New atlas** card:
   - a title
   - an id (lowercase; it appears in URLs)
   - a brief: what belongs to the field and what does not, the story the survey should tell, and the key benchmarks

   Then click **Create atlas**.
3. Click **Draft with Claude** on the new card. Claude drafts the field definition the way NavAtlas does it: queries, taxonomy, classification rules and must-have landmark papers.
   This takes a few minutes. Progress and logs show under **Jobs** at the bottom of the page.
4. **Review** `atlases/<id>/atlas.py` and `DRAFT_NOTES.md`. Focus on scope and paradigm.
   You can also ask Claude Code to edit them.
5. Click **Check**, then **Mark ready**, then **Run first build**. The first build harvests and classifies everything, which takes about 1–2 hours.
6. To back out: the **New atlas** form has **Cancel**. An atlas that is created but not built yet has **Discard** on its card, which deletes it.
   An atlas that has been built and has data cannot be deleted from the web page. Run `./atlas delete <id> --force`.
7. The new atlas then appears in the atlas menu. You can make it the default in Settings.

From the command line, the same steps are:

```
./atlas new <id> --title "Embodied Agents"
./atlas draft <id> --brief "what is in, what is out, the story, key benchmarks"
./atlas check <id>
./atlas ready <id>
./atlas update <id> --full
```

## Rename, default atlas, appearance <!-- #rename -->

- **Rename**: on the Atlases page, click the pencil at the top right of a card to change the title, subtitle, description and id.
  Changing the id breaks old bookmarks; they open the default atlas instead.
- **Settings** (gear icon at the top right):
  - Default atlas: the one shown when the site opens.
  - Theme: light (default), dark, or follow the system.
  - Background: paper, white, mist blue, sand, grid, dots, aurora, or a custom colour.
  - Accent colour.
  - Density of Library cards.
- Settings changed on the hub machine apply to everyone. Settings changed on another device that is not unlocked apply only to that device's browser.

## Deep reading, results tables and survey projects <!-- #reading -->

- **Deep reading** runs in the background and can take days: `./atlas read navatlas`. It pauses when it hits a usage limit and continues when quota returns.
  Check progress with `./atlas read navatlas --status`.
  - Highly cited and landmark papers are read with Opus, the rest with Sonnet.
  - Papers whose numbers do not match the source text can be re-read by Opus with `--redo-unverified`.
  - When the reading method is upgraded (a new version number), all older notes are re-read.
  - For papers without an arXiv id (conference or journal versions), the reader looks for an arXiv version or an open-access PDF on Semantic Scholar and OpenAlex.
    `./atlas read navatlas --links` also runs a slower arXiv title search. Restart reading afterwards; papers that were read from the abstract only are re-read from the full text.
    Papers with no full text found are read from the abstract only. `--status` shows how many there are.
  - Optional reading window: set `"read_window": "23:30-08:00"` in `local.json` to start new papers only inside that window. `--anytime` overrides it.
- **Results table** (Benchmarks page): pick a benchmark and split, then click a column name to sort by that metric.
  - For fair comparisons, tick **standard setting only**. It removes special settings such as beam search, pre-exploration and ground-truth maps.
  - Also tick **Full split** to see only results on the complete split.
- **Survey projects** (Surveys → a project):
  - Each leaf of the outline tree lists the papers assigned to that section, sorted by date and marked landmark or representative.
  - On the right are the problem threads and the status of each chapter's LaTeX. The top bar opens `main.tex`, the PDF and `refs.bib`.
  - A survey outline lives either in `SURVEYS` in `atlases/<id>/atlas.py`, or in `atlases/<id>/surveys/<sid>/survey.py` as a dict named `SURVEY`. The second form lets you share an atlas while keeping a manuscript private.
  - The commands are `./atlas survey <id> <sid> init|assign|packs|bib|pdf|status`: create the LaTeX workspace, assign every paper to an outline leaf, build per-chapter material packs for writing, collect `refs.bib` from the cited keys, compile the PDF, and show progress.

## Fix a label <!-- #fix -->

If a paper is misclassified or its venue is wrong, add an entry to `atlases/<id>/overrides.json`:

```
{
  "2412.04453": {"venue": "RSS", "venue_year": 2025},
  "2509.12345": {"scope": "core", "paradigm": "agentic", "tasks": ["VLN"]}
}
```

Then click **Rebuild site data** on the Pipeline page, or run `./atlas build <id>`. Overrides are permanent; later updates never overwrite them.

## Use it from other devices <!-- #devices -->

- **Open the hub to the network**: by default the hub listens on 127.0.0.1, so only the hub machine can open it. Start it with `./atlas start --host 0.0.0.0`, or set `"host": "0.0.0.0"` in `local.json`.
- **Site password**: other devices must then sign in with the site password. It starts as `0000`. Change it in Settings or with `./atlas password`. The hub machine itself is never asked.
- **Browse**: open `http://<hub machine IP>:8668/`. A LAN or Tailscale both work.
- **Edit** (create, rename, update, back up, and get answers on Ask): unlock the device first.
  - On the hub machine, run `./atlas admin-key`. It prints a one-time unlock link. Open it once on the other device.
  - Or enter the key in Settings → Editing.
- Devices that are not unlocked can browse, star and write notes.

## Backup and moving machines <!-- #backup -->

- **Snapshot**: `./atlas snapshot <id> [--with-raw]` packs an atlas's costly data (classifier labels, venue and BibTeX lookups, citation keys, deep-reading notes, benchmark-protocol checks, stars and notes; raw harvests with `--with-raw`) into `atlases/<id>/snapshot/`, which is meant to be committed. If the repo is public, local.json `"snapshot": {"skip": ["marks", "surveys"], "drop_fields": ["summary_zh"]}` keeps private parts out of it.
- **Restore**: `./atlas restore <id>` unpacks the snapshot into `data/` and rebuilds the site data.
- **One-step backup**: click Settings → **Back up now**, or run `./atlas backup`. It snapshots every atlas, commits only the snapshots and `settings.json`, and pushes if the repo has a remote.
- **New machine**:

```
git clone https://github.com/billzhao1030/SurveyAtlas.git && cd SurveyAtlas && ./install.sh --start
claude        # log in once; classification, deep reading and Ask use it
```

  `install.sh` restores the libraries from the snapshots, builds them, and starts the hub on port 8668. Add `--cron` to also install the weekly update.

## Publish a read-only copy <!-- #publish -->

`./atlas export <id> <dir>` writes a static, read-only copy of the site. Host it on GitHub Pages or any web server.

- It contains Library, Ask, Map, Timeline, Benchmarks, Surveys and paper pages.
- Ask matches papers in the browser; it does not write answers.
- Stars and notes stay in each visitor's own browser.
- Editing, jobs and backups are turned off.
- Survey projects (LaTeX workspaces) are left out unless you add `--keep-surveys`.

Preview it locally:

```
./atlas export navatlas /tmp/navatlas-site
python3 -m http.server -d /tmp/navatlas-site 8000
```

The repo ships a GitHub Actions workflow, `.github/workflows/pages.yml`, that exports NavAtlas to GitHub Pages.

## CLI cheat sheet <!-- #cli -->

Run every command from the repo root on the hub machine.

| Command | What it does |
|---|---|
| `./atlas list` | Atlases, paper counts, build dates |
| `./atlas add <id> <arXiv id or URL>` | Add one or more papers now |
| `./atlas update <id>` / `--all` / `--full` | Incremental update / update every atlas / full re-harvest |
| `./atlas update <id> --full --prescreen haiku` | For very large fields: a cheap Haiku scope pass before full classification |
| `./atlas build <id>` | Rebuild the site data from existing data (after editing overrides) |
| `./atlas ask <id> "question"` / `--no-llm` | Answer a question from the atlas / list the matching papers only |
| `./atlas new <id> --title …` | Create an atlas |
| `./atlas draft <id> --brief "…"` | Let Claude draft the field definition |
| `./atlas check <id>` / `./atlas ready <id>` | Validate the field definition / validate and unlock it |
| `./atlas meta <id> --title …` / `./atlas rename <old> <new>` | Change the display name / change the id |
| `./atlas delete <id>` | Delete an atlas that has not been built (add `--force` if it has data) |
| `./atlas read <id>` / `--status` / `--links` | Deep reading (background, resumable) / progress / find full text for papers without an arXiv id |
| `./atlas survey <id> <sid> init\|assign\|packs\|bib\|pdf\|status` | Survey project: workspace, outline assignment, writing packs, bibliography, PDF, progress |
| `./atlas export <id> <dir>` / `--keep-surveys` | Static read-only copy of the site / include survey projects |
| `./atlas snapshot <id> [--with-raw]` / `./atlas restore <id>` | Pack data into `snapshot/` / unpack it and rebuild |
| `./atlas backup` | Snapshot every atlas, commit the snapshots, push if a remote exists |
| `./atlas start` / `stop` / `status` | Start, stop or check the hub (port 8668) |
| `./atlas start --host 0.0.0.0` | Start the hub for other devices (behind the site password) |
| `./atlas password` | Change the site password |
| `./atlas admin-key` | Print the unlock link for another device |
| `./atlas cron install` / `--daily` / `remove` | Weekly update (Sunday 05:13) / daily at 05:13 / remove the schedule |

## Where things live <!-- #files -->

| Location | Contents |
|---|---|
| `atlases/<id>/atlas.py` | The whole field definition: queries, taxonomy, classification rules, landmark papers, survey outlines |
| `atlases/<id>/overrides.json` | Your manual corrections |
| `atlases/<id>/seeds.txt` | arXiv ids that are always included |
| `atlases/<id>/read_system.md` | The deep-reading prompt for this atlas |
| `atlases/<id>/surveys/<sid>/` | A survey project: `survey.py` (optional outline), LaTeX sources, PDF |
| `atlases/<id>/snapshot/` | Committed snapshot of the data, used by restore and on new machines |
| `atlases/<id>/data/` | Working data: harvests, labels, reading notes, marks (`marks.json`), Ask cache (`ask_cache.jsonl`), update logs `logs/new-<date>.md` |
| `settings.json` | Site settings |
| `local.json` | Per-machine settings, not committed: host, admin key, site password hash, contact e-mail, reading window |
| `.github/workflows/pages.yml` | Publishes NavAtlas to GitHub Pages |
| `claude/skills/literature-atlas/playbook.md` | The full method manual. Written for Claude, and useful if you want the details |
| `docs/GUIDE.md` | This page |
