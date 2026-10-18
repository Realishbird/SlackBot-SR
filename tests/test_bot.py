import json

import httpx

from bot.module import Module, Reply
from bot.modules.dell import DellClient, DellHardware, categorize
from bot.modules.help import Help
from bot.registry import Registry

COMPONENTS = {
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
WARRANTY = [{"serviceTag": "ABC1234", "entitlements": [{"endDate": "2023-03-05T00:00:00Z"}, {"endDate": "2025-03-05T00:00:00Z"}]}]


def fake_dell(status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer t"
        if status != 200:
            return httpx.Response(status)
        if "asset-components" in request.url.path:
            return httpx.Response(200, json=COMPONENTS)
        return httpx.Response(200, json=WARRANTY)
    return DellClient("id", "secret", "https://dell.test", httpx.Client(transport=httpx.MockTransport(handler)))


def registry_with(*mods):
    r = Registry()
    r.add(Help(r))
    for m in mods:
        r.add(m)
    return r


def test_categorize():
    assert categorize("Intel Xeon Gold 6230") == "Processor"
    assert categorize("16GB RDIMM 3200") == "Memory"
    assert categorize("PERC H755") == "RAID / storage controller"
    assert categorize("Bezel") == "Other"


def test_dell_summary():
    reply = registry_with(DellHardware(fake_dell())).dispatch("dell abc1234", "U1")
    assert "PowerEdge R740" in reply.text
    assert "2 x Intel Xeon Gold" in reply.text
    assert "12 x 32GB RDIMM" in reply.text
    assert "Warranty until: 2025-03-05" in reply.text
    assert "Bezel" not in reply.text
    assert reply.blocks


def test_dell_alias_and_errors():
    r = registry_with(DellHardware(fake_dell(status=404)))
    assert "no record" in r.dispatch("tag ABC1234", "U1").text
    assert "doesn't look like" in r.dispatch("dell !!", "U1").text
    assert "Usage" in r.dispatch("dell", "U1").text


def test_unknown_falls_back_to_help():
    reply = registry_with(DellHardware(fake_dell())).dispatch("frobnicate", "U1")
    assert "`dell <service tag>" in reply.text


def test_module_error_is_contained():
    class Boom(Module):
        keyword = "boom"
        def handle(self, args, user_id):
            raise RuntimeError("x")
    assert "hit an error" in registry_with(Boom()).dispatch("boom", "U1").text


def test_discover_loads_modules(monkeypatch):
    monkeypatch.setenv("DELL_CLIENT_ID", "a")
    monkeypatch.setenv("DELL_CLIENT_SECRET", "b")
    r = Registry()
    r.discover()
    assert {"help", "dell"} <= set(r.modules)
    r2 = Registry()
    r2.discover({"nothing"})
    assert set(r2.modules) == {"help"}


def test_missing_dell_keys_skips_module(monkeypatch):
    monkeypatch.delenv("DELL_CLIENT_ID", raising=False)
    r3 = Registry()
    r3.discover()
    assert "dell" not in r3.modules
