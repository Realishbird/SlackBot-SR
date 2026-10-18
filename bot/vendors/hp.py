"""HP, via the JSON backend of the new PartSurfer site (no keys needed).

partsurfer.hp.com is a JavaScript app; the data comes from
partsurfer.hpcloud.hp.com. A serial search returns the unit's factory
configuration (SerialNumberBOM.unit_configuration) and the product family
(ProductBOM). HPE servers live on partsurfer.hpe.com, which still serves
plain HTML; that is tried second.
"""
import re
from collections import OrderedDict
from html.parser import HTMLParser

from bot.vendors.base import NotFound, SystemSpecs, Vendor
from bot.vendors.http import make_client

API = "https://partsurfer.hpcloud.hp.com/bff/proxy/get"
HEADERS = {"Origin": "https://partsurfer.hp.com", "Referer": "https://partsurfer.hp.com/"}
HPE_SITE = "https://partsurfer.hpe.com"

# Packaging, labels, cables, documentation and the pseudo-parts HP lists for
# firmware/MAC/UUID placeholders. None of these say anything about the hardware.
SKIP = re.compile(
    r"^(LBL|BAG|CARTON|CUSHION|GDE|CARD|DOC|KIT, ?DOC|PWRCORD|CA,|CA ASSY|CABLE|SCREW|RUBBER|FOAM|SHEET)"
    r"|^(BIOS_|IMAGE_|IMG_|MAC_|MANUF_|MSFT_|PCA_|UUID_|FeatureByte)"
    r"|holder$|-N/A-",
    re.I,
)


def tidy(desc: str) -> str:
    """Make HP's terse part names a little more readable."""
    desc = " ".join(desc.split())
    desc = re.sub(r"^IC,\s*uP,\s*[A-Z],\s*", "CPU ", desc)   # IC,uP,I,RKL i9-11900K -> CPU RKL i9-11900K
    desc = re.sub(r"^PCA,\s*", "", desc)                      # PCA, Radeon Pro WX 3200 -> Radeon Pro WX 3200
    desc = re.sub(r"^DPK,\s*", "OS license: ", desc)          # DPK, WIN 10 PRO -> OS license: WIN 10 PRO
    return desc


def parse_api(data: dict, serial: str) -> SystemSpecs | None:
    body = data.get("Body") or {}
    snb = body.get("SerialNumberBOM") or {}
    unit = snb.get("unit_configuration") or []
    product_bom = body.get("ProductBOM") or []
    if not unit and not product_bom:
        return None
    meta = snb.get("wwsnrsinput") or {}
    model = (product_bom[0].get("ProductName") if product_bom else "") or meta.get("user_name") or "Unknown model"
    model = re.sub(r"\s*RCTO Base Model$", "", model)
    product_no = meta.get("product_no") or (product_bom[0].get("ProductNumber") if product_bom else "")

    specs = SystemSpecs(vendor="HP", serial=serial, model=f"{model} ({product_no})" if product_no else model,
                        url=f"https://partsurfer.hp.com/?searchtext={serial}")
    merged: "OrderedDict[str, int]" = OrderedDict()
    for row in unit:
        desc = (row.get("part_description") or "").strip()
        if not desc or SKIP.search(desc):
            continue
        try:
            qty = int(float(row.get("part_quantity") or 1))
        except ValueError:
            qty = 1
        merged[tidy(desc)] = merged.get(tidy(desc), 0) + qty   # HP repeats a line per unit
    for desc, qty in merged.items():
        specs.add(desc, qty)
    return specs


class _Tables(HTMLParser):
    """Collects every table as rows of cell text, plus element text by id (HPE pages)."""

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


def parse_hpe_html(html: str, serial: str) -> SystemSpecs | None:
    if "No additional information" in html:
        return None
    p = _Tables()
    p.feed(html)
    model = next((v for k, v in p.by_id.items() if re.search(r"(Description|ProductName|lblDesc)", k, re.I) and v), "")
    specs = SystemSpecs(vendor="HPE", serial=serial, url=f"{HPE_SITE}/Search.aspx?searchText={serial}")
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
            qty = int(row[qty_col]) if qty_col is not None and len(row) > qty_col and row[qty_col].isdigit() else 1
            specs.add(row[desc_col], qty)
        if not model and len(rows) > 1 and len(rows[1]) > desc_col:
            model = rows[1][desc_col]
    if not model and not specs.parts:
        return None
    specs.model = model or "Unknown model"
    return specs


class HP(Vendor):
    keyword = "hp"
    aliases = ("hpe",)
    name = "HP"
    serial_hint = "serial number"

    def __init__(self, http=None) -> None:
        self.http = http or make_client()

    def looks_like(self, serial: str) -> bool:
        # HP serials are 10 chars, e.g. MXL2062PMN, 5CG1234ABC, CND...
        return bool(re.fullmatch(r"[A-Z0-9]{10}", serial))

    def lookup(self, serial: str) -> SystemSpecs:
        r = self.http.get(API, params={"input": f"/Search/GenericSearch/{serial}/country/US/usertype/EXT"}, headers=HEADERS)
        if r.status_code == 403:
            raise RuntimeError("HP refused the request (403, bot protection)")
        r.raise_for_status()
        try:
            specs = parse_api(r.json(), serial)
        except ValueError:
            specs = None
        if specs:
            return specs
        # Not an HP PC/printer serial; maybe an HPE server.
        try:
            r = self.http.get(f"{HPE_SITE}/Search.aspx", params={"searchText": serial}, headers={"Referer": f"{HPE_SITE}/"})
            specs = parse_hpe_html(r.text, serial) if r.status_code == 200 else None
        except Exception:
            specs = None
        if specs:
            return specs
        raise NotFound(serial)
