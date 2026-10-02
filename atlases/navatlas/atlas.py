"""NavAtlas — Embodied Navigation Literature Atlas: the domain definition.

Everything field-specific lives in this one file; engine/ and hub/ are generic.
To start a new field: `./atlas new <id>` copies atlases/_template/atlas.py —
use this file as the worked example.
"""
import re

DOMAIN_READY = True  # harvest / classify refuse to run while False
READ_ON_UPDATE = "all"  # read every new paper and any unread in-scope paper on each update

META = {
    "id": "navatlas",
    "title": "NavAtlas",
    "subtitle": "Embodied Navigation Literature Atlas",
    "field": "Embodied Navigation",
    "description": "VLN, ObjectNav, Instance / Image / Pixel / PointNav, multi-goal & lifelong, audio, social, EQA and generalist navigation models — from RL-era specialists to training-free agents (incl. agent harnesses).",
    "bulk_date": "2026-09-29",   # initial import day: never shown as NEW
    "timeline_start": 2017,       # first bar of the timeline pools everything up to this year
    "min_year": 2010,             # prefilter: drop older
    "classify_model": "sonnet",
    # deep reading (./atlas read): scopes not read, papers read with Opus
    # (extra local full-text folders are machine-specific: local.json "fulltext_cache": ["~/papers", ...])
    "read_skip_scopes": ["aerial", "out"],
    "read_opus_top": 300,
}

# ───────────────────────── harvest ─────────────────────────
# (key, arXiv query, note). Recall first — precision comes from the classifier.
ARXIV_QUERIES = [
    ('vln-vl', 'abs:"vision-and-language navigation"', 'VLN phrase'),
    ('vln-vl2', 'abs:"vision language navigation"', "VLN phrase, no 'and'"),
    ('vln-abbr', 'abs:VLN', 'VLN acronym'),
    ('vln-broad', 'all:vision AND all:language AND all:navigation', 'broad fallback query'),
    ('vln-instr', 'abs:instruction AND abs:navigation AND abs:embodied', 'instruction following'),
    ('vln-instr2', 'abs:"navigation instructions"', ''),
    ('vln-lang', 'abs:"language-guided navigation" OR abs:"language guided navigation" OR abs:"language-conditioned navigation"', ''),
    ('vln-nlnav', 'abs:"natural language" AND abs:navigation AND abs:agent', ''),
    ('bm-r2r', 'abs:"Room-to-Room" OR abs:R2R', 'R2R benchmark'),
    ('bm-rxr', 'abs:RxR OR abs:"Room-Across-Room"', ''),
    ('bm-reverie', 'abs:REVERIE OR abs:"remote embodied"', ''),
    ('bm-soon', 'abs:SOON AND abs:navigation', ''),
    ('bm-dialog', 'abs:CVDN OR abs:"dialog navigation" OR abs:"dialogue navigation" OR abs:"navigation from dialog" OR abs:"cooperative vision-and-dialog"', ''),
    ('bm-touchdown', 'abs:Touchdown AND abs:navigation', 'outdoor street VLN'),
    ('bm-streetnav', 'abs:"street view" AND abs:navigation', ''),
    ('bm-vlnce', 'abs:"VLN-CE" OR (abs:"continuous environments" AND abs:navigation)', ''),
    ('objnav', 'abs:ObjectNav OR abs:"object navigation" OR abs:"object-goal navigation" OR abs:"object goal navigation"', ''),
    ('objnav2', 'abs:"object search" AND abs:embodied', ''),
    ('ovnav', 'abs:"open-vocabulary" AND abs:navigation', ''),
    ('semnav', 'abs:"semantic navigation" OR abs:"semantic goal navigation" OR abs:"semantic visual navigation"', ''),
    ('pointnav', 'abs:PointNav OR abs:"point-goal navigation" OR abs:"point goal navigation"', ''),
    ('imgnav', 'abs:ImageNav OR abs:"image-goal navigation" OR abs:"image goal navigation" OR abs:"image-goal"', ''),
    ('instnav', 'abs:"instance image navigation" OR abs:"instance-image" OR abs:"instance navigation" OR abs:"InstanceNav"', ''),
    ('pixnav', 'abs:"pixel navigation" OR abs:PixNav OR abs:"pixel-guided navigation"', ''),
    ('multigoal', 'abs:"GOAT-Bench" OR abs:"multi-object navigation" OR abs:"lifelong navigation" OR abs:MultiON OR abs:"GO to Any Thing" OR abs:"multimodal lifelong"', ''),
    ('audionav', 'abs:"audio-visual navigation" OR abs:SoundSpaces OR abs:"audio-goal"', ''),
    ('eqa', 'abs:"embodied question answering"', 'adjacent task'),
    ('socialnav', 'abs:"social navigation" AND (abs:embodied OR abs:habitat OR abs:simulation)', ''),
    ('tracknav', 'abs:"embodied visual tracking" OR (abs:"person following" AND abs:embodied)', ''),
    ('explore', 'abs:exploration AND abs:embodied AND abs:navigation', ''),
    ('mobmanip', 'abs:"open-vocabulary mobile manipulation" OR (abs:"mobile manipulation" AND abs:navigation AND abs:embodied)', ''),
    ('embnav', 'abs:embodied AND abs:navigation', ''),
    ('visnav', 'abs:"visual navigation"', ''),
    ('goalnav', 'abs:"goal-oriented navigation" OR abs:"goal-conditioned navigation" OR abs:"goal-driven navigation" OR (abs:"target-driven" AND abs:navigation)', ''),
    ('navfm', 'abs:"navigation foundation model" OR abs:"general navigation model" OR abs:"navigation foundation models" OR abs:"visual navigation model"', ''),
    ('sim-habitat', 'abs:habitat AND abs:navigation', ''),
    ('sim-mp3d', 'abs:matterport AND abs:navigation', ''),
    ('sim-thor', '(abs:"AI2-THOR" AND abs:navigation) OR abs:ProcTHOR OR abs:RoboTHOR', ''),
    ('sim-gibson', 'abs:gibson AND abs:navigation', ''),
    ('sim-hm3d', 'abs:HM3D', ''),
    ('llm-nav', 'abs:navigation AND abs:"large language model"', ''),
    ('llm-nav2', 'abs:navigation AND abs:LLM AND abs:embodied', ''),
    ('vlm-nav', 'abs:navigation AND abs:"vision-language model"', ''),
    ('vlm-nav2', 'abs:navigation AND abs:VLM AND abs:embodied', ''),
    ('vla-nav', 'abs:"vision-language-action" AND abs:navigation', ''),
    ('zs-nav', 'abs:"zero-shot" AND abs:navigation', ''),
    ('agent-nav', 'abs:"embodied agent" AND abs:navigation', ''),
    ('agentic-nav', 'abs:agentic AND abs:navigation', ''),
    ('multiagent', 'abs:"multi-agent" AND abs:navigation AND abs:language', ''),
    ('wm-nav', 'abs:"world model" AND abs:navigation', ''),
    ('mllm-nav', 'abs:navigation AND abs:"multimodal large language model"', ''),
    ('survey-nav', 'ti:survey AND ti:navigation', ''),
    ('ti-nav', '(ti:navigation OR ti:navigate OR ti:navigating) AND (cat:cs.CV OR cat:cs.RO OR cat:cs.AI OR cat:cs.LG OR cat:cs.CL)', 'recall net; classifier filters'),
    ('ti-explore', 'ti:exploration AND ti:embodied', ''),
]

