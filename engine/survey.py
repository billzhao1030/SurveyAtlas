"""Survey workspace: outline-as-data → paper assignment → writer packs → LaTeX / PDF.

  ./atlas survey <atlas> <sid> init          create atlases/<atlas>/surveys/<sid>/ (main.tex + sections/)
  ./atlas survey <atlas> <sid> assign        Claude assigns every in-scope paper to one outline leaf
                                             (+ problem threads + role); uses deep-reading notes when present
  ./atlas survey <atlas> <sid> packs         per-chapter material packs for the writers (packs/*.md)
  ./atlas survey <atlas> <sid> bib           refs.bib = BibTeX of every key cited in sections/*.tex
  ./atlas survey <atlas> <sid> pdf           bib + latexmk → main.pdf
  ./atlas survey <atlas> <sid> status        coverage / reading / writing progress

The outline lives in the atlas domain file (SURVEYS) or in surveys/<sid>/survey.py (SURVEY). Assignments: data/surveys/<sid>/assign.jsonl.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from engine import ctx

LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def survey(sid: str) -> dict:
    for s in ctx.surveys():
        if s["id"] == sid:
            return s
    sys.exit(f"no survey '{sid}' in atlases/{ctx.ATLAS_ID}/atlas.py (SURVEYS) or surveys/{sid}/survey.py (SURVEY)")


def leaves(nodes: list[dict], path=()) -> list[tuple[dict, tuple]]:
    out = []
    for n in nodes:
        p = path + (n,)
        if "def" in n:
            out.append((n, p))
        out += leaves(n.get("children", []), p)
    return out


def in_scope(p: dict, spec: dict) -> bool:
    get = {"sc": lambda p: [p["sc"]], "tk": lambda p: p["tk"], "pd": lambda p: [p["pd"]], "st": lambda p: p["st"],
           "ct": lambda p: p["ct"], "tr": lambda p: p["tr"], "bm": lambda p: p.get("bm", [])}
    return all(any(v in vals for v in get[k](p)) for k, vals in spec.items() if vals)


def sdir(sid: str) -> Path:
    return ctx.ADIR / "surveys" / sid


def adir(sid: str) -> Path:
    return ctx.DATA / "surveys" / sid


def reading(uid: str) -> dict | None:
    f = ctx.READING / f"{uid.replace('/', '_').replace(':', '_')}.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def load_assign(sid: str) -> dict[str, dict]:
    f = adir(sid) / "assign.jsonl"
    out = {}
    if f.exists():
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r
    return out


def scope_papers(s: dict) -> list[dict]:
    """Papers matching the survey's scope, plus its context papers (see context_ids)."""
    papers = json.loads((ctx.PUBLIC / "papers.json").read_text())
    ins = [p for p in papers if in_scope(p, s["scope"])]
    ids = {p["id"] for p in ins}
    extra = context_ids(s, papers, ids)
    return ins + [p for p in papers if p["id"] in extra]


def context_ids(s: dict, papers: list[dict], ins: set[str]) -> set[str]:
    """Out-of-scope papers the in-scope work stands on: named in >= `mentions` in-scope readings' builds_on /
    compares_to (matched by short name) and cited >= `min_cites` times; plus landmarks and hand-picked ids."""
    spec = s.get("context") or {}
    if not spec:
        return set()
    norm = lambda x: re.sub(r"[^a-z0-9]", "", (x or "").lower())
    byname: dict[str, list] = {}
    for p in papers:
        if p.get("n") and p["id"] not in ins and p["sc"] != "out":
            byname.setdefault(norm(p["n"]), []).append(p)
    cnt: dict[str, int] = {}
    for p in papers:
        if p["id"] not in ins:
            continue
        r = reading(p["id"]) or {}
        for nm in {norm(x) for x in (r.get("builds_on") or []) + (r.get("compares_to") or [])}:
            for q in byname.get(nm, []):
                cnt[q["id"]] = cnt.get(q["id"], 0) + 1
    byid = {p["id"]: p for p in papers}
    auto = {u for u, c in cnt.items() if c >= spec.get("mentions", 4) and (byid[u].get("c") or 0) >= spec.get("min_cites", 50)}
    marks = {x[0] for x in getattr(ctx.D, "LANDMARKS", [])} & set(byid) - ins if spec.get("landmarks", True) else set()
    return (auto | set(spec.get("ids", [])) | marks) - ins


# ───────────────────────── assign ─────────────────────────
ASSIGN_SYSTEM = """You organise papers into the outline of a survey titled "{title}".
A paper goes to the leaf that matches its main contribution.
{rules}
Outline leaves (id: definition):
{leaves}

Problem threads (a paper can touch several):
{threads}

For every paper return an object:
{{"id": "...", "leaf": "<leaf id>", "alt": "<second-best leaf id or null>", "threads": ["T-..."],
 "role": "landmark | representative | incremental", "why": "<= 15 words"}}
role: landmark = defined or redirected the line (high impact / first of its kind); representative = a strong,
typical instance worth discussing; incremental = small variation, cite in passing.
"alt" is the second-best leaf: set it whenever a paper is also central to another leaf's story.
Return ONLY a JSON array, one object per input paper, in input order."""


def cmd_assign(a) -> None:
    s = survey(a.sid)
    L = leaves(s["outline"])
    leaf_ids = {n["id"] for n, _ in L}
    thread_ids = {t[0] for t in s.get("threads", [])}
    system = ASSIGN_SYSTEM.format(
        title=s["title"], rules=(s.get("assign_rules", "").strip() + "\n") if s.get("assign_rules") else "",
        leaves="\n".join(f"- {n['id']} ({' / '.join(x['title'] for x in path)}): {n['def']}" for n, path in L),
        threads="\n".join(f"- {t[0]}: {t[1]} — {t[2]}" for t in s.get("threads", [])))
    done = load_assign(a.sid)
    todo = [p for p in scope_papers(s) if a.force or p["id"] not in done or (a.upgrade and not done[p["id"]].get("with_reading") and reading(p["id"]))]
    print(f"survey {a.sid}: {len(todo)} papers to assign ({len(done)} already)", flush=True)
    adir(a.sid).mkdir(parents=True, exist_ok=True)

    def batch(ps: list[dict]) -> int:
        parts = []
        for p in ps:
            r = reading(p["id"])
            extra = ""
            if r:
                extra = f"\nkey idea: {r.get('key_idea', '')}\nmethod: {' | '.join((r.get('method') or [])[:5])}\nsetting: learned={r.get('setting', {}).get('learned')}, backbone={r.get('setting', {}).get('backbone')}"
            parts.append(f"### id: {p['id']}\n{p.get('n') or ''} — {p['t']} ({p['y']}, {p.get('v') or 'arXiv'}, {p.get('c', 0)} citations)\n"
                         f"paradigm: {p['pd']}; tasks: {', '.join(p['tk'])}; traits: {', '.join(p['tr'])}\n"
                         f"tldr: {p.get('tl', '')}{extra}\nabstract: {(p.get('ab') or '')[:900]}\n")
        user = f"Assign these {len(ps)} papers.\n\n" + "\n".join(parts)
        cmd = ["claude", "-p", "--model", a.model, "--system-prompt", system, "--output-format", "json",
               "--no-session-persistence", "--tools", "", "--strict-mcp-config"]
        for attempt in range(3):
            r = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=900, cwd="/tmp")
            try:
                env = json.loads(r.stdout)
                txt = env.get("result", "")
                arr = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
                break
            except (ValueError, KeyError):
                print(f"  retry {attempt + 1}: {r.stderr[-200:] or r.stdout[-200:]}", flush=True)
        else:
            return 0
        want = {p["id"]: p for p in ps}
        good = []
        for o in arr:
            if o.get("id") in want and o.get("leaf") in leaf_ids:
                good.append({"id": o["id"], "leaf": o["leaf"], "alt": o.get("alt") if o.get("alt") in leaf_ids else None,
                             "threads": [t for t in o.get("threads") or [] if t in thread_ids],
                             "role": o.get("role") if o.get("role") in ("landmark", "representative", "incremental") else "incremental",
                             "why": (o.get("why") or "")[:160], "with_reading": bool(reading(o["id"])), "at": now()})
        with LOCK, open(adir(a.sid) / "assign.jsonl", "a") as f:
            for g in good:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        print(f"  {len(good)}/{len(ps)} assigned", flush=True)
        return len(good)

    batches = [todo[i: i + a.batch] for i in range(0, len(todo), a.batch)]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        n = sum(ex.map(batch, batches))
    print(f"done: {n} assigned")


