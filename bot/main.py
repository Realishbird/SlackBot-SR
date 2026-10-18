import logging
import re

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot.config import enabled_modules, env
from bot.registry import Registry

logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MENTION = re.compile(r"<@[A-Z0-9]+>")
TEXT_FILE_TYPES = {"csv", "text", "txt", "tsv"}
MAX_FILE_BYTES = 512 * 1024


def attached_text(event: dict, token: str) -> str:
    """Contents of small text/CSV files attached to a message (needs files:read)."""
    parts = []
    for f in event.get("files") or []:
        kind = (f.get("filetype") or "").lower()
        if kind not in TEXT_FILE_TYPES and not (f.get("mimetype") or "").startswith("text/"):
            continue
        if int(f.get("size") or 0) > MAX_FILE_BYTES:
            continue
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            continue
        try:
            r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30, follow_redirects=True)
            if r.status_code == 200 and "text/html" not in r.headers.get("content-type", ""):
                parts.append(f"{f.get('name') or ''}\n{r.text}")
        except httpx.HTTPError as exc:
            logging.getLogger(__name__).warning("could not fetch attached file: %s", exc)
    return "\n".join(parts)


def build_app(registry: Registry) -> App:
    token = env("SLACK_BOT_TOKEN", required=True)
    app = App(token=token)
    command = env("SLASH_COMMAND", "/bot")

    def deliver(reply, say, client, channel: str, **kw) -> None:
        """Answer where the message came from, then copy to reply.broadcast if that is elsewhere."""
        say(text=reply.text, blocks=reply.blocks or None, **kw)
        if reply.broadcast and reply.broadcast != channel:
            try:
                client.chat_postMessage(channel=reply.broadcast, text=reply.text, blocks=reply.blocks or None)
                say(text=f"Posted to <#{reply.broadcast}>" if reply.broadcast.startswith("C") else f"Posted to {reply.broadcast}")
            except Exception as exc:  # unknown channel, bot not a member, ...
                say(text=f":warning: Couldn't post to {reply.broadcast}: {getattr(exc, 'response', None) and exc.response.get('error') or exc}")

    @app.command(command)
    def on_command(ack, command, respond):
        ack()
        reply = registry.dispatch(command.get("text", ""), command["user_id"])
        respond(text=reply.text, blocks=reply.blocks or None, response_type="in_channel")

    @app.event("app_mention")
    def on_mention(event, say, client):
        text = MENTION.sub("", event.get("text", "")) + "\n" + attached_text(event, token)
        reply = registry.dispatch(text, event["user"])
        deliver(reply, say, client, event.get("channel", ""), thread_ts=event.get("thread_ts") or event["ts"])

    @app.event("message")
    def on_dm(event, say, client):
        # Direct messages to the bot; ignore edits, bot posts and channel chatter.
        if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
            return
        text = (event.get("text") or "") + "\n" + attached_text(event, token)
        reply = registry.dispatch(text, event["user"])
        deliver(reply, say, client, event.get("channel", ""))

    return app


def main() -> None:
    registry = Registry()
    registry.discover(enabled_modules())
    app = build_app(registry)
    SocketModeHandler(app, env("SLACK_APP_TOKEN", required=True)).start()


if __name__ == "__main__":
    main()
