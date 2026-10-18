"""A hidden Chrome for vendor sites whose bot protection needs real JavaScript.

Dell's edge (Akamai) only answers once the site's sensor script has run in a
browser and earned a validated `_abck` cookie. Plain HTTP clients, even ones
with a Chrome TLS fingerprint, get "Access Denied". So for those sites the
bot drives a headless Chromium through Playwright: it loads one normal page,
lets the sensor run, then sends the lookup requests through the page's own
`fetch()`, which carries the cookies the sensor earned.

Each `open()` launches a fresh browser and closes it when the lookup is done,
so memory only climbs (about 250 to 350 MB) while a lookup runs. One lookup
at a time; a second caller waits for the lock.

Set DELL_BROWSER=off to disable this and get the "blocked" message instead.
"""
import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass

log = logging.getLogger(__name__)

LAUNCH_ARGS = [
    "--disable-dev-shm-usage",     # /dev/shm is tiny inside Docker
    "--disable-gpu",
    "--no-sandbox",                # no user namespaces in a nested container; we run as a non-root user instead
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
    "--window-size=1366,768",
]
# Pretend to be a current desktop Chrome on Windows; headless Chromium's own
# UA says "HeadlessChrome", which bot protection flags immediately.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# Runs inside the page. Returns status and body text of a same-site request.
FETCH_JS = """
async ({url, method, headers, body}) => {
  const r = await fetch(url, {method, headers, body, credentials: "include"});
  return {status: r.status, text: await r.text()};
}
"""

_lock = threading.Lock()


@dataclass
class Response:
    status: int
    text: str

    def json(self):
        return json.loads(self.text)


def available() -> bool:
    """True when Playwright is importable (the image installs it with Chromium)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class Session:
    """A page on the target site. `get` and `post` go through its fetch()."""

    def __init__(self, page, timeout_ms: int) -> None:
        self.page = page
        self.timeout_ms = timeout_ms

    def get(self, url: str, headers: dict | None = None) -> Response:
        return self._fetch("GET", url, headers, None)

    def post(self, url: str, body, headers: dict | None = None) -> Response:
        headers = {"Content-Type": "application/json", **(headers or {})}
        return self._fetch("POST", url, headers, json.dumps(body))

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def nudge(self) -> None:
        """A little mouse activity; some sensors score an idle page as a bot."""
        try:
            self.page.mouse.move(200, 300)
            self.page.mouse.move(640, 420, steps=8)
            self.page.mouse.wheel(0, 300)
        except Exception:
            pass

    def cookies(self) -> list[str]:
        try:
            return sorted({c["name"] for c in self.page.context.cookies()})
        except Exception:
            return []

    def _fetch(self, method, url, headers, body) -> Response:
        out = self.page.evaluate(FETCH_JS, {"url": url, "method": method, "headers": headers or {}, "body": body})
        return Response(int(out["status"]), out.get("text") or "")


class Browser:
    """Launches Chromium per `open()` call. Mock this class in tests."""

    def __init__(self, timeout: float = 45.0) -> None:
        self.timeout_ms = int(timeout * 1000)

    @contextmanager
    def open(self, url: str):
        from playwright.sync_api import sync_playwright
        with _lock, sync_playwright() as p:
            # Full Chromium (channel) gives the new headless mode, which looks
            # like a normal Chrome. CHROMIUM_PATH points at another binary.
            launch = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {"channel": "chromium"}
            browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS, **launch)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    timezone_id="America/Chicago",
                )
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass  # ad trackers never settle; the sensor has posted by now
                session = Session(page, self.timeout_ms)
                session.nudge()
                page.wait_for_timeout(1000)
                log.debug("browser: %s loaded, cookies %s", url, ", ".join(session.cookies()))
                yield session
            finally:
                browser.close()
