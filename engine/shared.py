"""Share what one atlas has already done for a paper with every other atlas that contains the same paper.

  ./atlas share <id>        copy deep-reading notes from sibling atlases (also runs inside `read` and `build`)

A paper is the same paper when its uid matches (arXiv id or OpenAlex id), or when its arXiv id / DOI matches a
sibling's paper. Three things are shared:
  - deep-reading notes: copied into this atlas's data/reading/ with "shared_from": <atlas id>, so each atlas stays
    self-contained (snapshots, website) and shows the notes at once. An atlas with its own reading prompt
    (atlases/<id>/read_system.md) still reads the paper itself later and replaces the copy; one without keeps it;
  - full texts: fulltext.get_text() also looks in the siblings' data/fulltext/ caches (read-only);
  - marks typed on the website (star, status, note): the hub writes them to every atlas that contains the paper.
A note read in this atlas is never overwritten by a sibling's copy, and a newer READ version wins over an older one.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from engine import ctx

ROOT = ctx.ADIR.parent  # atlases/


def safe(uid: str) -> str:
    return uid.replace("/", "_").replace(":", "_")


def siblings() -> list[Path]:
    return sorted(d for d in ROOT.iterdir() if d.is_dir() and d.name != ctx.ATLAS_ID and (d / "atlas.py").exists())


def _index(atlas: Path) -> dict[str, str]:
    """{arXiv id | doi | uid: uid} for a sibling atlas (from its built papers.json)."""
    f = atlas / "public" / "papers.json"
    if not f.exists():
        return {}
    idx = {}
    for p in json.loads(f.read_text()):
        idx[p["id"]] = p["id"]
        if p.get("ax"):
            idx[f"ax:{p['ax']}"] = p["id"]
        if p.get("doi"):
            idx[f"doi:{str(p['doi']).lower()}"] = p["id"]
    return idx


def fulltext_dirs() -> list[Path]:
    return [d / "data" / "fulltext" for d in siblings() if (d / "data" / "fulltext").is_dir()]


def sync_readings(papers: list[dict], log=print) -> int:
    """Copy notes for `papers` (this atlas's records) from sibling atlases; returns the number copied."""
    dest = ctx.READING
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for sib in siblings():
        src_dir = sib / "data" / "reading"
        if not src_dir.is_dir():
            continue
        idx = _index(sib)
        for p in papers:
            mine = dest / f"{safe(p['id'])}.json"
            other_uid = idx.get(p["id"]) or (idx.get(f"ax:{p['ax']}") if p.get("ax") else None) \
                or (idx.get(f"doi:{str(p['doi']).lower()}") if p.get("doi") else None)
            if not other_uid:
                continue
            src = src_dir / f"{safe(other_uid)}.json"
            if not src.exists():
                continue
            if mine.exists():
                try:
                    cur = json.loads(mine.read_text())
                    new = json.loads(src.read_text())
                except ValueError:
                    continue
                # keep our own reading; refresh only a copy that a sibling has since re-read with a newer version
                if not cur.get("shared_from") or cur.get("version", "") >= new.get("version", "") and cur.get("read_at", "") >= new.get("read_at", ""):
                    continue
            try:
                data = json.loads(src.read_text())
            except ValueError:
                continue
            data.setdefault("shared_from", sib.name)  # a copy of a copy keeps the original source
            tmp = mine.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            tmp.replace(mine)
            n += 1
    if n:
        log(f"shared notes: {n} papers' deep-reading notes copied from other atlases")
    return n


def main() -> None:
    papers = json.loads((ctx.PUBLIC / "papers.json").read_text())
    sync_readings(papers)


if __name__ == "__main__":
    main()