# OpenAlex title/abstract search (stemmed, case-insensitive → keep narrower than arXiv)
OPENALEX_QUERIES = [
    ('vln-vl', '"vision-and-language navigation"'),
    ('vln-vl2', '"vision language navigation"'),
    ('vln-abbr', 'VLN AND navigation'),
    ('vln-lang', '"language-guided navigation" OR "language guided navigation" OR "instruction-following navigation" OR "navigation instructions"'),
    ('bm', 'REVERIE OR "Room-to-Room" OR "Room-Across-Room" OR CVDN OR "dialog navigation"'),
    ('objnav', 'ObjectNav OR "object navigation" OR "object-goal navigation" OR "object goal navigation"'),
    ('pointnav', 'PointNav OR "point-goal navigation" OR "point goal navigation"'),
    ('imgnav', 'ImageNav OR "image-goal navigation" OR "image goal navigation" OR "instance image navigation" OR "instance navigation"'),
    ('multigoal', '"multi-object navigation" OR "lifelong navigation" OR "GOAT-Bench"'),
    ('audionav', '"audio-visual navigation" OR SoundSpaces'),
    ('eqa', '"embodied question answering"'),
    ('embnav', '"embodied navigation"'),
    ('visnav', '"visual navigation" AND (embodied OR habitat OR "language" OR "semantic" OR "object")'),
    ('semnav', '"semantic navigation" OR "open-vocabulary navigation" OR "zero-shot navigation" OR "zero-shot object navigation"'),
    ('llmnav', '"embodied" AND "navigation" AND ("large language model" OR "vision-language model")'),
    ('navfm', '"navigation foundation model" OR ("vision-language-action" AND navigation)'),
]

# ───────────────────────── prefilter (merge.py) ─────────────────────────
# A candidate must mention one of these somewhere in title+abstract.
RELEVANCE_RE = re.compile('navigat|objectnav|pointnav|imagenav|\\bvln\\b|explor|question answering|wayfind|goal-reaching|room-to-room|reverie|\\br2r\\b|\\brxr\\b|habitat|object search|path follow|instruction following', re.I)
# OpenAlex-only rows (no arXiv preprint) must also hit a strict field phrase…
OA_STRICT_RE = re.compile('vision[- ]and[- ]language navigation|vision[- ]language navigation|object[- ]?goal navigation|object navigation|point[- ]goal navigation|image[- ]goal navigation|instance[- ]image|instance navigation|embodied navigation|room[- ]to[- ]room navigation|embodied question answering|audio[- ]visual navigation|semantic navigation|navigation instruction|language[- ]guided navigation|language[- ]conditioned navigation|zero[- ]shot (object )?navigation|multi[- ]object navigation|lifelong navigation|(habitat|matterport|ai2-thor|embodied|indoor|semantic|object|language|instruction)[^.]{0,80}navigat|navigat[^.]{0,80}(habitat|matterport|ai2-thor|embodied|indoor|semantic|language|instruction)', re.I)
# …or a field acronym in its canonical casing ("reverie" is an English word, REVERIE a benchmark).
OA_STRICT_CS_RE = re.compile('\\b(VLN|ObjectNav|PointNav|ImageNav|REVERIE|R2R|RxR|CVDN|VLN-CE|GOAT-Bench|SoundSpaces)\\b')
OK_CATS = ("cs.", "eess.", "stat.ML")  # arXiv categories that can host the field

