"""HTTP client for vendor support sites.

Lenovo's and HP's sites sit behind bot protection that answers plain Python
clients with 403 while letting browsers through. curl_cffi presents a real
Chrome TLS fingerprint and headers, which is enough for these read-only
lookups. httpx is the fallback when curl_cffi isn't installed (tests inject
their own client).
"""
import httpx

try:
    from curl_cffi import requests as _curl
except ImportError:  # pragma: no cover
    _curl = None

BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def make_client():
    """A client with .get(url, params=, headers=) returning .status_code/.text/.json()."""
    if _curl is not None:
        return _curl.Session(impersonate="chrome", timeout=30, allow_redirects=True, headers=BROWSER_HEADERS)
    return httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={**BROWSER_HEADERS, "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"},
    )
