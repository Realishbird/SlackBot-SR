"""Base class every bot module extends.

A module owns one keyword. The bot routes `/bot <keyword> args` and
`@bot <keyword> args` to `handle`, which returns a Reply.
"""
from dataclasses import dataclass, field


@dataclass
class Reply:
    text: str
    blocks: list[dict] = field(default_factory=list)
    broadcast: str = ""        # also post this reply to that channel (ID or #name)


class Module:
    keyword: str = ""          # e.g. "dell"
    aliases: tuple[str, ...] = ()
    summary: str = ""          # one line shown in `help`
    usage: str = ""            # e.g. "dell <service tag>"
    pass_keyword: bool = False  # True: handle() gets "<word used> <args>"

    def setup(self) -> None:
        """Called once at startup; read config here. Raise to disable."""

    def matches(self, text: str) -> bool:
        """True if this module should take a message that names no keyword,
        e.g. because the text is a pasted file it knows how to read."""
        return False

    def handle(self, args: str, user_id: str) -> Reply:
        raise NotImplementedError