# ───────────────────────── taxonomy (single source of truth) ─────────────────────────
# scope codes MUST include "core" and "out"; the site shows core by default.
SCOPES = [
    ('core', 'Core', "Embodied navigation is the paper's main subject (method, benchmark, dataset, simulator, survey or analysis)."),
    ('adjacent', 'Adjacent', 'Navigation is a substantial but secondary part: EQA, mobile manipulation / rearrangement, embodied instruction following, general embodied agents or VLAs with a navigation evaluation, 3D grounding built for navigation.'),
    ('aerial', 'Aerial (parked)', 'UAV / drone / aerial vision-language navigation or tracking. Kept, but out of the first survey.'),
    ('out', 'Out of scope', 'Not embodied navigation: web/GUI/app navigation, autonomous driving, pure geometric motion planning / SLAM / LiDAR obstacle avoidance / legged locomotion with no goal semantics, 2D crowd simulation, manipulation-only, text-only, non-robotics uses of the word navigation.'),
]
TASKS = [
    ('VLN', 'Instruction VLN', 'Follow fine-grained route instructions (R2R, RxR, R4R, VLN-CE, Touchdown-style).'),
    ('GoalVLN', 'Goal-oriented VLN', "High-level language goal / remote object grounding (REVERIE, SOON, 'find the X in the Y')."),
    ('DialogNav', 'Dialog / Interactive', 'Dialogue, question asking, ask-for-help or human-in-the-loop navigation (CVDN, HANNA, NDH).'),
    ('ObjectNav', 'ObjectNav', 'Object-goal navigation to a category, incl. open-vocabulary / zero-shot ObjectNav (HM3D, MP3D, OVON).'),
    ('InstanceNav', 'Instance Nav', 'Navigate to a specific object instance given an image or description (Instance-ImageNav, InstanceNav, text-goal).'),
    ('ImageNav', 'ImageNav', 'Image-goal navigation.'),
    ('PixelNav', 'PixelNav', 'Pixel-goal navigation (goal given as a pixel in the current view, PixNav).'),
    ('PointNav', 'PointNav', 'Point-goal / coordinate navigation.'),
    ('MultiGoal', 'Multi-goal / Lifelong', 'Sequential multi-object or multimodal lifelong navigation (MultiON, GOAT, GOAT-Bench).'),
    ('AudioNav', 'Audio-visual Nav', 'Audio-goal or audio-visual navigation (SoundSpaces).'),
    ('SocialNav', 'Social / Tracking', 'Human-aware navigation, human following, embodied visual tracking.'),
    ('Exploration', 'Exploration', 'Active exploration, coverage, map building for embodied agents.'),
    ('EQA', 'Embodied QA', 'Navigate to answer questions (EQA, OpenEQA, HM-EQA).'),
    ('MobileManip', 'Mobile Manipulation', 'Navigation as part of mobile manipulation / rearrangement / OVMM.'),
    ('GeneralNav', 'Generalist Nav', 'One model/agent across several navigation task families, or general-purpose navigation foundation model.'),
]
SETTINGS = [
    ('discrete', 'Discrete graph', 'Nav-graph / panoramic viewpoints (Matterport3D simulator).'),
    ('continuous', 'Continuous sim', 'Continuous control in a 3D simulator (Habitat, AI2-THOR, Isaac, iGibson).'),
    ('outdoor', 'Outdoor / street', 'Street-view or outdoor ground environments.'),
    ('real', 'Real robot', 'Deployed or evaluated on a physical robot.'),
]
# (code, label, line, definition) — order = the survey's storyline; colours follow this order.
PARADIGMS = [
    ('specialist', 'Task-specific learning', 'learned', 'Policy trained for the task from scratch or on frozen small encoders via IL/RL: seq2seq, cross-modal attention, RL, learned modular mapping (SemExp), graph planners. Dominant 2017-2021.'),
    ('pretrain', 'Large-scale pretraining', 'learned', 'Transformer vision-language pretraining for navigation (PREVALENT, VLN-BERT, HAMT, DUET), large-scale data generation/augmentation (ScaleVLN), self-supervised visual pretraining (OVRL), cross-embodiment navigation foundation models without an LLM (GNM, ViNT, NoMaD).'),
    ('fm-tuned', 'Foundation model fine-tuned', 'learned', 'An LLM / VLM / video-LLM / VLA backbone whose weights are fine-tuned (SFT, RL, adapters) into the navigation policy or planner (NavGPT-2, NaVid, Uni-NaVid, NaVILA, StreamVLN).'),
    ('zs-modular', 'Zero-shot modular', 'training-free', 'No navigation-specific weight updates for the decision model. Frozen CLIP / detectors / LLM / VLM composed in a FIXED human-designed pipeline: value maps, frontiers, scene graphs, LLM-as-planner called once per step (CoW, ESC, VLFM, NavGPT, MapGPT, InstructNav, SG-Nav).'),
    ('agentic', 'Agentic', 'training-free', 'The LLM/VLM owns the control flow: chooses tools / skills / modes, self-reflects and replans, manages its own memory, or several agents collaborate (DiscussNav, multi-agent, tool-using nav agents). Agent HARNESSES are agentic too — systems whose contribution is the orchestration layer around the model (control loop, context / memory management, tools, verification) or that write / evolve code, skills, prompts or policies (self-evolving, agent-as-optimizer); give those the Harness trait as well. Fine-tuned agents still count here if the agent loop is the contribution.'),
    ('none', 'No method', '-', 'Benchmark, dataset, simulator, survey or analysis with no new navigation method.'),
]
# Old paradigm codes → (current code, traits to add). Keeps labels written before a taxonomy
# change valid until they are re-classified (./atlas classify <id> --redo-paradigm <old codes>).
PARADIGM_ALIASES = {"harness": ("agentic", ["Harness"])}  # 2026-09-29: harness is a kind of agentic

