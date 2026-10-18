import json

import httpx

from bot.module import Module
from bot.modules.help import Help
from bot.modules.specs import Specs
from bot.registry import Registry
from bot.vendors.base import NotFound, SystemSpecs, Vendor, categorize
from bot.vendors import browser
from bot.vendors.dell import Dell, DellClient, parse_warranty
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

LENOVO_PRODUCTS = [{"Id": "LAPTOPS-AND-NETBOOKS/THINKPAD-P-SERIES-LAPTOPS/THINKPAD-P1-GEN-4-TYPE-20Y3-20Y4/20Y3/20Y3003XUS/R914DXCT", "Name": "P1 Gen 4 (type 20Y3, 20Y4 ) Laptop (ThinkPad) - Type 20Y3", "Serial": "R914DXCT"}]
LENOVO_SPECS = {"code": 1, "data": {"ProductName": "ThinkPad P1 Gen 4", "SpecData": [
    {"title": "PERFORMANCE", "name": "Processor", "note": [], "content": ["Intel Xeon&reg; W-11855M (6C / 12T, 3.2 / 4.9GHz, 18MB)"]},
    {"name": "Graphics", "content": ["NVIDIA&reg; RTX A2000 4GB GDDR6"]},
    {"name": "Memory", "content": ["2x 32GB SO-DIMM DDR4-3200 Non-ECC"]},
    {"name": "Storage", "content": ["2TB SSD M.2 2280 PCIe&reg; 4.0x4 Performance NVMe&reg; Opal 2.0"]},
    {"name": "Optical", "content": ["None"]},
    {"name": "Battery", "content": ["Integrated 90Wh"]},
    {"title": "DESIGN", "name": "Display", "content": ["16\" WQXGA (2560x1600) IPS 400nits Anti-glare"]},
    {"name": "Case Color", "content": ["Black"]},
    {"title": "SOFTWARE", "name": "Operating System", "content": ["Windows&reg; 10 Pro 64 for Workstations, English"]},
    {"title": "CONNECTIVITY", "name": "WLAN + Bluetooth", "content": ["Intel&reg; Wi-Fi&reg; 6E AX210, 11ax 2x2 + BT5.1"]},
    {"name": "WWAN", "content": ["None"]},
]}}
LENOVO_WARRANTY_HTML = ('<html><script>var ds_warranties = window.ds_warranties || {"ProductName":"P1","ManufactureDate":"2021-09-30T00:00:00",'
                        '"BaseWarranties":[{"Name":"1YR Battery","Start":"2021-10-15","End":"2022-10-14"},{"Name":"3Y Depot","Start":"2021-10-15","End":"2024-10-14"}],'
                        '"UpmaWarranties":[{"Name":"3Y Premier","End":"2024-10-14"}],"Nested":{"a":[1,{"b":"};"}]}};\nvar other = 1;</script>'
                        + 'x' * 5000 + '</html>')

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


DELL_SITE_HTML = """<div id="dep_reviewspec_div">...</div><script>
    var EncryptServiceTag = "";
    var OriginalConfigData = """ + json.dumps([
    {"skuNumber": "817-BBBC", "skuDescription": "Not selected in this configura tion", "displayPartsData": [{"partNumber": "W21JJ", "description": "INFO,GNRC,OEM,TRACKING,PN", "qty": 1}]},
    {"skuNumber": "658-BFZH", "skuDescription": "Dell Pro Max Tower 9 T2 PW9T22 60", "displayPartsData": []},
    {"skuNumber": "619-BBPL", "skuDescription": "Windows 11 Pro for Workstation s", "displayPartsData": []},
    {"skuNumber": "555-BNLC", "skuDescription": "MediaTek Wi-Fi 7 MT7925, 2x2, 802.11be, MU-MIMO, Bluetooth 5 .4 wireless card", "displayPartsData": []},
    {"skuNumber": "555-BNLB", "skuDescription": "MediaTek MT7925 WLAN Driver", "displayPartsData": []},
    {"skuNumber": "540-BGDX", "skuDescription": "Intel E810-XXV Dual Port 25GbE  SFP28, PCIe Full Height", "displayPartsData": []},
    {"skuNumber": "490-BLMN", "skuDescription": "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition (300 W), 96 GB GDDR7, 4 DP", "displayPartsData": []},
    {"skuNumber": "450-AAFS", "skuDescription": "System Power Cord (US/Thai/Phi lippines)", "displayPartsData": []},
    {"skuNumber": "429-BBCJ", "skuDescription": "8x DVD+/-RW 9.5mm RAM ODD", "displayPartsData": []},
    {"skuNumber": "400-BVDF", "skuDescription": "2 TB Performance SSD, Gen 5, S ED Ready", "displayPartsData": [{"partNumber": "X", "description": "SSDR,2TB,SED,G54,80S3,PM9E1", "qty": 2}]},
    {"skuNumber": "400-BSYT", "skuDescription": "No Hard Drive", "displayPartsData": []},
    {"skuNumber": "389-FQGR", "skuDescription": "Precision 9 T2 Regulatory Labe l, 1500W, DAO", "displayPartsData": []},
    {"skuNumber": "370-BDWS", "skuDescription": "32 GB: 1 x 32 GB, DDR5, 5600 M T/s, RDIMM, ECC", "displayPartsData": []},
    {"skuNumber": "340-DYNT", "skuDescription": "Quick Setup Guide (DAO)", "displayPartsData": []},
    {"skuNumber": "338-CVHG", "skuDescription": "Intel Xeon 658X (144 MB cache,  24 cores, up to 4.9 GHz, 250 W)", "displayPartsData": []},
    {"skuNumber": "328-BHHB", "skuDescription": "Shipping Material (DAO)", "displayPartsData": []},
    {"skuNumber": "321-BMSW", "skuDescription": "Dell Pro Precision 9 T2, 1500W  PSU", "displayPartsData": []},
    {"skuNumber": "210-BVDD", "skuDescription": "Dell Pro Precision 9 T2 (PW9T2 260) CTO base", "displayPartsData": []},
]) + """;
    var tableColumnHeaders = [];
</script>"""
DELL_SITE_EMPTY = '<div></div><script>var OriginalConfigData = [];</script>'


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


