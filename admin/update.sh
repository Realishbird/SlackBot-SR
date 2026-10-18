#!/usr/bin/env bash
# Pull the latest code and rebuild whatever changed. Runs in a throwaway
# sibling container (started by the admin UI or its auto-updater), so it
# survives the admin container itself being replaced mid-update.
set -uo pipefail
REPO_DIR="${REPO_DIR:-/opt/slackbot-sr}"
LOG="$REPO_DIR/data/update.log"
mkdir -p "$REPO_DIR/data"
exec >>"$LOG" 2>&1

echo "=== update started $(date -Is) ==="
cd "$REPO_DIR" || { echo "repo dir missing"; exit 1; }
before="$(git rev-parse --short HEAD)"
if ! git pull --ff-only; then
  echo "git pull failed"; echo "=== update failed $(date -Is) ==="; exit 1
fi
after="$(git rev-parse --short HEAD)"
echo "code: $before -> $after"
export GIT_COMMIT="$after"
if docker compose up -d --build --remove-orphans; then
  docker image prune -f >/dev/null 2>&1 || true
  echo "=== update finished $(date -Is) ==="
else
  echo "=== update failed $(date -Is) ==="; exit 1
fi
