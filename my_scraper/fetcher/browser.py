"""Headless browser: renders JavaScript and reaches what plain HTTP cannot see.

Beyond loading the page it waits for slow content, scrolls, presses "load more",
steps through button-driven pagination and tabs, reads iframes and shadow DOM, and
keeps the JSON the page fetches for itself. It identifies itself honestly and makes
no attempt to hide from detection.
"""
import asyncio
import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from my_scraper.utils.urls import site_key

MAX_OPEN_PAGES = 3
MIN_VISIBLE_TEXT = 200

MAX_EXPAND_SECONDS = 30     # all scrolling and clicking on one page, however long it could go on
PAGE_LOAD_QUIET = 2.5       # longest wait for the network after a page loads
ACTION_QUIET = 2.0          # and after a click or scroll
QUIET_FOR = 0.4             # no requests for this long counts as quiet
ACTION_HEAD_START = 0.15    # a click's request needs a moment to begin
STABLE_SECONDS = 1.0        # text unchanged for this long means rendering has finished
SPARSE_PATIENCE = 13.0      # a nearly empty page may still be waiting on slow data
BUSY_PATIENCE = 4.0
POLL_SECONDS = 0.25

MAX_CAPTURED_RESPONSES = 40
MAX_CAPTURED_BYTES = 5 * 1024 * 1024
MAX_FRAMES = 8
MAX_FRAME_CHARS = 2_000_000
MAX_TAB_CLICKS = 8
MAX_PAGE_CLICKS = 8

HEAVY_RESOURCES = {"image", "media", "font"}
LONG_LIVED_RESOURCES = {"websocket", "eventsource"}  # these stay open by design

LOAD_MORE_PATTERN = (
    r"^\s*(load|show|view|see|get|read)\s+(\d+\s+)?more\b|^\s*more\s+(results|items|products|posts|articles|stories)\b"
    r"|^\s*(mehr (laden|anzeigen)|weitere (laden|anzeigen)|voir plus|afficher plus|charger plus|ver más|mostrar más"
    r"|cargar más|mostra altro|もっと見る|さらに表示|加载更多|查看更多|더 보기|показать (ещё|еще)|загрузить (ещё|еще))"
)
NEXT_PATTERN = r"^\s*(next(\s+page)?|›|»|>|→|suivant|weiter|siguiente|次へ|下一页|다음|далее)\s*$"

# Controls that act in place. A real link is left to the crawler, which fetches it politely.
IN_PLACE_CONTROLS = "button, [role=button], a[href='#'], a[href^='#'], a[href^='javascript'], a:not([href])"

# One pass inside the page. Asking about each control from Python took minutes on busy pages.
MARK_CONTROL = """([selector, pattern]) => {
  const wanted = new RegExp(pattern, 'i');
  for (const el of document.querySelectorAll(selector)) {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!text || !wanted.test(text)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    document.querySelectorAll('[data-scraper-click]').forEach(old => old.removeAttribute('data-scraper-click'));
    el.setAttribute('data-scraper-click', '1');
    return true;
  }
  return false;
}"""

MEASURE_PAGE = "() => ({h: document.documentElement.scrollHeight, n: document.getElementsByTagName('*').length})"
TEXT_LENGTH = "() => document.body ? document.body.innerText.length : 0"

# cloneNode leaves shadow roots behind, so copy each one into its host by hand.
SERIALIZE_WITH_SHADOW = """() => {
  const hasShadow = [...document.querySelectorAll('*')].some(el => el.shadowRoot);
  if (!hasShadow) return null;
  const copy = (root) => {
    const clone = root.cloneNode(true);
    const live = [root, ...root.querySelectorAll('*')];
    const copied = [clone, ...clone.querySelectorAll('*')];
    live.forEach((el, i) => {
      if (!el.shadowRoot) return;
      const holder = document.createElement('div');
      holder.setAttribute('data-shadow-root', '');
      el.shadowRoot.childNodes.forEach(node => {
        holder.appendChild(node.nodeType === 1 ? copy(node) : node.cloneNode(true));
      });
      copied[i].appendChild(holder);
    });
    return clone;
  };
  return '<!DOCTYPE html>' + copy(document.documentElement).outerHTML;
}"""

# Tabs and filters: at least three look-alike controls that swap content in place.
FIND_TAB_GROUP = """(maxClicks) => {
  const controls = [...document.querySelectorAll(
    "[role=tab], a[href='#'], a[href^='javascript'], button")].filter(el => {
      const text = (el.innerText || '').trim();
      return text && text.length <= 24 && el.offsetParent !== null
        && !el.closest('nav, header, footer, form');
  });
  const groups = {};
  controls.forEach(el => {
    const key = el.tagName + '|' + el.className;
    (groups[key] = groups[key] || []).push(el);
  });
  const best = Object.values(groups).sort((a, b) => b.length - a.length)[0] || [];
  if (best.length < 3) return 0;
  best.slice(0, maxClicks).forEach((el, i) => el.setAttribute('data-scraper-tab', i));
  return Math.min(best.length, maxClicks);
}"""


