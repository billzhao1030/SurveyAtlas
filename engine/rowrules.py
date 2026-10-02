"""Rules for reading leaderboard rows: which values are percentages, which rows are usable and which are comparable.

No atlas context is needed, so the hub process (engine/ask.py) and the survey builder share the same rules."""
from __future__ import annotations

import re

# What makes a result NOT comparable to standard single-run numbers (extra training data is NOT such a flag)
NONSTD_RE = re.compile(r"beam|pre-?explor|ensemble|test[- ]time aug|ground[- ]truth|\bGT\b|oracle|privileged|"
                       r"pre-?built map|prior map|known map|teleport|full observ|panoramic action space in continuous", re.I)


_NEGATED = re.compile(r"\b(no|without|not using|not|w/o|free of)\s+(the\s+)?(simulator\s+)?(ground[- ]truth|GT|oracle|privileged|pre-?explor\w*|beam)[\w -]{0,20}", re.I)


def nonstandard(r: dict) -> bool:
    if "audit_std" in r:  # checked against the paper's own table (engine/audit.py)
        return not r["audit_std"]
    if "cmp" in r:  # judged against the benchmark's protocol (engine/comparable.py)
        return not r["cmp"]
    priv = _NEGATED.sub("", (r.get("priv") or "").strip())
    extra = _NEGATED.sub("", r.get("extra") or "")
    return bool((priv and not re.match(r"none", priv, re.I) and NONSTD_RE.search(priv)) or NONSTD_RE.search(extra))


PERCENT = {"SR", "SPL", "OSR", "nDTW", "SDTW", "CLS", "RGS", "RGSPL", "SoftSPL"}


def fmt_metric(c: str, v) -> str:
    """Table cell: percent metrics reported as fractions (0.401) are shown as percentages (40.1)."""
    if v is None:
        return "--"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return tex_escape(v)
    if c in PERCENT and 0 < f <= 1.0:
        f = f * 100
    if c in ("NE", "TL", "DTG"):
        return f"{f:.2f}"
    if c in PERCENT or c in ("t-nDTW", "e-SR", "GP") or abs(f) >= 1:
        return f"{f:.1f}"
    return f"{f:g}"


def model_case(b: str) -> str:
    """One spelling per model family in table cells: gpt-4o -> GPT-4o, GPT-6-Astra -> GPT-6 Astra, fable-5 -> Fable 5."""
    b = re.sub(r"\bgpt-", "GPT-", b)
    b = re.sub(r"\b(GPT-\d+(?:\.\d+)?)-(?=[A-Z][a-z])", r"\1 ", b)
    return re.sub(r"\b(fable|opus|sonnet|haiku)-(\d+(?:\.\d+)?)\b", lambda m: f"{m.group(1).capitalize()} {m.group(2)}", b)


def short_backbone(b: str, n: int = 30) -> str:
    """Backbone cell: drop parentheticals, keep up to two components ("+"-, " / "- or comma-separated), never cut a word."""
    if re.search(r"not stated|not reported|unknown|unspecified", b or "", re.I):
        return ""
    b = re.sub(r"\([^)]*\)|\[[^]]*\]", "", b or "")
    full = re.sub(r"\s+", " ", b).strip()
    b = model_case(b)
    parts = [x.strip(" ,;") for x in re.split(r"\s+\+\s+|\s+/\s+|,|;|\s+with\s+|\s+and\s+", b) if x.strip(" ,;")]
    out = ""
    for x in parts[:2]:
        cand = (out + " + " + x) if out else x
        if len(cand) > n and out:
            break
        out = cand
    if len(out) > n:  # one long component: prefer ending before a qualifying clause ("DUET pretrained on SID-46M" -> "DUET")
        clause = r"(?:pretrained|trained|fine-tuned|initiali[sz]ed|replacing|with|on|in|for|from|using|via)"
        cands = [out[:m.start()].strip(" ,-") for m in re.finditer(r"\s+" + clause + r"\b", out)]
        cands = [c for c in cands if 3 <= len(c) <= n and not re.search(r"\b" + clause + r"$", c)]
        if cands:
            return max(cands, key=len)
        words = out.split()  # no clause to drop: drop leading modifiers, the head noun comes last
        while len(" ".join(words)) > n and len(words) > 2:
            words = words[1:]
        if len(" ".join(words)) <= n:
            return " ".join(words)
    if len(out) > n:  # otherwise cut at a word boundary
        words, acc = out.split(), ""
        for w in words:
            if len(acc) + len(w) + 1 > n and acc:
                break
            acc = (acc + " " + w).strip()
        out = acc
    out = re.sub(r"(\s+(on|of|for|with|and|the|from|in|to|by|as|via|a|an|\+))+$", "", out.strip(), flags=re.I)
    out = re.sub(r"\s+", " ", out)
    if len(parts) == 1 and len(out) < len(full.strip(" ,;")) - 2:
        out += "…"  # a single long component was cut
    return out


