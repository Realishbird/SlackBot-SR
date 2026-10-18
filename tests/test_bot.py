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

HP_API = {
    "Status": {"Code": "E-0099", "Message": "Internal Error Occcured"},
    "Body": {
        "SearchString": "MXL2062PMN", "Identifier": "SERIAL",
        "ProductBOM": [{"ProductName": "HP Z2 G8 Tower Workstation Desktop PC RCTO Base Model", "ProductNumber": "287S5AV", "EnhancedDescription": "SPS-Dust Filter Z2 TWR"}],
        "SerialNumberBOM": {
            "wwsnrsinput": {"serial_no": "MXL2062PMN", "product_no": "287S5AV", "user_name": "BU HP Z2 TWR G8 RCTO"},
            "unit_configuration": [
                {"part_number": "703216-001", "part_description": "CA, Mini DisplayPort (Mini DP) to DP", "part_quantity": "8"},
                {"part_number": "708984-001", "part_description": "LBL, Quality Tracking", "part_quantity": "2"},
                {"part_number": "837117-003", "part_description": "HDD, 1TB,WD XL1000C,SATA-6G,512e", "part_quantity": "1"},
                {"part_number": "L42505-001", "part_description": "DPK, WIN 10 PRO HIGH-END", "part_quantity": "1"},
                {"part_number": "L55990-002", "part_description": "WLAN 11ax 2x2 INT AX201NGW-W HrP 2 NV", "part_quantity": "1"},
                {"part_number": "L57519-002", "part_description": "PCA, Radeon Pro WX 3200 4GB", "part_quantity": "1"},
                {"part_number": "L57519-002", "part_description": "PCA, Radeon Pro WX 3200 4GB", "part_quantity": "1"},
                {"part_number": "L94255-5A1", "part_description": "DIMM,16GB PC4-3200MHz,1Rx8,nECC", "part_quantity": "1"},
                {"part_number": "L94255-5A1", "part_description": "DIMM,16GB PC4-3200MHz,1Rx8,nECC", "part_quantity": "1"},
                {"part_number": "L97053-001", "part_description": "Cooler, CPU, 125W Z2G5 TWR", "part_quantity": "1"},
                {"part_number": "M16789-001", "part_description": "512GB M.2 2280 PC711 PCIe Gen3x4 TLC", "part_quantity": "1"},
                {"part_number": "M45850-001", "part_description": "IC,uP,I,RKL i9-11900K,3.5GHz,125W", "part_quantity": "1"},
                {"part_number": "M48476-001", "part_description": "SSA, L6, Z2 TWR G8, 92, 700W, WKS", "part_quantity": "1"},
                {"part_number": "M21748-001MX", "part_description": "MBD,Titan,RKL-W580,Z2 TWR WS (", "part_quantity": "1"},
                {"part_number": "BIOS_VERSION", "part_description": "-N/A-", "part_quantity": "1"},
                {"part_number": "IMG_Desc1", "part_description": "3X47 6J6S 6b7N", "part_quantity": "1"},
            ],
        },
    },
}
HP_API_EMPTY = {"Status": {"Code": "E-0099"}, "Body": {"SearchString": "MLX2062PMN", "Identifier": "SERIAL", "SerialNumberBOM": {"spare_part": []}}}


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
        if request.url.host == "partsurfer.hpcloud.hp.com":
            assert request.headers["Origin"] == "https://partsurfer.hp.com"
            if "MXL2062PMN" in request.url.params["input"]:
                return httpx.Response(200, json=HP_API)
            return httpx.Response(200, json=HP_API_EMPTY)
        return httpx.Response(200, text="<html>No additional information for X found in PartSurfer.</html>")
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
    text = bot(fake_hp()).dispatch("hp mxl2062pmn", "U1").text
    assert "HP Z2 G8 Tower Workstation Desktop PC (287S5AV)" in text
    assert "CPU RKL i9-11900K" in text
    assert "2 x DIMM,16GB PC4-3200MHz" in text
    assert "2 x Radeon Pro WX 3200 4GB" in text
    assert "HDD, 1TB" in text and "512GB M.2" in text
    assert "*Power*\n• SSA, L6, Z2 TWR G8, 92, 700W" in text
    assert "*System board*" in text and "*Cooling*" in text
    assert "OS license: WIN 10 PRO" in text
    assert "LBL" not in text and "Mini DisplayPort" not in text and "BIOS" not in text and "3X47" not in text
    assert "No match" in bot(fake_hp()).dispatch("hp MLX2062PMN", "U1").text


def test_specs_guesses_vendor():
    r = bot(fake_dell(), fake_lenovo(), fake_hp())
    assert "ThinkPad" in r.dispatch("specs PF2ABCDE", "U1").text
    assert "Z2 G8" in r.dispatch("specs MXL2062PMN", "U1").text
    assert "PowerEdge" in r.dispatch("specs ABC1234", "U1").text


def test_specs_multiple_and_errors():
    r = bot(fake_lenovo(), fake_hp())
    reply = r.dispatch("specs PF2ABCDE, MXL2062PMN", "U1")
    assert "ThinkPad" in reply.text and "Z2 G8" in reply.text
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
