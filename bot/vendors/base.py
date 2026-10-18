from dataclasses import dataclass, field

# Display order and keyword rules for grouping parts. First match wins.
CATEGORIES = [
    ("Processor", ("processor", "cpu", "xeon", "epyc", "core i", "core ultra", "ryzen")),
    ("Memory", ("memory", "dimm", "rdimm", "ram")),
    ("Storage", ("hard drive", "hdd", "ssd", "nvme", "sata", "sas ", "boss", "m.2", "solid state", "storage")),
    ("RAID / storage controller", ("perc", "raid", "hba", "smart array")),
    ("Graphics", ("graphics", "gpu", "nvidia", "radeon", "video")),
    ("Display", ("display", "lcd", "screen", "panel")),
    ("Network", ("ethernet", "network", "nic", "lom", "broadcom", "intel x", "mellanox", "connectx", "wireless", "wlan", "wifi", "wi-fi", "bluetooth")),
    ("Power", ("power supply", "psu", "watt", "battery", "ac adapter", "power adapter")),
    ("Remote management", ("idrac", "ilo", "xclarity")),
    ("Operating system", ("windows", "ubuntu", "linux", "vmware", "operating system")),
]


def categorize(text: str) -> str:
    t = text.lower()
    for name, words in CATEGORIES:
        if any(w in t for w in words):
            return name
    return "Other"


class NotFound(Exception):
    """The vendor has no record of this serial."""


@dataclass
class SystemSpecs:
    vendor: str
    serial: str
    model: str = "Unknown model"
    ship_date: str = ""
    warranty_end: str = ""
    parts: dict[str, list[str]] = field(default_factory=dict)
    url: str = ""

    def add(self, description: str, qty: int = 1, category: str | None = None) -> None:
        description = " ".join(description.split())
        if not description:
            return
        item = f"{qty} x {description}" if qty > 1 else description
        self.parts.setdefault(category or categorize(description), []).append(item)


class Vendor:
    keyword: str = ""             # e.g. "dell"; becomes `/bot dell <serial>`
    aliases: tuple[str, ...] = ()
    name: str = ""                # e.g. "Dell"
    serial_hint: str = ""         # shown in help, e.g. "service tag"

    def setup(self) -> None:
        """Read config here. Raise to disable this vendor."""

    def looks_like(self, serial: str) -> bool:
        """Cheap guess used by `/bot specs <serial>` to try likely vendors first."""
        return False

    def lookup(self, serial: str) -> SystemSpecs:
        raise NotImplementedError
