#!/usr/bin/env bash
# SurveyAtlas — set up on this machine (idempotent; safe to re-run after every git pull).
#
#   ./install.sh                 check deps, deploy claude/ into ~/.claude, restore snapshots, build
#   ./install.sh --cron          … and install the weekly `./atlas update --all` cron entry
#   ./install.sh --start         … and start the hub on :8668 (ATLAS_PORT=… to change)
#   ./install.sh --no-claude     skip the ~/.claude deployment
#
# ~/.claude gets SYMLINKS into this repo (skill + playbook) plus a marked block in
# ~/.claude/CLAUDE.md, so editing the repo updates Claude's copy and `git commit` saves it.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"
CRON=0; START=0; CLAUDE=1
for a in "$@"; do
  case "$a" in --cron) CRON=1 ;; --start) START=1 ;; --no-claude) CLAUDE=0 ;; *) echo "unknown option $a"; exit 1 ;; esac
done
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

echo "== prerequisites"
python3 -c 'import sys; assert sys.version_info >= (3, 9), sys.version' || { echo "need Python >= 3.9"; exit 1; }
ok "python $(python3 -c 'import sys; print(sys.version.split()[0])')"
if ! python3 -c 'import requests' 2>/dev/null; then
  python3 -m pip install --user --quiet requests && ok "installed requests" || warn "could not install requests — run: python3 -m pip install --user requests"
else ok "requests"; fi
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null; then ok "claude CLI ($(claude --version 2>/dev/null | head -1))"
else warn "claude CLI not found — install Claude Code and log in once; classification (./atlas update/add) needs it"; fi
command -v crontab >/dev/null || warn "crontab not available — weekly updates must be run by hand"

echo "== local.json (contact e-mail for API polite pools; not committed)"
if [ ! -f local.json ]; then
  EMAIL="$(git config user.email 2>/dev/null || true)"
  printf '{\n  "contact_email": "%s"\n}\n' "$EMAIL" > local.json
  ok "wrote local.json (${EMAIL:-no e-mail — fill it in for faster OpenAlex/CrossRef})"
else ok "local.json exists"; fi

if [ "$CLAUDE" = 1 ]; then
  echo "== deploy claude/ → ${CLAUDE_HOME:=$HOME/.claude}"
  link() {  # link <src> <dst>: replace a stale symlink; back up a real file once
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    if [ -L "$dst" ]; then ln -sfn "$src" "$dst"
    elif [ -e "$dst" ]; then mv "$dst" "$dst.bak-$(date +%Y%m%d%H%M%S)"; ln -s "$src" "$dst"; warn "backed up existing $dst"
    else ln -s "$src" "$dst"; fi
    ok "$dst → $src"
  }
  link "$REPO/claude/skills/literature-atlas" "$CLAUDE_HOME/skills/literature-atlas"
  link "$REPO/claude/skills/literature-atlas/playbook.md" "$CLAUDE_HOME/reference/literature-atlas-pattern.md"
  python3 - "$CLAUDE_HOME/CLAUDE.md" "$REPO/claude/CLAUDE.snippet.md" "$REPO" <<'EOF'
import pathlib, re, sys
target, snippet, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
begin, end = "<!-- ATLAS-HUB:BEGIN -->", "<!-- ATLAS-HUB:END -->"
block = f"{begin}\n{snippet.read_text().replace('{{REPO}}', repo).strip()}\n{end}"
text = target.read_text() if target.exists() else ""
if begin in text:
    text = re.sub(re.escape(begin) + ".*?" + re.escape(end), lambda _: block, text, flags=re.S)
else:
    text = text.rstrip() + ("\n\n" if text.strip() else "") + block + "\n"
target.write_text(text)
EOF
  ok "CLAUDE.md block (between ATLAS-HUB markers)"
fi

echo "== atlases"
for d in atlases/*/; do
  id="$(basename "$d")"; [ "$id" = "_template" ] && continue
  if [ -f "$d/snapshot/MANIFEST.json" ] && [ ! -f "$d/data/labels/labels.jsonl" ]; then
    ./atlas restore "$id" && ok "$id restored from snapshot"
  elif [ -f "$d/data/candidates.jsonl" ]; then
    ./atlas build "$id" > /dev/null && ok "$id rebuilt"
  else
    warn "$id has no data yet — run: ./atlas update $id --full"
  fi
done

if [ "$CRON" = 1 ]; then echo "== cron"; ./atlas cron install; fi
if [ "$START" = 1 ]; then echo "== hub"; ./atlas start; fi
echo "== done. Site: ./atlas start → http://localhost:${ATLAS_PORT:-8668}/   ·   ./atlas list"
