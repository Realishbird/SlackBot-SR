from bot.module import Module, Reply


class Help(Module):
    keyword = "help"
    summary = "List what I can do"
    usage = "help"

    def __init__(self, registry) -> None:
        self.registry = registry

    def handle(self, args: str, user_id: str) -> Reply:
        lines = [f"• `{m.usage or m.keyword}`: {m.summary}" for m in self.registry.modules.values()]
        return Reply("Here's what I can do:\n" + "\n".join(lines))