CONTRIBS = [
    ('method', 'Method'),
    ('benchmark', 'Benchmark'),
    ('dataset', 'Dataset / data engine'),
    ('simulator', 'Simulator / platform'),
    ('survey', 'Survey'),
    ('analysis', 'Analysis / study'),
    ('system', 'Real-robot system'),
]
TRAITS = [
    ('LLM', 'uses a large language model'),
    ('VLM', 'uses a vision-language model / MLLM'),
    ('VLA', 'vision-language-action model'),
    ('WorldModel', 'world model / future prediction / imagination'),
    ('Map', 'explicit metric or semantic map / value map'),
    ('Graph', 'topological graph or scene graph'),
    ('Memory', 'long-term memory mechanism'),
    ('RL', 'reinforcement learning (incl. RL fine-tuning of FMs)'),
    ('IL', 'imitation / behaviour cloning'),
    ('DataGen', 'data generation / augmentation / synthesis'),
    ('Reasoning', 'chain-of-thought / explicit reasoning'),
    ('MultiAgent', 'multiple agents or experts'),
    ('Harness', 'agent harness / scaffold / self-evolving: the orchestration layer around the model is the contribution, or the agent writes / evolves code, skills, prompts or policies (always together with paradigm agentic)'),
    ('ToolUse', 'tool / API / skill calling'),
    ('Sim2Real', 'sim-to-real transfer'),
    ('3D', '3D representation (point cloud, NeRF, 3DGS, BEV)'),
    ('Efficiency', 'efficiency / latency / compression'),
    ('Robustness', 'robustness / safety / adversarial / failure recovery'),
    ('Continual', 'continual / lifelong / test-time adaptation'),
]
BENCHMARKS = {'Instruction VLN': ['R2R', 'R2R-CE', 'RxR', 'RxR-CE', 'R4R', 'R2R-Last', 'IR2R', 'IR2R-CE', 'LHPR-VLN', 'VLN-CE'],
 'Goal VLN': ['REVERIE', 'SOON', 'REVERIE-CE'],
 'Dialog': ['CVDN', 'HANNA', 'TEACh', 'DialFRED'],
 'Outdoor': ['Touchdown', 'Map2Seq', 'StreetNav'],
 'ObjectNav': ['HM3D-ObjNav',
               'MP3D-ObjNav',
               'Gibson-ObjNav',
               'RoboTHOR-ObjNav',
               'ProcTHOR-ObjNav',
               'HM3D-OVON',
               'HSSD-ObjNav'],
 'Instance/Image': ['HM3D-IIN', 'Gibson-ImageNav', 'HM3D-ImageNav', 'TextNav'],
 'PointNav': ['Gibson-PointNav', 'HM3D-PointNav', 'MP3D-PointNav'],
 'Multi-goal': ['GOAT-Bench', 'MultiON'],
 'Other': ['SoundSpaces', 'MP3D-EQA', 'OpenEQA', 'HM-EQA', 'HomeRobot-OVMM', 'Habitat3-SocialNav', 'ALFRED']}

# ───────────────────────── classifier prompt ─────────────────────────
# What each benchmark's STANDARD protocol provides — used by engine/comparable.py to decide which reported
# results are directly comparable (information listed here is not a deviation).
BENCH_PROTOCOLS = {
    "R2R / R4R / RxR / R2R-Last (discrete, Matterport3D simulator)": "panoramic RGB (GT depth available), agent pose / heading, "
        "and the navigable neighbouring viewpoints of the nav-graph are provided; one trajectory per episode; success = stop within 3 m. "
        "Beam search, pre-exploration of test scenes, or multiple attempts are deviations.",
    "REVERIE / SOON": "as R2R, plus the benchmark's object bounding boxes / proposals at each viewpoint (standard); RGS / RGSPL need "
        "the correct object among them. Pre-exploration or oracle stop are deviations.",
    "CVDN (NDH)": "the dialog history up to the current turn (incl. the oracle's answers, which are part of the data) is standard input; "
        "metric goal progress (GP). Using future dialog turns or the planner path at test time is a deviation.",
    "R2R-CE / RxR-CE / VLN-CE": "Habitat continuous environments: egocentric RGB-D (simulator depth is standard), low-level actions "
        "(FORWARD 0.25 m, TURN 15°, STOP) or a LEARNED waypoint predictor; GPS / compass pose may be used; step budget as defined; "
        "success within 3 m. Deviations: ground-truth / nav-graph (connectivity-graph) waypoints, teleporting between nodes, "
        "pre-built maps of the test scenes, a panoramic action space not available in continuous control.",
    "HM3D-ObjNav / MP3D-ObjNav / Gibson-ObjNav / HM3D-OVON (Habitat ObjectNav)": "egocentric RGB-D + GPS / compass are standard; "
        "500-step budget; success = STOP within 1 m (viewpoint-based) of a goal instance. Deviations: ground-truth semantic "
        "segmentation or semantic maps, known object locations, pre-built maps of test scenes, larger success radius, more steps.",
    "GOAT-Bench / MultiON / IVLN (lifelong, multi-goal)": "memory carried across the sub-goals of one episode / tour is the point of "
        "the benchmark (standard); deviations as for ObjectNav / VLN-CE.",
}

# Benchmarks whose name hides incompatible versions: build.py renames a result row to its variant.
# HM3D ObjectNav val v1 (HM3D-Sem v0.1, Habitat Challenge 2022: 2,000 episodes / 20 scenes) and v2 (v0.2, Challenge
# 2023: 1,000 episodes / 36 scenes) differ by 15-20 SR for the same agent, so they must never share a ranking.
_HM3D_V1 = re.compile(r"\bv1\b|v1\.0|v0\.1|2,?000 (val(idation)? )?(episodes|eps)|challenge[- ]?2022|2022 challenge|objectnav[- ]?v1|hm3d[- ]?v1", re.I)
_HM3D_V2 = re.compile(r"\bv2\b|v2\.0|v0\.2|1,?000 (val(idation)? )?(episodes|eps)|challenge[- ]?2023|2023 challenge|objectnav[- ]?v2|hm3d[- ]?v2", re.I)


