"""Dell, two ways.

Without API keys (default): Dell's support site exposes the "Original
Configuration" of a service tag at
/support/product-details/en-us/ReviewSpecs/GetOriginalConfiguration. It
returns an HTML fragment with a `var OriginalConfigData = [...]` JSON blob:
one entry per order SKU (338-xxxx processor, 370-xxxx memory, ...).

Dell's bot protection (Akamai) refuses that call unless its JavaScript sensor
has run, so when a plain request gets 403 the lookup repeats it from inside a
hidden Chromium (see bot/vendors/browser.py). The browser session also lets
us ask the warranty widget's backend for the service end date.

With TechDirect API keys (DELL_CLIENT_ID / DELL_CLIENT_SECRET): the official
asset-components and asset-entitlements endpoints, which add ship date and
warranty end. Get keys at https://techdirect.dell.com (APIs > Warranty).
"""
import json
import logging
import re
import time
from datetime import datetime

import httpx

from bot.config import env
from bot.vendors import browser
from bot.vendors.base import Blocked, NotFound, SystemSpecs, Vendor, categorize
from bot.vendors.http import make_client

log = logging.getLogger(__name__)

SUPPORT = "https://www.dell.com"
CONFIG_URL = f"{SUPPORT}/support/product-details/en-us/ReviewSpecs/GetOriginalConfiguration"
WARMUP_URL = f"{SUPPORT}/support/home/en-us"
# The warranty page: the browser starts here because everything it needs
# lives under paths Dell serves to it (contractservices, components, productsmfe).
WARRANTY_PAGE = f"{SUPPORT}/support/contractservices/en-us"
PRODUCT_URL = f"{SUPPORT}/support/home/en-us/product-support/servicetag/{{tag}}/overview"
# Model name and picture, as the warranty page shows them (HTML fragment).
DETAILS_URL = f"{SUPPORT}/support/productsmfe/en-us/productdetails?selection={{tag}}&assettype=svctag&appname=warranty&inccomponents=false&isolated=false"
# Warranty widget backend: encode the tag, then ask for the entitlement.
ENC_URL = f"{SUPPORT}/support/components/detectproduct/encvalue/{{tag}}?appname=warranty"
WARRANTY_URL = f"{SUPPORT}/support/contractservices/en-us/entitlement/contractservicesapi/v1"
BLOCKED_MSG = ("Dell's website blocked the lookup (its bot protection wants a real browser). "
               "Add Dell TechDirect API keys on the admin page to look up Dell tags.")
BROWSER_BLOCKED_MSG = ("Dell's bot protection blocked the lookup even from the bot's built-in browser. "
                       "Try again in a minute; if it keeps happening, add Dell TechDirect API keys on the admin page.")
PARTS_BLOCKED_NOTE = ("Dell's website only shows the parts list to a person's browser, so the bot can't fetch it. "
                      "Add Dell TechDirect API keys on the admin page for the full configuration.")
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


def parse_details(html: str) -> str:
    """Model name from the productdetails fragment: the first <h4> (e.g.
    "Dell Pro Precision 9 T2 PW9T2260"), or the product image title."""
    m = re.search(r"<h4[^>]*>(.*?)</h4>", html, re.S) or re.search(r'title="([^"]+)"', html)
    name = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    if not name or re.search(r"not found|no product|unable", name, re.I):
        return ""
    return re.sub(r"\s+", " ", name)


