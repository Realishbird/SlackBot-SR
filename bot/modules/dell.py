"""Hardware summary for Dell service tags, via Dell's TechDirect API.

Dell's support website blocks automated requests, so this uses the official
API that backs the "Original configuration" page. Get keys at
https://techdirect.dell.com (APIs > Warranty / Asset API).
"""
import re
import time
from collections import OrderedDict

import httpx

from bot.config import env
from bot.module import Module, Reply

TAG = re.compile(r"^[A-Z0-9]{5,7}$")

# First matching rule wins; order matters (e.g. "riser" before "processor").
CATEGORIES = [
    ("Processor", ("processor", "cpu", "xeon", "epyc", "core i", "core ultra")),
    ("Memory", ("memory", "dimm", "rdimm", "ram")),
    ("Storage", ("hard drive", "hdd", "ssd", "nvme", "sata", "sas ", "boss", "m.2", "solid state")),
    ("RAID / storage controller", ("perc", "raid", "hba")),
    ("Network", ("ethernet", "network", "nic", "lom", "broadcom", "intel x", "mellanox", "connectx", "wireless", "wlan")),
    ("GPU", ("graphics", "gpu", "nvidia", "radeon")),
    ("Power supply", ("power supply", "psu", "watt")),
    ("Remote management", ("idrac",)),
    ("Operating system", ("windows", "ubuntu", "linux", "vmware", "operating system")),
]


def categorize(description: str) -> str:
    d = description.lower()
    for name, words in CATEGORIES:
        if any(w in d for w in words):
            return name
    return "Other"


class DellClient:
    def __init__(self, client_id: str, client_secret: str, base_url: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expires = 0.0

    def _auth(self) -> dict:
        if not self._token or time.time() > self._token_expires - 60:
            r = self.http.post(
                f"{self.base_url}/auth/oauth/v2/token",
                data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret},
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def components(self, tag: str) -> dict:
        r = self.http.get(f"{self.base_url}/PROD/sbil/eapi/v5/asset-components", params={"servicetag": tag}, headers=self._auth())
        r.raise_for_status()
        return r.json()

    def warranty(self, tag: str) -> dict:
        r = self.http.get(f"{self.base_url}/PROD/sbil/eapi/v5/asset-entitlements", params={"servicetags": tag}, headers=self._auth())
        r.raise_for_status()
        data = r.json()
        return data[0] if isinstance(data, list) and data else {}


def summarize(tag: str, comp: dict, warranty: dict) -> Reply:
    model = comp.get("productLineDescription") or warranty.get("productLineDescription") or "Unknown model"
    ship = (comp.get("shipDate") or warranty.get("shipDate") or "")[:10]

    groups: "OrderedDict[str, list[str]]" = OrderedDict((name, []) for name, _ in CATEGORIES)
    groups["Other"] = []
    for c in comp.get("components") or []:
        desc = (c.get("partDescription") or c.get("itemDescription") or "").strip()
        if not desc:
            continue
        qty = int(c.get("partQuantity") or c.get("quantity") or 1)
        groups[categorize(desc)].append(f"{qty} x {desc}" if qty > 1 else desc)

    ends = [e.get("endDate", "")[:10] for e in warranty.get("entitlements") or [] if e.get("endDate")]
    lines = [f"*{model}* (`{tag}`)"]
    if ship:
        lines.append(f"Shipped: {ship}")
    if ends:
        lines.append(f"Warranty until: {max(ends)}")
    body = []
    for name, items in groups.items():
        if items and name != "Other":
            body.append(f"*{name}*\n" + "\n".join(f"• {i}" for i in items))
    if not body:
        body.append("_Dell returned no component list for this tag._")
    url = f"https://www.dell.com/support/home/en-us/product-support/servicetag/{tag}/overview"
    footer = f"<{url}|Open on Dell support>"

    text = "\n".join(lines) + "\n\n" + "\n\n".join(body) + "\n\n" + footer
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        *({"type": "section", "text": {"type": "mrkdwn", "text": b[:2900]}} for b in body),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]},
    ]
    return Reply(text=text, blocks=blocks)


class DellHardware(Module):
    keyword = "dell"
    aliases = ("tag", "servicetag")
    summary = "Hardware summary for a Dell service tag (original factory config)"
    usage = "dell <service tag> [more tags]"

    def __init__(self, client: DellClient | None = None) -> None:
        self.client = client

    def setup(self) -> None:
        if self.client:
            return
        self.client = DellClient(
            env("DELL_CLIENT_ID", required=True),
            env("DELL_CLIENT_SECRET", required=True),
            env("DELL_API_BASE", "https://apigtwb2c.us.dell.com"),
        )

    def handle(self, args: str, user_id: str) -> Reply:
        tags = [t.upper() for t in re.split(r"[\s,]+", args) if t]
        if not tags:
            return Reply(f"Usage: `{self.usage}`")
        bad = [t for t in tags if not TAG.match(t)]
        if bad:
            return Reply(f"That doesn't look like a service tag: `{'`, `'.join(bad)}`")
        replies = [self._one(t) for t in tags[:5]]
        return Reply(
            text="\n\n---\n\n".join(r.text for r in replies),
            blocks=[b for i, r in enumerate(replies) for b in ([{"type": "divider"}] if i else []) + r.blocks],
        )

    def _one(self, tag: str) -> Reply:
        try:
            comp = self.client.components(tag)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return Reply(f":lock: Dell rejected the API credentials while looking up `{tag}`.")
            if exc.response.status_code == 404:
                return Reply(f"Dell has no record of `{tag}`.")
            raise
        try:
            warranty = self.client.warranty(tag)
        except httpx.HTTPError:
            warranty = {}
        return summarize(tag, comp, warranty)
