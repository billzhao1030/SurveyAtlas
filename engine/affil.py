"""Industry authorship: which companies sign a paper (Google, Meta, NVIDIA, ByteDance, Alibaba/Qwen, Xiaomi, ...).

  ./atlas affil <id>              detect from the cached full text (author block + e-mails); no network
  ./atlas affil <id> --openalex   also ask OpenAlex (institution type "company") for papers without a cached text

A paper is marked when a company appears in its author block: the text before the abstract (LaTeX comments,
the title and template boilerplate removed), the \\author / \\affiliation / \\thanks commands, or an author
e-mail on a company domain. Results are cached in data/meta/affil.json ({id: {"co": [...], "src": ...}});
`build` copies them to papers.json as "co" and the hub shows an industry badge. Hand corrections go to
overrides.json as {"<id>": {"co": ["Google"]}} (an empty list clears a false positive).
"""
from __future__ import annotations

import argparse
import json
import re
import time

from engine import ctx

CACHE = ctx.META_DIR / "affil.json"

# display name -> patterns matched in the author block (case-sensitive unless the pattern says otherwise)
COMPANIES: dict[str, list[str]] = {
    "Google": [r"\bGoogle\b", r"\bDeepMind\b", r"\bWaymo\b", r"@google\.com", r"@deepmind\.com"],
    "Meta": [r"\bMeta AI\b", r"\bMeta FAIR\b", r"\bMeta Reality Labs\b", r"\bReality Labs\b", r"\bMeta Platforms\b",
             r"\bFacebook\b", r"\bFAIR\b", r"@fb\.com", r"@meta\.com", r"\bMeta\s*(?:\\\\|,|\}|$)"],
    "Microsoft": [r"\bMicrosoft\b", r"\bMSRA\b", r"@microsoft\.com"],
    "NVIDIA": [r"\bNVIDIA\b", r"\bNvidia\b", r"@nvidia\.com"],
    "Amazon": [r"\bAmazon\b", r"\bAWS AI\b", r"@amazon\.com"],
    "Apple": [r"\bApple Inc\b", r"\bApple\s*(?:\\\\|,|\}|$)", r"@apple\.com"],
    "IBM": [r"\bIBM\b"],
    "Intel": [r"\bIntel Labs\b", r"\bIntel Corporation\b", r"\bIntel\s*(?:\\\\|,|\}|$)", r"@intel\.com"],
    "Adobe": [r"\bAdobe\b"],
    "Salesforce": [r"\bSalesforce\b"],
    "OpenAI": [r"\bOpenAI\b(?![- ]?(?:compatible|API|model))"],
    "Samsung": [r"\bSamsung\b"],
    "Sony": [r"\bSony\b"],
    "Qualcomm": [r"\bQualcomm\b"],
    "Honda Research Institute": [r"\bHonda Research\b"],
    "Toyota": [r"\bToyota Research Institute\b", r"\bToyota Motor\b", r"\bToyota InfoTech\b", r"\bToyota Central R", r"\bWoven\b"],
    "Bosch": [r"\bBosch\b"],
    "Hyundai": [r"\bHyundai\b"],
    "MERL": [r"\bMitsubishi Electric\b", r"\bMERL\b"],
    "NAVER": [r"\bNAVER\b", r"\bNaver Labs\b"],
    "LG AI Research": [r"\bLG AI\b", r"\bLG Electronics\b"],
    "Disney Research": [r"\bDisney Research\b"],
    "Boston Dynamics": [r"\bBoston Dynamics\b"],
    "Hugging Face": [r"\bHugging ?Face\b(?! (?:model|hub|transformers))"],
    "ByteDance": [r"\bByte[Dd]ance\b", r"\bTikTok\b", r"\bDouyin\b", r"@bytedance\.com"],
    "Alibaba": [r"\bAlibaba\b", r"\bDAMO Academy\b", r"\bQwen Team\b", r"\bTongyi\b", r"\bAnt Group\b", r"\bAMAP\b",
                r"\bAmap\b", r"@alibaba-inc\.com", r"@antgroup\.com"],
    "Tencent": [r"\bTencent\b", r"@tencent\.com"],
    "Baidu": [r"\bBaidu\b", r"@baidu\.com"],
    "Huawei": [r"\bHuawei\b", r"\bNoah'?s Ark\b", r"@huawei\.com"],
    "Xiaomi": [r"\bXiaomi\b", r"@xiaomi\.com"],
    "SenseTime": [r"\bSense[Tt]ime\b", r"@sensetime\.com"],
    "Megvii": [r"\bMegvii\b", r"\bMEGVII\b", r"\bFace\+\+"],
    "JD": [r"\bJD Explore\b", r"\bJD\.com\b", r"\bJD Research\b"],
    "Meituan": [r"\bMeituan\b"],
    "Kuaishou": [r"\bKuaishou\b", r"\bKwai\b"],
    "DiDi": [r"\bDiDi\b", r"\bDidi Chuxing\b"],
    "Lenovo": [r"\bLenovo\b"],
    "OPPO": [r"\bOPPO\b"],
    "vivo": [r"\bvivo (?:AI|Mobile|Communication)\b"],
    "Horizon Robotics": [r"\bHorizon Robotics\b"],
    "Unitree": [r"\bUnitree\b(?! (?:Go|H1|G1|B2|A1))"],
    "AgiBot": [r"\bAgi[Bb]ot\b", r"\bZhiyuan Robotics\b"],
    "Galbot": [r"\bGalbot\b", r"@galbot\.com"],
    "Li Auto": [r"\bLi Auto\b"],
    "XPeng": [r"\bXPeng\b", r"\bXpeng\b"],
    "NIO": [r"\bNIO Inc\b"],
    "Geely": [r"\bGeely\b"],
    "iFlytek": [r"\biFLYTEK\b", r"\biFlytek\b"],
    "Zhipu AI": [r"\bZhipu\b"],
    "DeepSeek": [r"\bDeepSeek-AI\b", r"\bDeepSeek AI\b"],
    "China Mobile": [r"\bChina Mobile\b"],
    "China Telecom": [r"\bChina Telecom\b", r"\bTeleAI\b"],
    "Midea": [r"\bMidea\b"],
    "Hikvision": [r"\bHikvision\b"],
    "UBTech": [r"\bUBTECH\b", r"\bUBTech\b"],
    "Ping An": [r"\bPing An\b"],
    "Hon Hai (Foxconn)": [r"\bFoxconn\b", r"\bHon Hai\b"],
    "Uber": [r"\bUber ATG\b", r"\bUber AI\b"],
}
_COMPILED = {name: [re.compile(p, re.M) for p in pats] for name, pats in COMPANIES.items()}
# a company named only in these contexts is not an affiliation
_NOT_AFFIL = re.compile(r"(?:funded|supported|sponsored|gift|grant|donat|award|fellowship|compute credits?|cloud credits?)"
                        r"[^.\n]{0,80}$", re.I)


