"""{{TITLE}} — {{SUBTITLE}}: the domain definition.

`./atlas new` created this from atlases/_template. Everything field-specific
lives here; engine/ and hub/ are generic. Worked example: atlases/navatlas/atlas.py.

Fill in, in this order (the playbook explains each step — see the
literature-atlas skill, or claude/skills/literature-atlas/playbook.md):
  1. SCOPES / TASKS / PARADIGMS …   agree the boundaries with the user FIRST
  2. CLASSIFY_ROLE / CLASSIFY_RULES concrete in/out examples decide the labels
  3. ARXIV_QUERIES / OPENALEX_QUERIES  probe counts before harvesting
  4. RELEVANCE_RE / OA_STRICT_RE …   prefilter
  5. LANDMARKS                       verify every id against its arXiv title
  6. UI                              map groups, presets, survey outlines
then set DOMAIN_READY = True and run `./atlas update {{ID}} --full`.
READ_ON_UPDATE = "new"  # read the papers each update adds ("all" also catches up, False turns it off)
"""
import re

DOMAIN_READY = False  # harvest / classify refuse to run while False

META = {
    "id": "{{ID}}",
    "title": "{{TITLE}}",
    "subtitle": "{{SUBTITLE}}",
    "field": "{{FIELD}}",
    "description": "TODO one or two sentences shown on the hub's atlas card.",
    "bulk_date": "{{TODAY}}",     # initial import day: never shown as NEW (set to the day of the first full run)
    "timeline_start": 2018,       # first bar of the timeline pools everything up to this year
    "min_year": 2012,             # prefilter: drop older papers
    "classify_model": "sonnet",
}

# ───────────────────────── harvest ─────────────────────────
# (key, arXiv query, note). Recall first — precision comes from the classifier.
# Layers: field phrases (all spellings) · benchmark / dataset names · task names ·
# method family × field term · a title safety net restricted to CS categories.
# arXiv search is stemmed ("agentic" == "agent"); parenthesise mixed AND/OR.
ARXIV_QUERIES = [
    ("field",     'abs:"TODO field phrase"', "field phrase"),
    ("bench",     'abs:TODO_BENCHMARK', "benchmark name"),
    ("ti-net",    '(ti:TODO) AND (cat:cs.CV OR cat:cs.RO OR cat:cs.AI OR cat:cs.LG OR cat:cs.CL)', "title safety net"),
]

# OpenAlex title/abstract search (stemmed, case-insensitive → keep narrower than arXiv)
OPENALEX_QUERIES = [
    ("field", '"TODO field phrase"'),
]

# ───────────────────────── prefilter (merge.py) ─────────────────────────
# A candidate must mention one of these somewhere in title+abstract.
RELEVANCE_RE = re.compile(r"TODO|todo field term", re.I)
# OpenAlex-only rows (no arXiv preprint) must also hit a strict field phrase…
OA_STRICT_RE = re.compile(r"TODO strict field phrase", re.I)
# …or a field acronym in its canonical casing (case-SENSITIVE on purpose).
OA_STRICT_CS_RE = re.compile(r"\b(TODOACRONYM)\b")
OK_CATS = ("cs.", "eess.", "stat.ML")  # arXiv categories that can host the field

# ───────────────────────── taxonomy (single source of truth) ─────────────────────────
# scope codes MUST include "core" and "out"; the site shows core by default.
# Write concrete positive AND negative examples — the classifier decides boundaries from these words.
SCOPES = [
    ("core", "Core", "TODO: the field is the paper's main subject (method, benchmark, dataset, survey, analysis)."),
    ("adjacent", "Adjacent", "TODO: substantial but secondary relevance."),
    ("out", "Out of scope", "TODO: what looks similar but is NOT the field (list the confusable neighbours)."),
]
# (code, label, definition) — multi-label task families
TASKS = [
    ("TaskA", "Task A", "TODO definition."),
]
# (code, label, definition) — experimental setting / environment, multi-label
SETTINGS = [
    ("sim", "Simulation", "TODO."),
    ("real", "Real world", "TODO."),
]
# (code, label, line, definition) — ORDER is the survey's storyline; colours follow this order.
# Keep a final "none" paradigm for benchmark / survey papers.
PARADIGMS = [
    ("trained", "Trained", "learned", "TODO: weights trained on field data."),
    ("training-free", "Training-free", "training-free", "TODO: frozen models in a pipeline."),
    ("agentic", "Agentic", "training-free", "TODO: the model owns the control flow."),
    ("none", "No method", "-", "Benchmark, dataset, survey or analysis with no new method."),
]
CONTRIBS = [
    ("method", "Method"), ("benchmark", "Benchmark"), ("dataset", "Dataset / data engine"),
    ("survey", "Survey"), ("analysis", "Analysis / study"), ("system", "System"),
]
TRAITS = [
    ("LLM", "uses a large language model"), ("VLM", "uses a vision-language model"),
    ("RL", "reinforcement learning"), ("Memory", "long-term memory"), ("ToolUse", "tool / API calling"),
]
BENCHMARKS = {
    "TODO group": ["TODO-Bench"],
}

# ───────────────────────── classifier prompt ─────────────────────────
CLASSIFY_ROLE = ("You are a senior researcher curating a literature database for a survey on **{{FIELD}}**. "
                 "You label papers from their title and abstract only. Be precise and conservative: the database must be clean.")
CLASSIFY_RULES = """\
- `scope`: decide first. TODO: state what counts as {{FIELD}}, then list concrete out-of-scope neighbours and parked areas.
- `tasks`: every task family the paper actually addresses or evaluates. Empty list only for `out`.
- `settings`: what the abstract states or clearly implies. Empty if unknowable.
- `paradigm`: the paper's OWN approach. TODO: write the deciding questions as testable yes/no questions.
- `contrib`: all that apply.
- `traits`: only what the abstract supports.
- `benchmarks`: canonical names only (or other:<Name>). Do not guess benchmarks that are not mentioned.
- `name`: the method / benchmark short name as the paper calls it; null if none.
- `tldr`: ONE sentence, ≤ 28 words, in English, saying what the paper does and how (not hype).
- `conf`: "high" or "low" — "low" when the scope decision is borderline or the abstract is missing/uninformative.
"""

# ───────────────────────── recall check ─────────────────────────
# Papers that MUST end up in the library. Verify each id against its arXiv title.
LANDMARKS = [
    # ("1706.03762", "Transformer"),
]

# ───────────────────────── website (hub) ─────────────────────────
UI = {
    "task_groups": [["Tasks", [t[0] for t in TASKS]]],
    "paradigm_lines": {"learned": "Learned", "training-free": "Training-free", "-": "No method"},
    "paradigm_note": "TODO: one sentence on how the paradigms are told apart.",
    "timeline_intro": "TODO: one sentence on how the field moved over time.",
    "era_split": 2023,
    "ask_examples": ["TODO: a question a newcomer to the field would ask", "What are the main open problems?"],
    "presets": [
        ["Benchmarks & datasets", {"ct": ["benchmark", "dataset"]}],
        ["Surveys", {"ct": ["survey"]}],
        ["Most cited", {"sort": "cited"}],
        ["Recently added", {"sort": "added"}],
    ],
    "surveys": [],
}
