#!/usr/bin/env bash
# Run inside a Debian/Ubuntu LXC or VM (as root). Installs Docker, clones the
# bot to /opt/slackbot-sr and starts it. Safe to re-run: it updates instead.
#   curl -fsSL https://raw.githubusercontent.com/Realishbird/SlackBot-SR/main/proxmox/install.sh | bash
set -euo pipefail

REPO="https://github.com/Realishbird/SlackBot-SR.git"
DIR="${DIR:-/opt/slackbot-sr}"

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

echo "== Installing packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates >/dev/null
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

echo "== Getting the code =="
if [[ -d "$DIR/.git" ]]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

if [[ ! -f .env ]]; then
  if [[ -f /root/slackbot.env ]]; then
    mv /root/slackbot.env .env
  else
    cp .env.example .env
    if [[ -r /dev/tty ]]; then
      read -rsp "Slack bot token (xoxb-...): " bot </dev/tty; echo
      read -rsp "Slack app token (xapp-...): " app </dev/tty; echo
      sed -i "s|^SLACK_BOT_TOKEN=.*|SLACK_BOT_TOKEN=$bot|; s|^SLACK_APP_TOKEN=.*|SLACK_APP_TOKEN=$app|" .env
    else
      echo "Edit $DIR/.env with your tokens, then run: cd $DIR && docker compose up -d --build"
      exit 0
    fi
  fi
  chmod 600 .env
fi
if ! grep -q '^ADMIN_PASSWORD=.\+' .env; then
  ADMIN_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)"
  sed -i '/^ADMIN_PASSWORD=/d' .env
  printf 'ADMIN_PASSWORD=%s\n' "$ADMIN_PASSWORD" >> .env
  echo "Generated admin password: $ADMIN_PASSWORD (saved in $DIR/.env)"
fi
grep -q '^ADMIN_AUTO_UPDATE_MINUTES=' .env || echo 'ADMIN_AUTO_UPDATE_MINUTES=30' >> .env
GIT_COMMIT="$(git rev-parse --short HEAD)"
export GIT_COMMIT
export REPO_DIR="$DIR"

echo "== Starting the bot =="
docker compose up -d --build
docker compose ps
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Done. Admin page: http://${IP:-<this host>}:$(grep -oP '^ADMIN_PORT=\K.*' .env || echo 8080)  (any username, password from $DIR/.env)"
echo "Logs: cd $DIR && docker compose logs -f"
