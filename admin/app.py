"""sr-bot admin: a small web page to update, restart, watch and configure the bot.

Runs as the `admin` service in docker-compose.yml with the Docker socket and
the repo directory mounted, so it can run `git` and `docker compose` for the
real stack. Protect it with ADMIN_PASSWORD; it has root-equivalent power on
the host through the socket, so keep it on your LAN.
"""
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

log = logging.getLogger("admin")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

REPO_DIR = Path(os.environ.get("REPO_DIR", "/opt/slackbot-sr"))
ENV_FILE = REPO_DIR / ".env"
UPDATE_LOG = REPO_DIR / "data" / "update.log"
BRANCH = os.environ.get("GIT_BRANCH", "main")
BOT_SERVICE = "slackbot"
SECRET_KEYS = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY)$")
# Settings the page lets you edit. Everything else in .env is left alone.
EDITABLE = [
    ("ENABLED_MODULES", "Modules to load (* for all, or e.g. specs,ping)"),
    ("LOG_LEVEL", "Bot log level (DEBUG, INFO, WARNING)"),
    ("SLASH_COMMAND", "Slash command (must match the Slack app manifest)"),
    ("SLACK_BOT_TOKEN", "Slack bot token (xoxb-...)"),
    ("SLACK_APP_TOKEN", "Slack app token (xapp-...)"),
    ("DELL_CLIENT_ID", "Dell TechDirect client ID"),
    ("DELL_CLIENT_SECRET", "Dell TechDirect client secret"),
    ("DELL_BROWSER", "Dell: use the built-in hidden Chromium when no API keys (auto or off)"),
    ("INTAKE_CHANNEL", "Channel to post pasted Dell exports to (ID like C0123... or #name; blank = DM only)"),
    ("LLM_BASE_URL", "Chat model server, OpenAI-compatible (e.g. http://192.168.1.50:11434/v1 for Ollama; blank = chat off)"),
    ("LLM_MODEL", "Chat model name (e.g. llama3.2:3b, qwen2.5:7b)"),
    ("LLM_API_KEY", "Chat model API key (blank for Ollama)"),
    ("LLM_SYSTEM_PROMPT", "Chat model instructions (who the bot is, how to answer)"),
    ("ADMIN_AUTO_UPDATE_MINUTES", "Auto-update check interval in minutes (0 = off)"),
]

app = Flask(__name__)
action_lock = threading.Lock()
state = {"last_check": None, "remote": None, "last_action": "", "last_action_at": None}


# ---- shell helpers ---------------------------------------------------------

def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 127, str(exc)


def compose(*args: str, timeout: int = 120) -> tuple[int, str]:
    return run(["docker", "compose", *args], timeout=timeout)


def git(*args: str, timeout: int = 60) -> tuple[int, str]:
    return run(["git", *args], timeout=timeout)


# ---- .env handling ---------------------------------------------------------

def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    return values


def write_env(updates: dict[str, str]) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    tmp = ENV_FILE.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(ENV_FILE)


def mask(key: str, value: str) -> str:
    if not value or not SECRET_KEYS.search(key):
        return value
    return value[:4] + "…" + value[-2:] if len(value) > 8 else "••••"


# ---- status ----------------------------------------------------------------

def local_commit() -> dict:
    code, out = git("log", "-1", "--format=%h%n%cI%n%s")
    if code != 0:
        return {"sha": "?", "date": "", "message": out}
    sha, date, *msg = out.splitlines() + ["", ""]
    return {"sha": sha, "date": date[:16].replace("T", " "), "message": msg[0]}


def check_remote() -> dict | None:
    """git fetch and report the newest commit on origin. Cached in state."""
    code, out = git("fetch", "--quiet", "origin", BRANCH, timeout=90)
    state["last_check"] = now()
    if code != 0:
        log.warning("git fetch failed: %s", out)
        state["remote"] = {"error": out[-300:]}
        return state["remote"]
    code, out = git("log", "-1", "--format=%h%n%cI%n%s", f"origin/{BRANCH}")
    sha, date, *msg = (out.splitlines() if code == 0 else ["?", "", out]) + ["", ""]
    _, behind = git("rev-list", "--count", f"HEAD..origin/{BRANCH}")
    state["remote"] = {"sha": sha, "date": date[:16].replace("T", " "), "message": msg[0], "behind": int(behind or 0) if behind.isdigit() else 0}
    return state["remote"]


def containers() -> list[dict]:
    code, out = compose("ps", "-a", "--format", "json")
    if code != 0:
        return [{"Service": "?", "State": "error", "Status": out[-200:]}]
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        elif line.startswith("["):
            rows.extend(json.loads(line))
    return [{"service": r.get("Service"), "state": r.get("State"), "status": r.get("Status"), "image": r.get("Image")} for r in rows]


