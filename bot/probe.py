"""Diagnose a vendor lookup step by step, printing HTTP statuses.

    docker compose run --rm slackbot python -m bot.probe 9C4BPM4
"""
import sys

from bot.vendors.dell import CONFIG_URL, PAGE_HEADERS, SITE_HEADERS, WARMUP_URL, extract_config
from bot.vendors.http import make_client


def status(label: str, r) -> None:
    ct = r.headers.get("content-type", "")
    body = r.text or ""
    hint = ""
    if "Access Denied" in body[:400]:
        hint = "  <- Akamai Access Denied page"
    elif "OriginalConfigData" in body:
        hint = f"  <- config found, {len(extract_config(body))} SKUs"
    print(f"{label}: HTTP {r.status_code}, {len(body)} bytes, {ct[:40]}{hint}")


def main() -> None:
    tag = (sys.argv[1] if len(sys.argv) > 1 else "9C4BPM4").upper()
    http = make_client()
    print(f"client: {type(http).__module__}.{type(http).__name__}")
    params = {"serviceTag": tag, "showOriginalConfig": "true", "showCurrentConfig": "true"}

    status("1. config call, cold", http.get(CONFIG_URL, params=params, headers=SITE_HEADERS))
    status("2. warm-up page   ", http.get(WARMUP_URL, headers=PAGE_HEADERS))
    jar = getattr(http, "cookies", None)
    try:
        names = sorted({c.name for c in jar.jar}) if hasattr(jar, "jar") else sorted(jar.keys())
    except Exception:
        names = ["?"]
    print(f"   cookies now: {', '.join(names) or 'none'}")
    status("3. config call, warm", http.get(CONFIG_URL, params=params, headers=SITE_HEADERS))
    status("4. product page    ", http.get(f"https://www.dell.com/support/home/en-us/product-support/servicetag/{tag}/overview", headers=PAGE_HEADERS))
    status("5. config call again", http.get(CONFIG_URL, params=params, headers=SITE_HEADERS))


if __name__ == "__main__":
    main()