def _strip_comments(t: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", t)


def _brace_arg(t: str, i: int) -> str:
    """The balanced {...} argument starting at t[i] == '{'."""
    depth, j = 0, i
    while j < len(t):
        if t[j] == "{":
            depth += 1
        elif t[j] == "}":
            depth -= 1
            if depth == 0:
                return t[i + 1: j]
        j += 1
    return t[i + 1: i + 3000]


def author_block(text: str, source: str) -> str:
    """The part of a paper that names its authors' institutions."""
    if source == "latex":
        t = _strip_comments(text)
        parts = []
        for m in re.finditer(r"\\(author|affiliation|affil|institute|institution|thanks|blfootnote|icmlaffiliation|"
                             r"authorblockA|IEEEauthorblockA|address|email|correspondingauthor)\*?\s*(\[[^\]]*\])?\s*\{", t):
            parts.append(_brace_arg(t, m.end() - 1)[:3000])
        a = t.find("\\begin{abstract}")
        b = t.find("\\begin{document}")
        if a > 0:
            head = t[max(b if 0 <= b < a else 0, a - 4000): a]
            head = re.sub(r"\\title\s*(\[[^\]]*\])?\s*\{", "\\title{", head)
            i = head.find("\\title{")
            if i >= 0:
                head = head[:i] + head[i + len(_brace_arg(head, i + 6)) + 8:]
            parts.append(head)
        return "\n".join(parts)
    t = text[:6000]
    m = re.search(r"\n\s*(Abstract|ABSTRACT|A B S T R A C T)\b", t)
    head = t[: m.start()] if m else t[:2500]
    return head.split("\n", 2)[-1] if head.count("\n") > 2 else head  # drop the title line(s)


def detect(block: str) -> list[str]:
    found = []
    for name, pats in _COMPILED.items():
        for rx in pats:
            for m in rx.finditer(block):
                if _NOT_AFFIL.search(block[max(0, m.start() - 120): m.start()]):
                    continue
                found.append(name)
                break
            if name in found:
                break
    return found


def load() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def save(cache: dict) -> None:
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True))
    tmp.replace(CACHE)


