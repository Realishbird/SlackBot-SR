"""Tidy a Dell configuration export that someone pastes or uploads.

Dell's support site has an "Export" link on the Review Product
Specifications panel that downloads `<service tag>.csv`:

    Component,Part Number,Description,Quantity
    "338-CVHG : Intel Xeon 658X (144 MB cache, ...)","ABC12","...","1"
    "","XYZ34","...","1"            <- parts under the SKU above

Paste that text (or drop the file) in a DM to the bot, optionally with the
service tag in front, and it answers with the same grouped summary `specs`
gives: processor, memory, storage, graphics, network, power, OS. No keyword
needed; the bot recognises the file. `csv <text>` works too.

Set INTAKE_CHANNEL (admin page) and the summary is also posted there, so a
paste in a DM ends up in the team's intake channel. Mentioning a channel in
the message (`#intake` before the CSV) sends it to that one instead.
"""
import csv
import io
import re

from bot.config import env
from bot.module import Module, Reply
from bot.modules.specs import render
from bot.vendors.dell import SUPPORT, parse_skus

HEADER = "Component,Part Number"
SKU_LINE = re.compile(r'^\s*"?(\d{3}-[A-Z0-9]{4}) : ', re.M)
TAG = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{7}\b")
CHANNEL_REF = re.compile(r"<#(C[A-Z0-9]+)(?:\|[^>]*)?>")   # how Slack sends #channel in a message


def looks_like_dell_export(text: str) -> bool:
    return HEADER in text[:400] or len(SKU_LINE.findall(text)) >= 2


def parse_export_csv(text: str) -> list[dict]:
    """SKU rows ("338-CVHG : description" in the Component column) in the
    shape the Dell parser expects; the part rows under each SKU are skipped."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    skus = []
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if not row:
            continue
        number, sep, desc = row[0].strip().partition(" : ")
        if sep and re.fullmatch(r"\d{3}-[A-Z0-9]{4}", number):
            skus.append({"skuNumber": number, "skuDescription": desc.strip()})
    return skus


def find_tag(text: str) -> str:
    """A service tag written before the CSV (or in the file name) if any."""
    cut = min(i for i in (text.find(HEADER), SKU_LINE.search(text).start() if SKU_LINE.search(text) else len(text)) if i >= 0)
    m = TAG.search(text[:cut].upper())
    return m.group(0) if m else ""


class Paste(Module):
    keyword = "csv"
    aliases = ("paste", "clean")
    summary = "Tidy a pasted Dell configuration export (the Export CSV from the product specs panel) and post it to the intake channel"
    usage = "csv [service tag] [#channel] <pasted CSV>, or just paste the CSV in a DM"

    def __init__(self, channel: str | None = None) -> None:
        self.channel = channel

    def setup(self) -> None:
        if self.channel is None:
            self.channel = env("INTAKE_CHANNEL", "").strip()

    def matches(self, text: str) -> bool:
        return looks_like_dell_export(text)

    def handle(self, args: str, user_id: str) -> Reply:
        skus = parse_export_csv(args)
        if not skus:
            return Reply("Paste the CSV that Dell's *Export* link on the product specs panel gives you "
                         "(it starts with `Component,Part Number,Description,Quantity`), with the service tag in front if you like.")
        tag = find_tag(CHANNEL_REF.sub(" ", args))
        specs = parse_skus(skus, tag or "pasted export")
        specs.url = f"{SUPPORT}/support/home/en-us/product-support/servicetag/{tag}/overview" if tag else ""
        reply = render(specs)
        mentioned = CHANNEL_REF.search(args)
        reply.broadcast = mentioned.group(1) if mentioned else (self.channel or "")
        if reply.broadcast:
            reply.text += f"\n_Submitted by <@{user_id}>_"
        return reply
