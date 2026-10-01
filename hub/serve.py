"""SurveyAtlas hub — one web server for every atlas in atlases/ (stdlib only).

  ./atlas serve [--port 8668] [--host 0.0.0.0]        (or: python3 hub/serve.py; binds 127.0.0.1 unless told otherwise)

Routes
  /                          the single-page app (hub/site)
  /api/atlases               [{id, title, subtitle, n_core, built_at, by_year, …}] from atlases/*/public/meta.json
  /api/settings   GET/POST   {default_atlas, theme, background, bg_custom, accent, density} → settings.json
  /api/marks?atlas=<id>      GET/POST {uid, star?, status?, note?} → atlases/<id>/data/marks.json
  /a/<id>/<file>             atlases/<id>/public/{papers,excluded,taxonomy,stats,meta}.json | atlas.bib

Admin — from this machine (loopback / its own IPs) automatically; from any other device with the
admin key (local.json → admin_key; `./atlas admin-key` prints it and a one-time unlock link), sent
as the X-Atlas-Key header. ATLAS_ADMIN_LAN=1 opens admin to everyone on the network.
  GET  /api/whoami                      {admin}
  POST /api/atlases                     {id, title, subtitle?, field?, description?, brief?}  → ./atlas new
  POST /api/atlases/<id>/meta           {title?, subtitle?, description?, new_id?}            → ./atlas meta / rename
  POST /api/atlases/<id>/jobs           {kind: update|update-full|build|draft|check|ready|recall, brief?}
  POST /api/atlases/<id>/delete         discard an atlas that has no data yet (→ ./atlas delete)
  GET  /guide.md                        docs/GUIDE.md, rendered by the site's Guide page
  POST /api/backup                      → ./atlas backup (snapshot all + commit + push)
  GET  /api/jobs                        recent background jobs with log tails
  POST /api/ask                         {atlas, q} → answer from the atlas via headless Claude (engine/ask.py);
                                        from other devices only when ATLAS_ASK_OPEN=1 or with the admin key
Settings writes are admin-only too; other viewers keep their look in their own browser.

Site password — every request from another machine needs a session cookie obtained at /login with the site
password (initially 0000; stored as a salted PBKDF2 hash in the gitignored local.json). This machine is never
asked. Change it from Settings (POST /api/password {current, new}) or with `./atlas password`; a change signs
every other device out. ATLAS_OPEN=1 disables the gate.
  GET/POST /login, GET /logout
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import secrets
import socket
import subprocess
import sys
import time
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "hub" / "site"
ATLASES = REPO / "atlases"
SETTINGS = REPO / "settings.json"
LOCK = threading.Lock()
CLI = REPO / "atlas"
JOB_DIR = REPO / "hub" / "jobs"
JOBS: dict[str, dict] = {}
JOB_KINDS = {
    "update": lambda a, x: ["update", a], "update-full": lambda a, x: ["update", a, "--full"],
    "build": lambda a, x: ["build", a], "check": lambda a, x: ["check", a], "ready": lambda a, x: ["ready", a],
    "recall": lambda a, x: ["recall", a], "draft": lambda a, x: ["draft", a, "--brief-file", x],
}
ADMIN_LAN = os.environ.get("ATLAS_ADMIN_LAN") == "1"
ASK_OPEN = os.environ.get("ATLAS_ASK_OPEN") == "1"  # let every viewer ask (each question spends Claude usage)
ASK_SEM = threading.Semaphore(2)
sys.path.insert(0, str(REPO))
from engine import ask as ASK  # noqa: E402  (stdlib-only module; no ATLAS env needed)


def own_ips() -> set[str]:
    ips = {"127.0.0.1", "::1"}
    try:
        ips |= set(subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        ips.add(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    return ips


OWN_IPS = own_ips()


def admin_key() -> str:
    """Per-machine admin key in the gitignored local.json (created on first use)."""
    f = REPO / "local.json"
    try:
        cfg = json.loads(f.read_text())
    except (OSError, ValueError):
        cfg = {}
    if not cfg.get("admin_key"):
        cfg["admin_key"] = secrets.token_urlsafe(12)
        f.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg["admin_key"]


ADMIN_KEY = admin_key()

# ── site password (gate for every device except this machine) ──
GATE = os.environ.get("ATLAS_OPEN") != "1"
PW_ITER = 240_000
SESSION_DAYS = 30
FAILS: dict[str, list[float]] = {}
LOGIN_HTML = (REPO / "hub" / "site" / "login.html")


def _local_cfg() -> dict:
    try:
        return json.loads((REPO / "local.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_local_cfg(cfg: dict) -> None:
    f = REPO / "local.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(f)


def _pw_hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), PW_ITER).hex()


def set_site_password(pw: str) -> None:
    with LOCK:
        cfg = _local_cfg()
        salt = secrets.token_hex(16)
        cfg["site_password"] = {"salt": salt, "hash": _pw_hash(pw, salt), "iter": PW_ITER,
                                "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        cfg.setdefault("session_secret", secrets.token_hex(32))
        _save_local_cfg(cfg)


def site_password() -> dict:
    cfg = _local_cfg()
    if not cfg.get("site_password") or not cfg.get("session_secret"):
        set_site_password(cfg.get("_initial_password", "0000"))  # first run: the initial password is 0000
        cfg = _local_cfg()
    return cfg


def check_site_password(pw: str) -> bool:
    sp = site_password()["site_password"]
    got = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(sp["salt"]), int(sp.get("iter", PW_ITER))).hex()
    return hmac.compare_digest(got, sp["hash"])


def _session_sig(exp: int, cfg: dict) -> str:
    msg = f"{exp}|{cfg['site_password']['hash']}".encode()  # bound to the password: changing it signs everyone out
    return hmac.new(bytes.fromhex(cfg["session_secret"]), msg, hashlib.sha256).hexdigest()[:40]


def new_session() -> str:
    cfg = site_password()
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_session_sig(exp, cfg)}"


def session_ok(tok: str) -> bool:
    try:
        exp_s, sig = tok.split(".", 1)
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sig, _session_sig(exp, site_password()))


def too_many_fails(ip: str) -> bool:
    now = time.time()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 600]
    return len(FAILS[ip]) >= 8


site_password()  # create the initial password on first start


def start_job(kind: str, atlas: str, args: list[str]) -> dict:
    """Run `./atlas <args>` in the background; one running job per atlas (and one backup)."""
    with LOCK:
        for j in JOBS.values():
            if j["rc"] is None and j["atlas"] == atlas:
                return {"error": f"a {j['kind']} job is already running for {atlas or 'the hub'}"}
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        jid = f"{datetime.now():%Y%m%d-%H%M%S}-{kind}-{atlas or 'hub'}"
        log = JOB_DIR / f"{jid}.log"
        env = {**os.environ, "PATH": os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")]), "PYTHONUNBUFFERED": "1"}
        fh = open(log, "w")
        proc = subprocess.Popen([sys.executable, str(CLI), *args], cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        job = {"id": jid, "kind": kind, "atlas": atlas, "cmd": "./atlas " + " ".join(args), "started": time.time(), "ended": None, "rc": None, "log": str(log)}
        JOBS[jid] = job

    def wait():
        rc = proc.wait()
        fh.close()
        job["rc"], job["ended"] = rc, time.time()
    threading.Thread(target=wait, daemon=True).start()
    return job


def job_view(j: dict) -> dict:
    try:
        tail = Path(j["log"]).read_text("utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        tail = []
    return {k: j[k] for k in ("id", "kind", "atlas", "cmd", "started", "ended", "rc")} | {"tail": tail}


def run_cli(*args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(CLI), *args], cwd=REPO, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip()

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
UID_RE = re.compile(r"^[A-Za-z0-9.:/_\-]{3,64}$")
PUBLIC_FILES = {"papers.json", "excluded.json", "taxonomy.json", "stats.json", "meta.json", "atlas.bib", "leaderboard.json", "surveys.json"}
SURVEY_FILE_RE = re.compile(r"^(?:[a-z0-9_-]{1,20}/)?(main\.tex|main\.pdf|refs\.bib|sections/[A-Za-z0-9._-]+\.tex|packs/[A-Za-z0-9._-]+\.md)$")
STATUSES = {"", "queued", "reading", "read", "skip"}
GZIP_TYPES = (".json", ".js", ".css", ".html", ".svg", ".bib", ".md")
SETTING_CHOICES = {
    "theme": {"auto", "light", "dark"},
    "background": {"paper", "white", "mist", "sand", "grid", "dots", "aurora", "custom"},
    "accent": {"indigo", "teal", "violet", "rose", "amber", "graphite"},
    "density": {"cards", "compact"},
}
DEFAULT_SETTINGS = {"default_atlas": "", "theme": "light", "background": "paper", "bg_custom": "#f4f1ea", "accent": "indigo", "density": "cards"}


def atlas_ids() -> list[str]:
    return sorted(p.name for p in ATLASES.iterdir() if p.is_dir() and ID_RE.match(p.name) and (p / "atlas.py").exists())


def atlas_list() -> list[dict]:
    out = []
    for i in atlas_ids():
        f = ATLASES / i / "public" / "meta.json"
        try:
            m = json.loads(f.read_text("utf-8"))
            m.pop("ui", None)
            m["built"] = True
        except (OSError, ValueError):
            # not built yet: read title/subtitle from the domain file as text (never import it here)
            src = (ATLASES / i / "atlas.py").read_text("utf-8", errors="replace")
            grab = lambda k: (re.search(rf'"{k}":\s*"([^"]*)"', src) or [None, ""])[1]
            m = {"id": i, "title": grab("title") or i, "subtitle": grab("subtitle"), "built": False,
                 "domain_ready": bool(re.search(r"^DOMAIN_READY = True", src, re.M)),
                 "has_brief": (ATLASES / i / "BRIEF.md").exists(), "has_draft": (ATLASES / i / "DRAFT_NOTES.md").exists(),
                 "brief": (ATLASES / i / "BRIEF.md").read_text("utf-8")[:4000] if (ATLASES / i / "BRIEF.md").exists() else "",
                 "description": f"Not built yet — adapt atlases/{i}/atlas.py, set DOMAIN_READY = True, then ./atlas update {i} --full"}
        m["id"] = i
        out.append(m)
    return sorted(out, key=lambda m: (not m["built"], m["id"]))


_ID_SETS: dict[str, tuple[float, set]] = {}


def atlases_with(uid: str) -> list[str]:
    """Atlases whose library contains this paper (cached per papers.json mtime) — marks are shared across them."""
    out = []
    for a in atlas_ids():
        f = ATLASES / a / "public" / "papers.json"
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if a not in _ID_SETS or _ID_SETS[a][0] != mt:
            try:
                _ID_SETS[a] = (mt, {p["id"] for p in json.loads(f.read_text("utf-8"))})
            except (OSError, ValueError):
                continue
        if uid in _ID_SETS[a][1]:
            out.append(a)
    return out


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS.read_text("utf-8")))
    except (OSError, ValueError):
        pass
    ids = [a["id"] for a in atlas_list() if a.get("built")]
    if s.get("default_atlas") not in ids:
        s["default_atlas"] = ids[0] if ids else ""
    return s


def atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", "utf-8")
    os.replace(tmp, path)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(SITE), **kw)

    def log_message(self, fmt, *args):  # quiet
        pass

    # ── helpers ──
    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, f: Path, ctype: str) -> None:
        if not f.is_file():
            return self.send_error(404)
        data = f.read_bytes()
        gz = f.suffix in GZIP_TYPES and "gzip" in self.headers.get("Accept-Encoding", "")
        if gz:
            data = gzip.compress(data, compresslevel=5)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n < 200_000 else None
        except (ValueError, UnicodeDecodeError):
            return None

    def _ip(self) -> str:
        return self.client_address[0].removeprefix("::ffff:")

    def _cookie(self, name: str) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _authed(self) -> bool:
        return not GATE or self._ip() in OWN_IPS or session_ok(self._cookie("atlas_session"))

    def _redirect(self, to: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _session_cookie(self) -> str:
        return f"atlas_session={new_session()}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax"

    def _login_page(self, code: int = 401, error: str = "") -> None:
        html = LOGIN_HTML.read_text("utf-8").replace("%ERROR%", error)
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _gate(self, path: str) -> bool:
        """True if the request may proceed; otherwise the login page or a 401 has been sent."""
        if self._authed():
            return True
        if path.startswith(("/api/", "/a/")) or re.search(r"\.(json|js|css|bib|pdf|tex|md|svg|png|ico)$", path):
            self._json(401, {"error": "login required", "login": "/login"})
        else:
            self._login_page()
        return False

    def _login_post(self) -> None:
        ip = self._ip()
        if too_many_fails(ip):
            return self._login_page(429, "Too many attempts. Wait ten minutes and try again.")
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if 0 < n < 10_000 else ""
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        if raw.startswith("{"):
            try:
                form = json.loads(raw)
            except ValueError:
                form = {}
        if check_site_password(str(form.get("password", ""))):
            FAILS.pop(ip, None)
            return self._redirect("/", self._session_cookie())
        FAILS.setdefault(ip, []).append(time.time())
        return self._login_page(401, "Wrong password.")

    def do_HEAD(self):
        if self._gate(urlsplit(self.path).path):
            return super().do_HEAD()

    def _admin(self) -> bool:
        ip = self.client_address[0].removeprefix("::ffff:")
        key = self.headers.get("X-Atlas-Key", "")
        return ADMIN_LAN or ip in OWN_IPS or (bool(key) and hmac.compare_digest(key, ADMIN_KEY))

    def _atlas_param(self) -> str | None:
        a = (parse_qs(urlsplit(self.path).query).get("atlas") or [""])[0]
        return a if ID_RE.match(a) and (ATLASES / a / "atlas.py").exists() else None

    # ── GET ──
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/login":
            return self._redirect("/") if self._authed() else self._login_page(200)
        if path == "/logout":
            return self._redirect("/login", "atlas_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        if not self._gate(path):
            return
        if path == "/api/atlases":
            return self._json(200, atlas_list())
        if path == "/api/settings":
            return self._json(200, load_settings())
        if path == "/api/whoami":
            ip = self.client_address[0].removeprefix("::ffff:")
            return self._json(200, {"admin": self._admin(), "local": ADMIN_LAN or ip in OWN_IPS, "gate": GATE, "on_hub": ip in OWN_IPS,
                                    "ask": ASK_OPEN or self._admin()})
        if path == "/api/jobs":
            jobs = sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)[:30]
            return self._json(200, [job_view(j) for j in jobs])
        if path == "/api/backup":
            git = lambda *x: subprocess.run(["git", *x], cwd=REPO, capture_output=True, text=True).stdout.strip()
            return self._json(200, {"last_commit": git("log", "-1", "--format=%ci|%s"), "remote": git("remote", "get-url", "origin").split("@")[-1],
                                    "dirty": bool(git("status", "--porcelain"))})
        if path == "/api/marks":
            a = self._atlas_param()
            if not a:
                return self._json(400, {"error": "bad atlas"})
            try:
                return self._json(200, json.loads((ATLASES / a / "data" / "marks.json").read_text("utf-8")))
            except (OSError, ValueError):
                return self._json(200, {})
        if path == "/guide.md":
            return self._send_file(REPO / "docs" / "GUIDE.md", "text/markdown; charset=utf-8")
        m = re.match(r"^/a/([a-z0-9][a-z0-9_-]{0,40})/reading/([A-Za-z0-9._:-]{3,80})\.json$", path)
        if m:  # deep-reading notes, one file per paper
            aid, uid = m.groups()
            return self._send_file(ATLASES / aid / "data" / "reading" / f"{uid.replace(':', '_')}.json", "application/json; charset=utf-8")
        m = re.match(r"^/a/([a-z0-9][a-z0-9_-]{0,40})/survey/([a-z0-9_-]{1,40})/(.+)$", path)
        if m:  # survey workspace files (LaTeX sources, PDF, bib, writer packs)
            aid, sid, rel = m.groups()
            if not SURVEY_FILE_RE.match(rel):
                return self.send_error(404)
            ctype = {"pdf": "application/pdf", "bib": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8"}.get(rel.rsplit(".", 1)[-1], "text/plain; charset=utf-8")
            return self._send_file(ATLASES / aid / "surveys" / sid / rel, ctype)
        m = re.match(r"^/a/([a-z0-9][a-z0-9_-]{0,40})/([a-z]+\.(?:json|bib))$", path)
        if m:
            aid, name = m.groups()
            if name not in PUBLIC_FILES:
                return self.send_error(404)
            ctype = "application/json; charset=utf-8" if name.endswith(".json") else "text/plain; charset=utf-8"
            return self._send_file(ATLASES / aid / "public" / name, ctype)
        f = SITE / path.lstrip("/")
        if path.endswith("/"):
            f = f / "index.html"
        if f.is_file() and f.resolve().is_relative_to(SITE.resolve()):
            ctype = self.guess_type(str(f))
            return self._send_file(f, ctype)
        return super().do_GET()

    # ── POST ──
    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/login":
            return self._login_post()
        if not self._gate(path):
            return
        body = self._body()
        if not isinstance(body, dict):
            return self._json(400, {"error": "bad json"})
        admin_paths = path in ("/api/atlases", "/api/backup") or re.match(r"^/api/atlases/[a-z0-9][a-z0-9_-]{0,40}/(meta|jobs|delete)$", path)
        if (admin_paths or path == "/api/settings") and not self._admin():
            return self._json(403, {"error": "locked — unlock editing with the admin key (Settings, or ./atlas admin-key on the hub machine)"})
        if path == "/api/atlases":
            aid = str(body.get("id", "")).strip().lower()
            if not ID_RE.match(aid) or aid.startswith("_") or (ATLASES / aid).exists():
                return self._json(400, {"error": "id must be new, lowercase letters/digits/-/_ (e.g. agentatlas)"})
            args = ["new", aid, "--title", str(body.get("title") or aid)]
            for k in ("subtitle", "field", "description"):
                if body.get(k):
                    args += [f"--{k}", str(body[k])]
            rc, out = run_cli(*args)
            if rc:
                return self._json(400, {"error": out[-400:]})
            if body.get("brief"):
                (ATLASES / aid / "BRIEF.md").write_text(str(body["brief"]).strip() + "\n")
            return self._json(200, {"ok": True, "atlases": atlas_list()})
        m = re.match(r"^/api/atlases/([a-z0-9][a-z0-9_-]{0,40})/(meta|jobs|delete)$", path)
        if m:
            aid, what = m.groups()
            if aid not in atlas_ids():
                return self._json(404, {"error": "no such atlas"})
            if what == "delete":
                if any(j["rc"] is None and j["atlas"] == aid for j in JOBS.values()):
                    return self._json(409, {"error": "a job is running for this atlas — wait for it to finish"})
                rc, out = run_cli("delete", aid)
                if rc:
                    return self._json(400, {"error": out[-400:]})
                return self._json(200, {"ok": True, "atlases": atlas_list()})
            if what == "meta":
                args = ["meta", aid] + [x for k in ("title", "subtitle", "description") if body.get(k) is not None for x in (f"--{k}", str(body[k]))]
                rc, out = run_cli(*args) if len(args) > 2 else (0, "")
                if rc:
                    return self._json(400, {"error": out[-400:]})
                new_id = str(body.get("new_id") or "").strip().lower()
                if new_id and new_id != aid:
                    rc, out = run_cli("rename", aid, new_id)
                    if rc:
                        return self._json(400, {"error": out[-400:]})
                return self._json(200, {"ok": True, "atlases": atlas_list(), "id": new_id or aid})
            kind = body.get("kind")
            if kind not in JOB_KINDS:
                return self._json(400, {"error": f"kind must be one of {sorted(JOB_KINDS)}"})
            extra = ""
            if kind == "draft":
                brief = str(body.get("brief") or "").strip()
                bf = ATLASES / aid / "BRIEF.md"
                if brief:
                    bf.write_text(brief + "\n")
                if not bf.exists():
                    return self._json(400, {"error": "write a brief first (what is in / out, the survey's storyline)"})
                extra = str(bf)
            job = start_job(kind, aid, JOB_KINDS[kind](aid, extra))
            return self._json(409 if "error" in job else 200, job if "error" in job else job_view(job))
        if path == "/api/ask":
            aid = str(body.get("atlas", ""))
            q = str(body.get("q", "")).strip()
            if aid not in atlas_ids() or not (ATLASES / aid / "public" / "papers.json").exists():
                return self._json(400, {"error": "unknown or unbuilt atlas"})
            if not 3 <= len(q) <= 500:
                return self._json(400, {"error": "ask a question of 3 to 500 characters"})
            llm = bool(body.get("llm", True)) and (ASK_OPEN or self._admin())
            if not ASK_SEM.acquire(timeout=5):
                return self._json(429, {"error": "two questions are already being answered, try again in a moment"})
            try:
                res = ASK.answer(ATLASES / aid, q, llm=llm)
            except Exception as e:  # noqa: BLE001  (claude missing, timeout, usage limit)
                res = ASK.answer(ATLASES / aid, q, llm=False)
                res["error"] = f"the model could not answer ({str(e)[:160]}); showing the most relevant papers"
            finally:
                ASK_SEM.release()
            if not llm:
                res["note"] = "Answers need Claude on the hub machine; this device sees the most relevant papers only."
            return self._json(200, res)
        if path == "/api/password":
            new_pw, cur = str(body.get("new", "")), str(body.get("current", ""))
            on_hub = self._ip() in OWN_IPS
            if not on_hub and too_many_fails(self._ip()):
                return self._json(429, {"error": "too many attempts, wait ten minutes"})
            if not on_hub and not check_site_password(cur):
                FAILS.setdefault(self._ip(), []).append(time.time())
                return self._json(403, {"error": "current password is wrong"})
            if not 4 <= len(new_pw) <= 128:
                return self._json(400, {"error": "the new password needs 4 to 128 characters"})
            set_site_password(new_pw)
            body_out = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if not on_hub:  # keep this device signed in; every other device must log in again
                self.send_header("Set-Cookie", self._session_cookie())
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)
            return
        if path == "/api/backup":
            job = start_job("backup", "", ["backup"])
            return self._json(409 if "error" in job else 200, job if "error" in job else job_view(job))
        if path == "/api/settings":
            with LOCK:
                s = load_settings()
                for k, v in body.items():
                    if k in SETTING_CHOICES and v in SETTING_CHOICES[k]:
                        s[k] = v
                    elif k == "default_atlas" and v in atlas_ids():
                        s[k] = v
                    elif k == "bg_custom" and isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}$", v):
                        s[k] = v
                atomic_write(SETTINGS, s)
            return self._json(200, s)
        if path == "/api/marks":
            a = self._atlas_param()
            if not a or not UID_RE.match(str(body.get("uid", ""))):
                return self._json(400, {"error": "bad request"})
            uid = body["uid"]
            result = {}
            with LOCK:  # the same paper in another atlas gets the same mark (star, status, note)
                for aa in dict.fromkeys([a] + atlases_with(uid)):
                    f = ATLASES / aa / "data" / "marks.json"
                    try:
                        marks = json.loads(f.read_text("utf-8"))
                    except (OSError, ValueError):
                        marks = {}
                    m = marks.get(uid, {})
                    if "star" in body:
                        m["star"] = bool(body["star"])
                    if "status" in body and body["status"] in STATUSES:
                        m["status"] = body["status"]
                    if "note" in body and isinstance(body["note"], str):
                        m["note"] = body["note"][:20000]
                    m["t"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if not m.get("star") and not m.get("status") and not m.get("note"):
                        marks.pop(uid, None)
                    else:
                        marks[uid] = m
                    f.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(f, marks)
                    if aa == a:
                        result = marks.get(uid, {})
            return self._json(200, {"ok": True, "mark": result})
        return self.send_error(404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("ATLAS_PORT", 8668)))
    ap.add_argument("--host", default=os.environ.get("ATLAS_HOST") or _local_cfg().get("host") or "127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Atlas hub on http://{a.host}:{a.port}/  ({', '.join(atlas_ids()) or 'no atlases'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
