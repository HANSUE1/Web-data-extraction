"""Politeness, robots.txt compliance, and rate limiting."""
import time
import random
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
from typing import Dict, Optional
from collections import deque
import asyncio


class RobotsChecker:
    """Respect robots.txt rules."""
    
    def __init__(self, user_agent: str = "*"):
        self.user_agent = user_agent
        self._cache: Dict[str, RobotFileParser] = {}
        self._cache_time: Dict[str, float] = {}
        self.cache_ttl = 3600
    
    def is_allowed(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt."""
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        # Check cache
        if base_url in self._cache:
            if time.time() - self._cache_time[base_url] < self.cache_ttl:
                rp = self._cache[base_url]
                return rp.can_fetch(self.user_agent, url)
        
        # Fetch and parse robots.txt
        try:
            rp = RobotFileParser()
            rp.set_url(f"{base_url}/robots.txt")
            rp.read()
            self._cache[base_url] = rp
            self._cache_time[base_url] = time.time()
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            # If robots.txt can't be fetched, be conservative
            return True


class RateLimiter:
    """Per-domain rate limiting with sliding window."""
    
    def __init__(self, requests_per_second: float = 1.0, burst_size: int = 5):
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 1.0
        self._windows: Dict[str, deque] = {}
        self._last_request: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    async def acquire(self, domain: str):
        """Acquire permission to make a request to domain."""
        async with self._lock:
            now = time.time()
            
            if domain not in self._windows:
                self._windows[domain] = deque()
                self._last_request[domain] = 0
            
            # Remove old entries
            window = self._windows[domain]
            while window and now - window[0] > 1.0:
                window.popleft()
            
            # Check rate limit
            if len(window) >= int(self.requests_per_second):
                # Calculate wait time
                oldest = window[0]
                wait_time = 1.0 - (now - oldest)
                if wait_time > 0:
                    await asyncio.sleep(wait_time + random.uniform(0, 0.1))  # jitter
                    now = time.time()
            
            # Enforce minimum interval
            if now - self._last_request[domain] < self.min_interval:
                wait_time = self.min_interval - (now - self._last_request[domain])
                await asyncio.sleep(wait_time + random.uniform(0, 0.05))  # jitter
                now = time.time()
            
            window.append(now)
            self._last_request[domain] = now
    
    def backoff_on_error(self, domain: str, status_code: int):
        """Reduce request rate on errors (429, 503)."""
        if status_code in [429, 503]:
            # Increase interval (slow down)
            self.min_interval *= 2
            self.min_interval = min(self.min_interval, 10.0)  # cap at 10s


class PolitenessManager:
    """Manage all politeness policies."""
    
    def __init__(self, config):
        self.robots_checker = RobotsChecker(config.robots.user_agent)
        self.rate_limiter = RateLimiter(
            config.rate_limit.requests_per_second,
            config.rate_limit.burst_size
        )
        self.respect_robots = config.robots.respect
    
    async def can_fetch(self, url: str) -> bool:
        """Check if URL can be fetched."""
        if self.respect_robots:
            if not self.robots_checker.is_allowed(url):
                return False
        return True
    
    async def wait_for_slot(self, url: str):
        """Wait for rate limit slot."""
        domain = urlparse(url).netloc
        await self.rate_limiter.acquire(domain)
    
    def handle_error(self, url: str, status_code: int):
        """Handle error responses."""
        domain = urlparse(url).netloc
        self.rate_limiter.backoff_on_error(domain, status_code)