def fake_dell_site(cold_403=False):
    seen = {"warm": False}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.dell.com"
        if request.url.path.endswith("/support/home/en-us"):
            seen["warm"] = True
            return httpx.Response(200, text="<html>Dell Support</html>", headers={"set-cookie": "bm_sz=abc; Path=/"})
        if cold_403 and not seen["warm"]:
            return httpx.Response(403, text="<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>")
        tag = request.url.params["serviceTag"]
        return httpx.Response(200, text=DELL_SITE_HTML if tag == "9C4BPM4" else DELL_SITE_EMPTY)
    return Dell(http=mock(handler))


DELL_WARRANTY_SITE = {"warrantyDisplayName": "ProSupport", "warrantyEndDate": "September 24, 2029",
                      "warrantyStartDate": "September 23, 2026", "warrantyResponseCode": "200", "warrantyType": "PROSUPPORT"}


class FakeBrowser:
    """Stands in for bot.vendors.browser.Browser: same open()/get()/post() surface."""

    def __init__(self, deny_first=0, deny_all=False):
        self.deny_first, self.deny_all, self.calls, self.opened = deny_first, deny_all, [], []

    def open(self, url):
        from contextlib import contextmanager

        @contextmanager
        def cm():
            self.opened.append(url)
            yield self
        return cm()

    def wait(self, ms): pass
    def nudge(self): pass
    def cookies(self): return ["_abck", "bm_sz"]

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        if self.deny_all or len([c for c in self.calls if "GetOriginalConfiguration" in c[1]]) <= self.deny_first:
            return browser.Response(403, "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>")
        if "GetOriginalConfiguration" in url:
            return browser.Response(200, DELL_SITE_HTML if "serviceTag=9C4BPM4" in url else DELL_SITE_EMPTY)
        if "encvalue" in url:
            return browser.Response(200, '"0-dGVzdA=="')
        return browser.Response(404, "")

    def post(self, url, body, headers=None):
        self.calls.append(("POST", url))
        assert body == {"assetFormat": "servicetag", "assetId": "0-dGVzdA==", "appName": "home"}
        return browser.Response(200, json.dumps(DELL_WARRANTY_SITE))


def dell_blocked_http():
    return mock(lambda req: httpx.Response(403, text="Access Denied"))


def fake_lenovo():
    def handler(request: httpx.Request) -> httpx.Response:
        if "getproducts" in request.url.path:
            if request.url.params["productId"] != "R914DXCT":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=LENOVO_PRODUCTS)
        if "SpecData" in request.url.path:
            assert request.url.params["model_code"] == "20Y3003XUS"
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


def test_dell_without_keys_uses_support_site():
    text = bot(fake_dell_site()).dispatch("dell 9c4bpm4", "U1").text
    assert "*Dell Dell Pro Precision 9 T2 (PW9T2260)*" in text
    assert "*Processor*\n• Intel Xeon 658X (144 MB cache, 24 cores, up to 4.9 GHz, 250W)" in text
    assert "*Memory*\n• 1 x 32 GB, DDR5, 5600 MT/s, RDIMM, ECC" in text
    assert "2 TB Performance SSD, Gen 5, SED Ready" in text and "DVD+/-RW" in text
    assert "NVIDIA RTX PRO 6000 Blackwell" in text
    assert "*Network*\n• MediaTek Wi-Fi 7 MT7925, 2x2, 802.11be, MU-MIMO, Bluetooth 5.4 wireless card\n• Intel E810-XXV Dual Port 25GbE SFP28, PCIe Full Height" in text
    assert "*Power*\n• Dell Pro Precision 9 T2, 1500W PSU" in text
    assert "Windows 11 Pro for Workstations" in text
    for junk in ("Not selected", "No Hard Drive", "Power Cord", "Regulatory", "Quick Setup", "Shipping", "WLAN Driver", "CTO base", "Max Tower"):
        assert junk not in text, junk
    assert "Warranty" not in text
    assert "No match" in bot(fake_dell_site()).dispatch("dell ZZZZZZZ", "U1").text


