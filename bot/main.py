import logging
import re

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot.config import enabled_modules, env
from bot.module import Image
from bot.registry import Registry

logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MENTION = re.compile(r"<@[A-Z0-9]+>")
TEXT_FILE_TYPES = {"csv", "text", "txt", "tsv"}
MAX_FILE_BYTES = 512 * 1024
IMAGE_TYPES = {"jpg", "jpeg", "png", "webp", "gif", "heic"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGES = 3


FILES_HINT = (":paperclip: I couldn't read the attached file. Reading dropped files needs the Slack app's `files:read` "
              "scope: update the app manifest from the repo and click Reinstall, or paste the file's text instead.")


def attached_text(event: dict, token: str) -> tuple[str, bool]:
    """(contents of small text/CSV files attached to the message, whether any
    text file was attached but could not be read). Needs the files:read scope;
    without it Slack answers the download with a login page instead."""
    log = logging.getLogger(__name__)
    parts, failed = [], False
    for f in event.get("files") or []:
        kind = (f.get("filetype") or "").lower()
        if kind not in TEXT_FILE_TYPES and not (f.get("mimetype") or "").startswith("text/"):
            continue
        if int(f.get("size") or 0) > MAX_FILE_BYTES:
            log.warning("attached file %s is too big (%s bytes)", f.get("name"), f.get("size"))
            failed = True
            continue
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            failed = True
            continue
        try:
            r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30, follow_redirects=True)
        except httpx.HTTPError as exc:
            log.warning("could not fetch attached file %s: %s", f.get("name"), exc)
            failed = True
            continue
        if r.status_code == 200 and "text/html" not in r.headers.get("content-type", ""):
            parts.append(f"{f.get('name') or ''}\n{r.text}")
        else:
            log.warning("attached file %s: HTTP %s %s (missing files:read scope?)", f.get("name"), r.status_code, r.headers.get("content-type", ""))
            failed = True
    return "\n".join(parts), failed


def attached_images(event: dict, token: str) -> tuple[list[Image], bool]:
    """(photos attached to the message, whether one could not be fetched)."""
    log = logging.getLogger(__name__)
    images, failed = [], False
    for f in event.get("files") or []:
        kind = (f.get("filetype") or "").lower()
        mime = f.get("mimetype") or ""
        if kind not in IMAGE_TYPES and not mime.startswith("image/"):
            continue
        if len(images) >= MAX_IMAGES:
            break
        if int(f.get("size") or 0) > MAX_IMAGE_BYTES:
            log.warning("attached image %s is too big (%s bytes)", f.get("name"), f.get("size"))
            failed = True
            continue
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            failed = True
            continue
        try:
            r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60, follow_redirects=True)
        except httpx.HTTPError as exc:
            log.warning("could not fetch attached image %s: %s", f.get("name"), exc)
            failed = True
            continue
        if r.status_code == 200 and "text/html" not in r.headers.get("content-type", ""):
            images.append(Image(f.get("name") or "photo", mime or r.headers.get("content-type", "image/jpeg"), r.content))
        else:
            log.warning("attached image %s: HTTP %s %s (missing files:read scope?)", f.get("name"), r.status_code, r.headers.get("content-type", ""))
            failed = True
    return images, failed


def build_app(registry: Registry) -> App:
    token = env("SLACK_BOT_TOKEN", required=True)
    app = App(token=token)
    registry.slack = app.client
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

    def answer(event, say, client, text: str, **kw) -> None:
        images, image_failed = attached_images(event, token)
        if images:
            reply = registry.dispatch_images(images, text, event["user"])
            if reply is None:
                say(text=":camera: I can't read photos yet: no module with a vision model is loaded. Type the serial instead.", **kw)
            else:
                deliver(reply, say, client, event.get("channel", ""), **kw)
            return
        files_text, unreadable = attached_text(event, token)
        if (unreadable or image_failed) and not files_text:
            say(text=FILES_HINT, **kw)
            return
        reply = registry.dispatch(f"{text}\n{files_text}", event["user"])
        deliver(reply, say, client, event.get("channel", ""), **kw)

    in_thread = (env("REPLY_IN_THREAD", "off") or "off").strip().lower() in ("on", "true", "yes", "1")

    @app.event("app_mention")
    def on_mention(event, say, client):
        # Answer in the channel; inside an existing thread, stay in that thread.
        # REPLY_IN_THREAD=on starts a thread under every mention instead.
        kw = {}
        if event.get("thread_ts"):
            kw["thread_ts"] = event["thread_ts"]
        elif in_thread:
            kw["thread_ts"] = event["ts"]
        answer(event, say, client, MENTION.sub("", event.get("text", "")), **kw)

    @app.event("message")
    def on_dm(event, say, client):
        # Direct messages to the bot; ignore edits, bot posts and channel chatter.
        if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
            return
        answer(event, say, client, event.get("text") or "")

    return app


def main() -> None:
    registry = Registry()
    registry.discover(enabled_modules())
    app = build_app(registry)
    SocketModeHandler(app, env("SLACK_APP_TOKEN", required=True)).start()


if __name__ == "__main__":
    main()