BENCH_VARIANTS = {"HM3D-ObjNav": {
    "HM3D-ObjNav-v1": "HM3D ObjectNav val v1 = HM3D-Semantics v0.1, Habitat ObjectNav Challenge 2022: 2,000 episodes, 20 scenes, 6 goal categories",
    "HM3D-ObjNav-v2": "HM3D ObjectNav val v2 = HM3D-Semantics v0.2, Habitat ObjectNav Challenge 2023: 1,000 episodes, 36 scenes, 6 goal categories"}}


# Different benchmark names used for the same benchmark (applied before variants).
BENCH_DISPLAY = {"HM3D-ObjNav-v1": "HM3D ObjectNav v1", "HM3D-ObjNav-v2": "HM3D ObjectNav v2", "MP3D-ObjNav": "MP3D ObjectNav"}
BENCH_ALIASES = {"IVLN-CE": "IR2R-CE", "IVLN": "IR2R", "Iterative R2R-CE": "IR2R-CE", "Iterative R2R": "IR2R", "VLN-CE": "R2R-CE"}
# How questions on the Ask page name benchmarks (matched after punctuation and case are removed).
ASK_ALIASES = {"Instance-ImageNav": "HM3D-IIN", "Instance ImageNav": "HM3D-IIN", "instance image navigation": "HM3D-IIN",
               "OVON": "HM3D-OVON", "GOAT": "GOAT-Bench", "R2R in continuous environments": "R2R-CE"}
# Metric names that mean the benchmark's main metric on these benchmarks (e.g. subtask success = SR on GOAT-Bench).
METRIC_ALIASES = {"GOAT-Bench": {"s-SR": "SR", "Subtask SR": "SR"}, "IR2R-CE": {"s-SR": "SR"}, "IR2R": {"s-SR": "SR"}}
# Iterative VLN (IVLN, Krantz et al. 2022): tours of consecutive episodes in one scene — a different benchmark from R2R(-CE).
_ITERATIVE = re.compile(r"\bIR2R|\bIVLN|iterative (vln|vision|r2r)|\btours?\b", re.I)
# Evaluation subsets shared by several papers: results on them are comparable with each other (engine/variants.py tags them).
SUBSET_PROTOCOLS = {
    "R2R": {"72-scene (NavGPT)": "NavGPT's 72-scene, 216-instruction set (61 training + 11 val-unseen scenes), reused by MapGPT, DiscussNav, MC-GPT and others"},
    "R2R-CE": {"100-ep (Open-Nav)": "Open-Nav's 100 randomly sampled R2R-CE val-unseen episodes, reused by AgenticNav, Three-Step Nav, minimal-interface agents and others",
               "100-ep (Uni-LaViRA)": "Uni-LaViRA's stratified 100-episode R2R-CE val-unseen subset"},
    "GOAT-Bench": {"278-subtask (3D-Mem)": "3D-Mem's GOAT-Bench val-unseen protocol: the first episode of each of the 36 scenes, 278 subtasks"},
}


def bench_variant(row: dict, paper: dict) -> str | None:
    if row.get("bench") in ("R2R-CE", "R2R"):
        t = " ".join(str(row.get(k) or "") for k in ("eval_note", "split", "method", "extra"))
        return (row["bench"].replace("R2R", "IR2R", 1)) if _ITERATIVE.search(t) else None
    if row.get("bench") != "HM3D-ObjNav":
        return None
    for field in ("eval_note", "split", "ev", "extra", "method"):  # most specific field first
        t = str(row.get(field) or "")
        a, b = bool(_HM3D_V1.search(t)), bool(_HM3D_V2.search(t))
        if a != b:
            return "HM3D-ObjNav-v1" if a else "HM3D-ObjNav-v2"
    if str(paper.get("d") or "9999")[:7] < "2023-04":  # only v1 existed before the 2023 challenge
        return "HM3D-ObjNav-v1"
    return None  # version not stated: stays "HM3D-ObjNav" and is kept out of rankings


# Results tables group rows by paradigm. A "task-specific" method whose backbone is a pretrained navigator is shown
# with the pretraining family (the survey's era rule: the backbone decides the era).
_PRETRAINED_NAV = re.compile(r"\b(DUET|HAMT|ScaleVLN|VLN-?BERT|Recurrent VLN|PREVALENT|ETPNav|BEVBert|GridMM|Airbert|HOP\b|LAD|BSG|GR-DUET|ETP-R1|VLN-GOAT|LXMERT|ViLBERT|dual-scale graph)", re.I)
_LLM = re.compile(r"\b(LLM|LLaMA|Llama|Vicuna|Qwen|Flan-?T5|InstructBLIP|GPT|LLaVA|InternVL|Gemma|Mistral|Phi-\d|VILA|NaviLLM|MLLM|VLM)", re.I)