class BrowserUnavailable(Exception):
    """The browser could not start, which trying again will not change."""


def _registered_domain(url: str) -> str:
    """The last two labels of a host, enough to tell a site's own API from a third party's."""
    return ".".join(site_key(urlsplit(url).hostname or "").split(".")[-2:])


class NetworkWatch:
    """Counts requests in flight.

    Playwright's networkidle fires once per page load, so after a click or a scroll it
    returns at once, before the data the page just asked for has arrived.
    """

    def __init__(self, page):
        self.active = 0
        self.last_activity = time.monotonic()
        page.on("request", self._started)
        page.on("requestfinished", self._ended)
        page.on("requestfailed", self._ended)

    def _started(self, request):
        if request.resource_type not in LONG_LIVED_RESOURCES:
            self.active += 1
            self.last_activity = time.monotonic()

    def _ended(self, request):
        if request.resource_type not in LONG_LIVED_RESOURCES:
            self.active = max(self.active - 1, 0)
            self.last_activity = time.monotonic()

    async def quiet(self, limit: float):
        """Wait until nothing is loading, for at most limit seconds."""
        await asyncio.sleep(ACTION_HEAD_START)
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            if self.active == 0 and time.monotonic() - self.last_activity >= QUIET_FOR:
                return
            await asyncio.sleep(0.05)


