# Slack helper bot

A small, modular Slack bot that runs in one container. It connects to Slack
over Socket Mode, so it needs outbound internet only: no public IP, port
forward or reverse proxy.

Talk to it three ways:

- `/bot dell 9C4BPM4`
- `@HelperBot dell 9C4BPM4` in a channel it's in (replies in a thread)
- a DM to the bot: `dell 9C4BPM4`

`/bot help` lists every loaded module.

## Modules

| Keyword | What it does | Needs |
|---|---|---|
| `help` | Lists modules | nothing |
| `dell` (aliases `tag`, `servicetag`) | Factory hardware summary for up to 5 service tags: model, ship date, warranty end, CPUs, memory, drives, RAID, NICs, GPUs, PSUs, iDRAC | Dell TechDirect API keys |

### Dell API keys

Dell's support website blocks automated requests, so the `dell` module uses
Dell's official TechDirect API, which serves the same "Original configuration"
data the website shows.

1. Sign in at https://techdirect.dell.com and open **APIs**.
2. Request the **Warranty** API (asset entitlements and asset components).
   Dell approves requests manually, which can take a few days.
3. Put the client ID and secret into `.env` as `DELL_CLIENT_ID` and
   `DELL_CLIENT_SECRET`.

Without keys the bot still starts; the `dell` module is skipped and a warning
is logged.

## Create the Slack app

1. Go to https://api.slack.com/apps, click **Create New App > From an app
   manifest**, pick your workspace and paste `slack-manifest.yml`.
2. **Basic Information > App-Level Tokens**: generate a token with the
   `connections:write` scope. That's `SLACK_APP_TOKEN` (`xapp-...`).
3. **Install App**: install to the workspace. The Bot User OAuth Token is
   `SLACK_BOT_TOKEN` (`xoxb-...`).
4. Invite the bot to channels where you want to @mention it: `/invite @HelperBot`.

If you change the slash command name in the manifest, set `SLASH_COMMAND` to match.

## Deploy on Proxmox

### One command (creates the LXC for you)

Create the Slack app first (above) so you have both tokens. Then in the
Proxmox host shell (Datacenter > your node > Shell):

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Realishbird/SlackBot-SR/main/proxmox/create-lxc.sh)"
```

It asks for your Slack tokens (and Dell keys, optional), creates an
unprivileged Debian 12 LXC with nesting enabled (1 core, 512 MB RAM, 4 GB disk,
DHCP on `vmbr0`, starts on boot), installs Docker inside it, clones this repo
to `/opt/slackbot-sr` and starts the bot. Change defaults by setting them in
front of the command, for example `STORAGE=local-zfs BRIDGE=vmbr1 CTID=150`.
The variables are listed at the top of `proxmox/create-lxc.sh`.

To update later, run this inside the LXC (`pct enter <id>`):

```sh
curl -fsSL https://raw.githubusercontent.com/Realishbird/SlackBot-SR/main/proxmox/install.sh | bash
```

`install.sh` also works on its own in an LXC or VM you made yourself.

### Manual

Pick one. Both run the same `docker compose` stack.

**Option A: Debian LXC with Docker (lightest)**

1. In Proxmox, create a container from the Debian 12 template (1 core, 512 MB
   RAM, 4 GB disk is plenty). Under **Options > Features**, enable `nesting`
   (and `keyctl` if the container is unprivileged).
2. Start it, open the console and install Docker:
   ```sh
   apt update && apt install -y curl git
   curl -fsSL https://get.docker.com | sh
   ```

**Option B: a small VM**

Any Debian or Ubuntu VM, then the same Docker install as above. Use this if
you'd rather not run Docker inside an LXC.

**Then, in either one:**

```sh
git clone https://github.com/Realishbird/SlackBot-SR.git
cd slackbot-sr
cp .env.example .env
nano .env            # fill in the tokens
docker compose up -d --build
docker compose logs -f
```

You should see `Loaded module dell` and `A new session has been established`.
The container restarts on its own after reboots (`restart: unless-stopped`).

To update: `git pull && docker compose up -d --build`.

## Add a module

Drop a file in `bot/modules/`. Any `Module` subclass there is loaded
automatically at startup:

```python
# bot/modules/ping.py
from bot.module import Module, Reply

class Ping(Module):
    keyword = "ping"
    summary = "Check the bot is alive"
    usage = "ping"

    def setup(self):
        # read env vars here; raise to disable the module
        pass

    def handle(self, args: str, user_id: str) -> Reply:
        return Reply("pong")
```

`handle` gets everything after the keyword. Return plain `text`, and
optionally Slack `blocks` for richer formatting. Exceptions are caught and
reported in Slack without crashing the bot. Limit what loads with
`ENABLED_MODULES=dell,ping`.

## Run tests

```sh
pip install -r requirements-dev.txt
python -m pytest
```
