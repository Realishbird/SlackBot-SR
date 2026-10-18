"""Dell, two ways.

Without API keys (default): Dell's support site exposes the "Original
Configuration" of a service tag at
/support/product-details/en-us/ReviewSpecs/GetOriginalConfiguration. It
returns an HTML fragment with a `var OriginalConfigData = [...]` JSON blob:
one entry per order SKU (338-xxxx processor, 370-xxxx memory, ...). No
warranty data comes with it; the warranty widget needs a browser session.

With TechDirect API keys (DELL_CLIENT_ID / DELL_CLIENT_SECRET): the official
asset-components and asset-entitlements endpoints, which add ship date and
warranty end. Get keys at https://techdirect.dell.com (APIs > Warranty).
"""
import json
import re
import time

import httpx

from bot.config import env
from bot.vendors.base import Blocked, NotFound, SystemSpecs, Vendor, categorize
from bot.vendors.http import make_client

SUPPORT = "https://www.dell.com"
CONFIG_URL = f"{SUPPORT}/support/product-details/en-us/ReviewSpecs/GetOriginalConfiguration"
WARMUP_URL = f"{SUPPORT}/support/home/en-us"
# Mimic the XHR the Parts & Repairs page makes.
SITE_HEADERS = {
    "Referer": f"{SUPPORT}/support/home/en-us",
    "Accept": "text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Dell order SKU families. First three digits of skuNumber -> category.
# None means "packaging, paperwork or a service line; don't show it".
SKU_FAMILIES = {
    "338": "Processor", "374": "Processor",
    "370": "Memory", "375": "Memory",
    "400": "Storage", "401": "Storage", "345": "Storage", "429": "Storage", "449": "Storage",
    "780": "RAID / storage controller", "405": "RAID / storage controller",
    "490": "Graphics", "320": "Display",
    "555": "Network", "540": "Network", "406": "Network", "407": "Network", "542": "Network", "543": "Network",
    "321": "Power", "450": None, "451": "Power", "750": "Power",
    "385": "Remote management", "528": "Remote management", "631": "Remote management",
    "619": "Operating system", "634": "Operating system", "605": "Operating system",
    "384": "Cooling", "412": "Cooling",
    "580": "Accessories", "570": "Accessories", "575": "Accessories",
    "340": None, "328": None, "389": None, "387": None, "332": None, "343": None,
    "630": None, "632": None, "650": None, "658": None,
    "470": None, "379": None, "409": None, "492": None, "210": None, "817": None,
}
# Lines that describe an absence or paperwork, whatever the family.
SKIP_DESC = re.compile(
    r"^(No |Not |None\b)|not (included|selected)|\b(label|documentation|shipping material|quick setup|setup guide|"
    r"power cord|placemat|regulatory|energy star|epeat|upc|trial|driver|autopilot|tracking|certification)\b",
    re.I,
)


def unwrap(desc: str) -> str:
    """Dell stores SKU text in 30-char rows and joins them with a space, so
    words get split ("configura tion"). Drop a lone space at a row boundary."""
    chars = list(desc)
    for i in (92, 61, 60, 30):
        if i < len(chars) - 1 and chars[i] == " " and chars[i - 1] != " " and chars[i + 1] != " ":
            del chars[i]
    return " ".join("".join(chars).split())


def extract_config(page: str) -> list[dict]:
    i = page.find("var OriginalConfigData")
    if i < 0:
        return []
    start = page.find("[", i)
    depth = 0
    for k in range(start, len(page)):
        if page[k] == "[":
            depth += 1
        elif page[k] == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(page[start:k + 1])
                except ValueError:
                    return []
    return []


def parse_site(page: str, tag: str) -> SystemSpecs | None:
    skus = extract_config(page)
    if not skus:
        return None
    specs = SystemSpecs(vendor="Dell", serial=tag, url=f"{SUPPORT}/support/home/en-us/product-support/servicetag/{tag}/overview")
    model = ""
    for sku in skus:
        number = str(sku.get("skuNumber") or "")
        desc = unwrap(str(sku.get("skuDescription") or ""))
        if not desc:
            continue
        family = number.split("-")[0]
        if family == "210":
            model = re.sub(r"\s*CTO base$", "", desc, flags=re.I).strip() or model
            continue
        if SKIP_DESC.search(desc):
            continue
        category = SKU_FAMILIES.get(family, "?")
        if category is None:
            continue
        if category == "?":
            category = categorize(desc)
        if category == "Memory":
            desc = re.sub(r"^\d+\s*GB:\s*", "", desc)  # "32 GB: 1 x 32 GB, DDR5..." -> "1 x 32 GB, DDR5..."
        specs.add(desc, category=category)
    specs.model = model or next((unwrap(s.get("skuDescription", "")) for s in skus if str(s.get("skuNumber", "")).startswith("658-")), "") or "Dell system"
    return specs


class DellClient:
    """TechDirect API (only used when keys are configured)."""

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


class Dell(Vendor):
    keyword = "dell"
    aliases = ("servicetag",)
    name = "Dell"
    serial_hint = "service tag"

    def __init__(self, client: DellClient | None = None, http=None) -> None:
        self.client = client
        self.http = http

    def setup(self) -> None:
        if self.client is None and env("DELL_CLIENT_ID") and env("DELL_CLIENT_SECRET"):
            self.client = DellClient(env("DELL_CLIENT_ID"), env("DELL_CLIENT_SECRET"), env("DELL_API_BASE", "https://apigtwb2c.us.dell.com"))
        if self.http is None:
            self.http = make_client()

    def looks_like(self, serial: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9]{7}", serial))

    def lookup(self, tag: str) -> SystemSpecs:
        return self._lookup_api(tag) if self.client else self._lookup_site(tag)

    # ---- support site (no keys) ----
    def _lookup_site(self, tag: str) -> SystemSpecs:
        params = {"serviceTag": tag, "showOriginalConfig": "true", "showCurrentConfig": "true"}
        r = self.http.get(CONFIG_URL, params=params, headers=SITE_HEADERS)
        if r.status_code == 403:
            # Dell's edge (Akamai) wants the cookies a normal page view sets
            # before it answers the XHR. Load the support home, then retry.
            self.warm_up()
            r = self.http.get(CONFIG_URL, params=params, headers=SITE_HEADERS)
        if r.status_code == 403:
            raise Blocked("Dell's website blocked the lookup (its bot protection wants a real browser). "
                          "Add Dell TechDirect API keys on the admin page to look up Dell tags.")
        r.raise_for_status()
        specs = parse_site(r.text, tag)
        if specs is None:
            raise NotFound(tag)
        return specs

    def warm_up(self):
        try:
            return self.http.get(WARMUP_URL, headers=PAGE_HEADERS)
        except Exception:
            return None

    # ---- TechDirect API ----
    def _lookup_api(self, tag: str) -> SystemSpecs:
        try:
            comp = self.client.components(tag)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise NotFound(tag) from exc
            raise
        if not comp.get("components") and not comp.get("productLineDescription"):
            raise NotFound(tag)
        try:
            warranty = self.client.warranty(tag)
        except httpx.HTTPError:
            warranty = {}

        specs = SystemSpecs(
            vendor=self.name,
            serial=tag,
            model=comp.get("productLineDescription") or warranty.get("productLineDescription") or "Unknown model",
            ship_date=(comp.get("shipDate") or warranty.get("shipDate") or "")[:10],
            url=f"{SUPPORT}/support/home/en-us/product-support/servicetag/{tag}/overview",
        )
        ends = [e["endDate"][:10] for e in warranty.get("entitlements") or [] if e.get("endDate")]
        specs.warranty_end = max(ends) if ends else ""
        for c in comp.get("components") or []:
            specs.add(c.get("partDescription") or c.get("itemDescription") or "", int(c.get("partQuantity") or c.get("quantity") or 1))
        return specs
