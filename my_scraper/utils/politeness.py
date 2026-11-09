"""Politeness, robots.txt compliance, and rate limiting."""
import asyncio
import random
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
from protego import Protego

from my_scraper.utils.http import create_session

BACKOFF_STATUSES = (429, 503)

ALLOW_EVERYTHING = "User-agent: *\nAllow: /\n"
DISALLOW_EVERYTHING = "User-agent: *\nDisallow: /\n"


class RobotsChecker:
    """Respect robots.txt rules, following RFC 9309.

    Protego does the matching because Python's own parser ignores * and $ wildcards
    and applies the first matching rule where the standard says the longest one wins.
    """

    FAILURE_TTL = 60  # seconds; an outage should not block a site for the full cache_ttl
    FETCH_TIMEOUT = 10  # seconds
    FETCH_ATTEMPTS = 2
    MAX_BYTES = 512 * 1024  # RFC 9309 lets a crawler stop reading here

    def __init__(self, user_agent: str = "*", cache_ttl: int = 3600):
        self.user_agent = user_agent
        # Rules match on the product name alone. The full string holds words like "bot" in
        # its URL, which would wrongly match a robots.txt section for a different crawler.
        self.agent_token = user_agent.split("/")[0].split()[0] if user_agent.strip() else "*"
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, Tuple[Protego, float]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._failures: Dict[str, str] = {}

    async def is_allowed(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt."""
        rules = await self._rules_for(url)
        return rules.can_fetch(url, self.agent_token)

    async def crawl_delay(self, url: str) -> Optional[float]:
        """The site's requested seconds between requests, if it sets one."""
        return (await self._rules_for(url)).crawl_delay(self.agent_token)

    async def sitemaps(self, url: str) -> List[str]:
        """Sitemaps the site declares in robots.txt."""
        return list((await self._rules_for(url)).sitemaps)

    def failure_for(self, url: str) -> Optional[str]:
        """Why robots.txt could not be read, if that is why url was refused.

        Keeps "the site forbids this" apart from "we could not find out".
        """
        return self._failures.get(self._base_url(url))

    @staticmethod
    def _base_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def _rules_for(self, url: str) -> Protego:
        base_url = self._base_url(url)

        # One fetch per site, even when many workers ask at the same moment.
        async with self._locks.setdefault(base_url, asyncio.Lock()):
            cached = self._cache.get(base_url)
            if cached is None or time.monotonic() >= cached[1]:
                rules, ttl = await self._fetch_rules(base_url)
                cached = (rules, time.monotonic() + ttl)
                self._cache[base_url] = cached

        return cached[0]

    async def _read_limited(self, response: aiohttp.ClientResponse) -> str:
        data = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            data.extend(chunk)
            if len(data) >= self.MAX_BYTES:
                break
        return bytes(data[:self.MAX_BYTES]).decode("utf-8", errors="replace")

    async def _fetch_rules(self, base_url: str) -> Tuple[Protego, int]:
        """Download robots.txt without blocking the event loop."""
        self._failures.pop(base_url, None)
        timeout = aiohttp.ClientTimeout(total=self.FETCH_TIMEOUT)

        status, body, error = None, "", None
        for _ in range(self.FETCH_ATTEMPTS):
            try:
                async with create_session({"User-Agent": self.user_agent}, timeout) as session:
                    async with session.get(f"{base_url}/robots.txt") as response:
                        status = response.status
                        body = await self._read_limited(response)
                error = None
                if status < 500:
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                error = f"{type(e).__name__}: {e}"

        if error:
            self._failures[base_url] = error
            return Protego.parse(DISALLOW_EVERYTHING), self.FAILURE_TTL

        if status in (401, 403) or status >= 500:
            self._failures[base_url] = f"robots.txt returned HTTP {status}"
            return Protego.parse(DISALLOW_EVERYTHING), self.FAILURE_TTL
        if status >= 400:
            return Protego.parse(ALLOW_EVERYTHING), self.cache_ttl  # no robots.txt means no restrictions
        return Protego.parse(body), self.cache_ttl


class RateLimiter:
    """Per-domain pacing: requests to one domain are spaced at least one interval apart."""

    MAX_BACKOFF_INTERVAL = 10.0  # seconds
    RECOVERY_FACTOR = 0.9

    def __init__(self, requests_per_second: float = 1.0):
        self.base_interval = 1.0 / requests_per_second if requests_per_second > 0 else 1.0
        self._intervals: Dict[str, float] = {}
        self._floors: Dict[str, float] = {}
        self._next_slot: Dict[str, float] = {}

    def interval_for(self, domain: str) -> float:
        return max(self._intervals.get(domain, self.base_interval), self._floors.get(domain, 0.0))

    def ready_in(self, domain: str) -> float:
        """Seconds until domain's next free slot, 0 if a request could go now."""
        return max(0.0, self._next_slot.get(domain, 0.0) - time.monotonic())

    def set_floor(self, domain: str, seconds: float):
        """Never go faster than this for domain, as with a robots.txt Crawl-delay."""
        self._floors[domain] = seconds

    async def acquire(self, domain: str):
        """Reserve the next slot for domain, then wait for it.

        Reserving involves no await, so workers cannot race, and a wait on one
        domain never holds up requests to another.
        """
        now = time.monotonic()
        slot = max(now, self._next_slot.get(domain, 0.0))
        self._next_slot[domain] = slot + self.interval_for(domain)
        if slot > now:
            await asyncio.sleep(slot - now + random.uniform(0, 0.05))  # jitter

    def backoff_on_error(self, domain: str, status_code: int):
        """Slow down one domain when it signals overload (429, 503)."""
        if status_code in BACKOFF_STATUSES:
            limit = max(self.MAX_BACKOFF_INTERVAL, self.base_interval)
            self._intervals[domain] = min(self.interval_for(domain) * 2, limit)

    def recover(self, domain: str):
        """Ease a slowed domain back towards the configured rate."""
        if domain in self._intervals:
            eased = self._intervals[domain] * self.RECOVERY_FACTOR  # floors still apply on top
            if eased <= self.base_interval:
                del self._intervals[domain]
            else:
                self._intervals[domain] = eased


class PolitenessManager:
    """Manage all politeness policies."""

    def __init__(self, config):
        self.robots_checker = RobotsChecker(config.robots.user_agent, config.robots.cache_ttl)
        self.rate_limiter = RateLimiter(config.rate_limit.requests_per_second)
        self.respect_robots = config.robots.respect

    async def can_fetch(self, url: str) -> bool:
        """Check if URL can be fetched, and adopt any Crawl-delay the site asks for."""
        if not self.respect_robots:
            return True
        delay = await self.robots_checker.crawl_delay(url)
        if delay:
            self.rate_limiter.set_floor(urlparse(url).netloc, float(delay))
        return await self.robots_checker.is_allowed(url)

    async def wait_for_slot(self, url: str):
        """Wait for rate limit slot."""
        await self.rate_limiter.acquire(urlparse(url).netloc)

    def handle_status(self, url: str, status_code: int):
        """Adjust pacing for a domain based on a response status."""
        domain = urlparse(url).netloc
        if status_code in BACKOFF_STATUSES:
            self.rate_limiter.backoff_on_error(domain, status_code)
        elif status_code < 400:
            self.rate_limiter.recover(domain)