def from_openalex(papers: list[dict], cache: dict) -> int:
    """Companies among the authors' institutions (OpenAlex institution type 'company'); stops when the budget is gone."""
    import requests
    n = 0
    todo = [p for p in papers if cache.get(p["id"], {}).get("src") in (None, "none")]
    for i in range(0, len(todo), 40):
        chunk = todo[i: i + 40]
        dois = [f"10.48550/arxiv.{p['ax']}" for p in chunk if p.get("ax")] + [p["doi"] for p in chunk if p.get("doi")]
        if not dois:
            continue
        r = requests.get("https://api.openalex.org/works", timeout=60, headers=ctx.UA,
                         params={"filter": "doi:" + "|".join(dois), "select": "doi,authorships", "per-page": 100,
                                 **({"mailto": ctx.meta("mailto")} if ctx.meta("mailto") else {})})
        if r.status_code == 429:
            print("  OpenAlex budget exhausted; stopping", flush=True)
            break
        if r.status_code != 200:
            continue
        by_doi = {}
        for w in r.json().get("results", []):
            cos = sorted({inst["display_name"] for a in w.get("authorships", []) for inst in a.get("institutions", [])
                          if inst.get("type") == "company"})
            by_doi[(w.get("doi") or "").lower().replace("https://doi.org/", "")] = cos
        for p in chunk:
            key = (f"10.48550/arxiv.{p['ax']}" if p.get("ax") else (p.get("doi") or "")).lower()
            if key in by_doi:
                named = detect("\n".join(by_doi[key])) or by_doi[key]
                cache[p["id"]] = {"co": named, "src": "openalex"}
                n += 1
        time.sleep(0.2)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--openalex", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    from engine import fulltext
    papers = json.loads((ctx.PUBLIC / "papers.json").read_text())
    cache = {} if a.force else load()
    n_text = 0
    for p in papers:
        if not a.force and cache.get(p["id"], {}).get("src") in ("latex", "pdf", "openalex"):
            continue
        aid = p.get("ax") or fulltext.links().get(p["id"], {}).get("ax")
        safe = p["id"].replace("/", "_").replace(":", "_")
        text, src = None, "none"
        for name in ((aid,) if aid else ()) + (safe,):
            for ext, s in ((".tex", "latex"), (".txt", "pdf")):
                f = fulltext.CACHE / f"{name}{ext}"
                if f.exists():
                    text, src = f.read_text(errors="ignore"), s
                    break
            if text:
                break
        cache[p["id"]] = {"co": detect(author_block(text, src)) if text else [], "src": src}
        n_text += text is not None
    print(f"affil: {n_text} papers checked from cached full text", flush=True)
    if a.openalex:
        print(f"affil: {from_openalex(papers, cache)} papers from OpenAlex", flush=True)
    save(cache)
    marked = sum(bool(v["co"]) for v in cache.values())
    print(f"affil: {marked} of {len(cache)} papers have an industry author", flush=True)


if __name__ == "__main__":
    main()
