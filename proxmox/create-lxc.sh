#!/usr/bin/env bash
# Run on the Proxmox host. Creates a Debian 12 LXC and installs the bot in it.
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Realishbird/SlackBot-SR/main/proxmox/create-lxc.sh)"
# Override any default by setting it first, e.g. STORAGE=local-zfs CTID=150 bash create-lxc.sh
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/Realishbird/SlackBot-SR/main"
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_NAME="${CT_NAME:-slackbot}"
STORAGE="${STORAGE:-local-lvm}"          # where the container disk goes
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
DISK_GB="${DISK_GB:-8}"
MEMORY_MB="${MEMORY_MB:-1536}"   # Dell lookups run a hidden Chromium, which needs ~1 GB headroom
CORES="${CORES:-1}"

command -v pct >/dev/null || { echo "Run this on the Proxmox host (pct not found)." >&2; exit 1; }

ask() {  # ask VAR "prompt" [secret]
  local value=""
  if [[ -n "${!1:-}" ]]; then return; fi
  if [[ "${3:-}" == secret ]]; then read -rsp "$2: " value; echo; else read -rp "$2: " value; fi
  printf -v "$1" '%s' "$value"
}

echo "== Slack bot LXC installer =="
ask SLACK_BOT_TOKEN "Slack bot token (xoxb-...)" secret
ask SLACK_APP_TOKEN "Slack app token (xapp-...)" secret
ask DELL_CLIENT_ID "Dell TechDirect client ID (blank to skip for now)"
if [[ -n "$DELL_CLIENT_ID" ]]; then ask DELL_CLIENT_SECRET "Dell TechDirect client secret" secret; fi
ask ADMIN_PASSWORD "Password for the admin web page (blank to generate one)" secret

echo "== Finding Debian 12 template =="
pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || { echo "No debian-12-standard template found." >&2; exit 1; }
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

echo "== Creating container $CTID ($CT_NAME) =="
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$CT_NAME" \
  --cores "$CORES" --memory "$MEMORY_MB" --swap 512 \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 --onboot 1 --start 1

echo "== Waiting for network =="
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts github.com >/dev/null || { echo "Container $CTID has no network." >&2; exit 1; }

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
chmod 600 "$ENV_FILE"
cat > "$ENV_FILE" <<ENV
SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN
SLACK_APP_TOKEN=$SLACK_APP_TOKEN
SLASH_COMMAND=/bot
ENABLED_MODULES=*
LOG_LEVEL=INFO
DELL_CLIENT_ID=${DELL_CLIENT_ID:-}
DELL_CLIENT_SECRET=${DELL_CLIENT_SECRET:-}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-}
ADMIN_PORT=8080
ADMIN_AUTO_UPDATE_MINUTES=30
ENV
pct push "$CTID" "$ENV_FILE" /root/slackbot.env --perms 600

echo "== Installing the bot inside the container =="
pct exec "$CTID" -- bash -c "apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null && curl -fsSL $REPO_RAW/proxmox/install.sh | bash"

echo
IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"
echo "Done. Container $CTID ($CT_NAME) is running the bot."
echo "Admin page: http://${IP:-<container ip>}:8080  (any username; the password you entered, or the generated one printed above)"
echo "Logs:    pct exec $CTID -- bash -c 'cd /opt/slackbot-sr && docker compose logs -f'"
echo "Shell:   pct enter $CTID"