def tex_name(n: str) -> str:
    """Method names: drop inline math / markup that papers put in their names, keep the text."""
    n = re.sub(r"\$\^?\{?(\w+)\}?\$", r"\1", n)          # DM$^3$-Nav -> DM3-Nav
    n = n.replace("↻", "-").replace("$", "")
    n = "".join(ch for ch in n if ord(ch) < 0x2190 or ch.isalnum())
    return tex_escape(n)


def short_name(r: dict, p: dict) -> str:
    """A method's short name; names shared by several papers in the atlas get the first author (OmniNav (Xue))."""
    if not _NAME_COUNT:
        for q in json.loads((ctx.PUBLIC / "papers.json").read_text()):
            if q.get("n"):
                _NAME_COUNT[q["n"].lower()] = _NAME_COUNT.get(q["n"].lower(), 0) + 1
    n = r.get("n") or p.get("n")
    from engine import taxonomy
    if n and n in {b for names in taxonomy.BENCHMARKS.values() for b in names} | set(getattr(ctx.D, "BENCH_ALIASES", {})):
        m = re.sub(r"\s*\([^)]*\)", "", r.get("method") or "").strip()  # a benchmark paper's own baseline: show the method
        m = re.split(r"\s+[\u2014\u2013-]\s+|;\s*|:\s+", m)[0].strip()  # "Cross-Modal — VLN-CE path ..." -> "Cross-Modal"
        return m if m and len(m) <= 24 else f"{n} baseline"  # a long baseline description reads worse than its benchmark name
    if n:
        au = (p.get("a") or [""])[0].split()
        return f"{n} ({au[-1]})" if _NAME_COUNT.get(n.lower(), 0) > 1 and au else n
    t = r.get("t") or p.get("t") or ""
    head = t.split(":")[0].strip()
    if ":" in t and len(head) <= 26:
        return head
    au = (p.get("a") or [""])[0].split()
    return f"{au[-1]} et al." if au else _clip(t, 26)


