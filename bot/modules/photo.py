"""Read a serial number off a photo, then look the machine up.

Drop a picture of the service tag or serial sticker on the bot (a DM, or
@sr-bot with the photo attached) and it asks a vision model where the
serial is, checks that what came back is serial-shaped, and runs the usual
`specs` lookup on it.

The vision model is served the same way as chat: an OpenAI-compatible
server at LLM_BASE_URL. LLM_VISION_MODEL names a model that can see
(Ollama: `ollama pull qwen2.5vl:7b`; qwen3-vl:8b, llama3.2-vision and
minicpm-v work too). Blank turns photo reading off.
"""
import base64
import io
import json
import logging
import re

import httpx

from bot.config import env
from bot.module import Image, Module, Reply

log = logging.getLogger(__name__)
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
MAX_SIDE = 1600       # px; phone photos are 4000+ and the model does not need that
PROMPT = (
    "This photo shows a computer, server, laptop or its label/sticker. Find the serial number "
    "(labels say Serial, S/N, SN, Serial No, or for Dell 'Service Tag' / 'ST'). Serials are 7 to 12 "
    "letters and digits, no spaces, no lowercase letters. Ignore part numbers, model numbers, "
    "regulatory numbers, MAC addresses and express service codes. Reply with only JSON like "
    '{"serials": ["MXL2062PMN"], "vendor": "HP"} with the serials exactly as printed, most likely '
    'first. If none is readable: {"serials": [], "vendor": ""}.'
)
TOKEN = re.compile(r"[A-Z0-9]{5,12}")
TOKEN_WITH_DIGIT = re.compile(r"(?<![A-Z0-9])(?=[A-Z0-9]*\d)[A-Z0-9]{5,12}(?![A-Z0-9])")
THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S)
# Common misreads on stickers: O/0, I/1, S/5, B/8 are the usual ones.
SWAPS = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "B": "8", "8": "B", "Z": "2", "2": "Z"}


def shrink(image: Image) -> tuple[bytes, str]:
    """Downscale big photos so the upload and the model stay quick. Needs Pillow;
    without it the photo goes as is."""
    try:
        from PIL import Image as PILImage, ImageOps
    except ImportError:
        return image.data, image.mimetype
    try:
        im = PILImage.open(io.BytesIO(image.data))
        im = ImageOps.exif_transpose(im)           # phones store rotation in EXIF
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=88)
        return out.getvalue(), "image/jpeg"
    except Exception as exc:                       # HEIC without a decoder, truncated file, ...
        log.info("could not resize %s (%s), sending as is", image.name, exc)
        return image.data, image.mimetype


def parse_answer(text: str) -> tuple[list[str], str]:
    """Serials and vendor hint from the model's reply, JSON or not."""
    text = THINK_RE.sub("", text or "")
    serials, vendor, parsed = [], "", False
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            serials = [str(s) for s in data.get("serials") or [] if s]
            vendor = str(data.get("vendor") or "")
            parsed = True
        except ValueError:
            pass
    if not parsed:
        # Prose instead of JSON: take serial-shaped words (must contain a digit).
        serials = TOKEN_WITH_DIGIT.findall(text.upper())
    cleaned = []
    for s in serials:
        s = re.sub(r"[^A-Z0-9]", "", s.upper())
        if TOKEN.fullmatch(s) and s not in cleaned:
            cleaned.append(s)
    return cleaned, vendor


class Photo(Module):
    keyword = "photo"
    aliases = ("pic", "picture", "image")
    summary = "Read the serial off a photo of the label and look it up (just drop the picture on me)"
    usage = "attach a photo of the service tag / serial sticker"
    accepts_images = True

    def __init__(self, base_url: str | None = None, model: str = "", api_key: str = "", http=None) -> None:
        self.base_url, self.model, self.api_key, self.http = base_url, model, api_key, http

    def setup(self) -> None:
        if self.base_url is None:
            self.base_url = (env("LLM_BASE_URL", "") or "").strip()
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL not set; photo reading is off")
        self.base_url = self.base_url.rstrip("/")
        self.model = self.model or (env("LLM_VISION_MODEL", DEFAULT_VISION_MODEL) or "").strip()
        if not self.model:
            raise RuntimeError("LLM_VISION_MODEL blank; photo reading is off")
        self.api_key = self.api_key or env("LLM_API_KEY", "") or ""
        if self.http is None:
            self.http = httpx.Client(timeout=httpx.Timeout(240.0, connect=10.0))

    @property
    def specs(self):
        return self.registry.modules.get("specs") if self.registry else None

    def read(self, image: Image) -> tuple[list[str], str]:
        data, mime = shrink(image)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = self.http.post(f"{self.base_url}/chat/completions", headers=headers, json={
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}},
            ]}],
            "temperature": 0,
            "max_tokens": 300,
            "stream": False,
        })
        r.raise_for_status()
        return parse_answer(r.json()["choices"][0]["message"].get("content") or "")

    def handle(self, args: str, user_id: str) -> Reply:
        return Reply("Attach a photo of the service tag or serial sticker and I'll read it and look the machine up.")

    def handle_images(self, images: list[Image], text: str, user_id: str) -> Reply:
        specs = self.specs
        found: list[str] = []
        vendor_hint = ""
        try:
            for image in images:
                serials, vendor = self.read(image)
                vendor_hint = vendor_hint or vendor
                found += [s for s in serials if s not in found]
        except httpx.HTTPStatusError as exc:
            log.warning("vision model answered HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
            return Reply(f":warning: The vision model `{self.model}` answered HTTP {exc.response.status_code}. "
                         f"Is it pulled? On the Ollama machine: `ollama pull {self.model}`.")
        except httpx.HTTPError as exc:
            log.warning("vision model unreachable: %r", exc)
            return Reply(f":warning: Couldn't reach the model server at `{self.base_url}` to read the photo.")
        if not found:
            return Reply(":mag: I couldn't make out a serial number in that photo. Try a closer, straight-on shot of "
                         "the sticker, or type the serial.")
        if specs is None:
            return Reply(f"I read `{'`, `'.join(found)}` off the photo, but the specs module isn't loaded.")
        # Prefer serials a vendor recognises; keep the rest only if nothing better was read.
        likely = [s for s in found if any(v.looks_like(s) for v in specs.vendors)] or found
        hint = f" (looks like {vendor_hint})" if vendor_hint and vendor_hint.lower() != "unknown" else ""
        reply = specs.handle("specs " + " ".join(likely[:3]), user_id)
        head = f":camera: Read `{'`, `'.join(likely[:3])}` off the photo{hint}."
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": head}}] + (
            reply.blocks or [{"type": "section", "text": {"type": "mrkdwn", "text": reply.text[:2900]}}])
        return Reply(text=f"{head}\n\n{reply.text}", blocks=blocks, broadcast=reply.broadcast)
