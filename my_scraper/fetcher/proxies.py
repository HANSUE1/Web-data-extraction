"""Operator-supplied HTTP proxies, with failover when one stops working.

A site keeps the same proxy for the whole crawl, so its visitor never changes identity mid-session.
It moves to another proxy only when its own cannot be reached. A refusal from the site (403, 429, a
block page) is the site's answer and never a reason to come back from another address, and the rate
limit counts per site whichever proxy carries the request.
"""
import asyncio
import hashlib
import time
from typing import Dict, List, Optional
from urllib.parse import unquote, urlsplit

import aiohttp

from my_scraper.ops.metrics import METRICS
from my_scraper.utils.http import ssl_context
from my_scraper.utils.urls import site_key

CHECK_TIMEOUT = 10


class NoProxyAvailable(Exception):
    """Every proxy is resting after failures. Going out directly would reveal what the proxies are for."""


def label(proxy: str) -> str:
    """The proxy without its credentials, safe for logs and metrics."""
    parts = urlsplit(proxy)
    return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)


def for_browser(proxy: str) -> Dict[str, str]:
    parts = urlsplit(proxy)
    settings = {"server": f"{parts.scheme}://{label(proxy)}"}
    if parts.username:
        settings["username"] = unquote(parts.username)
        settings["password"] = unquote(parts.password or "")
    return settings


class ProxyPool:
    def __init__(self, urls: List[str], max_failures: int = 3, rest_seconds: float = 300):
        for url in urls:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError(f"proxy must look like http://host:port, got {label(url)}")
        self.urls = list(urls)
        self.max_failures, self.rest_seconds = max_failures, rest_seconds
        self._failures: Dict[str, int] = {}
        self._resting: Dict[str, float] = {}
        for url in self.urls:
            METRICS.set("scraper_proxy_up", 1, proxy=label(url))

    def healthy(self) -> List[str]:
        now = time.monotonic()
        return [url for url in self.urls if self._resting.get(url, 0) <= now]

    def for_url(self, url: str) -> Optional[str]:
        """The proxy this site uses. None when no proxies are configured."""
        if not self.urls:
            return None
        healthy = set(self.healthy())
        if not healthy:
            raise NoProxyAvailable("every proxy is resting after failures")
        # Rendezvous hashing: losing one proxy moves only the sites that were using it.
        site = site_key(urlsplit(url).netloc)
        return max(healthy, key=lambda proxy: hashlib.sha256(f"{site}|{proxy}".encode()).digest())

    def succeeded(self, proxy: Optional[str]):
        if proxy:
            self._failures.pop(proxy, None)
            METRICS.inc("scraper_proxy_requests_total", proxy=label(proxy), result="ok")
            METRICS.set("scraper_proxy_up", 1, proxy=label(proxy))

    def failed(self, proxy: Optional[str]):
        if not proxy:
            return
        METRICS.inc("scraper_proxy_requests_total", proxy=label(proxy), result="failed")
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        if self._failures[proxy] >= self.max_failures:
            self._rest(proxy)

    def _rest(self, proxy: str):
        self._failures.pop(proxy, None)
        self._resting[proxy] = time.monotonic() + self.rest_seconds
        METRICS.set("scraper_proxy_up", 0, proxy=label(proxy))

    async def check(self, url: str) -> Dict[str, bool]:
        """Request url through every proxy, and rest the ones that cannot carry it."""
        async def works(session: aiohttp.ClientSession, proxy: str) -> bool:
            try:
                async with session.get(url, proxy=proxy) as response:
                    return response.status != 407  # any other answer came through the proxy
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                return False

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT),
                                         connector=aiohttp.TCPConnector(ssl=ssl_context())) as session:
            results = await asyncio.gather(*(works(session, proxy) for proxy in self.urls))
        for proxy, good in zip(self.urls, results, strict=True):
            if good:
                self._resting.pop(proxy, None)
                self.succeeded(proxy)
            else:
                self._rest(proxy)
        return {label(proxy): good for proxy, good in zip(self.urls, results, strict=True)}