def parse_warranty(data: dict) -> tuple[str, str]:
    """(end date ISO, service name) from the contractservices entitlement reply."""
    if str(data.get("warrantyResponseCode") or "") not in ("200", "") and not data.get("warrantyEndDate"):
        return "", ""
    end = str(data.get("warrantyEndDate") or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            end = datetime.strptime(end[:19], fmt).date().isoformat()
            break
        except ValueError:
            continue
    return end, str(data.get("warrantyDisplayName") or data.get("warrantyType") or "").strip()


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

    def __init__(self, client: DellClient | None = None, http=None, browser_=None) -> None:
        self.client = client
        self.http = http
        self.browser = browser_  # bot.vendors.browser.Browser, or None when unavailable/off

    def setup(self) -> None:
        if self.client is None and env("DELL_CLIENT_ID") and env("DELL_CLIENT_SECRET"):
            self.client = DellClient(env("DELL_CLIENT_ID"), env("DELL_CLIENT_SECRET"), env("DELL_API_BASE", "https://apigtwb2c.us.dell.com"))
        if self.http is None:
            self.http = make_client()
        if self.browser is None and env("DELL_BROWSER", "auto").lower() not in ("off", "0", "false", "no"):
            if browser.available():
                self.browser = browser.Browser()
            else:
                log.info("dell: Playwright not installed, no browser fallback")

    def looks_like(self, serial: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9]{7}", serial))

    def lookup(self, tag: str) -> SystemSpecs:
        return self._lookup_api(tag) if self.client else self._lookup_site(tag)

    # ---- support site (no keys) ----
    def _lookup_site(self, tag: str) -> SystemSpecs:
        params = {"serviceTag": tag, "showOriginalConfig": "true", "showCurrentConfig": "true"}
        r = self.http.get(CONFIG_URL, params=params, headers=SITE_HEADERS)
        if r.status_code == 403 and self.browser is None:
            # Dell's edge (Akamai) wants the cookies a normal page view sets
            # before it answers the XHR. Load the support home, then retry.
            self.warm_up()
            r = self.http.get(CONFIG_URL, params=params, headers=SITE_HEADERS)
        if r.status_code == 403:
            if self.browser is None:
                raise Blocked(BLOCKED_MSG)
            log.info("dell: plain request for %s got 403, switching to the browser", tag)
            return self._lookup_browser(tag)
        r.raise_for_status()
        specs = parse_site(r.text, tag)
        if specs is None:
            raise NotFound(tag)
        return specs

    # ---- support site through the hidden browser ----
    def _lookup_browser(self, tag: str) -> SystemSpecs:
        """Dell serves the warranty widget and the product card to the hidden
        browser, but (as of October 2026) refuses the product-details paths
        that hold the parts list. So: model and warranty always, parts when
        Dell lets them through, otherwise a note saying why they are missing."""
        report = lambda step, resp: log.info("dell browser: %s -> HTTP %s", step, resp.status)  # noqa: E731
        with self.browser.open(WARRANTY_PAGE) as s:
            model = ""
            try:
                d = s.get(DETAILS_URL.format(tag=tag), headers={"Accept": "text/html, */*; q=0.01"})
                report("product card", d)
                if d.status == 200:
                    model = parse_details(d.text)
            except Exception as exc:
                log.info("dell: product card for %s failed: %r", tag, exc)
            try:
                warranty_end, service = self.fetch_warranty(s, tag)
            except Exception as exc:  # warranty is a bonus; never fail the lookup over it
                log.info("dell: warranty lookup for %s failed: %r", tag, exc)
                warranty_end, service = "", ""

            r = self.fetch_config(s, tag, report=report)
            specs = parse_site(r.text, tag) if r.status == 200 else None
            if specs is None:
                if r.status == 200 or (not model and not warranty_end):
                    if r.status == 403:
                        raise Blocked(BROWSER_BLOCKED_MSG)
                    if r.status >= 400:
                        raise RuntimeError(f"Dell support site answered HTTP {r.status}")
                    raise NotFound(tag)
                specs = SystemSpecs(vendor=self.name, serial=tag, url=PRODUCT_URL.format(tag=tag), note=PARTS_BLOCKED_NOTE)
            specs.model = model or specs.model
            if warranty_end:
                specs.warranty_end = f"{warranty_end} ({service})" if service else warranty_end
        return specs

    @staticmethod
    def fetch_config(s, tag: str, report=None):
        """Get the Original Configuration through the browser session, trying
        progressively more "human" routes while Akamai keeps saying 403:
        fetch() from the support home, then from the tag's own product page,
        then navigating straight to the configuration URL."""
        url = f"{CONFIG_URL}?serviceTag={tag}&showOriginalConfig=true&showCurrentConfig=true"
        headers = {"Accept": "text/html, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"}
        say = report or (lambda *_: None)

        r = s.get(url, headers=headers)
        say("fetch from support home", r)
        if r.status == 403:
            s.settle()
            r = s.get(url, headers=headers)
            say("fetch again after settling", r)
        if r.status == 403:
            r = s.goto(url)
            say("navigate to config url", r)
            if r.status == 200 and "OriginalConfigData" not in r.text:
                r = browser.Response(403, r.text)
        return r

    @staticmethod
    def fetch_warranty(s, tag: str) -> tuple[str, str]:
        enc = s.get(ENC_URL.format(tag=tag), headers={"Accept": "application/json, text/plain, */*"})
        if enc.status != 200:
            return "", ""
        token = enc.text.strip().strip('"')
        if not token or token.lower().startswith("<"):
            return "", ""
        r = s.post(WARRANTY_URL, {"assetFormat": "servicetag", "assetId": token, "appName": "home"},
                   headers={"Accept": "application/json, text/plain, */*"})
        if r.status != 200:
            return "", ""
        return parse_warranty(r.json())

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
