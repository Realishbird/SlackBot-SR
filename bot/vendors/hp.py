"""HP and HPE, via PartSurfer's serial search (no keys needed).

PartSurfer returns an HTML page with the product description and, for most
business PCs and servers, the factory parts list (BOM). Consumer models often
have no BOM, in which case only the model is shown. This parses the HTML
loosely, so a PartSurfer redesign may need a fix here.
"""
import re
from html.parser import HTMLParser

import httpx

from bot.vendors.base import NotFound, SystemSpecs, Vendor

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) sr-bot"}
SITES = ("https://partsurfer.hp.com", "https://partsurfer.hpe.com")


class _Tables(HTMLParser):
    """Collects every table as rows of cell text, plus element text by id."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.by_id: dict[str, str] = {}
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None
        self._id_stack: list[tuple[str, str, list[str]]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = []
        if a.get("id") and tag in ("span", "div", "td", "h1", "h2", "label"):
            self._id_stack.append((tag, a["id"], []))

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._stack and self._stack[-1]:
            self._stack[-1][-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
        if self._id_stack and self._id_stack[-1][0] == tag:
            _, ident, text = self._id_stack.pop()
            self.by_id[ident] = " ".join("".join(text).split())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        for _, _, text in self._id_stack:
            text.append(data)


def parse(html: str, serial: str, site: str) -> SystemSpecs | None:
    p = _Tables()
    p.feed(html)
    model = next((v for k, v in p.by_id.items() if re.search(r"(Description|ProductName|lblDesc)", k, re.I) and v), "")

    specs = SystemSpecs(vendor="HP", serial=serial, url=f"{site}/Search.aspx?searchText={serial}")
    for rows in p.tables:
        if not rows:
            continue
        header = [c.lower() for c in rows[0]]
        desc_col = next((i for i, c in enumerate(header) if "description" in c), None)
        if desc_col is None or not any("part" in c for c in header):
            continue
        qty_col = next((i for i, c in enumerate(header) if "qty" in c or "quantity" in c), None)
        for row in rows[1:]:
            if len(row) <= desc_col:
                continue
            qty = 1
            if qty_col is not None and len(row) > qty_col and row[qty_col].isdigit():
                qty = int(row[qty_col])
            specs.add(row[desc_col], qty)
        if not model:
            # The product line usually sits in the first row of the BOM.
            model = rows[1][desc_col] if len(rows) > 1 and len(rows[1]) > desc_col else ""
    if not model and not specs.parts:
        return None
    specs.model = model or "Unknown model"
    return specs


class HP(Vendor):
    keyword = "hp"
    aliases = ("hpe",)
    name = "HP"
    serial_hint = "serial number"

    def __init__(self, http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(timeout=30, headers=UA, follow_redirects=True)

    def looks_like(self, serial: str) -> bool:
        # HP serials are 10 chars, e.g. 5CG1234ABC, CND..., MXL..., CZ...
        return bool(re.fullmatch(r"[A-Z0-9]{10}", serial))

    def lookup(self, serial: str) -> SystemSpecs:
        for site in SITES:
            try:
                r = self.http.get(f"{site}/Search.aspx", params={"searchText": serial})
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            specs = parse(r.text, serial, site)
            if specs:
                return specs
        raise NotFound(serial)
