import base64
import io
import json

import httpx
from PIL import Image as PILImage

from bot.module import Image
from bot.modules.help import Help
from bot.modules.photo import Photo, parse_answer, shrink
from bot.modules.specs import Specs
from bot.registry import Registry
from bot.vendors.base import SystemSpecs, Vendor


class Dummy(Vendor):
    keyword = "dummy"
    name = "Dummy"

    def looks_like(self, serial):
        return len(serial) == 10

    def lookup(self, serial):
        return SystemSpecs(vendor="Dummy", serial=serial, model="Z2 G8 Tower")


def photo(width=3000, height=2000) -> Image:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), (200, 200, 200)).save(buf, format="PNG")
    return Image("sticker.png", "image/png", buf.getvalue())


def vision_server(answer, seen=None, status=200):
    def handler(request):
        body = json.loads(request.read())
        if seen is not None:
            seen.append(body)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "model not found"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": answer}}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def bot(module):
    r = Registry()
    r.add(Help(r))
    specs = Specs([Dummy()])
    specs.setup()
    r.add(specs)
    module.setup()
    r.add(module)
    return r


def test_photo_is_read_and_looked_up():
    seen = []
    p = Photo(base_url="http://llm.test/v1", model="qwen2.5vl:7b", http=vision_server('{"serials": ["MXL2062PMN"], "vendor": "HP"}', seen))
    reply = bot(p).dispatch_images([photo()], "", "U1")
    assert "Read `MXL2062PMN` off the photo (looks like HP)" in reply.text
    assert "*Dummy Z2 G8 Tower* (`MXL2062PMN`)" in reply.text
    body = seen[0]
    assert body["model"] == "qwen2.5vl:7b"
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # the 3000px photo was shrunk before upload
    sent = base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1])
    assert max(PILImage.open(io.BytesIO(sent)).size) <= 1600


def test_messy_answers_still_yield_serials():
    assert parse_answer('<think>hmm</think>{"serials": ["mxl-2062pmn", "MXL2062PMN"], "vendor": "HP"}') == (["MXL2062PMN"], "HP")
    assert parse_answer("The service tag is 9C4BPM4.") == (["9C4BPM4"], "")
    assert parse_answer('{"serials": [], "vendor": ""}') == ([], "")


def test_no_serial_and_model_errors():
    assert "couldn't make out a serial" in bot(Photo(base_url="http://llm.test/v1", http=vision_server('{"serials": [], "vendor": ""}'))).dispatch_images([photo(10, 10)], "", "U1").text
    text = bot(Photo(base_url="http://llm.test/v1", model="nope", http=vision_server("", status=404))).dispatch_images([photo(10, 10)], "", "U1").text
    assert "HTTP 404" in text and "ollama pull nope" in text


def test_registry_without_photo_module_says_none():
    r = Registry()
    r.add(Help(r))
    assert r.dispatch_images([photo(10, 10)], "", "U1") is None


def test_shrink_keeps_unreadable_files_as_is():
    img = Image("x.heic", "image/heic", b"not really an image")
    assert shrink(img) == (b"not really an image", "image/heic")
