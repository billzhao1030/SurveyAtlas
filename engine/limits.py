"""Shared handling of Claude usage / rate limits for the long headless runs (classify, read).

A limit error makes every worker of the process wait until the limit resets instead of failing the
batch, so a multi-hour run survives the 5-hour usage windows unattended. If the Claude login changes
while paused (an account switcher rewrote the credentials), the wait ends at once:
every call is a fresh `claude -p`, so the next one runs on the new account.

Reading window: local.json "read_window": "23:30-08:00" (local time, may wrap midnight) makes long runs start new
work only inside that window (for example to leave daytime usage free). Papers already in flight
finish; the run waits and continues when the window opens. The file is re-read every minute (edit it live).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

_pause_until = [0.0]
_paused_login = [None]
_lock = threading.Lock()
CREDENTIALS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json"


LOCAL = Path(__file__).resolve().parent.parent / "local.json"
_window_waiting = [False]


def window() -> tuple[int, int] | None:
    """(start, end) in minutes after midnight from local.json "read_window" ("23:30-08:00"); None = no window."""
    try:
        w = json.loads(LOCAL.read_text()).get("read_window") or ""
        a, b = (x.strip() for x in w.split("-"))
        return tuple(int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in (a, b))
    except (OSError, ValueError, AttributeError):
        return None


def in_window(w: tuple[int, int] | None) -> bool:
    if not w:
        return True
    t = time.localtime()
    now = t.tm_hour * 60 + t.tm_min
    return w[0] <= now < w[1] if w[0] < w[1] else now >= w[0] or now < w[1]


def wait_for_window(log=print) -> None:
    """Block (before starting a new paper) while the local time is outside the reading window."""
    while not in_window(w := window()):
        with _lock:
            if not _window_waiting[0]:
                _window_waiting[0] = True
                log(f"outside the reading window {w[0] // 60:02d}:{w[0] % 60:02d}-{w[1] // 60:02d}:{w[1] % 60:02d} — "
                    f"papers in flight finish, new ones wait")
        time.sleep(60)
    with _lock:
        if _window_waiting[0]:
            _window_waiting[0] = False
            log("reading window open — continuing")


def _login() -> float | None:
    try:
        return CREDENTIALS.stat().st_mtime
    except OSError:
        return None


LIMIT_RE = re.compile(r"usage limit|rate limit|session limit|weekly limit|hit your .{0,20}limit|limit reached"
                      r"|overloaded|\b429\b|too many requests", re.I)


def _until_reset(err: str) -> float | None:
    """Seconds until a reset time printed as 'resets 12:50am (Europe/Berlin)' / 'resets 5pm'."""
    m = re.search(r"resets? (?:at )?(\d{1,2})(?::(\d{2}))?\s*([ap]m)(?:\s*\(([^)]+)\))?", err, re.I)
    if not m:
        return None
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(m.group(4)) if m.group(4) else None
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now()
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    t = now.replace(hour=h, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return (t - now).total_seconds()


def limited(err: str) -> float | None:
    """Seconds to wait if `err` is a usage / rate-limit error, else None."""
    if not LIMIT_RE.search(err):
        return None
    m = re.search(r"\|(\d{10})", err)  # 'Claude AI usage limit reached|<reset epoch>'
    if m:
        return max(60.0, float(m.group(1)) - time.time() + 60)
    w = _until_reset(err)
    if w is not None:
        return max(60.0, w + 90)
    return 1200.0


def pause_for(err: str, log=print) -> bool:
    """If `err` is a limit error, pause all workers until it resets and return True."""
    wait = limited(err)
    if not wait:
        return False
    with _lock:
        if time.time() + wait > _pause_until[0] + 30:
            _pause_until[0] = time.time() + wait
            _paused_login[0] = _login()
            log(f"usage limit — pausing all workers for {wait / 60:.0f} min ({err[:80]})")
    return True


def wait_if_paused() -> None:
    while time.time() < _pause_until[0]:
        if _paused_login[0] is not None and _login() != _paused_login[0]:
            with _lock:
                if _pause_until[0] > time.time():
                    _pause_until[0] = 0.0
                    print("limits: Claude login changed (account switched) — resuming", flush=True)
            break
        time.sleep(30)
