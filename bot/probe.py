"""Diagnose the Dell lookup step by step, printing HTTP statuses.

    docker compose run --rm slackbot python -m bot.probe 9C4BPM4
"""
import sys
import time

from bot.vendors import browser
from bot.vendors.dell import CONFIG_URL, DETAILS_URL, PAGE_HEADERS, SITE_HEADERS, WARMUP_URL, WARRANTY_PAGE, Dell, extract_config, parse_details
from bot.vendors.http import make_client


def status(label: str, code: int, body: str, ct: str = "") -> None:
    hint = ""
    if "Access Denied" in body[:400]:
        hint = "  <- Akamai Access Denied page"
    elif "OriginalConfigData" in body:
        hint = f"  <- config found, {len(extract_config(body))} SKUs"
    print(f"{label}: HTTP {code}, {len(body)} bytes, {ct[:40]}{hint}", flush=True)


def http_status(label: str, r) -> None:
    status(label, r.status_code, r.text or "", r.headers.get("content-type", ""))


def main() -> None:
    tag = (sys.argv[1] if len(sys.argv) > 1 else "9C4BPM4").upper()
    http = make_client()
    print(f"client: {type(http).__module__}.{type(http).__name__}")
    params = {"serviceTag": tag, "showOriginalConfig": "true", "showCurrentConfig": "true"}

    http_status("1. config call, cold", http.get(CONFIG_URL, params=params, headers=SITE_HEADERS))
    http_status("2. warm-up page   ", http.get(WARMUP_URL, headers=PAGE_HEADERS))
    http_status("3. config call, warm", http.get(CONFIG_URL, params=params, headers=SITE_HEADERS))

    if not browser.available():
        print("4. browser: Playwright is not installed in this image")
        return
    print("4. browser: launching Chromium ...", flush=True)
    t0 = time.time()
    try:
        with browser.Browser().open(WARRANTY_PAGE) as s:
            print(f"   page loaded in {time.time() - t0:.1f}s, title: {s.title[:60]!r}")
            print(f"   ua: {s.page.evaluate('navigator.userAgent')}")
            print(f"   cookies: {', '.join(s.cookies()) or 'none'}")
            d = s.get(DETAILS_URL.format(tag=tag), headers={"Accept": "text/html, */*; q=0.01"})
            status("5. product card      ", d.status, d.text)
            print(f"   model: {parse_details(d.text) or 'none'}")
            r = Dell.fetch_config(s, tag, report=lambda step, resp: status(f"5. {step:<26}", resp.status, resp.text))
            enc = s.get(f"https://www.dell.com/support/components/detectproduct/encvalue/{tag}?appname=warranty")
            status("6. warranty token   ", enc.status, enc.text)
            end, service = Dell.fetch_warranty(s, tag)
            print(f"7. warranty: {end or 'none'} {service}")
    except Exception as exc:
        print(f"   browser failed: {type(exc).__name__}: {str(exc)[:300]}")
    print(f"   total {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
