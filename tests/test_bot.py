import httpx

from bot.module import Module
from bot.modules.help import Help
from bot.modules.specs import Specs
from bot.registry import Registry
from bot.vendors.base import NotFound, SystemSpecs, Vendor, categorize
from bot.vendors.dell import Dell, DellClient
from bot.vendors.hp import HP
from bot.vendors.lenovo import Lenovo

DELL_COMPONENTS = {
    "serviceTag": "ABC1234",
    "productLineDescription": "PowerEdge R740",
    "shipDate": "2020-03-04T00:00:00Z",
    "components": [
        {"itemDescription": "Intel Xeon Gold 6230 2.1G, 20C/40T", "partQuantity": 2},
        {"itemDescription": "32GB RDIMM, 2933MT/s, Dual Rank", "partQuantity": 12},
        {"partDescription": "1.92TB SSD SATA Mixed Use 6Gbps", "partQuantity": 4},
        {"itemDescription": "PERC H740P RAID Controller", "partQuantity": 1},
        {"itemDescription": "Broadcom 57416 2 Port 10Gb Base-T Network Daughter Card"},
        {"itemDescription": "Dual, Hot-plug, Redundant Power Supply, 750W", "partQuantity": 2},
        {"itemDescription": "iDRAC9 Enterprise"},
        {"itemDescription": "Bezel"},
    ],
}
DELL_WARRANTY = [{"serviceTag": "ABC1234", "entitlements": [{"endDate": "2023-03-05T00:00:00Z"}, {"endDate": "2025-03-05T00:00:00Z"}]}]

LENOVO_PRODUCTS = [{"Id": "LAPTOPS-AND-NETBOOKS/THINKPAD-T-SERIES-LAPTOPS/THINKPAD-T14-GEN-2-TYPE-20W0-20W1/20W0/20W0S00G00/PF2ABCDE", "Name": "ThinkPad T14 Gen 2 (Type 20W0, 20W1) - Type 20W0"}]
LENOVO_SPECS = {"data": {"SpecData": [
    {"Name": "Processor", "Content": ["Intel Core i5-1135G7"]},
    {"Name": "Memory", "Content": "16GB DDR4-3200"},
    {"Name": "Weight", "Content": "1.5 kg"},
]}}
LENOVO_WARRANTY_HTML = 'x\nvar ds_warranties = window.ds_warranties || {"BaseWarranties":[{"End":"2024-05-01"},{"End":"2025-05-01"}]};\ny'

HP_HTML = """
<html><body>
<span id="ctl00_BodyContentPlaceHolder_lblDescription">HP EliteBook 840 G8 Notebook PC</span>
<table id="ctl00_BodyContentPlaceHolder_gridCOMBOM">
<tr><th>Part Number</th><th>Description</th><th>Qty</th></tr>
<tr><td>M36311-001</td><td>Processor Intel Core i7-1165G7</td><td>1</td></tr>
<tr><td>L67710-001</td><td>8GB DDR4 3200 SODIMM memory</td><td>2</td></tr>
<tr><td>M17414-001</td><td>512GB NVMe SSD</td><td>1</td></tr>
</table></body></html>
"""


def mock(handler, **kw):
    return httpx.Client(transport=httpx.MockTransport(handler), **kw)


def fake_dell(status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer t"
        if status != 200:
            return httpx.Response(status)
        if "asset-components" in request.url.path:
            return httpx.Response(200, json=DELL_COMPONENTS)
        return httpx.Response(200, json=DELL_WARRANTY)
    return Dell(DellClient("id", "secret", "https://dell.test", mock(handler)))


def fake_lenovo():
    def handler(request: httpx.Request) -> httpx.Response:
        if "getproducts" in request.url.path:
            if request.url.params["productId"] != "PF2ABCDE":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=LENOVO_PRODUCTS)
        if "SpecData" in request.url.path:
            assert request.url.params["model_code"] == "20W0S00G00"
            return httpx.Response(200, json=LENOVO_SPECS)
        return httpx.Response(200, text=LENOVO_WARRANTY_HTML)
    return Lenovo(mock(handler))


