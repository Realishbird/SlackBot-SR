"""Lenovo, via Lenovo's public support and PSREF endpoints (no keys needed).

1. pcsupport getproducts turns a serial into a product id that ends in
   .../<model code>/<serial>.
2. PSREF SpecData returns the spec sheet for that model code.
3. The warranty page embeds a `ds_warranties` JSON object with the
   manufacture date and warranty terms.
"""
import html
import json
import re

import httpx

from bot.vendors.base import NotFound, SystemSpecs, Vendor, categorize

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36 sr-bot"}
# PSREF rows worth showing (matched against the row label, case-insensitive).
WANTED = ("processor", "graphics", "memory", "storage", "display", "touchscreen", "battery", "power adapter",
          "wlan", "wwan", "ethernet", "operating system", "camera", "security chip")
WARRANTY_MARK = "window.ds_warranties ||"


def _field(row: dict, *names: str):
    """PSREF has used both `Name` and `name`; accept either."""
    for n in names:
        for key in (n, n.lower(), n.capitalize()):
            if key in row and row[key] not in (None, ""):
                return row[key]
    return None


def clean(text) -> str:
    if isinstance(text, list):
        text = "; ".join(str(t) for t in text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text)))
    return " ".join(text.replace("®", "").replace("™", "").split())


def parse_warranty(page: str) -> dict:
    i = page.find(WARRANTY_MARK)
    if i < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(page[i + len(WARRANTY_MARK):].lstrip())
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


class Lenovo(Vendor):
    keyword = "lenovo"
    name = "Lenovo"
    serial_hint = "serial number"

    def __init__(self, http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(timeout=30, headers=UA, follow_redirects=True)

    def looks_like(self, serial: str) -> bool:
        # Typical PC serials: 8 chars, e.g. R914DXCT, PF2ABCDE, MP1ABCDE.
        return bool(re.fullmatch(r"[A-Z0-9]{8}", serial))

    def lookup(self, serial: str) -> SystemSpecs:
        r = self.http.get("https://pcsupport.lenovo.com/us/en/api/v4/mse/getproducts", params={"productId": serial}, headers=UA)
        r.raise_for_status()
        data = r.json()
        product = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else {}
        product_id = product.get("Id") or product.get("id") or ""
        if not product_id:
            raise NotFound(serial)
        path = product_id.strip("/").split("/")
        model_code = path[-2] if len(path) >= 2 else ""

        specs = SystemSpecs(
            vendor=self.name,
            serial=serial,
            model=product.get("Name") or product.get("name") or model_code,
            url=f"https://pcsupport.lenovo.com/us/en/products/{product_id.lower()}",
        )
        sheet = self._spec_sheet(model_code)
        if sheet.get("ProductName"):
            specs.model = f"{sheet['ProductName']} ({model_code})" if model_code else sheet["ProductName"]
        for row in sheet.get("SpecData") or []:
            label = clean(_field(row, "Name", "Title") or "")
            value = clean(_field(row, "Content") or "")
            if not label or not value or value.lower() in ("none", "no support", "n/a"):
                continue
            if any(w in label.lower() for w in WANTED):
                specs.add(value if label.lower() in value.lower() else f"{label}: {value}", category=categorize(label))

        warranty = self._warranty(product_id)
        specs.ship_date = str(warranty.get("ManufactureDate") or "")[:10]
        ends = [str(w.get("End"))[:10] for key in ("BaseWarranties", "UpmaWarranties") for w in warranty.get(key) or [] if w.get("End")]
        specs.warranty_end = max(ends) if ends else ""
        return specs

    def _spec_sheet(self, model_code: str) -> dict:
        if not model_code:
            return {}
        try:
            r = self.http.get("https://psref.lenovo.com/api/model/Info/SpecData", params={"model_code": model_code}, headers=UA)
            r.raise_for_status()
            return r.json().get("data") or {}
        except (httpx.HTTPError, ValueError, AttributeError):
            return {}

    def _warranty(self, product_id: str) -> dict:
        try:
            r = self.http.get(f"https://pcsupport.lenovo.com/us/en/products/{product_id.lower()}/warranty", headers=UA)
            return parse_warranty(r.text)
        except httpx.HTTPError:
            return {}
