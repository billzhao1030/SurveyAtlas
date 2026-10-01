"""Taxonomy helpers — the codes themselves live in atlases/<id>/atlas.py.

Used three ways: (1) rendered into the classifier prompt, (2) exported to
public/taxonomy.json for the site's facets / legends, (3) validation of
classifier output. Change a code in the domain file and all three follow.

Generic axes (every atlas has them, names are fixed so engine + hub stay generic):
  scope (single; must contain "core" and "out") · tasks (multi) · settings (multi)
  paradigm (single; order = storyline, `line` groups them) · contrib (multi)
  traits (multi) · benchmarks (canonical names, "other:<Name>" allowed)
"""
from __future__ import annotations

from engine.ctx import D

SCOPES = D.SCOPES
TASKS = D.TASKS
SETTINGS = D.SETTINGS
PARADIGMS = D.PARADIGMS
CONTRIBS = D.CONTRIBS
TRAITS = D.TRAITS
BENCHMARKS = D.BENCHMARKS


def as_json() -> dict:
    return {
        "scopes": [dict(code=c, label=l, desc=d) for c, l, d in SCOPES],
        "tasks": [dict(code=c, label=l, desc=d) for c, l, d in TASKS],
        "settings": [dict(code=c, label=l, desc=d) for c, l, d in SETTINGS],
        "paradigms": [dict(code=c, label=l, line=g, desc=d) for c, l, g, d in PARADIGMS],
        "contribs": [dict(code=c, label=l) for c, l in CONTRIBS],
        "traits": [dict(code=c, desc=d) for c, d in TRAITS],
        "benchmarks": BENCHMARKS,
    }


def prompt_block() -> str:
    """Taxonomy rendered for the classifier prompt."""
    L = ["## scope (exactly one)"]
    L += [f"- {c}: {d}" for c, _, d in SCOPES]
    L.append("\n## tasks (zero or more; empty only if scope is out)")
    L += [f"- {c}: {d}" for c, _, d in TASKS]
    L.append("\n## settings (zero or more)")
    L += [f"- {c}: {d}" for c, _, d in SETTINGS]
    L.append("\n## paradigm (exactly one — the paper's OWN method, not its baselines)")
    L += [f"- {c}: {d}" for c, _, _, d in PARADIGMS]
    L.append("\n## contrib (one or more)")
    L.append(", ".join(c for c, _ in CONTRIBS))
    L.append("\n## traits (zero or more)")
    L += [f"- {c}: {d}" for c, d in TRAITS]
    L.append("\n## benchmarks (canonical names; use other:<Name> for anything not listed; only list benchmarks the paper evaluates on or introduces)")
    for g, names in BENCHMARKS.items():
        L.append(f"- {g}: {', '.join(names)}")
    return "\n".join(L)


CODES = {
    "scope": {c for c, *_ in SCOPES},
    "tasks": {c for c, *_ in TASKS},
    "settings": {c for c, *_ in SETTINGS},
    "paradigm": {c for c, *_ in PARADIGMS},
    "contrib": {c for c, *_ in CONTRIBS},
    "traits": {c for c, *_ in TRAITS},
}
