"""Full text for deep reading: cache → arXiv LaTeX source → arXiv PDF text → abstract.

get_text(paper) returns (text, source) where source is latex | pdf | abstract.
Texts are cached under atlases/<id>/data/fulltext/<id>.tex|.txt; extra read-only
caches (folders holding <id>/fulltext.tex or <id>.tex|.txt from another tool) are listed in the
domain file as META["fulltext_cache"] or, machine-specific, in local.json "fulltext_cache".

condense(text) strips LaTeX noise and keeps what a reader needs within a budget:
abstract / intro / method head, every table (results live there) and the tail
(experiments / conclusion).
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from engine import ctx

CACHE = ctx.DATA / "fulltext"
EXTRA = [Path(os.path.expanduser(p)) for p in [*ctx.meta("fulltext_cache", []), *ctx._local().get("fulltext_cache", [])]]
_rate = threading.Lock()
_last = [0.0]


ARXIV = "https://export.arxiv.org"  # arXiv's host for programmatic access; arxiv.org cuts long downloads


class FetchLater(Exception):
    """A transient download failure (throttling, dropped connection): try this paper again later."""


def _polite_get(url: str, timeout: int = 120) -> bytes:
    """GET with one request per 3 s across threads, resuming dropped downloads with Range requests.
    Raises FileNotFoundError on 404 and FetchLater when the download keeps failing."""
    import requests
    with _rate:
        wait = 3.2 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    buf, total = bytearray(), None
    for attempt in range(6):
        headers = dict(ctx.UA, **({"Range": f"bytes={len(buf)}-"} if buf else {}))
        try:
            with requests.get(url, headers=headers, timeout=timeout, stream=True) as r:
                if r.status_code == 404:
                    raise FileNotFoundError(url)
                if r.status_code not in (200, 206):
                    raise IOError(f"http {r.status_code}")
                if r.status_code == 200:
                    buf, total = bytearray(), int(r.headers.get("content-length") or 0) or None
                elif total is None:
                    m = re.search(r"/(\d+)$", r.headers.get("content-range", ""))
                    total = int(m.group(1)) if m else None
                for chunk in r.iter_content(1 << 16):
                    buf += chunk
            if total is None or len(buf) >= total:
                return bytes(buf)
        except FileNotFoundError:
            raise
        except Exception:
            pass
        time.sleep(5 * (attempt + 1))
    raise FetchLater(url)


def _flatten(main: Path, root: Path, depth: int = 0) -> str:
    if depth > 8:
        return ""
    txt = main.read_text(errors="ignore")

    if depth and "\\documentclass" in txt:  # a \subfile carries its own preamble: keep only its body
        m = re.search(r"\\begin\{document\}(.*?)(\\end\{document\}|$)", txt, re.S)
        txt = m.group(1) if m else txt

    def rep(m):
        d, f = m.group(1) or "", m.group(2).strip()
        f = f if f.endswith(".tex") else f + ".tex"
        for cand in (root / d / f, main.parent / d / f, root / f):
            if cand.exists() and cand.resolve() != main.resolve():
                return "\n" + _flatten(cand, root, depth + 1) + "\n"
        return ""
    # \input{f} \include{f} \subfile{f} \import{dir/}{f} \subimport{dir/}{f}
    return re.sub(r"\\(?:input|include|subfile|(?:sub)?import\{([^}]*)\})\{([^}]+)\}", rep, txt)


MIN_BODY = 6000  # chars of cleaned text below which a "full text" is really a stub
BAD_MAIN = re.compile(r"response|rebuttal|reply|suppl|supplement|appendix|cover|letter|poster|slides|review", re.I)


def choose_main(src: Path, title: str = "") -> str | None:
    """Pick the paper's main .tex in an arXiv source tree and return it flattened.

    The largest file with \\documentclass is often a rebuttal or supplement (NaVid ships
    main_response.tex next to main_submitted.tex), so candidates are scored by their
    flattened length, heavily penalised for rebuttal/supplement-like names, and rewarded
    when their \\title shares words with the paper's title."""
    tw = {w for w in re.findall(r"[a-z]{4,}", title.lower())}
    best = None
    for f in src.rglob("*.tex"):
        try:
            raw = f.read_text(errors="ignore")
        except OSError:
            continue
        if "\\documentclass" not in raw:
            continue
        flat = _flatten(f, src)
        score = len(flat)
        if BAD_MAIN.search(f.stem):
            score *= 0.15
        if "\\begin{abstract}" in flat or "\\abstract" in flat:
            score *= 1.3
        m = re.search(r"\\title\{(.{0,300}?)\}", flat, re.S)
        if m and tw:
            overlap = len(tw & set(re.findall(r"[a-z]{4,}", m.group(1).lower()))) / len(tw)
            score *= 1 + overlap
        if best is None or score > best[0]:
            best = (score, flat)
    return best[1] if best and len(best[1]) > 2000 else None


