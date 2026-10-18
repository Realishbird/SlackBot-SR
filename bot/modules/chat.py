"""Talk to the bot: anything that isn't a command goes to a language model.

Point LLM_BASE_URL at any OpenAI-compatible chat server (Ollama's
http://<host>:11434/v1, LM Studio, llama.cpp's server, vLLM, or a hosted
API with LLM_API_KEY) and name the model in LLM_MODEL. Then `@sr-bot why
does my laptop fan spin up when docked?` gets an answer in the thread, and
`/bot ask <question>` works too. With no URL the module stays off and
unknown messages show help, as before.

The model can use the bot's own lookups. A serial number in the question
("what's in MXL2062PMN?") is looked up before the model sees it, and the
model also gets a `lookup_hardware` tool for serials it spots itself, so it
answers with the real parts list and attaches the specs card.

Each message is answered on its own (no memory between messages yet); the
system prompt in LLM_SYSTEM_PROMPT sets the bot's role and tone.
"""
import json
import logging
import re

import httpx

from bot.config import env
from bot.module import Module, Reply

log = logging.getLogger(__name__)
DEFAULT_PROMPT = "You are sr-bot, the IT team's Slack assistant. Answer briefly and plainly."
TOOL_PROMPT = (
    "You can look up a computer's factory hardware by its serial number or Dell service tag "
    "(Dell, HP, Lenovo). When lookup results are given, answer the question from them: name the "
    "model and list the main parts (CPU, memory, storage, graphics, warranty). Do not ask the user "
    "to check the vendor's website and do not ask for clarification when the question can be answered."
)
MAX_REPLY = 2900  # Slack section block limit is 3000 characters
MAX_LOOKUPS = 3
THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S)
# Something serial-shaped: 7-14 letters/digits (dashes allowed), with both a letter and a digit.
SERIALISH = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{5,12}[A-Za-z0-9](?![A-Za-z0-9])")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "lookup_hardware",
        "description": "Factory hardware specs, model and warranty for a Dell service tag or an HP/Lenovo serial number.",
        "parameters": {
            "type": "object",
            "properties": {"serial": {"type": "string", "description": "The serial number or service tag"}},
            "required": ["serial"],
        },
    },
}]


def strip_thinking(text: str) -> str:
    """Reasoning models (qwen3, deepseek-r1) wrap their working in <think>
    tags before the answer; a cut-off reply may never close the tag."""
    return THINK_RE.sub("", text or "").strip()


def to_mrkdwn(text: str) -> str:
    """Markdown as models write it -> Slack mrkdwn (bold, headers, bullets, code)."""
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)        # headers -> bold
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)                        # **bold** -> *bold*
    text = re.sub(r"(?<![*\w])_(?!_)(.+?)_(?![*\w])", r"_\1_", text)      # italics unchanged
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)                 # - item -> • item
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)  # [text](url) -> <url|text>
    return text.strip()


