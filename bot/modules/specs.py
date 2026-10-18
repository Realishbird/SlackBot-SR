"""Hardware specs by serial number, across vendors.

`specs <serial>` guesses the vendor; `dell <tag>`, `hp <serial>`,
`lenovo <serial>` ask one vendor directly. Vendors live in bot/vendors/.
"""
import importlib
import inspect
import logging
import pkgutil
import re

from bot import vendors as vendors_pkg
from bot.module import Module, Reply
from bot.vendors.base import CATEGORIES, Blocked, NotFound, SystemSpecs, Vendor

log = logging.getLogger(__name__)
SERIAL = re.compile(r"^[A-Z0-9]{5,12}$")
MAX_SERIALS = 5


def discover_vendors() -> list[Vendor]:
    found = []
    for info in pkgutil.iter_modules(vendors_pkg.__path__):
        mod = importlib.import_module(f"{vendors_pkg.__name__}.{info.name}")
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, Vendor) and cls is not Vendor and cls.keyword and cls.__module__ == mod.__name__:
                vendor = cls()
                try:
                    vendor.setup()
                except (Exception, SystemExit) as exc:
                    log.warning("Vendor %s disabled: %s", cls.keyword, exc)
                    continue
                log.info("Loaded vendor %s", cls.keyword)
                found.append(vendor)
    return found


def render(specs: SystemSpecs) -> Reply:
    head = [f"*{specs.vendor} {specs.model}* (`{specs.serial}`)"]
    if specs.ship_date:
        head.append(f"Shipped: {specs.ship_date}")
    if specs.warranty_end:
        head.append(f"Warranty until: {specs.warranty_end}")
    order = [name for name, _ in CATEGORIES] + sorted(k for k in specs.parts if k not in dict(CATEGORIES) and k != "Other")
    body = [
        f"*{name}*\n" + "\n".join(f"• {item}" for item in specs.parts[name])
        for name in order
        if specs.parts.get(name)
    ] or ["_No parts list was returned for this serial._"]
    footer = f"<{specs.url}|Open on {specs.vendor} support>" if specs.url else ""

    text = "\n".join(head) + "\n\n" + "\n\n".join(body) + (f"\n\n{footer}" if footer else "")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(head)}}]
    blocks += [{"type": "section", "text": {"type": "mrkdwn", "text": b[:2900]}} for b in body]
    if footer:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return Reply(text=text, blocks=blocks)


class Specs(Module):
    keyword = "specs"
    summary = "Factory hardware specs for a Dell, HP or Lenovo serial"
    usage = "specs <serial> [more serials]"
    pass_keyword = True

    def __init__(self, vendors: list[Vendor] | None = None) -> None:
        self.vendors = vendors

    def setup(self) -> None:
        if self.vendors is None:
            self.vendors = discover_vendors()
        if not self.vendors:
            raise RuntimeError("no vendors available")
        self.by_word = {w: v for v in self.vendors for w in (v.keyword, *v.aliases)}
        # Lets the registry route `/bot dell ...`, `/bot hp ...` straight here.
        self.aliases = tuple(self.by_word)
        names = ", ".join(v.name for v in self.vendors)
        self.summary = f"Factory hardware specs by serial ({names}). Or ask one vendor: " + ", ".join(
            f"`{v.keyword} <{v.serial_hint}>`" for v in self.vendors
        )

    def handle(self, args: str, user_id: str) -> Reply:
        word, _, rest = args.partition(" ")
        vendor = self.by_word.get(word)
        # Serials are often typed with dashes or spaces (R9-14DXCT); strip them.
        serials = [re.sub(r"[^A-Z0-9]", "", s.upper()) for s in re.split(r"[\s,]+", rest) if s]
        serials = [s for s in serials if s]
        if not serials:
            return Reply(f"Usage: `{vendor.keyword if vendor else 'specs'} <serial>`")
        bad = [s for s in serials if not SERIAL.match(s)]
        if bad:
            return Reply(f"That doesn't look like a serial number: `{'`, `'.join(bad)}`")

        replies = [self._one(s, vendor) for s in serials[:MAX_SERIALS]]
        blocks: list[dict] = []
        for i, r in enumerate(replies):
            if i:
                blocks.append({"type": "divider"})
            blocks += r.blocks or [{"type": "section", "text": {"type": "mrkdwn", "text": r.text}}]
        return Reply(text="\n\n---\n\n".join(r.text for r in replies), blocks=blocks)

    def _one(self, serial: str, only: Vendor | None) -> Reply:
        if only:
            candidates = [only]
        else:
            candidates = sorted(self.vendors, key=lambda v: not v.looks_like(serial))
        errors = []
        blocked = []
        for vendor in candidates:
            try:
                return render(vendor.lookup(serial))
            except NotFound:
                continue
            except Blocked as exc:
                log.warning("%s lookup for %s blocked: %s", vendor.keyword, serial, exc)
                blocked.append(str(exc))
            except Exception as exc:
                log.warning("%s lookup for %s failed: %r", vendor.keyword, serial, exc)
                errors.append(vendor.name)
        tried = ", ".join(v.name for v in candidates)
        if blocked and only:
            return Reply(f":no_entry: {blocked[0]}")
        if errors or blocked:
            return Reply(f":warning: Couldn't reach {', '.join(errors + [b.split(' ')[0] for b in blocked])} while looking up `{serial}`. Check the container logs.")
        return Reply(f"No match for `{serial}` at {tried}.")