def table_paradigm(row: dict, paper: dict) -> str:
    """Group used in the results tables = the training regime behind this result (Fig. paradigms):
    training-free groups (zs-modular, agentic) hold only results obtained without navigation training."""
    pd = paper.get("pd", row.get("pd"))
    bb = str(row.get("backbone") or "")
    if pd == "agentic" and row.get("learned") is True and _LLM.search(bb):
        return "fm-tuned"  # a fine-tuned model in an agentic or modular loop is not training-free
    if pd == "specialist" and _PRETRAINED_NAV.search(bb):
        return "pretrain"
    if pd == "fm-tuned" and _PRETRAINED_NAV.search(bb) and not _LLM.search(bb):
        return "pretrain"  # e.g. a DUET follower trained on instructions from an LLM-based generator
    return pd


CLASSIFY_ROLE = 'You are a senior embodied-AI researcher curating a literature database for a survey on **Embodied Navigation** (vision-language navigation, object/image/point-goal navigation and related tasks). You label papers from their title and abstract only. Be precise and conservative: the database must be clean.'
CLASSIFY_RULES = """\
- `scope`: decide first. "Embodied navigation" means an agent that moves through a 3D environment (simulated or real) from egocentric perception towards a goal given as language, an object category, an image, a point, audio, dialogue, or a mix — plus benchmarks, datasets, simulators, surveys and analyses built for such tasks. General-purpose visual-navigation foundation models (GNM, ViNT, NoMaD) and real-robot semantic/language navigation (LM-Nav, VLMaps) are `core`.
  - A robotics paper doing classical point-to-point planning with LiDAR/geometry only, collision avoidance, legged locomotion, or crowd navigation among 2D agents is `out`.
  - Autonomous driving (cars on roads) is `out`. Web, GUI, app, document or text-world "navigation" is `out`.
  - UAV / drone / aerial anything → `aerial` (even if it is VLN).
  - Generalist embodied agents / VLAs / world models where navigation is one of several evaluated abilities, EQA, mobile manipulation, rearrangement, ALFRED-style household instruction following → `adjacent` (use `core` if navigation is clearly the main contribution).
- `tasks`: every navigation task family the paper actually addresses or evaluates. Empty list only for `out`.
- `settings`: what the abstract states or clearly implies (R2R ⇒ discrete, R2R-CE / Habitat ObjectNav ⇒ continuous, "real robot"/"real-world deployment" ⇒ real). Empty if unknowable.
- `paradigm`: the paper's OWN approach. Key tests: (1) are the decision model's weights trained/fine-tuned on navigation data? yes → specialist / pretrain / fm-tuned; no → zs-modular / agentic. (2) For training-free: is the control flow a fixed human-designed pipeline (zs-modular) or does the LLM/VLM decide which tools/modes/sub-agents to invoke, reflect and replan (agentic)? A single LLM/VLM prompted once per step to pick the next viewpoint (NavGPT, MapGPT) is zs-modular. Agent harnesses and self-evolving / code-writing agents are agentic (there is no separate harness paradigm) and additionally get the Harness trait. (3) A method that fine-tunes an LLM/VLM/VLA is fm-tuned even if it also has a map. (4) Pure dataset/benchmark/simulator/survey papers → none.
- `contrib`: all that apply.
- `traits`: only what the abstract supports.
- `benchmarks`: canonical names only (or other:<Name>). Map "R2R in continuous environments"/"VLN-CE R2R" → R2R-CE; "HM3D ObjectNav"/"Habitat ObjectNav challenge 2022/2023 (HM3D)" → HM3D-ObjNav; "Matterport3D ObjectNav" → MP3D-ObjNav. Do not guess benchmarks that are not mentioned.
- `name`: the method / benchmark short name as the paper calls it (e.g. "NaVid", "VLFM", "GOAT-Bench"); null if none.
- `tldr`: ONE sentence, ≤ 28 words, in English, saying what the paper does and how (not hype).
- `conf`: "high" or "low" — "low" when the scope decision is borderline or the abstract is missing/uninformative.
"""

