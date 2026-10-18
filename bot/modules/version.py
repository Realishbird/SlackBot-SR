import os

from bot.module import Module, Reply


class Version(Module):
    keyword = "version"
    summary = "Show which build of sr-bot is running"
    usage = "version"

    def handle(self, args: str, user_id: str) -> Reply:
        return Reply(f"sr-bot build `{os.environ.get('GIT_COMMIT', 'unknown')}`")