def _from_arxiv(aid: str, title: str = "", patient: bool = False) -> tuple[str, str] | None:
    """LaTeX source, else PDF text. patient=True: a dropped source download raises FetchLater
    (the caller retries the paper later) instead of settling for the PDF."""
    safe = aid.replace("/", "_")
    try:
        blob = _polite_get(f"{ARXIV}/e-print/{aid}")
    except FileNotFoundError:
        blob = b""
    except FetchLater:
        if patient:
            raise
        blob = b""
    tmp = CACHE / f".src-{safe}"
    if blob and blob[:4] != b"%PDF":
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        ok = False
        for mode in ("r:gz", "r:"):
            try:
                with tarfile.open(fileobj=io.BytesIO(blob), mode=mode) as tf:
                    tf.extractall(tmp, filter="data")
                ok = True
                break
            except Exception:  # TarError, OSError, zlib.error on a corrupt / truncated archive
                continue
        if not ok:
            try:
                raw = gzip.decompress(blob)
                (tmp / "main.tex").write_bytes(raw)
                ok = True
            except Exception:
                pass
        if ok:
            text = choose_main(tmp, title)
            if text is None:  # no \\documentclass anywhere: fall back to the largest .tex
                texs = sorted(tmp.rglob("*.tex"), key=lambda p: p.stat().st_size)
                text = _flatten(texs[-1], tmp) if texs else ""
            shutil.rmtree(tmp, ignore_errors=True)
            if len(clean(text, "latex")) >= MIN_BODY:
                return text, "latex"
        shutil.rmtree(tmp, ignore_errors=True)
    # PDF fallback
    if not shutil.which("pdftotext"):
        return None
    try:
        pdf = blob if blob[:4] == b"%PDF" else _polite_get(f"{ARXIV}/pdf/{aid}")
    except FileNotFoundError:
        return None
    except FetchLater:
        if patient:
            raise
        return None
    pf = CACHE / f".{safe}-{threading.get_ident()}.pdf"
    try:
        pf.write_bytes(pdf)
        out = subprocess.run(["pdftotext", "-layout", str(pf), "-"], capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return None
    finally:
        pf.unlink(missing_ok=True)
    return (out, "pdf") if len(out) >= MIN_BODY else None


# ── papers without an arXiv id: find an arXiv version or an open-access PDF ──
LINKS = ctx.META_DIR / "fulltext_links.json"   # {uid: {"ax": str|None, "pdf": [url, ...], "at": iso}}
_links: dict | None = None
ARXIV_ABS = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})", re.I)


