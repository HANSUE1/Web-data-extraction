"""Production-grade fetcher with static and browser fallback."""
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
from parsel import Selector

from my_scraper.config.settings import FetcherType

# Below this much visible text, an HTML page is probably rendered by JavaScript.
MIN_VISIBLE_TEXT = 200
VISIBLE_TEXT_XPATH = "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"


class StaticFetcher:
    """Lightweight static HTTP fetcher using aiohttp. Reuses one session for all requests."""

    def __init__(self, headers: Dict[str, str] = None, timeout: int = 30):
        self.headers = headers
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def fetch(self, url: str) -> Dict[str, Any]:
        """Fetch URL with static HTTP request."""
        start_time = time.time()

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers, timeout=self.timeout)

        async with self._session.get(url) as response:
            body = await response.read()
            content = await response.text(errors="replace")
            return {
                "url": url,
                "final_url": str(response.url),
                "status_code": response.status,
                "content": content,
                "body": body,
                "headers": {key.lower(): value for key, value in response.headers.items()},
                "duration_ms": (time.time() - start_time) * 1000,
                "fetcher_type": "static",
                "content_length": len(content),
            }

    async def close(self):
        if self._session is not None:
            await self._session.close()


class BrowserFetcher:
    """Browser fetcher for JavaScript-rendered pages."""

    def __init__(self, headless: bool = True, timeout: int = 30000,
                 wait_until: str = "networkidle", user_agent: str = None):
        self.headless = headless
        self.timeout = timeout
        self.wait_until = wait_until
        self.user_agent = user_agent

    async def fetch(self, url: str) -> Dict[str, Any]:
        """Fetch URL with headless browser."""
        start_time = time.time()

        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError("Playwright not installed. Use 'pip install playwright' and 'playwright install'") from e

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context(user_agent=self.user_agent)
                page = await context.new_page()
                response = await page.goto(url, wait_until=self.wait_until, timeout=self.timeout)
                content = await page.content()
                return {
                    "url": url,
                    "final_url": page.url,
                    "status_code": response.status if response else 200,
                    "content": content,
                    "headers": response.headers if response else {},
                    "duration_ms": (time.time() - start_time) * 1000,
                    "fetcher_type": "browser",
                    "content_length": len(content),
                }
            finally:
                await browser.close()


class SmartFetcher:
    """Smart fetcher with automatic fallback."""

    def __init__(self, config):
        self.config = config
        user_agent = config.robots.user_agent
        # Identify with the same agent robots.txt was checked against and the audit log records.
        self.static_fetcher = StaticFetcher(headers={"User-Agent": user_agent})
        self.browser_fetcher = BrowserFetcher(
            headless=config.browser.headless,
            timeout=config.browser.timeout,
            wait_until=config.browser.wait_until,
            user_agent=user_agent,
        )

    @property
    def fallback_enabled(self) -> bool:
        return self.config.fetcher is FetcherType.AUTO and self.config.browser.enabled

    def is_content_sparse(self, content: str, headers: Dict[str, str] = None) -> bool:
        """Check if a response is likely an empty shell that needs JavaScript to render."""
        content_type = (headers or {}).get("content-type", "")
        if content_type and "html" not in content_type.lower():
            return False

        stripped = content.strip()
        if not stripped:
            return True
        if stripped[0] in "{[":
            return False

        visible = "".join(part.strip() for part in Selector(text=content).xpath(VISIBLE_TEXT_XPATH).getall())
        return len(visible) < MIN_VISIBLE_TEXT

    async def fetch(self, url: str, force_browser: bool = False) -> Dict[str, Any]:
        """Fetch with automatic fallback strategy."""
        if force_browser or self.config.fetcher is FetcherType.BROWSER:
            return await self.browser_fetcher.fetch(url)

        try:
            result = await self.static_fetcher.fetch(url)
        except Exception as static_error:
            if not self.fallback_enabled:
                raise
            try:
                return await self._fetch_with_fallback(url)
            except Exception:
                raise static_error

        needs_browser = (
            self.fallback_enabled
            and result["status_code"] < 400
            and self.is_content_sparse(result["content"], result["headers"])
        )
        if not needs_browser:
            return result

        try:
            return await self._fetch_with_fallback(url)
        except Exception:
            result["fallback_attempted"] = True
            result["fallback_failed"] = True
            return result

    async def _fetch_with_fallback(self, url: str) -> Dict[str, Any]:
        result = await self.browser_fetcher.fetch(url)
        result["fallback_used"] = True
        return result

    async def close(self):
        await self.static_fetcher.close()


class RawHtmlStorage:
    """Store raw HTML in blob storage."""

    def __init__(self, storage_path: str = "./data/raw_html"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def save(self, url: str, content: str) -> str:
        """Save raw HTML and return pointer."""
        filepath = self.storage_path / f"{hashlib.sha256(url.encode()).hexdigest()}.html"
        filepath.write_text(content, encoding="utf-8", errors="replace")
        return str(filepath)

    def load(self, pointer: str) -> Optional[str]:
        """Load raw HTML from pointer."""
        try:
            return Path(pointer).read_text(encoding="utf-8")
        except OSError:
            return None
