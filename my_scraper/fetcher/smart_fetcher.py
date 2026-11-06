"""Production-grade fetcher with static and browser fallback."""
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
import charset_normalizer
from parsel import Selector
from w3lib.encoding import html_to_unicode
from w3lib.html import get_meta_refresh

from my_scraper.config.settings import FetcherType
from my_scraper.core.page_checks import classify_page
from my_scraper.fetcher.browser import BrowserFetcher, BrowserUnavailable
from my_scraper.utils.http import create_session
from my_scraper.utils.urls import has_route_fragment

# Below this much visible text, an HTML page is probably rendered by JavaScript.
MIN_VISIBLE_TEXT = 200
VISIBLE_TEXT_XPATH = "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"

MAX_BODY_BYTES = 30 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
UNREADABLE_TYPES = ("image/", "video/", "audio/", "font/")

MAX_META_REFRESHES = 3
MAX_META_REFRESH_DELAY = 10  # seconds. Longer delays are pages reloading themselves, not redirects
META_REFRESH_SCAN_CHARS = 16 * 1024

DETECT_SAMPLE_BYTES = 200_000
GARBLED_SHARE = 0.01


def _detect_encoding(body: bytes) -> Optional[str]:
    """Work out an undeclared encoding. Valid UTF-8 is almost never anything else."""
    try:
        body.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = charset_normalizer.from_bytes(body[:DETECT_SAMPLE_BYTES]).best()
    return best.encoding if best else None


def decode_body(body: bytes, content_type: str = "") -> str:
    """Decode a response using its BOM, HTTP header or meta tag, in that order, then detection."""
    if not body:
        return ""
    _, text = html_to_unicode(content_type, body, default_encoding="utf-8", auto_detect_fun=_detect_encoding)

    # Servers do declare the wrong charset. A wall of replacement characters gives that away.
    if text.count("�") > GARBLED_SHARE * len(text):
        detected = _detect_encoding(body)
        if detected:
            retry = body.decode(detected, errors="replace")
            if retry.count("�") < text.count("�"):
                return retry
    return text


class StaticFetcher:
    """Lightweight static HTTP fetcher using aiohttp. Reuses one session for all requests."""

    def __init__(self, headers: Dict[str, str] = None, timeout: int = 30):
        self.headers = headers
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def fetch(self, url: str, headers: Dict[str, str] = None) -> Dict[str, Any]:
        """Fetch URL with static HTTP request, following meta refresh redirects as well."""
        start_time = time.time()

        target = url
        for _ in range(MAX_META_REFRESHES + 1):
            result = await self._get(target, headers)
            refresh_target = self._meta_refresh_target(result)
            if refresh_target is None or refresh_target == target:
                break
            target, headers = refresh_target, None

        result["url"] = url
        result["duration_ms"] = (time.time() - start_time) * 1000
        return result

    @staticmethod
    def _meta_refresh_target(result: Dict[str, Any]) -> Optional[str]:
        content_type = result["headers"].get("content-type", "")
        if result["status_code"] != 200 or (content_type and "html" not in content_type.lower()):
            return None
        delay, target = get_meta_refresh(result["content"][:META_REFRESH_SCAN_CHARS], result["final_url"])
        return target if target and delay <= MAX_META_REFRESH_DELAY else None

    async def _get(self, url: str, headers: Optional[Dict[str, str]]) -> Dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = create_session(self.headers, self.timeout)

        async with self._session.get(url, headers=headers) as response:
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            content_type = response_headers.get("content-type", "")
            body, truncated = await self._read(response, content_type)
            content = decode_body(body, content_type)
            return {
                "url": url,
                "final_url": str(response.url),
                "status_code": response.status,
                "content": content,
                "body": body,
                "truncated": truncated,
                "headers": response_headers,
                "fetcher_type": "static",
                "content_length": len(content),
            }

    @staticmethod
    async def _read(response: aiohttp.ClientResponse, content_type: str) -> tuple:
        """Read at most MAX_BODY_BYTES, so one huge or endless response cannot sink the crawl."""
        if content_type.lower().startswith(UNREADABLE_TYPES):
            return b"", False

        chunks = []
        size = 0
        async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BODY_BYTES:
                return b"".join(chunks)[:MAX_BODY_BYTES], True
        return b"".join(chunks), False

    async def close(self):
        if self._session is not None:
            await self._session.close()


class SmartFetcher:
    """Smart fetcher with automatic fallback."""

    def __init__(self, config):
        self.config = config
        self.browser_failed = False
        user_agent = config.robots.user_agent
        # Identify with the same agent robots.txt was checked against and the audit log records.
        self.static_fetcher = StaticFetcher(headers={"User-Agent": user_agent})
        self.browser_fetcher = BrowserFetcher(
            headless=config.browser.headless,
            timeout=config.browser.timeout,
            wait_until=config.browser.wait_until,
            user_agent=user_agent,
            expand_rounds=config.browser.expand_rounds,
            viewport={"width": config.browser.viewport_width, "height": config.browser.viewport_height},
        )

    @property
    def fallback_enabled(self) -> bool:
        return self.config.fetcher is FetcherType.AUTO and self.config.browser.enabled and not self.browser_failed

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

        visible = "".join(part.strip() for part in Selector(text=content, type="html").xpath(VISIBLE_TEXT_XPATH).getall())
        return len(visible) < MIN_VISIBLE_TEXT

    async def fetch(self, url: str, force_browser: bool = False,
                    headers: Dict[str, str] = None) -> Dict[str, Any]:
        """Fetch with automatic fallback strategy. Extra headers apply to static requests."""
        wants_browser = force_browser or self.config.fetcher is FetcherType.BROWSER
        if wants_browser or (self.fallback_enabled and has_route_fragment(url)):
            return await self._render(url)

        result = await self.static_fetcher.fetch(url, headers=headers)

        needs_browser = (
            self.fallback_enabled
            and 200 <= result["status_code"] < 300
            and classify_page(result) is None  # a block page stays a block page, however it is fetched
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

    async def _render(self, url: str) -> Dict[str, Any]:
        try:
            return await self.browser_fetcher.fetch(url)
        except BrowserUnavailable:
            self.browser_failed = True  # the rest of the crawl carries on with plain HTTP
            raise

    async def _fetch_with_fallback(self, url: str) -> Dict[str, Any]:
        result = await self._render(url)
        result["fallback_used"] = True
        return result

    async def close(self):
        await self.static_fetcher.close()
        await self.browser_fetcher.close()


class RawHtmlStorage:
    """Store raw HTML in blob storage."""

    def __init__(self, storage_path: str = "./data/raw_html"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def path_for(self, url: str) -> Path:
        return self.storage_path / f"{hashlib.sha256(url.encode()).hexdigest()}.html"

    def save(self, url: str, content: str) -> str:
        """Save raw HTML and return pointer."""
        filepath = self.path_for(url)
        filepath.write_text(content, encoding="utf-8", errors="replace")
        return str(filepath)

    def load(self, pointer: str) -> Optional[str]:
        """Load raw HTML from pointer."""
        try:
            return Path(pointer).read_text(encoding="utf-8")
        except OSError:
            return None