def links() -> dict:
    global _links
    if _links is None:
        try:
            _links = json.loads(LINKS.read_text())
        except (OSError, ValueError):
            _links = {}
    return _links


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _arxiv_by_title(title: str) -> tuple[bool, str | None]:
    """(answered, arXiv id) — search arXiv for a preprint with (almost) the same title."""
    import difflib
    import xml.etree.ElementTree as ET
    words = [w for w in re.findall(r"[A-Za-z0-9]{4,}", title)][:8]
    if not words:
        return True, None
    q = " AND ".join(f"ti:{w}" for w in words)
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": q, "max_results": 5})
    try:
        root = ET.fromstring(_polite_get(url, timeout=60))
    except Exception:
        return False, None
    ns = {"a": "http://www.w3.org/2005/Atom", "os": "http://a9.com/-/spec/opensearch/1.1/"}
    if root.find("os:totalResults", ns) is None:  # throttled: arXiv sends an empty feed
        return False, None
    want = _norm_title(title)
    for e in root.findall("a:entry", ns):
        got = _norm_title(e.findtext("a:title", default="", namespaces=ns))
        if difflib.SequenceMatcher(None, want, got).ratio() >= 0.92:
            m = ARXIV_ABS.search(e.findtext("a:id", default="", namespaces=ns))
            if m:
                return True, re.sub(r"v\d+$", "", m.group(1))
    return True, None


