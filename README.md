# sr-bot

A small, modular Slack bot that runs in one container. It connects to Slack
over Socket Mode, so it needs outbound internet only: no public IP, port
forward or reverse proxy.

Talk to it three ways:

- `/bot specs 9C4BPM4`
- `@sr-bot specs 9C4BPM4` in a channel it's in (replies in a thread)
- a DM to the bot: `specs 9C4BPM4`

`/bot help` lists every loaded module. `/bot version` shows the running build.

## Admin page and automatic updates

The stack runs two containers: the bot and a small admin web page at
`http://<lxc ip>:8080` (any username; password is `ADMIN_PASSWORD` in `.env`,
generated for you by the install script if you leave it blank). From it you can:

- see whether the bot is running and which commit it's on
- check GitHub for new commits and **Update now** (pulls, rebuilds, restarts)
- restart, stop, start or rebuild the bot
- read the bot's logs and the update log
- change settings (modules, tokens, Dell keys, log level) and restart the bot

Updates also happen on their own: the admin checks GitHub every
`ADMIN_AUTO_UPDATE_MINUTES` (default 30) and applies new commits on `main`.
Set it to 0 to update only by hand. Every push to this repo therefore reaches
the running bot within half an hour.

The admin can rebuild containers because it has the Docker socket, which is
root-level access to the LXC. Keep port 8080 on your LAN or behind a VPN and
don't forward it to the internet.

## Hardware specs (Dell, HP, Lenovo)

`specs <serial>` returns the factory configuration for a system: model, ship
date and warranty end where the vendor provides them, then CPUs, memory,
storage, RAID, graphics, display, network, power and management parts,
grouped and counted. Up to 5 serials per message, separated by spaces or
commas.

`specs` guesses the vendor from the serial's shape (Dell 7 characters, Lenovo
8, HP 10) and falls back to asking the others. To ask one vendor directly:
`dell <service tag>`, `hp <serial>` (or `hpe`), `lenovo <serial>`.

| Vendor | Source | Needs |
|---|---|---|
| Dell | Support site's "Original Configuration" (order SKUs) plus its warranty widget, fetched through a hidden Chromium in the container; TechDirect API when keys are set | nothing (about 1 GB of free RAM for the browser; API keys add ship date and skip the browser) |
| Lenovo | pcsupport.lenovo.com + PSREF spec sheets | nothing |
| HP / HPE | PartSurfer's JSON backend (the unit's factory configuration); HPE's PartSurfer pages as fallback | nothing |

The Lenovo and HP lookups use the public endpoints behind the vendors' own
support sites rather than official APIs, so a site redesign can break them.
Both sites sit behind bot protection that rejects plain Python clients, so
requests go through `curl_cffi`, which presents a real Chrome TLS fingerprint.
HP packaging, labels, cables and firmware placeholders are filtered out of
the list.

### Dell without API keys: the hidden browser

Without keys, Dell lookups read the same "Original Configuration" list the
support site shows under Parts & Repairs: processor, memory, drives, graphics,
network, power supply and OS, grouped by Dell's order SKU families, plus the
service end date from the warranty widget.

Dell's bot protection (Akamai) refuses that page unless its JavaScript has
run in a real browser, so the bot image ships a headless Chromium (Playwright)
that is used for Dell only. Each Dell lookup opens the support home in it,
lets the page settle, fetches the configuration from inside the page and
closes the browser again. That takes 5 to 15 seconds and about 300 MB of RAM
while it runs, so give the LXC at least 1.5 GB (`pct set <ctid> --memory 1536`
on the Proxmox host; the installer now defaults to that and an 8 GB disk).
Set `DELL_BROWSER=off` (on the admin page) to skip it; Dell lookups then
answer with a "blocked" message unless API keys are set. Dell can change its
protection at any time; if lookups start failing, run the probe below.

### Paste a Dell export instead

Dell's product page has a **Product Specifications** panel with an **Export**
link that downloads `<service tag>.csv`. Paste that file's contents into a DM
with the bot (or drop the file in), with the service tag in front if you
want it in the title, and the bot answers with the same grouped summary as
`specs`. No keyword needed; the bot recognises the file. Set `INTAKE_CHANNEL`
on the admin page (a channel ID like `C0123ABCD`, or `#intake`) and every
pasted export is also posted there with who submitted it; naming a channel in
the message (`#intake` before the CSV) overrides the default.

Dropping the file (rather than pasting its text) and posting to channels the
bot hasn't been invited to need two extra scopes, `files:read` and
`chat:write.public`. They are in `slack-manifest.yml`; after updating the
manifest in your Slack app settings, click **Reinstall** when Slack asks.
Without them, paste the text and `/invite @sr-bot` to the intake channel.

## Chat with a local model

