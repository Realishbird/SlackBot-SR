import json

from bot.modules.help import Help
from bot.modules.paste import Paste, find_tag, looks_like_dell_export, parse_export_csv
from bot.registry import Registry
from tests.test_bot import DELL_SITE_HTML

SKUS = json.loads(DELL_SITE_HTML.split("OriginalConfigData = ")[1].split(";\n")[0])
# What Dell's Export link downloads: a SKU row, then the parts under it with an empty Component.
EXPORT_CSV = "﻿Component,Part Number,Description,Quantity\r\n" + "".join(
    f'"{s["skuNumber"]} : {s["skuDescription"]}","W21JJ","INFO,GNRC,OEM,TRACKING,PN","1"\r\n"","9XHT0","INFO,FRONT NVME,RAID0","1"\r\n'
    for s in SKUS
)


def bot(channel=""):
    r = Registry()
    r.add(Help(r))
    r.add(Paste(channel=channel))
    return r


def test_parse_export_csv():
    skus = parse_export_csv(EXPORT_CSV)
    assert len(skus) == len(SKUS)
    assert {"skuNumber": "338-CVHG", "skuDescription": "Intel Xeon 658X (144 MB cache,  24 cores, up to 4.9 GHz, 250 W)"} in skus
    assert parse_export_csv("hello there") == []
    assert looks_like_dell_export(EXPORT_CSV) and not looks_like_dell_export("specs 9C4BPM4")


def test_find_tag():
    assert find_tag("9C4BPM4\n" + EXPORT_CSV) == "9C4BPM4"
    assert find_tag("here is the export for tag 9c4bpm4 please\n" + EXPORT_CSV) == "9C4BPM4"
    assert find_tag("PRODUCT EXPORT\n" + EXPORT_CSV) == ""          # words without a digit are not tags
    assert find_tag(EXPORT_CSV) == ""                                # nothing in the CSV itself counts


def test_pasted_export_is_recognised_without_keyword():
    reply = bot().dispatch(EXPORT_CSV, "U1")
    assert "*Dell Pro Precision 9 T2 (PW9T2260)* (`pasted export`)" in reply.text
    assert "*Processor*\n• Intel Xeon 658X (144 MB cache, 24 cores, up to 4.9 GHz, 250W)" in reply.text
    assert "NVIDIA RTX PRO 6000 Blackwell" in reply.text
    assert "Not selected" not in reply.text and "Power Cord" not in reply.text
    assert reply.broadcast == "" and "Submitted by" not in reply.text and "dell.com" not in reply.text


def test_tag_and_channel_in_front():
    reply = bot().dispatch("9C4BPM4 for <#C0INTAKE|intake>\n" + EXPORT_CSV, "U42")
    assert "(`9C4BPM4`)" in reply.text
    assert "servicetag/9C4BPM4/overview" in reply.text
    assert reply.broadcast == "C0INTAKE"
    assert "_Submitted by <@U42>_" in reply.text


def test_default_intake_channel_and_keyword():
    reply = bot(channel="#intake").dispatch("csv " + EXPORT_CSV, "U1")
    assert reply.broadcast == "#intake"
    assert "Intel Xeon 658X" in reply.text
    assert "Paste the CSV" in bot().dispatch("csv nothing useful", "U1").text