def resolve_links(papers: list[dict], log=print, title_search: bool = False) -> None:
    """Find an arXiv version / open-access PDF for papers without an arXiv id; cached per step.

    Steps (each recorded as done only when its service answered, so a rate-limited step is retried
    next time): s2 = Semantic Scholar externalIds + openAccessPdf; oa = OpenAlex locations;
    ti = arXiv title search (slow: one polite request per paper, so only with title_search=True)."""
    import requests
    L = links()
    todo = [p for p in papers if not p.get("ax")]
    for p in todo:
        L.setdefault(p["id"], {"ax": None, "pdf": []})
    stamp = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add(u: str, ax: str | None = None, pdf: str | None = None) -> None:
        e = L[u]
        if ax and not e.get("ax"):
            e["ax"] = ax
            e["at"] = stamp()
        if pdf and pdf not in e.setdefault("pdf", []):
            e["pdf"].append(pdf)
            e["at"] = stamp()

    def save() -> None:
        LINKS.parent.mkdir(parents=True, exist_ok=True)
        tmp = LINKS.with_suffix(".tmp")
        tmp.write_text(json.dumps(L, indent=0))
        os.replace(tmp, LINKS)

    # 1. Semantic Scholar batch (by S2 id from the venue step, else DOI)
    s2ids = {}
    s2file = ctx.META_DIR / "s2.jsonl"
    if s2file.exists():
        for line in s2file.read_text().splitlines():
            d = json.loads(line)
            if d.get("s2id"):
                s2ids[d["uid"]] = d["s2id"]
    need = [(p["id"], s2ids.get(p["id"]) or (f"DOI:{p['doi']}" if p.get("doi") else None)) for p in todo if not L[p["id"]].get("s2")]
    need = [(u, k) for u, k in need if k]
    if need:
        log(f"full-text links: Semantic Scholar lookup for {len(need)} papers")
    for i in range(0, len(need), 400):
        chunk = need[i: i + 400]
        for attempt in range(12 if title_search else 2):  # patient only in the `--links` run
            try:
                r = requests.post("https://api.semanticscholar.org/graph/v1/paper/batch",
                                  params={"fields": "externalIds,openAccessPdf"}, json={"ids": [k for _, k in chunk]},
                                  headers=ctx.UA, timeout=120)
                if r.status_code == 200:
                    res = r.json()
                    if not isinstance(res, list) or len(res) != len(chunk):
                        raise ValueError(f"unexpected S2 reply: {str(res)[:120]}")
                    for (u, _), d in zip(chunk, res):
                        L[u]["s2"] = True
                        if d:
                            add(u, (d.get("externalIds") or {}).get("ArXiv"), (d.get("openAccessPdf") or {}).get("url"))
                    log(f"  S2: {sum(1 for d in res if d)}/{len(chunk)} found, "
                        f"{sum(1 for d in res if d and (d.get('externalIds') or {}).get('ArXiv'))} with an arXiv id")
                    break
                log(f"  S2 http {r.status_code}, retry {attempt + 1}")
            except Exception as ex:
                log(f"  S2 error {str(ex)[:120]}")
            time.sleep(min(30 * (attempt + 1), 180))
    # 2. OpenAlex locations (arXiv landing pages, repository / publisher PDFs)
    need = [(p["id"][3:], p["id"]) for p in todo if p["id"].startswith("oa:") and not L[p["id"]].get("oa")]
    oa_key = ctx._local().get("openalex_api_key", "")  # optional free key (anonymous requests share a daily budget per IP)
    out_of_budget = False
    for i in range(0, len(need), 50):
        if out_of_budget:
            break
        chunk = dict(need[i: i + 50])
        params = {"filter": "openalex:" + "|".join(chunk), "per-page": 50, "select": "id,locations,best_oa_location"}
        if ctx.CONTACT:
            params["mailto"] = ctx.CONTACT
        if oa_key:
            params["api_key"] = oa_key
        for attempt in range(5):
            try:
                r = requests.get("https://api.openalex.org/works", params=params, headers=ctx.UA, timeout=60)
                if r.status_code == 429 and "budget" in r.text.lower():  # daily budget used up: retrying today is pointless
                    log(f"  OpenAlex: daily budget used up — skipping {len(need) - i} lookups this run (they are retried next run; "
                        f"a free key in local.json as \"openalex_api_key\" avoids this)")
                    out_of_budget = True
                    break
                if r.status_code == 200:
                    for w in r.json().get("results", []):
                        u = chunk.get(w["id"].rsplit("/", 1)[-1])
                        if not u:
                            continue
                        for loc in ([w["best_oa_location"]] if w.get("best_oa_location") else []) + (w.get("locations") or []):
                            for url in (loc.get("landing_page_url"), loc.get("pdf_url")):
                                m = ARXIV_ABS.search(url or "")
                                if m:
                                    add(u, ax=m.group(1))
                            add(u, pdf=loc.get("pdf_url"))
                    for u in chunk.values():
                        L[u]["oa"] = True
                    break
            except Exception:
                pass
            time.sleep(3 * (attempt + 1))
    save()
    # 3. arXiv title search for what is still missing
    if title_search:
        need = [p for p in todo if not L[p["id"]].get("ax") and not L[p["id"]].get("ti")]
        log(f"full-text links: arXiv title search for {len(need)} papers (~{len(need) * 3.3 / 60:.0f} min)")
        for k, p in enumerate(need):
            for attempt in range(8):
                ok, ax = _arxiv_by_title(p["t"])
                if ok:
                    L[p["id"]]["ti"] = True
                    add(p["id"], ax=ax)
                    break
                time.sleep(20 * (attempt + 1))
            if k % 25 == 24:
                save()
        save()
    # 4. open-access proceedings pages (CVF: CVPR / ICCV / WACV, ECVA: ECCV) matched by title
    if title_search:
        need = [p for p in todo if not L[p["id"]].get("ax") and not L[p["id"]].get("pdf") and not L[p["id"]].get("proc")
                and _proc_venue(p)]
        if need:
            log(f"full-text links: open-access proceedings lookup for {len(need)} CVPR / ICCV / WACV / ECCV papers")
        for p in need:
            pdf = _proc_pdf(*_proc_venue(p), p["t"])
            L[p["id"]]["proc"] = True
            if pdf:
                add(p["id"], pdf=pdf)
        save()
    ent = [L[p["id"]] for p in todo]
    n_ax = sum(1 for e in ent if e.get("ax"))
    n_pdf = sum(1 for e in ent if not e.get("ax") and e.get("pdf"))
    pend = sum(1 for e in ent if not (e.get("s2") and e.get("ti")))
    log(f"full-text links for {len(todo)} papers without an arXiv id: {n_ax} arXiv versions, {n_pdf} more with an "
        f"open-access PDF, {len(todo) - n_ax - n_pdf} abstract only" + (f" ({pend} lookups still pending)" if pend else ""))


