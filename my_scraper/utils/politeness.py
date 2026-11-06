"""Politeness, robots.txt compliance, and rate limiting."""
import asyncio
import random
import time
from typing import Dict, Tuple
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp

BACKOFF_STATUSES = (429, 503)


class RobotsChecker:
    """Respect robots.txt rules, following RFC 9309 for missing or unreachable files."""

    FAILURE_TTL = 60  # seconds; an outage should not block a site for the full cache_ttl
    FETCH_TIMEOUT = 10  # seconds

    def __init__(self, user_agent: str = "*", cache_ttl: int = 3600):
        self.user_agent = user_agent
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, Tuple[RobotFileParser, float]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def is_allowed(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt."""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # One fetch per site, even when many workers ask at the same moment.
        async with self._locks.setdefault(base_url, asyncio.Lock()):
            cached = self._cache.get(base_url)
            if cached is None or time.monotonic() >= cached[1]:
                rules, ttl = await self._fetch_rules(base_url)
                cached = (rules, time.monotonic() + ttl)
                self._cache[base_url] = cached

        return cached[0].can_fetch(self.user_agent, url)

    async def _fetch_rules(self, base_url: str) -> Tuple[RobotFileParser, int]:
        """Download robots.txt without blocking the event loop."""
        rules = RobotFileParser(f"{base_url}/robots.txt")
        timeout = aiohttp.ClientTimeout(total=self.FETCH_TIMEOUT)
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": self.user_agent}, timeout=timeout) as session:
                async with session.get(rules.url) as response:
                    status = response.status
                    body = await response.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            rules.disallow_all = True
            return rules, self.FAILURE_TTL

        if status in (401, 403) or status >= 500:
            rules.disallow_all = True
            return rules, self.FAILURE_TTL
        if status >= 400:
            rules.allow_all = True  # no robots.txt means no restrictions
        else:
            rules.parse(body.splitlines())
        return rules, self.cache_ttl


class RateLimiter:
    """Per-domain pacing: requests to one domain are spaced at least one interval apart."""

    MAX_BACKOFF_INTERVAL = 10.0  # seconds
    RECOVERY_FACTOR = 0.9

    def __init__(self, requests_per_second: float = 1.0):
        self.base_interval = 1.0 / requests_per_second if requests_per_second > 0 else 1.0
        self._intervals: Dict[str, float] = {}
        self._next_slot: Dict[str, float] = {}

    def interval_for(self, domain: str) -> float:
        return self._intervals.get(domain, self.base_interval)

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
            eased = self._intervals[domain] * self.RECOVERY_FACTOR
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
        """Check if URL can be fetched."""
        return not self.respect_robots or await self.robots_checker.is_allowed(url)

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
