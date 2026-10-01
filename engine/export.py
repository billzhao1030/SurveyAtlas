"""Export an atlas as a static, read-only website (GitHub Pages, Netlify, any web server, or a folder).

  ./atlas export <id> <dir> [--project-page] [--keep-surveys] [--keep-native-summaries]
  ATLAS=<id> python3 -m engine.export <dir>

The copy has the same pages as the hub (Library, Map, Timeline, Benchmarks, Surveys, paper pages) built from
atlases/<id>/public/ and the deep-reading notes. What needs the hub is turned off: editing, jobs and backups;
stars and notes stay in the visitor's browser; "Ask" falls back to the best-matching papers.
Survey projects (LaTeX workspaces) are left out unless --keep-surveys; the live survey outlines stay.
--project-page puts the landing page (docs/project + docs/img) at <dir> and the atlas at <dir>/<id>/.
Summaries written in Chinese by older reading prompts (summary_zh) are dropped unless --keep-native-summaries.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from engine import ctx

NATIVE = ("summary_zh",)


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def overview(papers: list[dict], meta: dict, pub: Path) -> dict:
    """Compact summary for a landing page: headline numbers, papers per year by paradigm, and one point per paper
    (time, paradigm, citations, name) for a timeline visualization."""
    tax = json.loads((pub / "taxonomy.json").read_text("utf-8"))
    stats = json.loads((pub / "stats.json").read_text("utf-8"))
    board = json.loads((pub / "leaderboard.json").read_text("utf-8")) if (pub / "leaderboard.json").exists() else []
    pds = [x["code"] for x in tax["paradigms"]]
    order = [c for c in pds if c != "none"] + (["none"] if "none" in pds else [])
    pi = {c: i for i, c in enumerate(order)}
    keep = [p for p in papers if p.get("sc") in ("core", "adjacent") and (p.get("y") or 0) >= 2010]
    pts = []
    for p in sorted(keep, key=lambda p: p.get("d") or str(p.get("y"))):
        d = str(p.get("d") or "")
        t = p["y"] + ((int(d[5:7]) - 1) / 12 if len(d) >= 7 and d[5:7].isdigit() else 0.5)
        pts.append([round(t, 2), pi.get(p.get("pd"), len(order) - 1), p.get("c") or 0, p.get("n") or "", p["id"]])
    years: dict = {}
    for p in papers:
        if p.get("sc") == "core" and (p.get("y") or 0) >= 2010:
            years.setdefault(str(p["y"]), [0] * len(order))[pi.get(p.get("pd"), len(order) - 1)] += 1
    bench: dict = {}
    for p in papers:
        if p.get("sc") == "core":
            for b in p.get("bm") or []:
                if not str(b).startswith("other:"):
                    bench[b] = bench.get(b, 0) + 1
    f = stats.get("funnel", {})
    return {
        "atlas": ctx.ATLAS_ID, "title": meta.get("title"), "subtitle": meta.get("subtitle"), "field": meta.get("field"),
        "built_at": stats.get("built_at"),
        "stats": {"papers": len(papers), "core": sum(p.get("sc") == "core" for p in papers),
                  "read": sum(1 for p in papers if p.get("r")), "results": len(board),
                  "verified": round(sum(1 for r in board if r.get("ok")) / max(1, len(board)), 3),
                  "with_venue": sum(1 for p in papers if p.get("vn")),
                  "authors": len({a for p in papers for a in p.get("a") or []}),
                  "harvested": f.get("harvested"), "classified": (f.get("harvested") or 0) - (f.get("prefilter_drop") or 0)},
        "paradigms": [{"code": c, "label": next(x["label"] for x in tax["paradigms"] if x["code"] == c)} for c in order],
        "years": years,
        "benchmarks": sorted(bench.items(), key=lambda x: -x[1])[:12],
        "points": pts,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="output folder (created; its previous contents are replaced)")
    ap.add_argument("--project-page", action="store_true", help="landing page at <dir>, the atlas at <dir>/<id>/")
    ap.add_argument("--keep-surveys", action="store_true", help="include survey projects (outline + assignments)")
    ap.add_argument("--keep-native-summaries", action="store_true", help="keep non-English summary fields")
    a = ap.parse_args()

    pub = ctx.PUBLIC
    if not (pub / "papers.json").exists():
        raise SystemExit(f"atlas '{ctx.ATLAS_ID}' is not built yet: ./atlas build {ctx.ATLAS_ID}")
    site = Path(a.out).expanduser().resolve()
    if site == ctx.REPO or ctx.REPO.is_relative_to(site) or (site / ".git").exists():
        raise SystemExit("refusing to export over a repository: give a folder inside it")
    out = site / ctx.ATLAS_ID if a.project_page else site
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(ctx.REPO / "hub" / "site", out, ignore=shutil.ignore_patterns("login.html"))
    (out / ".nojekyll").write_text("")  # GitHub Pages: serve files as they are

    # hub API answers, frozen (api/atlases is meta.json without the UI block, as hub/serve.py serves it)
    meta = json.loads((pub / "meta.json").read_text("utf-8"))
    papers = json.loads((pub / "papers.json").read_text("utf-8"))
    entry = {k: v for k, v in meta.items() if k != "ui"}
    entry.update(id=ctx.ATLAS_ID, built=True)
    _dump(out / "api" / "atlases", [entry])
    settings = json.loads((ctx.REPO / "settings.json").read_text("utf-8")) if (ctx.REPO / "settings.json").exists() else {}
    _dump(out / "api" / "settings", {**settings, "default_atlas": ctx.ATLAS_ID})
    _dump(out / "api" / "whoami", {"admin": False, "local": False, "gate": False, "on_hub": False, "ask": False, "static": True})
    _dump(out / "api" / "jobs", [])
    if (ctx.REPO / "docs" / "GUIDE.md").exists():
        shutil.copy(ctx.REPO / "docs" / "GUIDE.md", out / "guide.md")

    # atlas data
    dst = out / "a" / ctx.ATLAS_ID
    dst.mkdir(parents=True)
    if not a.keep_native_summaries:
        for p in papers:
            p.pop("zh", None)
    _dump(dst / "papers.json", papers)
    for name in ("taxonomy.json", "stats.json", "meta.json", "leaderboard.json", "excluded.json", "atlas.bib"):
        if (pub / name).exists():
            shutil.copy(pub / name, dst / name)
    surveys = json.loads((pub / "surveys.json").read_text("utf-8")) if (pub / "surveys.json").exists() else []
    _dump(dst / "surveys.json", surveys if a.keep_surveys else [])

    _dump(dst / "overview.json", overview(papers, meta, pub))

    n = 0
    if ctx.READING.is_dir():
        rd = dst / "reading"
        rd.mkdir()
        for f in sorted(ctx.READING.glob("*.json")):
            try:
                note = json.loads(f.read_text("utf-8"))
            except ValueError:
                continue
            if not a.keep_native_summaries:
                for k in NATIVE:
                    note.pop(k, None)
            _dump(rd / f.name, note)
            n += 1
    if a.project_page:
        shutil.copytree(ctx.REPO / "docs" / "project", site, dirs_exist_ok=True)
        shutil.copytree(ctx.REPO / "docs" / "img", site / "img", dirs_exist_ok=True)
        (site / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in site.rglob("*") if f.is_file()) / 1e6
    print(f"static site → {site} ({size:.0f} MB, {len(papers)} papers, {n} reading notes). "
          f"Preview: python3 -m http.server -d {site} 8000")


if __name__ == "__main__":
    main()