_proc_cache: dict[tuple[str, int], dict[str, str]] = {}


def _proc_venue(p: dict) -> tuple[str, int] | None:
    v = f"{p.get('vn') or ''} {p.get('v') or ''}"
    m = re.search(r"\b(CVPR|ICCV|WACV|ECCV)\b", v)
    return (m.group(1), int(p["y"])) if m and p.get("y") else None


def _proc_index(conf: str, year: int) -> dict[str, str]:
    """{normalised title: pdf url} for one CVF / ECVA proceedings volume (cached per run)."""
    import requests
    if (conf, year) in _proc_cache:
        return _proc_cache[(conf, year)]
    idx: dict[str, str] = {}
    get = lambda url: requests.get(url, headers=ctx.UA, timeout=120).text
    try:
        if conf == "ECCV":
            html = _proc_cache.setdefault(("ECCV-page", 0), {"html": get("https://www.ecva.net/papers.php")})["html"]
            for block in html.split('class="ptitle"')[1:]:
                t = re.search(r"<a href=[^>]+>\s*([^<]+?)\s*</a>", block)
                pdf = re.search(r"href='(papers/eccv_(\d{4})/[^']+?(?<!-supp)\.pdf)'", block)
                if t and pdf and int(pdf.group(2)) == year:
                    idx[_norm_title(t.group(1))] = "https://www.ecva.net/" + pdf.group(1)
        else:
            base = "https://openaccess.thecvf.com"
            pages = [get(f"{base}/{conf}{year}?day=all")]
            if 'class="ptitle"' not in pages[0]:  # older volumes are split by day
                pages = [get(f"{base}/{conf}{year}?day={d}") for d in sorted(set(re.findall(r"\?day=([\d-]+)", get(f"{base}/{conf}{year}"))))]
            for html in pages:
                for href, t in re.findall(r'<dt class="ptitle"><br><a href="([^"]+)">([^<]+)</a>', html):
                    idx[_norm_title(t)] = base + href.replace("/html/", "/papers/").replace(".html", ".pdf")
    except Exception:
        pass
    _proc_cache[(conf, year)] = idx
    return idx


def _proc_pdf(conf: str, year: int, title: str) -> str | None:
    import difflib
    idx = _proc_index(conf, year)
    want = _norm_title(title)
    if want in idx:
        return idx[want]
    best = difflib.get_close_matches(want, list(idx), n=1, cutoff=0.92)
    return idx[best[0]] if best else None


def _from_pdf_urls(urls: list[str]) -> str | None:
    import requests
    for url in urls:
        if "arxiv.org" in url:
            continue
        try:
            r = requests.get(url, headers={**ctx.UA, "Accept": "application/pdf"}, timeout=90)
            if r.status_code != 200 or r.content[:4] != b"%PDF":
                continue
            pf = CACHE / f".dl-{threading.get_ident()}.pdf"
            pf.write_bytes(r.content)
            out = subprocess.run(["pdftotext", "-layout", str(pf), "-"], capture_output=True, text=True, timeout=120).stdout
            pf.unlink(missing_ok=True)
            if len(out) >= MIN_BODY:
                return out
        except Exception:
            continue
    return None


