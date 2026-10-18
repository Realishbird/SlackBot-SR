"""Base class every bot module extends.

A module owns one keyword. The bot routes `/bot <keyword> args` and
`@bot <keyword> args` to `handle`, which returns a Reply.
"""
from dataclasses import dataclass, field


@dataclass
class Reply:
    text: str
    blocks: list[dict] = field(default_factory=list)


class Module:
    keyword: str = ""          # e.g. "dell"
    aliases: tuple[str, ...] = ()
    summary: str = ""          # one line shown in `help`
    usage: str = ""            # e.g. "dell <service tag>"

    def setup(self) -> None:
        """Called once at startup; read config here. Raise to disable."""

    def handle(self, args: str, user_id: str) -> Reply:
        raise NotImplementedError