def cmd_tables(a) -> None:
    """sections/IV_tables.tex: one table per main benchmark. Upper block: full split, standard setting,
    ranked by the key metric (the comparable ranking). Lower block: subset (†) or non-standard (‡)
    results, listed for reference. One row per paper — its best row in the most comparable class."""
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    papers = {p["id"]: p for p in json.loads((ctx.PUBLIC / "papers.json").read_text())}
    out = ["% AUTO-GENERATED by ./atlas survey <atlas> <sid> tables — do not edit by hand; re-run after more papers are read.",
           "% upper block: full split + standard setting (comparable); lower block: † subset of the split, ‡ non-standard setting.", ""]
    made = []
    s = survey(a.sid)
    for bench, split, key, cols in s.get("tables", []):
        per: dict[str, tuple] = {}
        moved = {r["id"] for r in board if r["bench"] == bench and r["split"] == split and r.get("audit_std") is False}
        for r in board:
            if r["bench"] != bench or r["split"] != split or key not in r["m"] or r.get("audit_ok") is False:
                continue
            if not (r.get("ok") or r.get("audit_ok")):  # only numbers found in the paper (machine check or audit)
                continue
            try:
                v = float(r["m"][key])
            except (TypeError, ValueError):
                continue
            # 0 = comparable; a paper whose main row the audit moved keeps its other (unaudited) rows out of the ranking too
            cls = (r["eval_set"] != "full") * 2 + (nonstandard(r) or (r["id"] in moved and "audit_std" not in r))
            cur = per.get(r["id"])
            if cur is None or (cls, -v) < (cur[0], -cur[1]):
                per[r["id"]] = (cls, v, r)
        comp = sorted((x for x in per.values() if x[0] == 0), key=lambda x: -x[1])[: a.top]
        other = sorted((x for x in per.values() if x[0] > 0), key=lambda x: -x[1])[: max(8, a.top // 3)]
        if len(comp) < 3:
            continue
        lab = "tab:" + re.sub(r"[^a-z0-9]+", "-", f"{bench}-{split}".lower())
        vnote = next((f" {tex_escape(d)}; results that do not state the version are excluded." for vs in getattr(ctx.D, "BENCH_VARIANTS", {}).values()
                      for name, d in vs.items() if name == bench), "")
        made.append((bench, split, lab, len(comp), len(other)))
        ncol = 5 + len(cols)

        def row(r: dict) -> str:
            p = papers.get(r["id"], {})
            flag = (r"$^\dagger$" if r["eval_set"] == "subset" or r.get("audit_std") is False and "subset" in (r.get("audit_note") or "").lower() else "") \
                + (r"$^\ddagger$" if nonstandard(r) else "") + (r"$^\star$" if "audit_ok" in r else "")
            vals = " & ".join(fmt_metric(c, r["m"].get(c)) for c in cols)
            zs = r"\checkmark" if r.get("zs") else ""
            return (f"{tex_name(short_name(r, p))}~\\cite{{{p.get('key', '')}}}{flag} & {r['y']} & {PD_SHORT.get(r['pd'], r['pd'])} & "
                    f"{tex_name(_clip(r.get('backbone') or '', 44))} & {zs} & {vals} \\\\")
        out += [r"\begin{table*}[t]", r"\centering\footnotesize", r"\setlength{\tabcolsep}{3.5pt}",
                rf"\caption{{{tex_escape(bench)} ({tex_escape(split)}): results as reported by each paper, one row per paper.{vnote} "
                rf"Upper block: full split and standard setting, ranked by {key}. Lower block: evaluated on a subset of the split ($^\dagger$) "
                r"or in a non-standard setting ($^\ddagger$: beam search, pre-exploration, ground-truth or oracle information, ensembles) — not directly comparable. "
                r"Paradigm: Spec.\ task-specific, Pretr.\ large-scale pretraining, FM-FT fine-tuned foundation model, ZS-Mod.\ zero-shot modular, "
                r"Agentic: the foundation model owns the control flow, ---: no new method (benchmark or analysis paper). "
                r"ZS: no training on this benchmark. $^\star$: checked against the paper's own table.}", rf"\label{{{lab}}}",
                r"\resizebox{\textwidth}{!}{%",
                r"\begin{tabular}{@{}l l l >{\raggedright\arraybackslash}p{4.2cm} c " + "r" * len(cols) + r"@{}}", r"\toprule",
                "Method & Year & Paradigm & Decision model / backbone & ZS & " + " & ".join(cols) + r" \\", r"\midrule"]
        out += [row(x[2]) for x in comp]
        if other:
            out += [r"\midrule", rf"\multicolumn{{{ncol}}}{{@{{}}l}}{{\emph{{Subset or non-standard evaluation (not directly comparable)}}}} \\"]
            out += [row(x[2]) for x in other]
        out += [r"\bottomrule", r"\end{tabular}}", r"\end{table*}", ""]
    d = sdir(a.sid) / "sections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "IV_tables.tex").write_text("\n".join(out) + "\n")
    for m in made:
        print(f"table {m[2]}: {m[3]} comparable + {m[4]} other rows ({m[0]} {m[1]})")


# ───────────────────────── results tables v2: grouped main-text tables + extended appendix ─────────────────────────
LOWER_BETTER = {"NE", "TL", "DTG", "Collisions"}
GROUP_NAME = {"specialist": "Task-specific", "pretrain": "Pretraining", "fm-tuned": "Fine-tuned FM",
              "zs-modular": "Zero-shot", "agentic": "Agentic", "none": "Benchmark and analysis"}


GROUP_SHORT = {"specialist": "Spec.", "pretrain": "Pretr.", "fm-tuned": "FM", "zs-modular": "ZS", "agentic": "Agent.", "none": "Base."}


ISO4 = {"international": "Int.", "journal": "J.", "transactions": "Trans.", "conference": "Conf.", "neural": "Neural",
        "networks": "Netw.", "recognition": "Recognit.", "pattern": "Pattern", "information": "Inf.", "intelligence": "Intell.",
        "visual": "Vis.", "vision": "Vis.", "computer": "Comput.", "computers": "Comput.", "computing": "Comput.",
        "applied": "Appl.", "sciences": "Sci.", "science": "Sci.", "systems": "Syst.", "engineering": "Eng.",
        "robotics": "Robot.", "automation": "Autom.", "letters": "Lett.", "processing": "Process.", "applications": "Appl.",
        "artificial": "Artif.", "knowledge-based": "Knowl.-Based", "knowledge": "Knowl.", "fusion": "Fusion", "machine": "Mach.",
        "learning": "Learn.", "research": "Res.", "technology": "Technol.", "electronics": "Electron.", "advanced": "Adv.",
        "cognitive": "Cogn.", "sensors": "Sensors", "access": "Access", "ieee": "IEEE", "acm": "ACM", "expert": "Expert",
        "neurocomputing": "Neurocomputing", "image": "Image", "video": "Video", "multimedia": "Multimedia", "remote": "Remote",
        "sensing": "Sens.", "intelligent": "Intell.", "vehicles": "Veh.", "mechatronics": "Mechatron.", "asme": "ASME",
        "frontiers": "Front.", "physics": "Phys.", "communications": "Commun.", "nature": "Nature", "scientific": "Sci.",
        "reports": "Rep.", "electronic": "Electron.", "imaging": "Imaging", "graphics": "Graph."}


def venue_short(p: dict) -> str:
    v, vn, y = p.get("v") or "", p.get("vn") or "", p.get("y")
    m = re.search(r"(19|20)(\d\d)", v)
    yy = m.group(2) if m else (str(y)[2:] if y else "")
    if not vn:
        return f"arXiv'{yy}"
    vn = re.sub(r"\s*\([^)]*\)", "", vn)
    if p.get("vk") == "other" and len(vn) > 8:  # journals and long-tail venues: ISO-4 style abbreviation
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]*", vn) if w.lower() not in ("of", "on", "and", "the", "for", "in", "&")]
        core = [w for w in words if w.lower() not in ("international", "conference", "proceedings", "annual", "journal", "symposium")]
        pre = "IEEE " if words and words[0] == "IEEE" and core and core[0] != "IEEE" else ""
        pick = core if len(core) >= 2 else words
        ab = pre + " ".join(ISO4.get(w.lower(), w if len(w) <= 6 else w[:5] + ".") for w in pick[:4])
        return f"{ab}'{yy}"
    acr = "".join(w[0] for w in re.findall(r"[A-Za-z]+", vn) if w[0].isupper() and w.lower() not in ("of", "on", "and", "the"))
    if acr.startswith("IC") and len(acr) >= 5:  # "International Conference on Information and Knowledge Management" -> CIKM
        acr = acr[1:]
    name = vn if len(vn) <= 8 else acr[:6] if len(acr) >= 3 else vn.split()[0]
    if "workshop" in v.lower():
        name += "W"
    return f"{name}'{yy}"


_ABLATION = re.compile(r"w/o|without|\bno\b|ablation|\bonly\b|variant|†|tokens per|\bbaseline\b|, (?!and\b)", re.I)


_ABLATION_STRICT = re.compile(r"w/o|without|ablation|\bonly\b|\bvariant\b", re.I)


def is_ablation(r: dict) -> bool:
    """A paper's variant / ablation row rather than its main method (ranked after the main row of the same paper)."""
    return bool(_ABLATION.search(r.get("method") or ""))


def _pct(c: str, v) -> float | None:
    """A metric value on a 0-100 scale (fractions like 0.53 are scaled), None if not numeric."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x * 100 if c in PERCENT and 0 < x <= 1 else x


def consistent(r: dict) -> bool:
    """Reject rows whose metrics contradict their definitions (SPL <= SR, OSR >= SR): an extraction or paper error."""
    m = r.get("m") or {}
    sr, spl, osr = _pct("SR", m.get("SR")), _pct("SPL", m.get("SPL")), _pct("OSR", m.get("OSR"))
    if sr is not None and spl is not None and spl > sr + 0.05:
        return False
    if sr is not None and osr is not None and osr < sr - 0.05:
        return False
    return True


def usable(r: dict) -> bool:
    return (bool(r.get("ok")) or bool(r.get("audit_ok"))) and r.get("audit_ok") is not False and consistent(r)


def comparable(r: dict, moved: set) -> bool:
    return r["eval_set"] == "full" and not nonstandard(r) and not (r["id"] in moved and "audit_std" not in r)