class Chat(Module):
    keyword = "ask"
    aliases = ("chat", "llm")
    summary = "Ask the local chat model anything (also answers plain @mentions)"
    usage = "ask <question>, or just @mention me"
    fallback = True

    def __init__(self, base_url: str | None = None, model: str = "", api_key: str = "", system_prompt: str = "", http=None) -> None:
        self.base_url, self.model, self.api_key, self.system_prompt, self.http = base_url, model, api_key, system_prompt, http
        self.tools_ok = True   # flipped off if the server rejects the tools field

    def setup(self) -> None:
        if self.base_url is None:
            self.base_url = (env("LLM_BASE_URL", "") or "").strip()
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL not set; chat is off")
        self.base_url = self.base_url.rstrip("/")
        self.model = self.model or env("LLM_MODEL", "llama3.2:3b")
        self.api_key = self.api_key or env("LLM_API_KEY", "") or ""
        self.system_prompt = self.system_prompt or env("LLM_SYSTEM_PROMPT", DEFAULT_PROMPT) or DEFAULT_PROMPT
        if self.http is None:
            self.http = httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))

    # ----- lookups the model can use -------------------------------------
    @property
    def specs(self):
        return self.registry.modules.get("specs") if self.registry else None

    def find_serials(self, text: str) -> list[str]:
        """Serial-shaped words that one of the vendors says look like theirs."""
        specs = self.specs
        if not specs:
            return []
        found: list[str] = []
        for m in SERIALISH.finditer(text):
            s = re.sub(r"[^A-Z0-9]", "", m.group(0).upper())
            if s not in found and any(v.looks_like(s) for v in specs.vendors):
                found.append(s)
        return found[:MAX_LOOKUPS]

    def lookup(self, serial: str, user_id: str) -> Reply | None:
        specs = self.specs
        if not specs:
            return None
        return specs.handle(f"specs {serial}", user_id)

    # ----- the model -----------------------------------------------------
    def post(self, messages: list[dict], tools: bool) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "messages": messages, "temperature": 0.3,
                "max_tokens": 1500,  # thinking models spend a good share of this on reasoning
                "stream": False}
        if tools:
            body["tools"] = TOOLS
        r = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        if tools and r.status_code == 400:
            # Server or model without tool support: carry on without the tool.
            log.info("chat model rejected tools, continuing without: %s", r.text[:200])
            self.tools_ok = False
            return self.post(messages, tools=False)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    def complete(self, question: str, user_id: str) -> tuple[str, list[Reply]]:
        """Answer text plus the lookup cards that fed it."""
        cards: list[Reply] = []
        seen: set[str] = set()
        messages = [{"role": "system", "content": self.system_prompt}]
        if self.specs:
            messages.append({"role": "system", "content": TOOL_PROMPT})
        for serial in self.find_serials(question):
            card = self.lookup(serial, user_id)
            if card:
                seen.add(serial)
                cards.append(card)
                messages.append({"role": "system", "content": f"Lookup results for {serial}:\n{card.text}"})
        messages.append({"role": "user", "content": question})

        use_tools = bool(self.specs) and self.tools_ok
        for _ in range(MAX_LOOKUPS + 1):
            msg = self.post(messages, tools=use_tools)
            calls = msg.get("tool_calls") or []
            if not calls:
                return strip_thinking(msg.get("content") or ""), cards
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                try:
                    serial = json.loads(call["function"].get("arguments") or "{}").get("serial", "")
                except (ValueError, AttributeError):
                    serial = ""
                serial = re.sub(r"[^A-Z0-9]", "", str(serial).upper())
                card = self.lookup(serial, user_id) if serial else None
                result = card.text if card else f"No lookup possible for {serial!r}."
                if card and serial not in seen:
                    seen.add(serial)
                    cards.append(card)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
        return strip_thinking(msg.get("content") or ""), cards

    def handle(self, args: str, user_id: str) -> Reply:
        question = args.strip()
        if not question:
            return Reply("Ask me something, e.g. `ask what does a PERC H755 do?`")
        try:
            answer, cards = self.complete(question, user_id)
        except httpx.HTTPStatusError as exc:
            log.warning("chat model answered HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
            detail = exc.response.text[:200].strip()
            return Reply(f":warning: The chat model server answered HTTP {exc.response.status_code}"
                         + (f": `{detail}`" if detail else "") + "\nCheck the model name on the admin page.")
        except httpx.HTTPError as exc:
            log.warning("chat model unreachable: %r", exc)
            return Reply(f":warning: Couldn't reach the chat model at `{self.base_url}`. Is the server up?")
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            log.warning("chat model gave an unexpected reply: %r", exc)
            return Reply(":warning: The chat model answered in a shape I don't understand.")
        if not answer and not cards:
            return Reply("The model had nothing to say (or spent its whole reply thinking). Try rephrasing, "
                         "or add `/no_think` to the system prompt on the admin page.")
        text = to_mrkdwn(answer) or "Here's what I found:"
        if len(text) > MAX_REPLY:
            text = text[:MAX_REPLY].rsplit("\n", 1)[0] + "\n_(cut, the answer was longer)_"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
        for card in cards:
            blocks.append({"type": "divider"})
            blocks += card.blocks or [{"type": "section", "text": {"type": "mrkdwn", "text": card.text[:2900]}}]
        full = text + "".join(f"\n\n---\n\n{c.text}" for c in cards)
        return Reply(text=full, blocks=blocks)
