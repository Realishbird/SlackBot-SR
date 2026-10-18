import base64
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def admin(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "bot" / "modules").mkdir(parents=True)
    (repo / "bot" / "vendors").mkdir()
    for name in ("help", "specs"):
        (repo / "bot" / "modules" / f"{name}.py").write_text("")
    for name in ("__init__", "base", "dell", "hp", "lenovo"):
        (repo / "bot" / "vendors" / f"{name}.py").write_text("")
    (repo / ".env").write_text("SLACK_BOT_TOKEN=xoxb-123456789abc\nENABLED_MODULES=*\n# comment\nLOG_LEVEL=INFO\n")
    monkeypatch.setenv("REPO_DIR", str(repo))
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    spec = importlib.util.spec_from_file_location("admin_app", ROOT / "admin" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["admin_app"] = mod
    spec.loader.exec_module(mod)

    calls = []

    def fake_run(cmd, timeout=120):
        calls.append(cmd)
        if cmd[:2] == ["git", "log"] and "origin/main" in cmd:
            return 0, "def456\n2026-10-18T17:00:00+00:00\nnewer commit"
        if cmd[:2] == ["git", "log"]:
            return 0, "abc123\n2026-10-18T16:00:00+00:00\nfirst commit"
        if cmd[:2] == ["git", "rev-list"]:
            return 0, "2"
        if cmd[:3] == ["docker", "compose", "ps"]:
            return 0, '{"Service":"slackbot","State":"running","Status":"Up 3 minutes","Image":"slackbot:latest"}'
        if cmd[:3] == ["docker", "compose", "logs"]:
            return 0, "line1\nline2"
        return 0, ""

    monkeypatch.setattr(mod, "run", fake_run)
    mod.calls = calls
    mod.app.config["TESTING"] = True
    return mod


def auth(pw="pw"):
    return {"Authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}


def test_requires_password(admin):
    c = admin.app.test_client()
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers=auth("wrong")).status_code == 401
    assert c.get("/healthz").status_code == 200


def test_status(admin):
    s = admin.app.test_client().get("/api/status", headers=auth()).get_json()
    assert s["local"]["sha"] == "abc123"
    assert s["containers"][0]["state"] == "running"
    assert s["modules"]["modules"] == ["help", "specs"]
    assert s["modules"]["vendors"] == ["dell", "hp", "lenovo"]
    assert "dell" in s["modules"]["notes"]
    token = next(f for f in s["settings"] if f["key"] == "SLACK_BOT_TOKEN")
    assert token["secret"] and "123456789" not in token["value"] and token["value"].startswith("xoxb")


def test_check_and_update(admin):
    c = admin.app.test_client()
    r = c.post("/api/action/check", headers=auth()).get_json()
    assert r["ok"] and "2 new commit" in r["message"]
    r = c.post("/api/action/update", headers=auth()).get_json()
    assert r["ok"]
    assert ["docker", "compose", "run", "-d", "--rm", "--no-deps", "admin", "/app/update.sh"] in admin.calls


def test_save_settings_keeps_masked_secret(admin):
    c = admin.app.test_client()
    r = c.post("/api/action/save", json={"ENABLED_MODULES": "specs,ping", "SLACK_BOT_TOKEN": "xoxb…bc", "LOG_LEVEL": "DEBUG"}, headers=auth()).get_json()
    assert r["ok"]
    env = admin.read_env()
    assert env["ENABLED_MODULES"] == "specs,ping"
    assert env["LOG_LEVEL"] == "DEBUG"
    assert env["SLACK_BOT_TOKEN"] == "xoxb-123456789abc"
    assert "# comment" in admin.ENV_FILE.read_text()
    assert ["docker", "compose", "up", "-d", "--no-build", "slackbot"] in admin.calls


def test_logs_and_page(admin):
    c = admin.app.test_client()
    assert c.get("/api/logs", headers=auth()).get_json()["logs"] == "line1\nline2"
    assert c.get("/api/logs?service=evil", headers=auth()).status_code == 400
    assert b"sr-bot admin" in c.get("/", headers=auth()).data