`@sr-bot <anything that isn't a command>` goes to a language model and the
answer comes back in the thread (`/bot ask <question>` works too). Any
OpenAI-compatible chat server does: Ollama, LM Studio, llama.cpp's server,
vLLM, or a hosted API. Set on the admin page:

| Setting | Example |
|---|---|
| `LLM_BASE_URL` | `http://192.168.1.50:11434/v1` (Ollama) |
| `LLM_MODEL` | `llama3.2:3b`, `qwen2.5:7b`, `llama3.1:8b` |
| `LLM_API_KEY` | blank for Ollama |
| `LLM_SYSTEM_PROMPT` | who the bot is and how to answer |

Leave the URL blank and chat stays off (unknown messages show help).

The quickest way to a fast model is Ollama on a machine with a GPU, for
example your desktop: install from https://ollama.com, run `ollama pull
llama3.2:3b`, and start it listening on the LAN (`OLLAMA_HOST=0.0.0.0
ollama serve`, or set that variable in the Ollama app's settings), then use
`http://<that machine's IP>:11434/v1`. Ollama on the LXC itself works too
(`curl -fsSL https://ollama.com/install.sh | sh` inside it, then
`http://<lxc ip>:11434/v1`), but CPU-only answers take a while and a 3B
model wants about 4 GB of RAM on top of the bot.

Each message is answered on its own for now; the model does not see earlier
messages in the thread.

### Dell API keys (optional)

With TechDirect API keys the Dell lookup switches to Dell's official API,
which adds ship date and warranty end:

1. Sign in at https://techdirect.dell.com and open **APIs**.
2. Request the **Warranty** API (asset entitlements and asset components).
   Dell approves requests manually, which can take a few days.
3. Put the client ID and secret into `.env` as `DELL_CLIENT_ID` and
   `DELL_CLIENT_SECRET` (or on the admin page) and restart the bot.

### Try a lookup without Slack

From the LXC (or any machine with the repo and Docker):

```sh
docker compose run --rm slackbot python -m bot.cli specs PF2ABCDE
```

To see where a Dell lookup fails (plain request, then the hidden browser):

```sh
docker compose run --rm slackbot python -m bot.probe 9C4BPM4
```

## Create the Slack app

1. Go to https://api.slack.com/apps, click **Create New App > From an app
   manifest**, pick your workspace and paste `slack-manifest.yml`.
2. **Basic Information > App-Level Tokens**: generate a token with the
   `connections:write` scope. That's `SLACK_APP_TOKEN` (`xapp-...`).
3. **Install App**: install to the workspace. The Bot User OAuth Token is
   `SLACK_BOT_TOKEN` (`xoxb-...`).
4. Invite the bot to channels where you want to @mention it: `/invite @sr-bot`.

If you change the slash command name in the manifest, set `SLASH_COMMAND` to match.

## Deploy on Proxmox

### One command (creates the LXC for you)

Create the Slack app first (above) so you have both tokens. Then in the
Proxmox host shell (Datacenter > your node > Shell):

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Realishbird/SlackBot-SR/main/proxmox/create-lxc.sh)"
```

It asks for your Slack tokens (and Dell keys, optional), creates an
unprivileged Debian 12 LXC with nesting enabled (1 core, 1.5 GB RAM, 8 GB disk,
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

1. In Proxmox, create a container from the Debian 12 template (1 core, 1.5 GB
   RAM, 8 GB disk; the RAM is for Dell's hidden browser). Under **Options > Features**, enable `nesting`
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

You should see `Loaded module specs` and `A new session has been established`.
Both containers restart on their own after reboots (`restart: unless-stopped`).

If the repo isn't at `/opt/slackbot-sr`, set `REPO_DIR=/where/it/is` in
`.env` so the admin container can find it.

To update by hand: `git pull && docker compose up -d --build`, or click
**Update now** on the admin page.

## Add a hardware vendor

Drop a file in `bot/vendors/` with a `Vendor` subclass. It gets its own
command (`/bot <keyword> <serial>`) and joins `specs` automatically:

```python
# bot/vendors/acme.py
import httpx
from bot.vendors.base import NotFound, SystemSpecs, Vendor

class Acme(Vendor):
    keyword = "acme"
    name = "Acme"
    serial_hint = "serial number"

    def looks_like(self, serial):
        return serial.startswith("AC")   # helps `specs` try Acme first

    def lookup(self, serial):
        data = httpx.get(f"https://support.acme.example/api/{serial}").json()
        if not data:
            raise NotFound(serial)
        specs = SystemSpecs(vendor=self.name, serial=serial, model=data["model"])
        for part in data["parts"]:
            specs.add(part["description"], part.get("qty", 1))  # auto-grouped
        return specs
```

## Add a module

For anything that isn't a hardware lookup, drop a file in `bot/modules/`. Any `Module` subclass there is loaded
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
`ENABLED_MODULES=specs,ping`.

## Run tests

```sh
pip install -r requirements-dev.txt
python -m pytest
```