# ───────────────────────── document layout ─────────────────────────
# The outline decides the LaTeX structure. Units (I, each era of II, III, IV) are \section; their
# children \subsection; grandchildren \subsubsection. Headings are generated (sections/_<unit>.tex),
# writers only write bodies: sections/<leaf>.tex for leaves and synthesis nodes, sections/<id>_intro.tex
# for nodes with children. So several writers can work on one chapter without clashing.
LEVELS = ["section", "subsection", "subsubsection", "paragraph"]


def units(s: dict) -> list[dict]:
    return [u for top in s["outline"] for u in (top.get("children", []) if top["id"] == "II" else [top])]


def writable(s: dict) -> list[tuple[str, str, int]]:
    """(file stem, title, depth) of every body file, in reading order."""
    out = [("abstract", "Abstract", 0), ("intro", "Introduction", 0)]

    def walk(n: dict, depth: int) -> None:
        if n.get("children"):
            out.append((f"{n['id']}_intro", f"{n['id']} {n['title']} — opening", depth))
            for c in n["children"]:
                walk(c, depth + 1)
        else:
            out.append((n["id"], f"{n['id']} {n['title']}", depth))
    for u in units(s):
        walk(u, 0)
    return out + [("conclusion", "Conclusion", 0)]


MAIN_TABLE_NAMES: list[str] = []  # filled by cmd_init from SURVEYS[..]["main_tables"]


def layout_file(n: dict, depth: int = 0) -> list[str]:
    lines = [rf"\{LEVELS[min(depth, 3)]}{{{tex_escape(n['title'])}}}\label{{sec:{n['id']}}}"]
    if n.get("children"):
        lines.append(rf"\input{{sections/{n['id']}_intro.tex}}")
        for c in n["children"]:
            lines += layout_file(c, depth + 1)
    else:
        lines.append(rf"\input{{sections/{n['id']}.tex}}")
        if n.get("synth") == "leaderboard":
            lines += [r"\input{sections/IV_figures.tex}"] + [rf"\input{{sections/tab_{t}.tex}}" for t in MAIN_TABLE_NAMES]
    return lines