class BrowserFetcher:
    """Browser fetcher for JavaScript-rendered pages. Starts Chromium once and shares it."""

    def __init__(self, headless: bool = True, timeout: int = 30000, wait_until: str = "domcontentloaded",
                 user_agent: str = None, expand_rounds: int = 8, viewport: Optional[Dict[str, int]] = None):
        self.headless = headless
        self.timeout = timeout
        self.wait_until = wait_until
        self.user_agent = user_agent
        self.expand_rounds = expand_rounds
        self.viewport = viewport
        self._playwright = None
        self._browser = None
        self._start_lock: Optional[asyncio.Lock] = None
        self._slots: Optional[asyncio.Semaphore] = None

    async def _shared_browser(self):
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
            self._slots = asyncio.Semaphore(MAX_OPEN_PAGES)

        async with self._start_lock:
            if self._browser is None or not self._browser.is_connected():
                try:
                    from playwright.async_api import async_playwright

                    self._playwright = await async_playwright().start()
                    self._browser = await self._playwright.chromium.launch(headless=self.headless)
                except Exception as e:
                    raise BrowserUnavailable(
                        "Browser could not start. Use 'pip install playwright' and 'playwright install chromium'. "
                        f"({str(e).splitlines()[0] if str(e) else type(e).__name__})"
                    ) from e
        return self._browser

    async def close(self):
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = self._playwright = self._start_lock = self._slots = None

    async def fetch(self, url: str) -> Dict[str, Any]:
        """Render a page and read everything it shows."""
        start_time = time.time()
        browser = await self._shared_browser()

        async with self._slots:
            context = await browser.new_context(user_agent=self.user_agent, viewport=self.viewport)
            try:
                await context.route("**/*", self._skip_heavy_resources)
                page = await context.new_page()

                watch = NetworkWatch(page)
                reads: List[asyncio.Task] = []
                own_domain = _registered_domain(url)
                page.on("response", lambda response: self._capture_json(response, own_domain, reads))

                response = await self._open(page, url)
                await self._settle(page, watch)

                snapshots: List[str] = []
                if self.expand_rounds:
                    deadline = time.monotonic() + MAX_EXPAND_SECONDS
                    await self._expand(page, watch, deadline)
                    snapshots = await self._walk_controls(page, watch, deadline)

                content = await self._full_content(page)
                payloads = [value for value in await asyncio.gather(*reads) if value is not None]

                return {
                    "url": url,
                    "final_url": page.url,
                    "status_code": response.status if response else 200,
                    "content": content,
                    "snapshots": snapshots,
                    "json_payloads": payloads[:MAX_CAPTURED_RESPONSES],
                    "headers": response.headers if response else {},
                    "duration_ms": (time.time() - start_time) * 1000,
                    "fetcher_type": "browser",
                    "content_length": len(content),
                }
            finally:
                await context.close()

    @staticmethod
    async def _skip_heavy_resources(route):
        if route.request.resource_type in HEAVY_RESOURCES:
            await route.abort()
        else:
            await route.continue_()

    async def _open(self, page, url: str):
        """Go to the page. A site that never stops loading still gives what has arrived."""
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        try:
            return await page.goto(url, wait_until=self.wait_until, timeout=self.timeout)
        except PlaywrightTimeout:
            if await page.evaluate(TEXT_LENGTH) == 0:
                raise
            return None

    async def _settle(self, page, watch: NetworkWatch):
        """Wait until the text stops changing. Nearly empty pages get far more patience."""
        await watch.quiet(PAGE_LOAD_QUIET)

        started = time.monotonic()
        last_length = -1
        changed_at = started
        while True:
            now = time.monotonic()
            length = await page.evaluate(TEXT_LENGTH)
            if length != last_length:
                last_length, changed_at = length, now
            sparse = length < MIN_VISIBLE_TEXT
            if not sparse and now - changed_at >= STABLE_SECONDS:
                return
            if now - started >= (SPARSE_PATIENCE if sparse else BUSY_PATIENCE):
                return
            await asyncio.sleep(POLL_SECONDS)

    def _capture_json(self, response, own_domain: str, reads: List[asyncio.Task]):
        """Keep JSON the page requests from its own site: often the cleanest copy of its data.

        Third parties are left out, as they answer with cookie vendors and ad data.
        """
        if len(reads) >= MAX_CAPTURED_RESPONSES or response.request.resource_type not in ("xhr", "fetch"):
            return
        if not response.ok or _registered_domain(response.url) != own_domain:
            return
        if int(response.headers.get("content-length") or 0) <= MAX_CAPTURED_BYTES:
            reads.append(asyncio.ensure_future(self._read_json(response)))

    @staticmethod
    async def _read_json(response) -> Optional[Any]:
        """Parse a response as JSON whatever it is labelled. Many APIs send it as text/html."""
        try:
            text = (await response.text()).strip()
            return json.loads(text) if text[:1] in ("{", "[") else None
        except Exception:  # the body may be gone once the page moves on, or not really JSON
            return None

    async def _click_first(self, page, pattern: str) -> bool:
        """Click the first visible in-place control whose text or label matches."""
        try:
            if not await page.evaluate(MARK_CONTROL, [IN_PLACE_CONTROLS, pattern]):
                return False
            await page.locator("[data-scraper-click]").first.click(timeout=1500)
            return True
        except Exception:  # controls come and go as the page changes
            return False

    async def _expand(self, page, watch: NetworkWatch, deadline: float):
        """Scroll and press load-more until the page stops growing."""
        stalled = 0
        for _ in range(self.expand_rounds):
            if time.monotonic() >= deadline:
                return
            before = await page.evaluate(MEASURE_PAGE)
            await page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
            clicked = await self._click_first(page, LOAD_MORE_PATTERN)
            await watch.quiet(ACTION_QUIET)
            after = await page.evaluate(MEASURE_PAGE)

            grew = after["n"] > before["n"] or after["h"] > before["h"]
            stalled = 0 if grew or clicked else stalled + 1
            if stalled >= 2:
                return

    async def _walk_controls(self, page, watch: NetworkWatch, deadline: float) -> List[str]:
        """Step through pagination buttons and tabs, keeping each state the page shows."""
        snapshots: List[str] = []

        for _ in range(MAX_PAGE_CLICKS):
            if time.monotonic() >= deadline:
                return snapshots
            before = await page.content()
            if not await self._click_first(page, NEXT_PATTERN):
                break
            await watch.quiet(ACTION_QUIET)
            after = await page.content()
            if after == before:
                break
            snapshots.append(before)

        tabs = await page.evaluate(FIND_TAB_GROUP, MAX_TAB_CLICKS)
        for index in range(tabs):
            if time.monotonic() >= deadline:
                break
            try:
                await page.locator(f"[data-scraper-tab='{index}']").first.click(timeout=1500)
                await watch.quiet(ACTION_QUIET)
                snapshots.append(await page.content())
            except Exception:  # a tab may vanish after an earlier one was pressed
                continue

        return snapshots

    async def _frames_html(self, page) -> str:
        """Body of each same-site iframe, since page.content() only holds the frame tag."""
        site = site_key(urlsplit(page.url).netloc)
        pieces = []
        for frame in page.frames[1:MAX_FRAMES + 1]:
            if site_key(urlsplit(frame.url).netloc) != site:
                continue
            try:
                html = await frame.evaluate("() => document.body ? document.body.innerHTML : ''")
            except Exception:  # the frame may have navigated away or been removed
                continue
            if html:
                pieces.append(f'<div data-frame-src="{frame.url}">{html[:MAX_FRAME_CHARS]}</div>')
        return "".join(pieces)

    async def _full_content(self, page) -> str:
        content = await page.evaluate(SERIALIZE_WITH_SHADOW) or await page.content()
        frames = await self._frames_html(page)
        if not frames:
            return content
        position = content.lower().rfind("</body>")
        return content[:position] + frames + content[position:] if position >= 0 else content + frames