# ───────────────────────── recall check ─────────────────────────
# Papers that MUST be in the library (verify ids against arXiv titles, never from memory).
LANDMARKS = [
    ('1711.07280', 'R2R / VLN'),
    ('1806.02724', 'Speaker-Follower'),
    ('1811.10092', 'RCM'),
    ('1904.04195', 'EnvDrop'),
    ('2002.10638', 'PREVALENT'),
    ('2011.13922', 'VLN-BERT (recurrent)'),
    ('2110.13309', 'HAMT'),
    ('2202.11742', 'DUET'),
    ('2307.15644', 'ScaleVLN'),
    ('2305.16986', 'NavGPT'),
    ('2401.07314', 'MapGPT'),
    ('2402.15852', 'NaVid'),
    ('2412.04453', 'NaVILA'),
    ('2412.06224', 'Uni-NaVid'),
    ('2507.05240', 'StreamVLN'),
    ('2004.02857', 'VLN-CE'),
    ('2304.03047', 'ETPNav'),
    ('2010.07954', 'RxR'),
    ('1904.10151', 'REVERIE'),
    ('2103.17138', 'SOON'),
    ('1907.04957', 'CVDN'),
    ('1811.12354', 'Touchdown'),
    ('2407.12366', 'NavGPT-2'),
    ('2309.11382', 'DiscussNav'),
    ('2212.04385', 'BEVBert'),
    ('2203.12667', 'VLN survey (Gu)'),
    ('1905.12255', 'R4R'),
    ('2407.05890', 'AO-Planner'),
    ('2409.18794', 'Open-Nav'),
    ('2007.00643', 'SemExp'),
    ('2006.13171', 'ObjectNav Revisited'),
    ('1904.01201', 'Habitat'),
    ('1911.00357', 'DD-PPO'),
    ('2203.10421', 'CoW'),
    ('2301.13166', 'ESC'),
    ('2312.03275', 'VLFM'),
    ('2304.05501', 'L3MVN'),
    ('2206.12403', 'ZSON'),
    ('2204.03514', 'Habitat-Web'),
    ('2303.07798', 'PIRLNav'),
    ('2409.14296', 'HM3D-OVON'),
    ('2410.08189', 'SG-Nav'),
    ('2503.10630', 'UniGoal'),
    ('2406.04882', 'InstructNav'),
    ('2109.08238', 'HM3D'),
    ('1609.05143', 'Target-driven visual nav'),
    ('2211.15876', 'Instance ImageNav'),
    ('2309.10309', 'PixNav'),
    ('2004.05155', 'Active Neural SLAM'),
    ('1807.06757', 'On Evaluation of Embodied Navigation Agents'),
    ('2311.06430', 'GOAT'),
    ('2404.06609', 'GOAT-Bench'),
    ('2012.03912', 'MultiON'),
    ('2210.03370', 'GNM'),
    ('2306.14846', 'ViNT'),
    ('2310.07896', 'NoMaD'),
    ('2207.04429', 'LM-Nav'),
    ('2210.05714', 'VLMaps'),
    ('1912.11474', 'SoundSpaces'),
    ('1711.11543', 'EQA'),
    ('2310.13724', 'Habitat 3.0'),
    ('2005.12256', 'Neural Topological SLAM'),
    ('2104.04112', 'Aux tasks ObjectNav'),
    ('2201.10029', 'PONI'),
    ('2111.09888', 'EmbCLIP'),
    ('2204.13226', 'OVRL'),
    ('2212.00922', 'ObjectNav real world'),
    ('2202.11271', 'ViKiNG'),
    ('2104.05859', 'RECON'),
    ('1611.03673', 'Learning to navigate (Mirowski)'),
    ('1702.03920', 'CMP'),
    ('1803.00653', 'SPTM'),
    ('1901.03035', 'Self-Monitoring'),
    ('1903.02547', 'Tactical Rewind'),
    ('2108.09105', 'Airbert'),
    ('2203.11591', 'HOP'),
    ('1912.01734', 'ALFRED'),
    ('2110.02207', 'Waypoint models'),
    ('2203.02764', 'Discrete-to-continuous VLN'),
    ('2312.02010', 'NaviLLM'),
    ('2308.07997', 'A2Nav'),
    ('2308.10141', 'March in Chat'),
    ('2403.07376', 'NavCoT'),
    ('2310.07889', 'LangNav'),
    ('2309.04077', 'SayNav'),
    ('2211.16649', 'CLIP-Nav'),
    ('2307.06082', 'VELMA'),
    ('2206.06994', 'ProcTHOR'),
    ('2004.06799', 'RoboTHOR'),
    ('1808.10654', 'Gibson Env'),
    ('2206.08312', 'SoundSpaces 2.0'),
    ('2012.11583', 'Semantic AudioNav'),
    ('2403.15941', 'Explore until Confident'),
    ('2308.05602', 'Recursive implicit maps'),
    ('2202.02440', 'Zero Experience Required'),
    ('2412.03572', 'Navigation World Models'),
    ('2407.07775', 'Mobility VLA'),
    ('2307.12907', 'GridMM'),
    ('2404.01943', 'Lookahead NeRF VLN'),
    ('2412.10439', 'CogNav'),
    ('2411.16425', 'TopV-Nav'),
    ('2402.10670', 'OpenFMNav'),
    ('2401.02695', 'VoroNav'),
    ('2310.10103', 'LFG'),
    ('2309.16650', 'ConceptGraphs'),
    ('2509.12129', 'NavFoM'),
    ('2509.22548', 'JanusVLN'),
    ('2106.14405', 'Habitat 2.0'),
]