def get_text(p: dict, patient: bool = False) -> tuple[str, str]:
    link = links().get(p["id"], {})
    aid = p.get("ax") or link.get("ax")
    safe = p["id"].replace("/", "_").replace(":", "_")
    for name in ((aid,) if aid else ()) + (safe,):
        for ext, src in ((".tex", "latex"), (".txt", "pdf")):
            f = CACHE / f"{name}{ext}"
            if f.exists():
                t = f.read_text(errors="ignore")
                if len(clean(t, src)) >= MIN_BODY:
                    return t, src
                f.unlink()  # a stub (e.g. unresolved includes): fetch again
    from engine import shared
    for sib in shared.fulltext_dirs():  # another atlas already downloaded this paper
        for name in ((aid,) if aid else ()) + (safe,):
            for ext, src in ((".tex", "latex"), (".txt", "pdf")):
                f = sib / f"{name}{ext}"
                if f.exists():
                    t = f.read_text(errors="ignore")
                    if len(clean(t, src)) >= MIN_BODY:
                        return t, src
    CACHE.mkdir(parents=True, exist_ok=True)
    if aid:
        for root in EXTRA:
            if (root / aid / "src").is_dir():  # re-select the main file from the original source tree
                text = choose_main(root / aid / "src", p.get("t", ""))
                if text and len(clean(text, "latex")) >= MIN_BODY:
                    (CACHE / f"{aid}.tex").write_text(text)
                    return text, "latex"
            f = root / aid / "fulltext.txt"
            if f.exists() and f.stat().st_size > MIN_BODY:
                return f.read_text(errors="ignore"), "pdf"
        got = _from_arxiv(aid, p.get("t", ""), patient)
        if got:
            text, src = got
            (CACHE / f"{aid}{'.tex' if src == 'latex' else '.txt'}").write_text(text)
            return text, src
    if link.get("pdf") and shutil.which("pdftotext"):
        text = _from_pdf_urls(link["pdf"])
        if text:
            (CACHE / f"{safe}.txt").write_text(text)
            return text, "pdf"
    return f"Title: {p['t']}\n\nAbstract: {p.get('ab') or ''}", "abstract"


TABLE_RE = re.compile(r"\\begin\{(table\*?|tabular\*?|tabularx)\}.*?\\end\{\1\}", re.S)


def clean(text: str, source: str) -> str:
    """Remove noise but no content: LaTeX comments, preamble, bibliography, graphics commands."""
    if source != "latex":
        return text
    t = re.sub(r"(?<!\\)%.*", "", text)
    m = re.search(r"\\begin\{document\}", t)
    t = t[m.end():] if m else t
    t = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}|\\bibliography\{[^}]*\}|\\bibliographystyle\{[^}]*\}", "", t, flags=re.S)
    t = re.sub(r"\\includegraphics(\[[^\]]*\])?\{[^}]*\}", "", t)
    return re.sub(r"\n\s*\n+", "\n\n", t)


def condense(text: str, source: str, budget: int = 42000) -> str:
    if source == "latex":
        t = re.sub(r"(?<!\\)%.*", "", text)                                   # comments
        t = re.sub(r"\\begin\{figure\*?\}.*?\\end\{figure\*?\}", "", t, flags=re.S)
        t = re.sub(r"\\bibliography\{[^}]*\}|\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", t, flags=re.S)
        t = re.sub(r"\\(usepackage|newcommand|renewcommand|definecolor|def)\b[^\n]*", "", t)
        m = re.search(r"\\begin\{document\}", t)
        t = t[m.end():] if m else t
        t = re.sub(r"\n\s*\n+", "\n\n", t)
    else:
        t = re.sub(r"[ \t]+", " ", text)
        t = re.sub(r"\n\s*\n+", "\n\n", t)
        cut = re.search(r"\n\s*(References|REFERENCES|Bibliography)\s*\n", t)
        t = t[: cut.start()] if cut and cut.start() > len(t) * 0.5 else t
    if len(t) <= budget:
        return t
    head = t[: int(budget * 0.4)]
    tables = "\n\n".join(m.group(0) for m in TABLE_RE.finditer(t))[: int(budget * 0.35)] if source == "latex" else ""
    rest = budget - len(head) - len(tables)
    tail = t[-rest:] if rest > 0 else ""
    return f"{head}\n\n[… middle omitted …]\n\n=== TABLES ===\n{tables}\n\n=== LATER SECTIONS ===\n{tail}"