# ───────────────────────── packs (writer material) ─────────────────────────
def _clip(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _results(r: dict, k: int) -> list[str]:
    out = []
    for n in [x for x in (r.get("numbers") or []) if x.get("verified") is not False][:k]:  # only rows whose values occur in the paper
        m = ", ".join(f"{a} {b}" for a, b in list((n.get("metrics") or {}).items())[:5])
        sub = f" [SUBSET: {n.get('eval_set_note', '')}]" if n.get("eval_set") == "subset" else ""
        zs = "zero-shot" if n.get("zero_shot") else "trained"
        ex = f"; extra: {n['extra']}" if n.get("extra") else ""
        out.append(f"{n.get('bench')} {n.get('split')}{sub} — {n.get('method', '')} ({zs}, {n.get('backbone', '')}{ex}): {m}")
    return out


def paper_line(p: dict, a: dict, mode: str) -> str:
    """mode: full (landmark) | brief (representative) | line (incremental)."""
    head = (f"- \\cite{{{p['key']}}} **{p.get('n') or _clip(p['t'], 70)}** — {p['t']} ({p['y']}, {p.get('v') or 'arXiv'}; "
            f"{p['pd']}; {p.get('c', 0)} cites; {a.get('role')}) [id: {p['id']}]")
    r = reading(p["id"]) if mode != "line" else None
    if not r:
        return head + f" — {p.get('tl', '')}" + ("  [no deep-reading notes yet]" if mode != "line" else "")
    s = r.get("setting", {})
    ab = "  [read from the abstract only — do not cite numbers or details beyond the abstract]" if r.get("source") == "abstract" else ""
    lines = [head + ab, f"  - key idea: {r.get('key_idea', '')}"]
    if mode == "full":
        lines += [f"  - problem: {_clip(r.get('problem'), 400)}",
                  "  - method: " + " | ".join(_clip(m, 220) for m in (r.get("method") or [])[:5]),
                  f"  - insight: {_clip(r.get('insight'), 600)}",
                  f"  - setting: learned={s.get('learned')}; backbone={s.get('backbone')}; obs={_clip(s.get('observation'), 80)}; "
                  f"actions={_clip(s.get('action_space'), 80)}; privileged={_clip(s.get('privileged'), 100)}",
                  f"  - limitations: {_clip(' ; '.join(r.get('limitations') or []), 300)}"]
        res = _results(r, 5)
    else:
        lines += [f"  - insight: {_clip(r.get('insight'), 320)}",
                  f"  - setting: backbone={_clip(s.get('backbone'), 80)}; privileged={_clip(s.get('privileged'), 60)}"]
        res = _results(r, 2)
    if res:
        lines.append("  - results (verified against the paper): " + " ; ".join(res))
    if r.get("builds_on"):
        lines.append(f"  - builds on: {', '.join((r.get('builds_on') or [])[:5])}")
    return "\n".join(lines)


def cmd_packs(a) -> None:
    """packs/<file stem>.md — one pack per body file a writer produces (leaf / synthesis node / opening)."""
    s = survey(a.sid)
    papers = {p["id"]: p for p in scope_papers(s)}
    asg = load_assign(a.sid)
    out = sdir(a.sid) / "packs"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    by_leaf: dict[str, list] = {}
    for uid, r in asg.items():
        if uid in papers:
            by_leaf.setdefault(r["leaf"], []).append((papers[uid], r))
    rank = {"landmark": 0, "representative": 1, "incremental": 2}
    mode = {"landmark": "full", "representative": "brief", "incremental": "line"}
    parents = {}

    def index(n, path):
        for c in n.get("children", []):
            parents[c["id"]] = path + [n]
            index(c, path + [n])
    for top in s["outline"]:
        parents[top["id"]] = []
        index(top, [])
    sizes = []
    for n, path in leaves(s["outline"]):
        ps = sorted(by_leaf.get(n["id"], []), key=lambda x: (x[0]["y"], rank[x[1]["role"]]))
        ctxl = " › ".join(x["title"] for x in parents.get(n["id"], []) + [n])
        era = next((x for x in parents.get(n["id"], []) if x.get("era")), None)
        sib = [c for c in (parents[n["id"]][-1].get("children", []) if parents.get(n["id"]) else [])]
        L = [f"# Writer pack — {n['id']} {n['title']}", "",
             f"Survey: {s['title']}", f"Position in the outline: {ctxl}",
             f"Section scope: {n['def']}"]
        if era:
            L.append(f"Era: {era['title']} ({era['era']}). Transition out of this era: {era.get('transition', '(see next era)')}")
        L.append("Sibling sections (do not cover their material): " + "; ".join(f"{c['id']} {c['title']}" for c in sib if c["id"] != n["id"]))
        if era and leaves([era])[-1][0]["id"] == n["id"]:
            L.append("This is the LAST section of its era: end with the era's transition (rephrased).")
        L += ["", f"{len(ps)} papers, chronological. Landmarks carry full notes, representative papers brief notes, "
              f"incremental papers one line (cite them only in grouped statements).", ""]
        L += [paper_line(p, r, mode[r["role"]]) for p, r in ps]
        also = sorted(((papers[u], r) for u, r in asg.items() if u in papers and r.get("alt") == n["id"] and r["role"] != "incremental"),
                      key=lambda x: (x[0]["y"], rank[x[1]["role"]]))
        if also:
            L += ["", f"## Also relevant here (primarily discussed in another section — refer to them briefly, cite, do not re-describe)", ""]
            L += [paper_line(p, r, "line") + f"  [primary section: {r['leaf']}]" for p, r in also]
        (out / f"{n['id']}.md").write_text("\n".join(L) + "\n")
        sizes.append((n["id"], len(ps), (out / f"{n['id']}.md").stat().st_size))
    # threads (Part III): per thread and era leaf — landmarks with their key idea, representative papers by name
    tl = ["# Writer pack — III cross-cutting threads", "", "For each problem thread: the landmark papers (with key idea) and "
          "representative papers (names) that touch it, grouped by the outline leaf they belong to, chronological. "
          "Trace how the answer to each problem changed across the outline's periods. Full notes: data/reading/<id>.json.", ""]
    for t in s.get("threads", []):
        tl.append(f"\n## {t[0]} {t[1]}\n{t[2]}\n")
        for n, path in leaves(s["outline"]):
            ps = sorted([(papers[u], r) for u, r in asg.items() if u in papers and r["leaf"] == n["id"] and t[0] in r["threads"]
                         and r["role"] != "incremental"], key=lambda x: x[0]["y"])
            if not ps:
                continue
            tl.append(f"\n### in {n['id']} {n['title']}")
            tl += [f"- \\cite{{{p['key']}}} {p.get('n') or _clip(p['t'], 50)} ({p['y']}) [id: {p['id']}]: "
                   f"{_clip((reading(p['id']) or {}).get('key_idea') or p.get('tl', ''), 170)}" for p, r in ps if r["role"] == "landmark"]
            reps = [f"{p.get('n') or _clip(p['t'], 40)}~\\cite{{{p['key']}}} ({p['y']})" for p, r in ps if r["role"] == "representative"]
            if reps:
                tl.append("- representative: " + "; ".join(reps))
    (out / "III.md").write_text("\n".join(tl) + "\n")
    sizes.append(("III", 0, (out / "III.md").stat().st_size))
    # outlook (IV.2): stated limitations of recent landmark / representative work, per leaf
    ol = ["# Writer pack — IV.2 open problems and future directions", "", "Limitations stated by (or evident in) the landmark and "
          "representative papers of the last three years, grouped by outline leaf. Synthesise recurring open problems from them; "
          "do not list them paper by paper.", ""]
    recent = max((p["y"] for p in papers.values()), default=2026) - 2
    for n, path in leaves(s["outline"]):
        ps = sorted([(papers[u], r) for u, r in asg.items() if u in papers and r["leaf"] == n["id"] and r["role"] != "incremental"
                     and papers[u]["y"] >= recent], key=lambda x: -x[0]["y"])
        rows = []
        for p, r in ps:
            rd = reading(p["id"]) or {}
            if rd.get("limitations"):
                rows.append(f"- \\cite{{{p['key']}}} {p.get('n') or _clip(p['t'], 45)} ({p['y']}): " + _clip(" ; ".join(rd["limitations"]), 260))
        if rows:
            ol += [f"\n## {n['id']} {n['title']}"] + rows
    (out / "IV.2.md").write_text("\n".join(ol) + "\n")
    sizes.append(("IV.2", 0, (out / "IV.2.md").stat().st_size))
    for sid, n, b in sizes:
        print(f"pack {sid:9s} {n:4d} papers  {b / 1000:6.0f} KB")


# ───────────────────────── LaTeX ─────────────────────────
MAIN_TEX = r"""\documentclass[journal]{IEEEtran}
\usepackage{fontspec}
\setmainfont{texgyretermes}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setsansfont{texgyreheros}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\usepackage{amsmath,amssymb}
\usepackage{unicode-math}
\setmathfont{texgyretermes-math.otf}
\usepackage{pifont}\AtBeginDocument{\renewcommand{\checkmark}{\ding{51}}}% Termes has no U+2713
\makeatletter% appendix longtables captioned like IEEEtran floats: TABLE N on its own line, small caps, centred
\AtBeginDocument{\def\LT@makecaption#1#2#3{\LT@mcol\LT@cols c{\hbox to\z@{\hss\parbox[t]\LTcapwidth{\centering\footnotesize #2\\[1pt]{\scshape #3}\par\vskip 4pt}\hss}}}\setlength{\LTcapwidth}{\textwidth}}
\makeatother
\usepackage{booktabs,multirow,makecell,array,longtable,tabularx}
\usepackage[table]{xcolor}
\usepackage{graphicx,adjustbox}
\usepackage{tikz,forest}
\usetikzlibrary{arrows.meta,positioning,shapes.geometric,calc,fit,backgrounds}
\usepackage[most]{tcolorbox}
\usepackage{ragged2e}
\usepackage{placeins}
\usepackage{cite}\renewcommand{\citedash}{\mbox{--}}% no line break inside a citation range
\usepackage[colorlinks=true,linkcolor=blue!50!black,citecolor=blue!50!black,urlcolor=blue!50!black]{hyperref}
\definecolor{takeaway}{HTML}{2A78D6}
\colorlet{tabhead}{gray!13}
\colorlet{tabgroup}{takeaway!9}
\newtcolorbox{takeaways}[1][Takeaways]{enhanced,breakable,colback=takeaway!4!white,colframe=takeaway!78!black,
  boxrule=0pt,leftrule=3pt,arc=0pt,outer arc=0pt,left=8pt,right=7pt,top=2pt,bottom=6pt,before skip=10pt,after skip=10pt,
  title={#1},fonttitle=\sffamily\bfseries\normalsize,coltitle=takeaway!78!black,colbacktitle=takeaway!4!white,
  titlerule=0pt,toptitle=5pt,bottomtitle=1pt,fontupper=\normalsize,
  before upper={\RaggedRight\renewcommand{\labelitemi}{\textcolor{takeaway!78!black}{\scriptsize$\blacktriangleright$}}}}
\setcounter{secnumdepth}{3}
\hyphenation{%HYPHENATION%}
\begin{document}
\title{%TITLE%}
\author{%AUTHORS%}
\markboth{%JOURNAL%}{%SHORTTITLE%}
\maketitle
\begin{abstract}
\input{sections/abstract.tex}
\end{abstract}
\begin{IEEEkeywords}
%KEYWORDS%
\end{IEEEkeywords}
\section{Introduction}\label{sec:intro}
\input{sections/intro.tex}
%INPUTS%
\section{Conclusion}\label{sec:conclusion}
\input{sections/conclusion.tex}
\bibliographystyle{IEEEtran}
\bibliography{refs}
\clearpage
\appendices
\onecolumn
\IfFileExists{sections/A_overview.tex}{\input{sections/A_overview.tex}}{}
\IfFileExists{sections/A_surveys.tex}{\section{Relation to prior surveys}\label{app:surveys}\input{sections/A_surveys.tex}}{}
\IfFileExists{sections/A_method.tex}{\FloatBarrier\section{Survey methodology}\label{app:method}\input{sections/A_method.tex}}{}
\IfFileExists{sections/A_trends.tex}{\FloatBarrier\section{Growth of the field}\label{app:trends}\input{sections/A_trends.tex}}{}
\IfFileExists{sections/A_environments.tex}{\FloatBarrier\section{Environments and further benchmarks}\label{app:env}\input{sections/A_environments.tex}}{}
\FloatBarrier
\section{Extended results tables}\label{app:tables}
\input{sections/A_intro.tex}
\input{sections/A_tables.tex}
\end{document}
"""


def cmd_init(a) -> None:
    """(Re)generate main.tex and the heading files sections/_<unit>.tex from the outline; create empty
    body files that do not exist yet (never overwrites a body a writer produced)."""
    s = survey(a.sid)
    d = sdir(a.sid)
    (d / "sections").mkdir(parents=True, exist_ok=True)
    us = units(s)
    MAIN_TABLE_NAMES[:] = [t["name"] for t in s.get("main_tables", [])]
    (d / "main.tex").write_text(MAIN_TEX.replace("%TITLE%", tex_escape(s["title"])).replace("%AUTHORS%", s.get("authors", "Author names"))
                                .replace("%KEYWORDS%", ", ".join(s.get("keywords", [])))
                                .replace("%SHORTTITLE%", tex_escape(s.get("short_title", s["title"][:60])))
                                .replace("%JOURNAL%", tex_escape(s.get("journal", "Preprint")))
                                .replace("%HYPHENATION%", " ".join(s.get("hyphenation", [])))
                                .replace("%INPUTS%", "\n".join(rf"\input{{sections/_{u['id']}.tex}}" for u in us)))
    for u in us:
        (d / "sections" / f"_{u['id']}.tex").write_text("% AUTO-GENERATED from the outline (./atlas survey ... init). Edit the body files instead\n"
                                                        + "\n".join(layout_file(u)) + "\n")
    stems = {w[0] for w in writable(s)} | {"IV_tables", "IV_figures", "A_tables", "A_intro"}
    ai = d / "sections" / "A_intro.tex"
    if not ai.exists():
        ai.write_text("% appendix opening — to be written\n")
    for stem, title, _ in writable(s):
        f = d / "sections" / f"{stem}.tex"
        if not f.exists():
            f.write_text(f"% {title} — to be written\n")
    for f in (d / "sections").glob("*.tex"):  # stubs of an older layout
        if f.stem not in stems and not f.stem.startswith("_") and len(f.read_text()) < 200:
            f.unlink()
    (d / ".gitignore").write_text("main.pdf\n*.aux\n*.log\n*.out\n*.toc\n*.bbl\n*.blg\n*.fls\n*.fdb_latexmk\n*.xdv\npacks/\n")
    print(f"initialised {d}: main.tex, {len(us)} heading files, {len(writable(s))} body files")


def protect_title(field: str) -> str:
    """Wrap words that must keep their capitals (HM3D-OVON, RGB-D, NavGPT, GPT-4, AI) in braces."""
    k, _, v = field.partition("=")
    v = v.strip()
    if not (v.startswith("{") and v.endswith("}")) or v.startswith("{{"):
        return field
    inner = v[1:-1]
    def fix(m):
        w = m.group(0)
        if "$" in w or "\\" in w or "^" in w:  # leave math and macros alone
            return w
        core = w.strip(":,.;()")
        if re.search(r"[A-Z].*[A-Z]|[A-Za-z]\d|\d[A-Za-z]", core[1:] if len(core) > 1 else "") or re.fullmatch(r"[A-Z]{2,}s?", core) \
                or re.search(r"\d[A-Za-z]|[A-Za-z]\d", core) \
                or re.search(r"[a-z][A-Z]", core):
            return w.replace(core, "{" + core + "}", 1)
        return w
    if "$" in inner:  # titles with inline math: protect only whole words outside the math
        parts = re.split(r"(\$[^$]*\$)", inner)
        inner = "".join(x if x.startswith("$") else re.sub(r"(?<![{\\\w$])[^\s{}$]+", fix, x) for x in parts)
    else:
        inner = re.sub(r"(?<![{\\\w])[^\s{}]+", fix, inner)
    return f"{k}= {{{inner}}}"


VENUE_CANON = [
    (r"IEEE/CVF Conference on Computer Vision and Pattern Recognition", "IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)"),
    (r"(?:IEEE/CVF )?International Conference on Computer Vision(?! and)", "IEEE/CVF International Conference on Computer Vision (ICCV)"),
    (r"IEEE International Conference on Robotics and Automation", "IEEE International Conference on Robotics and Automation (ICRA)"),
    (r"IEEE/RSJ International Conference on Intelligent Robots and Systems", "IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)"),
    (r"AAAI Conference on Artificial Intelligence", "AAAI Conference on Artificial Intelligence (AAAI)"),
    (r"Conference on Empirical Methods in Natural Language Processing", "Conference on Empirical Methods in Natural Language Processing (EMNLP)"),
    (r"Conference of the North American Chapter of the Association for Computational Linguistics", "Conference of the North American Chapter of the Association for Computational Linguistics (NAACL)"),
    (r"Annual Meeting of the Association for Computational Linguistics", "Annual Meeting of the Association for Computational Linguistics (ACL)"),
]


def bib_clean(entry: str) -> str:
    """Make a CrossRef / S2 BibTeX entry safe for LaTeX: HTML entities, JATS tags, bare & % # (not in url / doi)."""
    import html
    m = re.match(r"(@\w+\{[^,]+,)(.*)\}\s*$", entry, re.S)
    if not m:
        return entry
    head, body = m.groups()
    if head.lower().startswith("@inbook") and re.search(r"\bbooktitle\s*=", body, re.I):
        head = "@inproceedings" + head[len("@inbook"):]  # Springer proceedings chapters: styles print the booktitle only for inproceedings
    body = body.replace("\u21bb", r"{$\circlearrowright$}")  # VLN↻BERT: no text font carries the glyph
    for pat, canon in VENUE_CANON:  # one spelling per venue: no year prefix, no "Proceedings of the", acronym in parentheses
        body = re.sub(r"(booktitle\s*=\s*\{)(?:\d{4}\s+)?(?:Proceedings of (?:the )?)?(?:\d+(?:st|nd|rd|th)\s+)?(?:\d{4}\s+)?" + pat + r"[^}]*\}",
                      lambda m, c=canon: m.group(1) + c + "}", body, flags=re.I)
    body = re.sub(r"(booktitle\s*=\s*\{)Computer Vision\s*[–-]+\s*ECCV \d{4}[^}]*\}", r"\1European Conference on Computer Vision (ECCV)}", body)
    fields, depth, cur = [], 0, ""
    for ch in body:  # split top-level fields on commas outside braces / quotes
        depth += (ch == "{") - (ch == "}")
        if ch == "," and depth == 0:
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    fields.append(cur)
    out = []
    for f in fields:
        name = f.split("=", 1)[0].strip().lower()
        if name == "pages":  # "7566–7574" -> "7566--7574" so styles print a range ("pp.")
            f = f.replace("–", "--").replace("—", "--")
        if name == "title":  # sentence-casing styles would print "Hm3d" / "Navgpt": brace acronyms and CamelCase names
            f = protect_title(f)
        if "=" in f and name not in ("url", "doi"):
            f = html.unescape(html.unescape(f))
            f = re.sub(r"</?(?:i|b|sub|sup|em|scp|mml:[a-z]+)[^>]*>", "", f)
            f = re.sub(r"(?<!\\)([&%#])", r"\\\1", f)
        elif "=" in f:
            f = html.unescape(f)
        out.append(f)
    return head + ",".join(out) + "}"


def cmd_bib(a) -> None:
    d = sdir(a.sid)
    keys = set()
    for f in (d / "sections").glob("*.tex"):
        for m in re.finditer(r"\\cite[pt]?\*?(?:\[[^\]]*\])*\{([^}]+)\}", f.read_text()):
            keys |= {k.strip() for k in m.group(1).split(",") if k.strip()}
    bib = (ctx.PUBLIC / "atlas.bib").read_text()
    extra = (d / "extra.bib").read_text() if (d / "extra.bib").exists() else ""  # hand-kept entries: outside works + corrections
    xkeys = set(re.findall(r"@\w+\{([^,]+),", extra))
    entries = [e for e in re.split(r"\n(?=@)", bib) if not ((m := re.match(r"@\w+\{([^,]+),", e.strip())) and m.group(1) in xkeys)]
    entries += re.split(r"\n(?=@)", extra) if extra else []
    got = [e for e in entries if (m := re.match(r"@\w+\{([^,]+),", e.strip())) and m.group(1) in keys]
    (d / "refs.bib").write_text("\n\n".join(bib_clean(e.strip()) for e in got) + "\n")
    missing = keys - {re.match(r"@\w+\{([^,]+),", e.strip()).group(1) for e in got}
    print(f"refs.bib: {len(got)} entries" + (f"; unknown keys: {', '.join(sorted(missing))}" if missing else ""))


def cmd_pdf(a) -> None:
    cmd_bib(a)
    d = sdir(a.sid)
    r = subprocess.run(["latexmk", "-xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"], cwd=d, capture_output=True, text=True, timeout=900)
    print("main.pdf built" if r.returncode == 0 and (d / "main.pdf").exists() else "latex failed:\n" + r.stdout[-2500:])


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


PD_SHORT = {"specialist": "Spec.", "pretrain": "Pretr.", "fm-tuned": "FM-FT", "zs-modular": "ZS-Mod.", "agentic": "Agentic", "none": "—"}


def tex_escape(t: str) -> str:
    return re.sub(r"([&%$#_{}])", r"\\\1", str(t)).replace("~", "\\textasciitilde{}").replace("^", "\\^{}")


_NAME_COUNT: dict[str, int] = {}


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


def best_rows(board: list, bench: str, split: str, key: str, allow_protocol: bool) -> dict:
    """{paper id: (class, value, row)} — class 0 comparable full split, 1 shared-protocol subset."""
    moved = {r["id"] for r in board if r["bench"] == bench and r["split"] == split and r.get("audit_std") is False}
    out: dict[str, tuple] = {}
    for r in board:
        if r["bench"] != bench or r["split"] != split or key not in r["m"] or not usable(r):
            continue
        try:
            v = float(r["m"][key])
        except (TypeError, ValueError):
            continue
        if v <= 1 and key in PERCENT:
            continue
        if comparable(r, moved):
            cls = 0
        elif allow_protocol and r.get("protocol") and not (r.get("audit_std") is False and "subset" not in (r.get("audit_note") or "").lower()):
            cls = 1
        else:
            continue
        sgn = -v if key in LOWER_BETTER else v
        cur = out.get(r["id"])
        rank = (cls, is_ablation(r), -sgn)  # comparable first, then the main method, then the value
        if cur is None or rank < (cur[0], is_ablation(cur[2]), -(-cur[1] if key in LOWER_BETTER else cur[1])):
            out[r["id"]] = (cls, v, r)
    return out


def bench_display(b: str) -> str:
    """Benchmark name as printed in tables and figures (domain file may map ids, e.g. HM3D-ObjNav-v1 → HM3D ObjectNav v1)."""
    return getattr(ctx.D, "BENCH_DISPLAY", {}).get(b, b)


def notable(p: dict, anchors: set, cfg: dict) -> bool:
    """A paper may appear in a main (body) table only if readers are likely to know it: a milestone listed as an
    anchor, well cited, signed by a company (engine/affil.py), or published at a refereed venue with some uptake
    (or too recently to have been cited). Everything else stays in the complete appendix rankings."""
    if p.get("key") in anchors or (p.get("n") or "") in anchors or p.get("co"):
        return True
    c = int(p.get("c") or 0)
    if c >= cfg.get("min_cites", 50):
        return True
    if p.get("v") and p.get("vk") != "workshop":
        m = re.search(r"(20\d\d)", p.get("v") or "")
        fresh = p.get("vk") == "main" and bool(m) and int(m.group(1)) >= datetime.now().year  # accepted at a main venue this year
        need = cfg.get("min_cites_venue", 10) * (2 if p.get("vk") == "other" else 1)
        return c >= need or fresh
    return False


def paper_font(plt) -> None:
    """Times-like serif in matplotlib figures (Liberation Serif is TrueType, so it embeds cleanly as Type 42)."""
    from matplotlib import font_manager
    for pat in ("~/.miktex/texmfs/install/fonts/opentype/public/tex-gyre/texgyretermes-*.otf",
                "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyretermes-*.otf",
                "/usr/share/texlive/texmf-dist/fonts/opentype/public/tex-gyre/texgyretermes-*.otf",
                "/usr/share/fonts/opentype/tex-gyre/texgyretermes-*.otf"):
        import glob as _glob
        for f in _glob.glob(str(Path(pat).expanduser())):
            font_manager.fontManager.addfont(f)
    plt.rcParams.update({"font.family": "serif", "font.serif": ["Liberation Serif", "Times New Roman", "TeX Gyre Termes", "Nimbus Roman", "DejaVu Serif"],
                         "mathtext.fontset": "stix", "pdf.fonttype": 42, "ps.fonttype": 42})


def cmd_main_tables(a) -> None:
    """sections/tab_<name>.tex: one table* per benchmark family (SURVEYS[..]["main_tables"]): benchmarks side by side,
    rows grouped by paradigm (multirow), the top papers of each paradigm, bold best / underlined second-best among
    full-split comparable results; training-free and agentic groups may show results on a shared subset (lettered)."""
    s = survey(a.sid)
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    papers = {p["id"]: p for p in json.loads((ctx.PUBLIC / "papers.json").read_text())}
    order = ["none"] + [c for c, *_ in ctx.D.PARADIGMS if c != "none"]  # "none" first: benchmark papers' own baselines (anchors only)
    hook = getattr(ctx.D, "table_paradigm", None)
    tpd = lambda r: hook(r, papers.get(r["id"], {})) if hook else (papers.get(r["id"]) or {}).get("pd", r["pd"])
    d = sdir(a.sid) / "sections"
    first_T = (s.get("main_tables") or [None])[0]
    for T in s.get("main_tables", []):
        benches = T["benches"]  # [[bench, split, key, [cols]], ...]
        per = [best_rows(board, b, sp, k, allow_protocol=True) for b, sp, k, _ in benches]
        # a body table shows well-known papers, plus the very best results even when their paper is not (yet) known:
        # the top-2 full-split standard rows and the best shared-subset row of each benchmark
        anchors = set(T.get("anchors", []))
        record: set[str] = set()
        for bi, (b, sp, k, cols) in enumerate(benches):
            for cls_, n_ in ((0, T.get("records", 2)), (1, 1)):
                vals = sorted(((v if k in LOWER_BETTER else -v), u) for u, (cls, v, r) in per[bi].items() if cls == cls_)
                record |= {u for _, u in vals[:n_]}
        leaders: set[str] = set()
        for bi, (b, sp, k, cols) in enumerate(benches):  # the best comparable result of every paradigm is always shown
            lead: dict[str, tuple] = {}
            for u, (cls, v, r) in per[bi].items():
                if cls or _ABLATION_STRICT.search(r.get("method") or ""):
                    continue
                g, sv = tpd(r), (-v if k in LOWER_BETTER else v)
                if g not in lead or sv > lead[g][0]:
                    lead[g] = (sv, u)
            record |= {u for _, u in lead.values()}
            leaders |= {u for _, u in lead.values()}
        known = lambda u: u in record or notable(papers.get(u, {}), anchors, T)
        # a body table shows each paper's main method: a paper whose only row here is an ablation / variant is left out
        per = [{u: x for u, x in pb.items() if known(u) and not _ABLATION_STRICT.search(x[2].get("method") or "")} for pb in per]
        pgroup: dict[str, str] = {}  # one group per paper, decided by its row on the first benchmark it appears in
        for bi in range(len(benches)):
            for u, (_, _, r) in per[bi].items():
                pgroup.setdefault(u, tpd(r))
        grp = lambda r: pgroup.get(r["id"], tpd(r))
        protos: dict[str, str] = {}
        lines, ncols = [], 4 + sum(len(c) for *_, c in benches)
        # best / second-best per column (comparable rows only)
        body = []
        chosen: list[tuple[str, list]] = []
        for g in order:
            ids: list[str] = []
            for bi, (b, sp, k, cols) in enumerate(benches):
                cand = [(cls, v, r) for cls, v, r in per[bi].values() if grp(r) == g]
                cand.sort(key=lambda x: (x[0], x[1] if k in LOWER_BETTER else -x[1]))
                lim = 0 if g == "none" else T.get("per_group", 4) if bi == 0 else T.get("per_group_other", 2)
                for cls, v, r in cand[:lim]:
                    if r["id"] not in ids:
                        ids.append(r["id"])
            anchors = set(T.get("anchors", []))
            for bi in range(len(benches)):
                for u, (cls, v, r) in per[bi].items():
                    p_ = papers.get(u, {})
                    if (grp(r) == g and u not in ids
                            and (p_.get("key") in anchors or (p_.get("n") or "") in anchors)):
                        ids.append(u)  # milestone methods stay in the table even when no longer in the top of their group
            for bi in range(len(benches)):  # and so does every paradigm's leader on every benchmark
                for u, (cls, v, r) in per[bi].items():
                    if u in leaders and grp(r) == g and u not in ids and g != "none":
                        ids.append(u)
            if not ids:
                continue
            # order the group's rows by the first benchmark's key metric (comparable first), then the others
            def sk(u):
                for bi, (b, sp, k, cols) in enumerate(benches):
                    if u in per[bi]:
                        cls, v, _ = per[bi][u]
                        return (bi, cls, v if k in LOWER_BETTER else -v)
                return (99, 9, 0)
            ids.sort(key=sk)
            chosen.append((g, ids))
        # bold / underline: best and second best among the rows actually shown (comparable, full split)
        shown = {u for _, ids in chosen for u in ids}
        rank_marks: dict[tuple, dict] = {}
        for bi, (b, sp, k, cols) in enumerate(benches):
            for c in cols:
                vals = sorted({_pct(c, r["m"][c]) for u, (cls, _, r) in per[bi].items() if u in shown and cls == 0 and c in r["m"]
                               and re.match(r"^-?[\d.]+$", str(r["m"][c]))}, reverse=c not in LOWER_BETTER)
                rank_marks[(bi, c)] = {v: i for i, v in enumerate(vals[:2])}
        for g, ids in chosen:
            rows = []
            for u in ids:
                p = papers.get(u, {})
                anyr = next(per[bi][u][2] for bi in range(len(benches)) if u in per[bi])
                cells = []
                for bi, (b, sp, k, cols) in enumerate(benches):
                    if u not in per[bi]:
                        cells += ["--"] * len(cols)
                        continue
                    cls, _, r = per[bi][u]
                    mark = ""
                    if cls == 1:
                        if r["protocol"] not in protos:
                            protos[r["protocol"]] = "abcdefghij"[len(protos)]
                        mark = f"$^{{{protos[r['protocol']]}}}$"
                    for c in cols:
                        val = r["m"].get(c)
                        txt = fmt_metric(c, val)
                        if cls == 0 and val is not None and re.match(r"^-?[\d.]+$", str(val)):
                            rk = rank_marks[(bi, c)].get(_pct(c, val))
                            txt = rf"\textbf{{{txt}}}" if rk == 0 else rf"\underline{{{txt}}}" if rk == 1 else txt
                        cells.append(txt + (mark if val is not None else ""))  # a subset result is marked in every metric
                bb = tex_name(short_backbone(anyr.get("backbone") or ""))
                rows.append(f" & {tex_name(short_name(anyr, p))}~\\cite{{{p.get('key', '')}}} & {venue_short(p)} & {bb} & " + " & ".join(cells) + r" \\")
            label = GROUP_NAME.get(g, g)
            if len(rows) >= max(3, math.ceil(len(label) / 2.6)):
                rows[0] = rf"\multirow{{{len(rows)}}}{{*}}{{\rotatebox[origin=c]{{90}}{{\scriptsize\textsc{{{label}}}}}}}" + rows[0]
            else:  # too few rows for a rotated label
                rows[0] = rf"\multirow{{{len(rows)}}}{{*}}{{\scriptsize\textsc{{{GROUP_SHORT.get(g, label[:5])}}}}}" + rows[0]
            body += rows + [r"\midrule"]
        if not body:
            continue
        body[-1] = r"\bottomrule"
        head1 = r"\multirow{2}{*}{} & \multirow{2}{*}{Method} & \multirow{2}{*}{Venue} & \multirow{2}{*}{Backbone} & " + " & ".join(
            rf"\multicolumn{{{len(c)}}}{{c}}{{{tex_escape(bench_display(b))} {tex_escape(sp)}}}" for b, sp, k, c in benches) + r" \\"
        cm, col = [], 5
        for *_, c in benches:
            cm.append(rf"\cmidrule(lr){{{col}-{col + len(c) - 1}}}")
            col += len(c)
        head2 = " & & & & " + " & ".join(" & ".join(f"{x}{'$\\downarrow$' if x in LOWER_BETTER else '$\\uparrow$'}" for x in c)
                                         for *_, c in benches) + r" \\"
        notes = ", ".join(f"$^{{{m}}}$ {tex_escape(n)} subset" for n, m in protos.items())
        spec = "@{}c l l l " + " ".join("c" * len(c) for *_, c in benches) + "@{}"
        col = T.get("width") == "column"
        env, width = ("table", r"\columnwidth") if col else ("table*", r"\textwidth")
        out = [f"% AUTO-GENERATED by ./atlas survey <atlas> <sid> tables — {T['label']}",
               rf"\begin{{{env}}}[!t]", r"\centering\footnotesize", r"\setlength{\tabcolsep}{3.2pt}\renewcommand{\arraystretch}{1.08}",
               rf"\caption{{{T['caption']} " + (
                   r"Rows are grouped by the training regime behind each result, named in full or, for groups of one or two rows, abbreviated (Base.\ benchmark and analysis papers, Spec.\ task-specific, Pretr.\ large-scale pretraining, FM fine-tuned foundation model, ZS zero-shot, Agent.\ agentic), and within a group the best papers on the first benchmark come first. "
                   r"Venue gives the publication venue and year, Backbone the main visual or language model, and arrows whether higher or lower values are better (metrics in Table~\ref{tab:metrics}). "
                   r"Bold and underlined values are the best and second-best full-split, standard-protocol results among the rows shown, and a dash marks a result that is not reported. "
                   if T is first_T else rf"Groups, columns and marks as in Table~\ref{{{first_T['label']}}}. ")
               + (rf"Lettered results are on a subset shared by several papers ({notes}) and are comparable only with each other. " if notes else "")
               + r"Extended rankings are in Appendix~\ref{app:tables}.}",
               rf"\label{{{T['label']}}}",
               rf"\begin{{adjustbox}}{{max width={width}}}", rf"\begin{{tabular}}{{{spec}}}", r"\toprule",
               r"\rowcolor{tabhead}" + head1, " ".join(cm), r"\rowcolor{tabhead}" + head2, r"\midrule"]
        out += body + [r"\end{tabular}", r"\end{adjustbox}", rf"\end{{{env}}}", ""]
        (d / f"tab_{T['name']}.tex").write_text("\n".join(out) + "\n")
        print(f"main table {T['label']}: {sum(1 for x in body if x.startswith((' ', r'\multirow')))} rows, protocols: {', '.join(protos) or '-'}")


def cmd_appendix_tables(a) -> None:
    """sections/A_tables.tex: per benchmark a longtable (one-column appendix, continues across pages): comparable rows
    grouped by paradigm, then results on shared subsets grouped by protocol, then other subset / non-standard results."""
    s = survey(a.sid)
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    papers = {p["id"]: p for p in json.loads((ctx.PUBLIC / "papers.json").read_text())}
    order = [c for c, *_ in ctx.D.PARADIGMS]
    cited = set()
    for f in (sdir(a.sid) / "sections").glob("*.tex"):
        if f.name != "A_tables.tex":
            for m in re.finditer(r"\\cite[pt]?\*?(?:\[[^\]]*\])*\{([^}]+)\}", f.read_text()):
                cited |= {k.strip() for k in m.group(1).split(",")}
    body_keys = set()  # papers shown in the body's results tables: they always appear in the extended tables too
    for f in (sdir(a.sid) / "sections").glob("*.tex"):  # body tables and text: every result they cite appears here
        if f.name.startswith("A_"):
            continue
        for m in re.finditer(r"\\cite[pt]?\{([^}]+)\}", f.read_text()):
            body_keys |= {k.strip() for k in m.group(1).split(",")}
    first_ext: list[str] = []
    out = ["% AUTO-GENERATED by ./atlas survey <atlas> <sid> tables — extended results tables", ""]
    for bench, split, key, cols in s.get("tables", []):
        moved = {r["id"] for r in board if r["bench"] == bench and r["split"] == split and r.get("audit_std") is False}
        comp, proto, other = {}, {}, {}
        for r in board:
            if r["bench"] != bench or r["split"] != split or key not in r["m"] or not usable(r):
                continue
            try:
                v = float(r["m"][key])
            except (TypeError, ValueError):
                continue
            v = _pct(key, v)  # fractions (0.38) and percentages (38.0) on one scale for ranking
            tgt = comp if comparable(r, moved) else proto if r.get("protocol") and not nonstandard(r) else other
            if tgt is comp and _ABLATION_STRICT.search(r.get("method") or ""):
                tgt = other  # an ablation / variant row is not the paper's main result
            sgn = v if key not in LOWER_BETTER else -v
            cur = tgt.get(r["id"])
            if cur is None or (is_ablation(r), -sgn) < (is_ablation(cur[1]), -cur[0]):
                tgt[r["id"]] = (sgn, r)
        for u in list(proto) + list(other):
            if u in comp:
                proto.pop(u, None), other.pop(u, None)
        if len(comp) + len(proto) < 3:
            continue
        first_lab = first_ext[0] if first_ext else ""
        lab = "tab:ext-" + re.sub(r"[^a-z0-9]+", "-", f"{bench}-{split}".lower())
        if not first_ext:
            first_ext.append(lab)
        n = 5 + len(cols)
        vnote = next((f" {tex_escape(dd)}." for vs in getattr(ctx.D, "BENCH_VARIANTS", {}).values() for nm, dd in vs.items() if nm == bench), "")
        hdr = r"Method & Venue & Backbone & ZS & " + " & ".join(f"{c}{'$\\downarrow$' if c in LOWER_BETTER else '$\\uparrow$'}" for c in cols) + r" \\"
        note = sdir(a.sid) / "sections" / "A_notes" / f"{lab[4:]}.tex"
        if note.exists():  # hand-written commentary on this table (what it shows, who leads, caveats)
            out += [rf"\subsection*{{{tex_escape(bench_display(bench))} {tex_escape(split)}}}", rf"\input{{sections/A_notes/{note.name}}}", ""]
        out += [r"\begingroup\footnotesize\setlength{\tabcolsep}{3.5pt}\renewcommand{\arraystretch}{1.05}\setlength{\LTleft}{\fill}\setlength{\LTright}{\fill}",
                rf"\begin{{longtable}}{{@{{}}l l >{{\raggedright\arraybackslash}}p{{5.1cm}} c {'r' * len(cols)}@{{}}}}",
                rf"\caption{{Extended results on {tex_escape(bench_display(bench))} {tex_escape(split)} (one row per paper, ranked by {key}).{vnote} "
                + (r"Venue is the publication venue and year, Backbone the main visual or language model, ZS marks results obtained without any component trained on this benchmark, and the metrics are defined in Table~\ref{tab:metrics}. $^\dagger$ marks a subset and $^\ddagger$ a non-standard setting.}"
                   if not first_lab else rf"Columns and marks as in Table~\ref{{{first_lab}}}.}}")
                + rf"\label{{{lab}}}\\", r"\toprule", r"\rowcolor{tabhead}" + hdr, r"\midrule", r"\endfirsthead",
                rf"\multicolumn{{{n - 1}}}{{@{{}}l}}{{\small\itshape Table~\ref{{{lab}}} (continued)}} \\", r"\toprule", r"\rowcolor{tabhead}" + hdr, r"\midrule", r"\endhead",
                rf"\midrule\multicolumn{{{n - 1}}}{{r@{{}}}}{{\small\itshape continued on next page}} \\", r"\endfoot", r"\bottomrule", r"\endlastfoot"]

        def row(r: dict, flag: str = "") -> str:
            p = papers.get(r["id"], {})
            st = ""
            vals = " & ".join(fmt_metric(c, r["m"].get(c)) for c in cols)
            # every row is cited (survey option "appendix_cite": "all"); with "cited", works the text does not cite
            # are identified by arXiv id instead, which keeps the bibliography lean
            ref = (f"~\\cite{{{p.get('key', '')}}}" if (p.get("key") in cited or s.get("appendix_cite", "all") == "all")
                   else (f" {{\\scriptsize\\texttt{{{p['ax']}}}}}" if p.get("ax") else ""))
            return (f"{tex_name(short_name(r, p))}{ref}{st}{flag} & {venue_short(p)} & "
                    f"{tex_name(short_backbone(r.get('backbone') or '', 44))} & {r'\checkmark' if r.get('zs') else ''} & {vals} \\\\")

        def worthy(r: dict) -> bool:
            """A row earns its reference when the work has a name, is well cited, or is discussed in the main text:
            unnamed, rarely cited papers would only lengthen the bibliography."""
            p = papers.get(r["id"], {})
            return (not short_name(r, p).endswith("et al.")) or int(p.get("c") or 0) >= 50 or p.get("key") in body_keys

        comp = {u: x for u, x in comp.items() if worthy(x[1])}
        proto = {u: x for u, x in proto.items() if worthy(x[1])}
        other = {u: x for u, x in other.items() if worthy(x[1])}
        for g in order:
            hook = getattr(ctx.D, "table_paradigm", None)
            allg = sorted((x for x in comp.values() if (hook(x[1], papers.get(x[1]["id"], {})) if hook else x[1]["pd"]) == g), key=lambda x: -x[0])
            rs = [x for i, x in enumerate(allg) if i < a.top or papers.get(x[1]["id"], {}).get("key") in body_keys]
            if rs:
                out += [rf"\multicolumn{{{n - 1}}}{{@{{}}l}}{{\cellcolor{{tabgroup}}\textsc{{{GROUP_NAME.get(g, g)}}}}} \\*"] + [row(r) for _, r in rs]
        byp: dict[str, list] = {}
        for sgn, r in proto.values():
            byp.setdefault(r["protocol"], []).append((sgn, r))
        for pn, rs in byp.items():
            out += [rf"\multicolumn{{{n - 1}}}{{@{{}}l}}{{\cellcolor{{tabgroup}}\textit{{Shared subset: {tex_escape(pn)}}}}} \\*"]
            out += [row(r, r"$^\dagger$") for _, r in sorted(rs, key=lambda x: -x[0])]
        rs = [x for i, x in enumerate(sorted(other.values(), key=lambda x: -x[0]))
              if i < 8 or papers.get(x[1]["id"], {}).get("key") in body_keys]
        if rs:
            out += [rf"\multicolumn{{{n - 1}}}{{@{{}}l}}{{\cellcolor{{tabgroup}}\textit{{Other subsets or non-standard settings (not comparable)}}}} \\*"]
            out += [row(r, (r"$^\dagger$" if r["eval_set"] == "subset" else "") + (r"$^\ddagger$" if nonstandard(r) else "")) for _, r in rs]
        out += [r"\end{longtable}", r"\endgroup", ""]
    d = sdir(a.sid) / "sections"
    (d / "A_tables.tex").write_text("\n".join(out) + "\n")
    print(f"appendix tables: {sum(1 for x in out if x.startswith(chr(92) + 'begin{longtable'))}")


# categorical palette, validated for CVD (same order as the site's --cat-1..8); paradigms take slots by ORDER
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def cmd_figures(a) -> None:
    """sections/fig_progress.pdf + IV_figures.tex: comparable results over time per main benchmark
    (one point per paper, colour = paradigm, triangle = training-free, step line = best so far)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib import patheffects
    board = json.loads((ctx.PUBLIC / "leaderboard.json").read_text())
    papers = {p["id"]: p for p in json.loads((ctx.PUBLIC / "papers.json").read_text())}
    order = [c for c, *_ in ctx.D.PARADIGMS]
    color = {c: (CAT[i] if c != "none" else "#a3a29a") for i, c in enumerate(order)}
    label = {c: n for c, n, *_ in ctx.D.PARADIGMS}
    label["none"] = "Benchmark and analysis"
    hook = getattr(ctx.D, "table_paradigm", None)
    tpd = lambda r, p: hook(r, p) if hook else p.get("pd", r["pd"])
    paper_font(plt)
    plt.rcParams.update({"font.size": 7.5, "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
                         "axes.spines.top": False, "axes.spines.right": False})
    figs_cfg = survey(a.sid).get("figures", [])
    ncol = min(4, len(figs_cfg)) if len(figs_cfg) <= 4 else 3
    nrow = 1 if len(figs_cfg) <= 4 else 2
    fig, axes = plt.subplots(nrow, ncol, figsize=(7.2, 2.35 if nrow == 1 else 4.4), constrained_layout=True)
    used = set()
    figs = figs_cfg[: nrow * ncol]
    for ax, (bench, split, key) in zip(axes.flat, figs):
        # the same comparable set as the results tables: one full-split, standard-protocol result per paper
        best = {u: (v, r) for u, (cls, v, r) in best_rows(board, bench, split, key, allow_protocol=False).items()
                if cls == 0 and v > 1 and not _ABLATION_STRICT.search(r.get("method") or "")}
        pts = []
        for uid, (v, r) in best.items():
            p = papers.get(uid, {})
            d = str(p.get("d") or "")
            x = float(r["y"]) + ((int(d[5:7]) - 0.5) / 12 if d[:4].isdigit() and int(d[:4]) == r["y"] and len(d) >= 7 else 0.5)
            pts.append((x, v, r, p))
        pts.sort(key=lambda t: t[0])
        for x, v, r, p in pts:
            g = tpd(r, p)
            zs = g in ("zs-modular", "agentic")
            ax.scatter([x], [v], s=10 if not zs else 13, marker="^" if zs else "o", color=color.get(g, "#a3a29a"),
                       edgecolors="white", linewidths=0.4, zorder=3)
            used.add(g)
        fx, fy, run, labelled = [], [], -1.0, []
        for x, v, r, p in pts:
            if v > run:
                run = v
                fx.append(x)
                fy.append(v)
                name = r.get("n") or p.get("n")
                gap = 1.6 if nrow == 1 else 0.8
                if name and len(fx) > 1 and (not labelled or x - labelled[-1][0] > gap or v - labelled[-1][1] > 12):
                    ax.annotate(name if len(name) <= 20 else name[:19].rsplit(" ", 1)[0] + "…", (x, v), xytext=(3.5, 3), textcoords="offset points", fontsize=5.4, color="#2b2b28", zorder=5,
                                path_effects=[patheffects.withStroke(linewidth=1.6, foreground="white")])
                    labelled.append((x, v))
        if fx:
            fx.append(max(t[0] for t in pts) + 0.2)
            fy.append(run)
            ax.step(fx, fy, where="post", color="#3d3d3a", linewidth=0.8, zorder=2)
        short = bench_display(bench)
        ax.set_title(f"{short} {split} (n = {len(pts)})", fontsize=7)
        from matplotlib.ticker import FormatStrFormatter, MultipleLocator
        span = (max(t[0] for t in pts) - min(t[0] for t in pts)) if pts else 1
        ax.xaxis.set_major_locator(MultipleLocator(1 if span < 4 else 2 if span < 8 else 3))
        ax.xaxis.set_major_formatter(FormatStrFormatter("%d"))
        ax.set_ylabel(key)
        ax.grid(axis="y", linewidth=0.3, color="#dcdbd5")
        ax.set_axisbelow(True)
    handles = [Line2D([], [], marker="o", linestyle="", color=color[c], label=label[c], markersize=4) for c in order if c in used]
    handles += [Line2D([], [], marker="o", linestyle="", color="#6d6d68", label="trained", markersize=4),
                Line2D([], [], marker="^", linestyle="", color="#6d6d68", label="training-free", markersize=4.5),
                Line2D([], [], color="#3d3d3a", linewidth=0.8, label="best so far")]
    fig.legend(handles=handles, loc="outside lower center", ncol=5, fontsize=6.5, frameon=False)
    d = sdir(a.sid) / "sections"
    fig.savefig(d / "fig_progress.pdf")
    (d / "IV_figures.tex").write_text(
        "% AUTO-GENERATED by ./atlas survey <atlas> <sid> figures\n"
        r"\begin{figure*}[t]\centering\includegraphics[width=\textwidth]{sections/fig_progress.pdf}" "\n"
        r"\caption{Reported results over time on the main benchmarks. Each point is one paper's best full-split result under the "
        r"standard setting. Colour gives the paradigm and triangles mark training-free methods. The step line is the best result "
        r"reported so far and labels mark new records. Results on subsets or under non-standard settings are listed in Appendix~\ref{app:tables}.}"
        "\n" r"\label{fig:progress}" "\n" r"\end{figure*}" "\n")
    print(f"figure: sections/fig_progress.pdf ({', '.join(f'{b} {sp}' for b, sp, _ in figs)})")


def cmd_status(a) -> None:
    s = survey(a.sid)
    ps = scope_papers(s)
    asg = load_assign(a.sid)
    read = sum(1 for p in ps if reading(p["id"]))
    print(f"{s['id']}: {len(ps)} papers in scope · {sum(1 for p in ps if p['id'] in asg)} assigned · {read} deep-read")
    counts = {}
    for r in asg.values():
        counts[r["leaf"]] = counts.get(r["leaf"], 0) + 1
    for n, _ in leaves(s["outline"]):
        print(f"  {n['id']:9s} {counts.get(n['id'], 0):4d}  {n['title']}")
    d = sdir(a.sid) / "sections"
    print("body files:")
    for stem, title, depth in writable(s):
        f = d / f"{stem}.tex"
        n = len(f.read_text()) if f.exists() else 0
        print(f"  {'  ' * depth}{'✓' if n > 400 else '·'} {stem:14s} {n / 1000:5.1f}k  {title}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("action", choices=["init", "assign", "packs", "tables", "tables-v1", "figures", "bib", "pdf", "status"])
    ap.add_argument("--top", type=int, default=20, help="tables: rows per paradigm group in the appendix")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--upgrade", action="store_true", help="re-assign papers assigned before they had deep-reading notes")
    ap.add_argument("--all-notes", action="store_true", help="packs: include reading notes for incremental papers too")
    a = ap.parse_args()
    {"init": cmd_init, "assign": cmd_assign, "packs": cmd_packs, "tables": lambda a: (cmd_main_tables(a), cmd_appendix_tables(a)), "tables-v1": cmd_tables, "figures": cmd_figures, "bib": cmd_bib, "pdf": cmd_pdf, "status": cmd_status}[a.action](a)


if __name__ == "__main__":
    main()