# ───────────────────────── website (hub) ─────────────────────────
# Everything the hub needs that is field-specific. JSON-serialisable.
UI = {
    # Map page: row groups of the task × paradigm matrix
    "task_groups": [
        ["Language-driven", ["VLN", "GoalVLN", "DialogNav"]],
        ["Goal-driven", ["ObjectNav", "InstanceNav", "ImageNav", "PixelNav", "PointNav", "MultiGoal", "AudioNav"]],
        ["Beyond goal-reaching", ["SocialNav", "Exploration", "EQA", "MobileManip", "GeneralNav"]],
    ],
    # headings for the paradigm `line` groups in the Library filter
    "paradigm_lines": {"learned": "Learned (weights trained on nav data)", "training-free": "Training-free", "-": "No method"},
    "paradigm_note": "Learned line: is the decision model trained on navigation data? Training-free line: who owns the control flow — a fixed pipeline or the model itself?",
    "timeline_intro": "From task-specific RL/IL policies, through vision-language pretraining, to foundation-model navigators and training-free agents.",
    # example questions on the Ask page
    "ask_examples": [
        "How far behind fine-tuned methods are training-free VLN agents on R2R-CE?",
        "Which zero-shot methods lead HM3D ObjectNav, and what do they rely on?",
        "What memory designs do lifelong navigation agents use on GOAT-Bench?",
        "Which VLN methods were deployed on real robots, and how well did they work?",
    ],
    # quick filters under the search bar: [label, {facet: [codes], sort?: ...}]
    "presets": [
        ["Instruction VLN", {"tk": ["VLN"]}],
        ["R2R-CE", {"bm": ["R2R-CE"]}],
        ["Zero-shot ObjectNav", {"tk": ["ObjectNav"], "pd": ["zs-modular", "agentic"]}],
        ["Agentic (incl. harness)", {"pd": ["agentic"]}],
        ["Harness only", {"tr": ["Harness"]}],
        ["FM navigators", {"pd": ["fm-tuned"]}],
        ["Benchmarks & datasets", {"ct": ["benchmark", "dataset", "simulator"]}],
        ["Surveys", {"ct": ["survey"]}],
        ["Real robot", {"st": ["real"]}],
        ["Most cited", {"sort": "cited"}],
        ["Recently added", {"sort": "added"}],
    ],
    # Surveys page: each section is a live query (base filter ∪ section filter)
    "surveys": [
        {
            "id": "vln", "title": "Vision-and-language navigation: instruction, goal and dialog",
            "blurb": "Language-driven navigation only (instruction VLN, goal-oriented VLN, dialog). Organised along the paradigm axis, so the narrative reads as a history: seq2seq/RL → VL pretraining → foundation-model navigators → zero-shot pipelines → agents (incl. agent harnesses).",
            "base": {"tk": ["VLN", "GoalVLN", "DialogNav"], "sc": ["core"]},
            "sections": [
                ["Tasks, datasets & benchmarks", "R2R, RxR, REVERIE, SOON, CVDN, VLN-CE, long-horizon…", {"ct": ["benchmark", "dataset", "simulator"]}],
                ["Task-specific learning", "seq2seq, speaker-follower, RL/IL, graph planners", {"pd": ["specialist"]}],
                ["Vision-language pretraining & data scaling", "VLN-BERT, HAMT, DUET, ScaleVLN", {"pd": ["pretrain"]}],
                ["Foundation-model navigators", "fine-tuned LLM / VLM / video-LLM / VLA", {"pd": ["fm-tuned"]}],
                ["Zero-shot modular navigation", "frozen LLM/VLM in a fixed pipeline", {"pd": ["zs-modular"]}],
                ["Agentic navigation", "the model owns the control flow — tools, reflection, multi-agent, harnesses", {"pd": ["agentic"]}],
                ["Continuous & real-world VLN", "VLN-CE, sim-to-real, robots", {"st": ["continuous", "real"]}],
                ["Surveys & analyses", "prior surveys, diagnostics", {"ct": ["survey", "analysis"]}],
            ],
        },
        {
            "id": "embnav", "title": "Embodied Navigation in the Foundation-Model Era",
            "blurb": "All goal specifications — language, object, image, point, audio, multi-goal. Organised by task family, with the paradigm shift as the running thread inside each section.",
            "base": {"sc": ["core"]},
            "sections": [
                ["Instruction & goal-oriented VLN", "", {"tk": ["VLN", "GoalVLN", "DialogNav"]}],
                ["Object-goal navigation", "closed-set → open-vocabulary", {"tk": ["ObjectNav"]}],
                ["Instance, image & pixel goals", "", {"tk": ["InstanceNav", "ImageNav", "PixelNav"]}],
                ["Point-goal navigation & exploration", "", {"tk": ["PointNav", "Exploration"]}],
                ["Multi-goal & lifelong navigation", "MultiON, GOAT, GOAT-Bench", {"tk": ["MultiGoal"]}],
                ["Audio, social & question-driven navigation", "", {"tk": ["AudioNav", "SocialNav", "EQA"]}],
                ["Generalist navigation models", "one model across tasks", {"tk": ["GeneralNav"]}],
                ["Real-world deployment", "", {"st": ["real"]}],
                ["Benchmarks, simulators & datasets", "", {"ct": ["benchmark", "dataset", "simulator"]}],
            ],
        },
        {
            "id": "agentic", "title": "Training-free & Agentic Embodied Navigation",
            "blurb": "The newest line: no navigation training of the decision model. From zero-shot modular pipelines (value maps, frontiers, LLM planners) to agents that pick tools, reflect and collaborate, and harnesses that write their own code.",
            "base": {"sc": ["core"], "pd": ["zs-modular", "agentic"]},
            "sections": [
                ["Zero-shot modular pipelines", "", {"pd": ["zs-modular"]}],
                ["Agentic navigators", "incl. agent harnesses", {"pd": ["agentic"]}],
                ["Agent harnesses & self-evolving systems", "the orchestration layer is the contribution", {"pd": ["agentic"], "tr": ["Harness"]}],
                ["Spatial representations: maps & graphs", "", {"tr": ["Map", "Graph", "3D"]}],
                ["Memory", "", {"tr": ["Memory"]}],
                ["Reasoning & reflection", "", {"tr": ["Reasoning"]}],
                ["Multi-agent collaboration", "", {"tr": ["MultiAgent"]}],
                ["Tools & skills", "", {"tr": ["ToolUse"]}],
                ["World models & imagination", "", {"tr": ["WorldModel"]}],
            ],
        },
    ],
    # Surveys page roadmap: [number, title, description, done|now|next]
    "roadmap": [
        ["01", "Collect & classify", "Multi-query arXiv + OpenAlex harvest, rule prefilter, LLM scope/task/paradigm labels, venue + BibTeX resolution.", "done"],
        ["02", "Deep reading", "Per-paper agent reads the full text: motivation, method components, key results, limitations — written into each paper page.", "done"],
        ["03", "Benchmark tables", "Extract SR / SPL / NE / nDTW per split into leaderboards (R2R, R2R-CE, RxR-CE, REVERIE, HM3D ObjectNav, …).", "done"],
        ["04", "Citations", "Official BibTeX for every accepted paper; arXiv entries upgraded as papers get accepted.", "done"],
        ["05", "Write", "Draft a survey from a live outline: writer packs per section, LaTeX results tables and figures generated from the data (./atlas survey).", "done"],
        ["∞", "Keep up", "Scheduled incremental harvest → classify new papers → reading queue → rebuild.", "done"],
    ],
}
