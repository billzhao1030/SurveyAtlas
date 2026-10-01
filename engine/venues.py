"""Venue detection from free text (arXiv comment / journal_ref / OpenAlex source names).

parse_venue("8 pages, accepted to CVPR 2024 (Highlight)") -> {"venue": "CVPR", "year": 2024, "kind": "main"}
Returns None when no top venue is named or the text only says it was submitted.
"""
from __future__ import annotations

import re

# canonical → (case-sensitive acronym patterns, case-insensitive long-name patterns)
VENUES: dict[str, tuple[list[str], list[str]]] = {
    "CVPR": ([r"CVPR"], [r"computer vision and pattern recognition"]),
    "ICCV": ([r"ICCV"], [r"international conference on computer vision(?! and)"]),
    "ECCV": ([r"ECCV"], [r"european conference on computer vision"]),
    "NeurIPS": ([r"NeurIPS", r"NIPS"], [r"neural information processing systems"]),
    "ICLR": ([r"ICLR"], [r"international conference on learning representations"]),
    "ICML": ([r"ICML"], [r"international conference on machine learning"]),
    "AAAI": ([r"AAAI"], [r"aaai conference"]),
    "IJCAI": ([r"IJCAI"], [r"international joint conference on artificial intelligence"]),
    "ACL": ([r"ACL"], [r"annual meeting of the association for computational linguistics"]),
    "EMNLP": ([r"EMNLP"], [r"empirical methods in natural language processing"]),
    "NAACL": ([r"NAACL"], [r"north american chapter of the association for computational linguistics"]),
    "CoRL": ([r"CoRL"], [r"conference on robot learning"]),
    "RSS": ([r"RSS"], [r"robotics:? science and systems"]),
    "ICRA": ([r"ICRA"], [r"international conference on robotics and automation"]),
    "IROS": ([r"IROS"], [r"intelligent robots and systems"]),
    "RA-L": ([r"RA-?L"], [r"robotics and automation letters"]),
    "T-RO": ([r"T-?RO"], [r"transactions on robotics"]),
    "TPAMI": ([r"T-?PAMI"], [r"pattern analysis and machine intelligence"]),
    "IJCV": ([r"IJCV"], [r"international journal of computer vision"]),
    "ACM MM": ([r"ACM ?MM", r"ACM Multimedia"], [r"acm international conference on multimedia"]),
    "WACV": ([r"WACV"], [r"winter conference on applications of computer vision"]),
    "BMVC": ([r"BMVC"], [r"british machine vision conference"]),
    "TMLR": ([r"TMLR"], [r"transactions on machine learning research"]),
    "COLING": ([r"COLING"], []),
    "EACL": ([r"EACL"], []),
    "3DV": ([r"3DV"], [r"international conference on 3d vision"]),
    "ICASSP": ([r"ICASSP"], []),
    "TNNLS": ([r"TNNLS"], [r"transactions on neural networks and learning systems"]),
    "TCSVT": ([r"TCSVT"], [r"transactions on circuits and systems for video technology"]),
    "TIP": ([r"TIP"], [r"transactions on image processing"]),
    "TMM": ([r"TMM"], [r"transactions on multimedia"]),
    "Science Robotics": ([], [r"science robotics"]),
    "Nature MI": ([], [r"nature machine intelligence"]),
}

NEG = re.compile(r"submitted|under review|in submission|under submission|to be submitted|preprint of .* submission", re.I)
# "Extension of our CVPR 2025 paper" names an earlier venue (an "extended version of" one keeps it)
PRIOR = re.compile(r"\b(?:extension|journal version|follow-up|successor) (?:of|to)\b[^,;.]*", re.I)
YEAR_RE = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)|['’`\-](\d{2})(?!\d)")

_COMPILED = []
for canon, (acrs, longs) in VENUES.items():
    pats = [re.compile(rf"(?<![A-Za-z]){a}(W)?(?![A-Za-z])") for a in acrs]
    pats += [re.compile(l, re.I) for l in longs]
    _COMPILED.append((canon, pats))


def _year_near(text: str, start: int, end: int) -> int | None:
    after = text[end : end + 18]
    m = YEAR_RE.search(after)
    if not m:
        # take the LAST year before the name ("2023 IEEE Int. Conf. on Robotics ...")
        ms = list(YEAR_RE.finditer(text[max(0, start - 60) : start]))
        m = ms[-1] if ms else None
    if not m:
        return None
    y = m.group(1) or m.group(2)
    y = int(y) if len(y) == 4 else 2000 + int(y)
    return y if 2010 <= y <= 2030 else None


def parse_venue(text: str | None) -> dict | None:
    if not text:
        return None
    best = None
    for sentence in re.split(r"(?<=[.;])\s+|\n", text):
        sentence = PRIOR.sub("", sentence)
        if NEG.search(sentence) and not re.search(r"accepted|published|appear", sentence, re.I):
            continue
        for canon, pats in _COMPILED:
            for p in pats:
                m = p.search(sentence)
                if not m:
                    continue
                kind = "main"
                if (m.groups() and m.group(1)) or re.search(r"workshop", sentence, re.I):
                    kind = "workshop"
                elif re.search(r"findings", sentence, re.I) and canon in ("ACL", "EMNLP", "NAACL", "EACL"):
                    kind = "findings"
                cand = {"venue": canon, "year": _year_near(sentence, m.start(), m.end()), "kind": kind}
                # prefer main-track matches and ones with a year
                score = (kind == "main") * 2 + (cand["year"] is not None)
                if best is None or score > best[0]:
                    best = (score, cand)
                break
    return best[1] if best else None


def label(v: dict | None) -> str | None:
    if not v:
        return None
    s = v["venue"] + (f" {v['year']}" if v.get("year") else "")
    if v.get("kind") == "workshop":
        s += " Workshop"
    elif v.get("kind") == "findings":
        s += " Findings"
    return s


if __name__ == "__main__":
    tests = [
        "8 pages, accepted to CVPR 2024 (Highlight)", "NeurIPS'24", "Accepted to IROS2026", "Accepted in CVPRW 2025.",
        "Accepted to the 2023 IEEE International Conference on Robotics and Automation (ICRA 2023)",
        "Submitted to ICRA 2025", "Workshop on Space in Vision, Language, and Embodied AI at NeurIPS 2025",
        "Accepted by EMNLP'26 Main Conference", "Findings of ACL 2024", "Accepted at NeurIPS, 2022",
        "2015 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS 2015), pp. 2943-2950",
        "IEEE Robotics and Automation Letters, 2024", "Proceedings of the AAAI Conference on Artificial Intelligence",
    ]
    for t in tests:
        print(f"{label(parse_venue(t))!s:22s} <- {t}")