def test_dell_site_warms_up_after_403():
    text = bot(fake_dell_site(cold_403=True)).dispatch("dell 9C4BPM4", "U1").text
    assert "Pro Precision 9 T2" in text


def test_dell_falls_back_to_browser_when_blocked():
    fake = FakeBrowser(deny_first=1)  # first in-page call still 403 while the sensor settles
    text = bot(Dell(http=dell_blocked_http(), browser_=fake)).dispatch("dell 9C4BPM4", "U1").text
    assert fake.opened == ["https://www.dell.com/support/home/en-us"]
    assert "*Dell Dell Pro Precision 9 T2 (PW9T2260)*" in text
    assert "Intel Xeon 658X" in text
    assert "Warranty until: 2029-09-24 (ProSupport)" in text
    assert sum(1 for m, u in fake.calls if "GetOriginalConfiguration" in u) == 2
    assert "No match" in bot(Dell(http=dell_blocked_http(), browser_=FakeBrowser())).dispatch("dell ZZZZZZZ", "U1").text


def test_dell_browser_still_blocked_is_explained():
    text = bot(Dell(http=dell_blocked_http(), browser_=FakeBrowser(deny_all=True))).dispatch("dell 9C4BPM4", "U1").text
    assert "even from the bot's built-in browser" in text


def test_dell_browser_not_used_when_plain_request_works():
    fake = FakeBrowser()
    dell = fake_dell_site()
    dell.browser = fake
    assert "Pro Precision 9 T2" in bot(dell).dispatch("dell 9C4BPM4", "U1").text
    assert fake.opened == []


def test_parse_dell_warranty():
    assert parse_warranty(DELL_WARRANTY_SITE) == ("2029-09-24", "ProSupport")
    assert parse_warranty({"warrantyResponseCode": "404", "warrantyEndDate": None}) == ("", "")
    assert parse_warranty({"warrantyEndDate": "2027-01-02T00:00:00", "warrantyType": "BASIC"}) == ("2027-01-02", "BASIC")


def test_dell_not_found():
    assert "No match" in bot(fake_dell(status=404)).dispatch("dell ABC1234", "U1").text


def test_lenovo():
    text = bot(fake_lenovo()).dispatch("lenovo r9-14dxct", "U1").text
    assert "Lenovo ThinkPad P1 Gen 4 (20Y3003XUS)" in text
    assert "*Processor*\n• Processor: Intel Xeon W-11855M" in text
    assert "*Graphics*\n• Graphics: NVIDIA RTX A2000 4GB" in text
    assert "2x 32GB SO-DIMM DDR4-3200" in text
    assert "*Network*\n• WLAN + Bluetooth: Intel Wi-Fi 6E AX210" in text
    assert "Windows 10 Pro 64" in text
    assert "&reg;" not in text and "Case Color" not in text and "Optical" not in text and "WWAN" not in text
    assert "Shipped: 2021-09-30" in text
    assert "Warranty until: 2024-10-14" in text


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
    assert "ThinkPad" in r.dispatch("specs R914DXCT", "U1").text
    assert "Z2 G8" in r.dispatch("specs MXL2062PMN", "U1").text
    assert "PowerEdge" in r.dispatch("specs ABC1234", "U1").text


def test_specs_multiple_and_errors():
    r = bot(fake_lenovo(), fake_hp())
    reply = r.dispatch("specs R9-14DXCT, MXL2062PMN", "U1")
    assert "ThinkPad" in reply.text and "Z2 G8" in reply.text
    assert any(b["type"] == "divider" for b in reply.blocks)
    assert "No match" in r.dispatch("specs ZZZZZZZZ", "U1").text
    assert "doesn't look like" in r.dispatch("specs AB", "U1").text
    assert "Usage" in r.dispatch("specs !!", "U1").text
    assert "Usage" in r.dispatch("specs", "U1").text


def test_bot_protection_403_is_explained():
    blocked = Lenovo(mock(lambda req: httpx.Response(403, text="Access Denied")))
    assert "Lenovo's website blocked the lookup" in bot(blocked).dispatch("lenovo R914DXCT", "U1").text
    dell = Dell(http=mock(lambda req: httpx.Response(403, text="Access Denied")))
    text = bot(dell).dispatch("dell 9C4BPM4", "U1").text
    assert "TechDirect API keys" in text


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
    assert {"hp", "lenovo", "dell"} <= set(specs.by_word)
    assert specs.by_word["dell"].client is None          # no keys: support-site path
    monkeypatch.setenv("DELL_CLIENT_ID", "a")
    monkeypatch.setenv("DELL_CLIENT_SECRET", "b")
    r2 = Registry()
    r2.discover()
    assert r2.modules["specs"].by_word["dell"].client is not None   # keys: TechDirect API
    r3 = Registry()
    r3.discover({"nothing"})
    assert set(r3.modules) == {"help"}
