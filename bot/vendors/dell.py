"""Dell, via the TechDirect API (the data behind "Original configuration").

Dell's support website blocks automated requests. Get API keys at
https://techdirect.dell.com (APIs > Warranty) and set DELL_CLIENT_ID and
DELL_CLIENT_SECRET.
"""
import re
import time

import httpx

from bot.config import env
from bot.vendors.base import NotFound, SystemSpecs, Vendor


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


class Dell(Vendor):
    keyword = "dell"
    aliases = ("servicetag",)
    name = "Dell"
    serial_hint = "service tag"

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

    def looks_like(self, serial: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9]{7}", serial))

    def lookup(self, tag: str) -> SystemSpecs:
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
            url=f"https://www.dell.com/support/home/en-us/product-support/servicetag/{tag}/overview",
        )
        ends = [e["endDate"][:10] for e in warranty.get("entitlements") or [] if e.get("endDate")]
        specs.warranty_end = max(ends) if ends else ""
        for c in comp.get("components") or []:
            specs.add(c.get("partDescription") or c.get("itemDescription") or "", int(c.get("partQuantity") or c.get("quantity") or 1))
        return specs
