import httpx

from bot.modules.chat import Chat, strip_thinking, to_mrkdwn
from bot.modules.help import Help
from bot.modules.specs import Specs
from bot.registry import Registry
from bot.vendors.base import SystemSpecs, Vendor


class Dummy(Vendor):
    keyword = "dummy"
    name = "Dummy"

    def lookup(self, serial):
        return SystemSpecs(vendor="Dummy", serial=serial, model="Box")


def fake_server(answer="Plug it in.", status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if seen is not None:
            seen.append((str(request.url), request.headers.get("authorization", ""), body))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "model 'nope' not found"}})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": answer}}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def bot(chat):
    r = Registry()
    r.add(Help(r))
    specs = Specs([Dummy()])
    specs.setup()
    r.add(specs)
    chat.setup()
    r.add(chat)
    return r


def test_plain_message_goes_to_the_model():
    seen = []
    chat = Chat(base_url="http://llm.test/v1/", model="llama3.2:3b", api_key="k", system_prompt="Be terse.", http=fake_server(seen=seen))
    text = bot(chat).dispatch("why is my docking station not charging", "U1").text
    assert text == "Plug it in."
    url, auth, body = seen[0]
    assert url == "http://llm.test/v1/chat/completions" and auth == "Bearer k"
    assert '"model":"llama3.2:3b"' in body and "Be terse." in body and "docking station" in body


def test_commands_still_win_over_chat():
    chat = Chat(base_url="http://llm.test/v1", http=fake_server())
    r = bot(chat)
    assert "Dummy Box" in r.dispatch("dummy ABC1234", "U1").text
    assert "Here's what I can do" in r.dispatch("help", "U1").text
    assert "Here's what I can do" in r.dispatch("", "U1").text
    assert r.dispatch("ask what is RAID", "U1").text == "Plug it in."


def test_model_errors_are_explained():
    assert "HTTP 404" in bot(Chat(base_url="http://llm.test/v1", http=fake_server(status=404))).dispatch("hi", "U1").text

    def down(request):
        raise httpx.ConnectError("refused")
    text = bot(Chat(base_url="http://llm.test/v1", http=httpx.Client(transport=httpx.MockTransport(down)))).dispatch("hi", "U1").text
    assert "Couldn't reach the chat model" in text


def test_chat_off_without_url(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    try:
        Chat().setup()
    except RuntimeError as exc:
        assert "chat is off" in str(exc)
    else:
        raise AssertionError("expected setup to refuse")


def test_markdown_to_mrkdwn():
    assert to_mrkdwn("## Steps\n- **Reboot** it\n- See [docs](https://x.y/z)") == "*Steps*\n• *Reboot* it\n• See <https://x.y/z|docs>"


def test_thinking_models_reasoning_is_hidden():
    chat = Chat(base_url="http://llm.test/v1", http=fake_server(answer="<think>\nhmm, docks...\n</think>\n\nCheck the USB-C cable."))
    assert bot(chat).dispatch("dock won't charge", "U1").text == "Check the USB-C cable."
    assert strip_thinking("<think>never closed because max_tokens hit") == ""
    assert "spent its whole reply thinking" in bot(Chat(base_url="http://llm.test/v1", http=fake_server(answer="<think>still going"))).dispatch("hi", "U1").text
