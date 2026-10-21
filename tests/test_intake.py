import io

import httpx
from PIL import Image as PILImage

from bot.module import Image
from bot.modules.help import Help
from bot.modules.intake import Intake, slug
from bot.modules.photo import Photo
from bot.modules.specs import Specs
from bot.registry import Registry
from bot.vendors.base import NotFound, SystemSpecs, Vendor


class Dummy(Vendor):
    keyword = "dummy"
    name = "HP"

    def looks_like(self, serial):
        return len(serial) == 10

    def lookup(self, serial):
        if serial == "NOPE000000":
            raise NotFound(serial)
        specs = SystemSpecs(vendor="HP", serial=serial, model="HP Z2 G8 Tower Workstation Desktop PC (287S5AV)")
        specs.add("Intel Core i9-11900K")
        return specs


class FakeSlack:
    """Records the Slack calls the module makes."""
    def __init__(self, taken=()):
        self.taken, self.calls, self.n = set(taken), [], 0

    def conversations_create(self, name, is_private=False):
        self.calls.append(("create", name))
        if name in self.taken:
            raise RuntimeError("The request to the Slack API failed. error: name_taken")
        self.n += 1
        return {"channel": {"id": f"C{self.n:03d}", "name": name}}

    def conversations_invite(self, channel, users):
        self.calls.append(("invite", channel, users))
        return {"ok": True}

    def conversations_setTopic(self, channel, topic):
        self.calls.append(("topic", channel, topic))

    def chat_postMessage(self, channel, text, blocks=None):
        self.calls.append(("post", channel, text))

    def users_list(self, limit=200, cursor=None):
        return {"members": [{"id": "U0ALICE", "name": "alice", "profile": {"display_name": "Alice W"}}]}


def photo_bytes():
    buf = io.BytesIO()
    PILImage.new("RGB", (100, 100)).save(buf, format="PNG")
    return buf.getvalue()


def vision(answer):
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})))


def bot(slack, invite="", prefix="", vision_answer='{"serials": ["MXL2062PMN"], "vendor": "HP"}'):
    r = Registry()
    r.slack = slack
    r.add(Help(r))
    specs = Specs([Dummy()])
    specs.setup()
    r.add(specs)
    p = Photo(base_url="http://llm.test/v1", http=vision(vision_answer))
    p.setup()
    r.add(p)
    i = Intake(invite=invite, prefix=prefix)
    i.setup()
    r.add(i)
    return r


def test_slug():
    assert slug("HP Z2 G8 Tower Workstation Desktop PC (287S5AV)") == "hp-z2-g8-tower"
    assert slug("Dell Precision 7920 Tower", "dev-") == "dev-dell-precision-7920-tower"
    assert slug("ThinkPad P1 Gen 4 [20Y3]") == "thinkpad-p1-gen-4"


def test_photo_plus_intake_makes_channel_adds_people_posts_specs():
    slack = FakeSlack()
    r = bot(slack, invite="@alice, U0BOSS")
    reply = r.dispatch_images([Image("sticker.png", "image/png", photo_bytes())], "intake <@U0BOB> please", "U0ME")
    assert "Made <#C001> for *HP Z2 G8 Tower Workstation Desktop PC (287S5AV)* (`MXL2062PMN`)" in reply.text
    assert "<@U0BOB>" in reply.text and "<@U0ALICE>" in reply.text and "<@U0BOSS>" in reply.text
    assert ("create", "hp-z2-g8-tower") in slack.calls
    assert ("invite", "C001", "U0ME,U0BOB,U0ALICE,U0BOSS") in slack.calls
    assert ("topic", "C001", "HP Z2 G8 Tower Workstation Desktop PC (287S5AV) | MXL2062PMN") in slack.calls
    post = next(c for c in slack.calls if c[0] == "post")
    assert post[1] == "C001" and "i9-11900K" in post[2]


def test_photo_without_intake_word_just_looks_up():
    slack = FakeSlack()
    reply = bot(slack).dispatch_images([Image("s.png", "image/png", photo_bytes())], "", "U0ME")
    assert "Read `MXL2062PMN` off the photo" in reply.text and slack.calls == []


def test_typed_serial_name_override_and_name_clash():
    slack = FakeSlack(taken={"brian-box"})
    reply = bot(slack).dispatch("intake mxl2062pmn name=brian-box", "U0ME")
    assert ("create", "brian-box") in slack.calls and ("create", "brian-box-2pmn") in slack.calls
    assert "Made <#C001>" in reply.text


def test_unknown_serial_still_gets_a_channel_named_after_it():
    slack = FakeSlack()
    reply = bot(slack).dispatch("intake NOPE000000", "U0ME")
    assert ("create", "nope000000") in slack.calls and "named after the serial" in reply.text


def test_missing_scope_is_explained():
    class NoScope(FakeSlack):
        def conversations_create(self, name, is_private=False):
            raise RuntimeError("error: missing_scope")
    reply = bot(NoScope()).dispatch("intake MXL2062PMN", "U0ME")
    assert "channels:manage" in reply.text and "Reinstall" in reply.text


def test_unreadable_photo_asks_for_the_serial():
    reply = bot(FakeSlack(), vision_answer='{"serials": [], "vendor": ""}').dispatch_images([Image("s.png", "image/png", photo_bytes())], "intake", "U0ME")
    assert "type `intake <serial>`" in reply.text
