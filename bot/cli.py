"""Try a module from the terminal, no Slack needed.

    python -m bot.cli specs PF2ABCDE
    docker compose run --rm slackbot python -m bot.cli lenovo PF2ABCDE
"""
import logging
import sys

from bot.config import enabled_modules
from bot.registry import Registry


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    registry = Registry()
    registry.discover(enabled_modules())
    print(registry.dispatch(" ".join(sys.argv[1:]) or "help", "cli").text)


if __name__ == "__main__":
    main()
