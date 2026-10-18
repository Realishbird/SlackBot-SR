"""Talk to the bot: anything that isn't a command goes to a language model.

Point LLM_BASE_URL at any OpenAI-compatible chat server (Ollama's
http://<host>:11434/v1, LM Studio, llama.cpp's server, vLLM, or a hosted
API with LLM_API_KEY) and name the model in LLM_MODEL. Then `@sr-bot why
does my laptop fan spin up when docked?` gets an answer in the thread, and
`/bot ask <question>` works too. With no URL the module stays off and
unknown messages show help, as before.

Each message is answered on its own (no memory between messages yet); the
system prompt in LLM_SYSTEM_PROMPT sets the bot's role and tone.
"""
import logging
import re

import httpx

from bot.config import env
from bot.module import Module, Reply

log = logging.getLogger(__name__)
DEFAULT_PROMPT = "You are sr-bot, the IT team's Slack assistant. Answer briefly and plainly."
MAX_REPLY = 2900  # Slack section block limit is 3000 characters
THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S)


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

    def complete(self, question: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = self.http.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": question}],
                "temperature": 0.3,
                "max_tokens": 1500,  # thinking models spend a good share of this on reasoning
                "stream": False,
            },
        )
        r.raise_for_status()
        data = r.json()
        return strip_thinking(data["choices"][0]["message"].get("content") or "")

    def handle(self, args: str, user_id: str) -> Reply:
        question = args.strip()
        if not question:
            return Reply(f"Ask me something, e.g. `ask what does a PERC H755 do?`")
        try:
            answer = self.complete(question)
        except httpx.HTTPStatusError as exc:
            log.warning("chat model answered HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
            detail = exc.response.text[:200].strip()
            return Reply(f":warning: The chat model server answered HTTP {exc.response.status_code}"
                         + (f": `{detail}`" if detail else "") + "\nCheck the model name on the admin page.")
        except httpx.HTTPError as exc:
            log.warning("chat model unreachable: %r", exc)
            return Reply(f":warning: Couldn't reach the chat model at `{self.base_url}`. Is the server up?")
        except (KeyError, IndexError, ValueError) as exc:
            log.warning("chat model gave an unexpected reply: %r", exc)
            return Reply(":warning: The chat model answered in a shape I don't understand.")
        if not answer:
            return Reply("The model had nothing to say (or spent its whole reply thinking). Try rephrasing, "
                         "or add `/no_think` to the system prompt on the admin page.")
        text = to_mrkdwn(answer)
        if len(text) > MAX_REPLY:
            text = text[:MAX_REPLY].rsplit("\n", 1)[0] + "\n_(cut, the answer was longer)_"
        return Reply(text)
