"""Which atlas is this process working on — paths + the atlas's domain module.

Every engine step runs as `ATLAS=<id> python3 -m engine.<step>` from the repo
root (the `atlas` CLI does this for you). The domain module is
`atlases/<id>/atlas.py`; it holds everything field-specific (queries,
taxonomy, classifier rules, landmarks, UI config). Nothing under engine/ may
hard-code a field.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ATLASES = REPO / "atlases"

ATLAS_ID = os.environ.get("ATLAS", "").strip()
if not ATLAS_ID:
    sys.exit("set ATLAS=<id> (or use ./atlas <cmd> <id>)")
ADIR = ATLASES / ATLAS_ID
if not (ADIR / "atlas.py").exists():
    sys.exit(f"no atlas '{ATLAS_ID}' (expected {ADIR}/atlas.py)")

_spec = importlib.util.spec_from_file_location(f"atlas_domain_{ATLAS_ID}", ADIR / "atlas.py")
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

# ── tracked, hand-edited ──
SEEDS = ADIR / "seeds.txt"            # extra arXiv ids to fetch by id
OVERRIDES = ADIR / "overrides.json"   # {uid: {scope?, paradigm?, tasks?, venue?, venue_year?}}

# ── runtime data (gitignored; `atlas snapshot` packs it) ──
DATA = ADIR / "data"
RAW_ARXIV = DATA / "raw" / "arxiv.jsonl"
RAW_OA = DATA / "raw" / "openalex.jsonl"
CANDS = DATA / "candidates.jsonl"
LABELS = DATA / "labels" / "labels.jsonl"
META_DIR = DATA / "meta"
READING = DATA / "reading"            # stage 2: {uid}.json
MARKS = DATA / "marks.json"           # stars / status / notes typed on the site
LOGS = DATA / "logs"

# ── built output served by the hub ──
PUBLIC = ADIR / "public"


def _local() -> dict:
    try:
        return json.loads((REPO / "local.json").read_text())
    except (OSError, ValueError):
        return {}


# contact address for polite API pools (OpenAlex / CrossRef / S2); lives in the
# gitignored local.json so it never lands in the repo
CONTACT = os.environ.get("ATLAS_CONTACT") or _local().get("contact_email", "")
UA = {"User-Agent": "LiteratureAtlas/1.0 (academic survey tool" + (f"; mailto:{CONTACT}" if CONTACT else "") + ")"}


def surveys() -> list[dict]:
    """Survey projects: SURVEYS in atlas.py plus one SURVEY per atlases/<id>/surveys/<sid>/survey.py.

    Keeping a survey's outline in its own workspace lets the atlas be shared while a manuscript stays private.
    """
    out = list(getattr(D, "SURVEYS", []))
    seen = {s["id"] for s in out}
    for f in sorted((ADIR / "surveys").glob("*/survey.py")):
        spec = importlib.util.spec_from_file_location(f"survey_{ATLAS_ID}_{f.parent.name}", f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        sv = getattr(m, "SURVEY", None)
        if isinstance(sv, dict) and sv.get("id") and sv["id"] not in seen:
            out.append(sv)
            seen.add(sv["id"])
    return out


def meta(key: str, default=None):
    return getattr(D, "META", {}).get(key, default)


def require_ready() -> None:
    """Harvest / classify refuse to spend API calls on an unadapted template."""
    if not getattr(D, "DOMAIN_READY", False):
        sys.exit(f"atlas '{ATLAS_ID}': DOMAIN_READY is False — finish adapting atlases/{ATLAS_ID}/atlas.py "
                 f"(queries, taxonomy, classifier rules, landmarks), then set DOMAIN_READY = True.")
