"""Lenovo, via Lenovo's public support and PSREF endpoints (no keys needed).

1. pcsupport getproducts turns a serial into a product id that ends in
   .../<model code>/<serial>.
2. PSREF SpecData returns the spec sheet for that model code.
3. The warranty page embeds `ds_warranties` JSON, used for the end date.
"""
import json
import re

import httpx

from bot.vendors.base import NotFound, SystemSpecs, Vendor

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) sr-bot"}
# PSREF rows worth showing; everything else (weight, colour, case...) is skipped.
WANTED = ("processor", "memory", "storage", "graphics", "display", "wlan", "ethernet", "battery", "operating system", "power adapter")


class Lenovo(Vendor):
    keyword = "lenovo"
    name = "Lenovo"
    serial_hint = "serial number"

    def __init__(self, http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(timeout=30, headers=UA, follow_redirects=True)

    def looks_like(self, serial: str) -> bool:
        # Typical PC serials: 8 chars, often starting PF, PC, MP, R9, S1...
        return bool(re.fullmatch(r"[A-Z0-9]{8}", serial))

    def lookup(self, serial: str) -> SystemSpecs:
        r = self.http.get("https://pcsupport.lenovo.com/us/en/api/v4/mse/getproducts", params={"productId": serial})
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
        for row in self._spec_rows(model_code):
            label = row.get("Name") or row.get("Title") or ""
            value = row.get("Content") or ""
            if isinstance(value, list):
                value = "; ".join(str(v) for v in value)
            value = re.sub(r"<[^>]+>", " ", str(value))
            if label and value and any(w in label.lower() for w in WANTED):
                specs.add(f"{label}: {value}" if label.lower() not in value.lower() else value)
        specs.warranty_end = self._warranty_end(product_id)
        return specs

    def _spec_rows(self, model_code: str) -> list[dict]:
        if not model_code:
            return []
        try:
            r = self.http.get("https://psref.lenovo.com/api/model/Info/SpecData", params={"model_code": model_code})
            r.raise_for_status()
            return (r.json().get("data") or {}).get("SpecData") or []
        except (httpx.HTTPError, ValueError, AttributeError):
            return []

    def _warranty_end(self, product_id: str) -> str:
        try:
            r = self.http.get(f"https://pcsupport.lenovo.com/us/en/products/{product_id.lower()}/warranty")
            m = re.search(r"var ds_warranties = window\.ds_warranties \|\| (\{.*?\});\s*$", r.text, re.M)
            if not m:
                return ""
            data = json.loads(m.group(1))
        except (httpx.HTTPError, ValueError):
            return ""
        ends = [
            str(w.get("End"))[:10]
            for key in ("BaseWarranties", "UpmaWarranties")
            for w in data.get(key) or []
            if w.get("End")
        ]
        return max(ends) if ends else ""
