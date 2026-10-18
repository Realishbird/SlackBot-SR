import logging
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot.config import enabled_modules, env
from bot.registry import Registry

logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MENTION = re.compile(r"<@[A-Z0-9]+>")


def build_app(registry: Registry) -> App:
    app = App(token=env("SLACK_BOT_TOKEN", required=True))
    command = env("SLASH_COMMAND", "/bot")

    @app.command(command)
    def on_command(ack, command, respond):
        ack()
        reply = registry.dispatch(command.get("text", ""), command["user_id"])
        respond(text=reply.text, blocks=reply.blocks or None, response_type="in_channel")

    @app.event("app_mention")
    def on_mention(event, say):
        text = MENTION.sub("", event.get("text", ""))
        reply = registry.dispatch(text, event["user"])
        say(text=reply.text, blocks=reply.blocks or None, thread_ts=event.get("thread_ts") or event["ts"])

    @app.event("message")
    def on_dm(event, say):
        # Direct messages to the bot; ignore edits, bot posts and channel chatter.
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        reply = registry.dispatch(event.get("text", ""), event["user"])
        say(text=reply.text, blocks=reply.blocks or None)

    return app


def main() -> None:
    registry = Registry()
    registry.discover(enabled_modules())
    app = build_app(registry)
    SocketModeHandler(app, env("SLACK_APP_TOKEN", required=True)).start()


if __name__ == "__main__":
    main()