def fake_hp():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "partsurfer.hp.com" and request.url.params["searchText"] == "5CG1234ABC":
            return httpx.Response(200, text=HP_HTML)
        return httpx.Response(200, text="<html>No results</html>")
    return HP(mock(handler))


def bot(*vendors):
    r = Registry()
    r.add(Help(r))
    specs = Specs(list(vendors))
    specs.setup()
    r.add(specs)
    return r


def test_categorize():
    assert categorize("Intel Xeon Gold 6230") == "Processor"
    assert categorize("16GB RDIMM 3200") == "Memory"
    assert categorize("PERC H755") == "RAID / storage controller"
    assert categorize("Bezel") == "Other"


def test_dell():
    text = bot(fake_dell()).dispatch("dell abc1234", "U1").text
    assert "Dell PowerEdge R740" in text
    assert "2 x Intel Xeon Gold" in text
    assert "12 x 32GB RDIMM" in text
    assert "Warranty until: 2025-03-05" in text
    assert "Bezel" not in text


def test_dell_not_found():
    assert "No match" in bot(fake_dell(status=404)).dispatch("dell ABC1234", "U1").text


def test_lenovo():
    text = bot(fake_lenovo()).dispatch("lenovo pf2abcde", "U1").text
    assert "ThinkPad T14 Gen 2" in text
    assert "Intel Core i5-1135G7" in text
    assert "16GB DDR4-3200" in text
    assert "Weight" not in text
    assert "Warranty until: 2025-05-01" in text


def test_hp():
    text = bot(fake_hp()).dispatch("hp 5CG1234ABC", "U1").text
    assert "HP EliteBook 840 G8" in text
    assert "2 x 8GB DDR4 3200 SODIMM memory" in text
    assert "*Storage*" in text


def test_specs_guesses_vendor():
    r = bot(fake_dell(), fake_lenovo(), fake_hp())
    assert "ThinkPad" in r.dispatch("specs PF2ABCDE", "U1").text
    assert "EliteBook" in r.dispatch("specs 5CG1234ABC", "U1").text
    assert "PowerEdge" in r.dispatch("specs ABC1234", "U1").text


def test_specs_multiple_and_errors():
    r = bot(fake_lenovo(), fake_hp())
    reply = r.dispatch("specs PF2ABCDE, 5CG1234ABC", "U1")
    assert "ThinkPad" in reply.text and "EliteBook" in reply.text
    assert any(b["type"] == "divider" for b in reply.blocks)
    assert "No match" in r.dispatch("specs ZZZZZZZZ", "U1").text
    assert "doesn't look like" in r.dispatch("specs !!", "U1").text
    assert "Usage" in r.dispatch("specs", "U1").text


def test_vendor_outage_is_reported():
    class Down(Vendor):
        keyword = "down"
        name = "Down"
        def lookup(self, serial):
            raise httpx.ConnectError("nope")
    assert "Couldn't reach Down" in bot(Down()).dispatch("down ABC1234", "U1").text


def test_unknown_falls_back_to_help():
    text = bot(fake_dell(), fake_lenovo()).dispatch("frobnicate", "U1").text
    assert "`specs <serial>" in text and "`lenovo <serial number>`" in text


def test_module_error_is_contained():
    class Boom(Module):
        keyword = "boom"
        def handle(self, args, user_id):
            raise RuntimeError("x")
    r = Registry()
    r.add(Help(r))
    r.add(Boom())
    assert "hit an error" in r.dispatch("boom", "U1").text


def test_discover(monkeypatch):
    monkeypatch.delenv("DELL_CLIENT_ID", raising=False)
    r = Registry()
    r.discover()
    assert {"help", "specs"} <= set(r.modules)
    specs = r.modules["specs"]
    assert {"hp", "lenovo"} <= set(specs.by_word) and "dell" not in specs.by_word
    monkeypatch.setenv("DELL_CLIENT_ID", "a")
    monkeypatch.setenv("DELL_CLIENT_SECRET", "b")
    r2 = Registry()
    r2.discover()
    assert "dell" in r2.modules["specs"].by_word
    r3 = Registry()
    r3.discover({"nothing"})
    assert set(r3.modules) == {"help"}