def modules() -> dict:
    env = read_env()
    enabled = env.get("ENABLED_MODULES", "*").strip() or "*"
    found = sorted(p.stem for p in (REPO_DIR / "bot" / "modules").glob("*.py") if not p.stem.startswith("_"))
    vendors = sorted(p.stem for p in (REPO_DIR / "bot" / "vendors").glob("*.py") if p.stem not in ("__init__", "base"))
    notes = {}
    if "dell" in vendors and not (env.get("DELL_CLIENT_ID") and env.get("DELL_CLIENT_SECRET")):
        if env.get("DELL_BROWSER", "auto").strip().lower() in ("off", "0", "false", "no"):
            notes["dell"] = "no API keys and browser off: Dell lookups will be blocked"
        else:
            notes["dell"] = "no API keys: lookups run through the built-in browser"
    return {"enabled": enabled, "modules": found, "vendors": vendors, "notes": notes}


def update_log_tail(lines: int = 60) -> str:
    if not UPDATE_LOG.exists():
        return ""
    return "\n".join(UPDATE_LOG.read_text(errors="replace").splitlines()[-lines:])


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def status() -> dict:
    env = read_env()
    return {
        "time": now(),
        "local": local_commit(),
        "remote": state["remote"],
        "last_check": state["last_check"],
        "containers": containers(),
        "modules": modules(),
        "update_log": update_log_tail(),
        "last_action": state["last_action"],
        "last_action_at": state["last_action_at"],
        "auto_update_minutes": auto_update_minutes(env),
        "settings": [{"key": k, "label": label, "value": mask(k, env.get(k, "")), "secret": bool(SECRET_KEYS.search(k))} for k, label in EDITABLE],
    }


# ---- actions ---------------------------------------------------------------

def start_update() -> tuple[int, str]:
    """Run update.sh in a sibling container so it outlives this one."""
    UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    return compose("run", "-d", "--rm", "--no-deps", "admin", "/app/update.sh", timeout=120)


def do_action(name: str, params: dict) -> tuple[bool, str]:
    if not action_lock.acquire(blocking=False):
        return False, "Another action is still running."
    try:
        if name == "check":
            remote = check_remote() or {}
            if "error" in remote:
                return False, f"Couldn't reach GitHub: {remote['error']}"
            return True, f"{remote['behind']} new commit(s) available." if remote["behind"] else "Up to date."
        if name == "update":
            code, out = start_update()
            return code == 0, "Update started; the log below will fill in. The page may blink while the admin itself restarts." if code == 0 else out
        if name in ("restart", "stop", "start"):
            code, out = compose(name, BOT_SERVICE, timeout=120)
            return code == 0, (f"Bot {name}ed." if name != "stop" else "Bot stopped.") if code == 0 else out
        if name == "rebuild":
            code, out = compose("up", "-d", "--build", BOT_SERVICE, timeout=600)
            return code == 0, "Bot rebuilt and restarted." if code == 0 else out
        if name == "save":
            updates = {}
            for key, _ in EDITABLE:
                if key in params:
                    value = str(params[key]).strip()
                    if SECRET_KEYS.search(key) and (not value or "…" in value or "•" in value):
                        continue  # untouched masked secret
                    updates[key] = value
            if not updates:
                return True, "Nothing changed."
            write_env(updates)
            # `up -d` recreates the container so it picks up the new env.
            code, out = compose("up", "-d", "--no-build", BOT_SERVICE, timeout=120)
            return code == 0, f"Saved {', '.join(updates)} and restarted the bot." if code == 0 else out
        return False, f"Unknown action {name!r}."
    finally:
        action_lock.release()


# ---- auto update -----------------------------------------------------------

def auto_update_minutes(env: dict | None = None) -> int:
    raw = (env or read_env()).get("ADMIN_AUTO_UPDATE_MINUTES") or os.environ.get("ADMIN_AUTO_UPDATE_MINUTES", "30")
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


def auto_updater() -> None:
    time.sleep(20)
    while True:
        minutes = auto_update_minutes()
        if minutes:
            ok, msg = do_action("check", {})
            if ok and state["remote"] and state["remote"].get("behind"):
                log.info("auto-update: %s", msg)
                ok, msg = do_action("update", {})
                record(f"auto-update: {msg}")
        time.sleep(max(60, minutes * 60) if minutes else 300)


def record(msg: str) -> None:
    state["last_action"], state["last_action_at"] = msg, now()


# ---- web -------------------------------------------------------------------

def authed(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not password:
            return Response("Set ADMIN_PASSWORD in .env and restart the admin service.", 503)
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.password or "", password):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="sr-bot admin"'})
        return fn(*a, **kw)
    return wrapper


@app.get("/")
@authed
def index():
    return render_template("index.html")


@app.get("/api/status")
@authed
def api_status():
    return jsonify(status())


@app.get("/api/logs")
@authed
def api_logs():
    lines = min(int(request.args.get("lines", 200)), 2000)
    service = request.args.get("service", BOT_SERVICE)
    if service not in (BOT_SERVICE, "admin"):
        return jsonify({"error": "unknown service"}), 400
    code, out = compose("logs", "--no-color", "--tail", str(lines), service, timeout=60)
    return jsonify({"logs": out})


@app.post("/api/action/<name>")
@authed
def api_action(name):
    ok, msg = do_action(name, request.get_json(silent=True) or {})
    record(msg)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    threading.Thread(target=auto_updater, daemon=True, name="auto-updater").start()
    app.run(host="0.0.0.0", port=int(os.environ.get("ADMIN_LISTEN_PORT", "8080")), threaded=True)
