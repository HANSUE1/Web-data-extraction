"""Proxy and User-Agent rotation manager."""
import random
import asyncio
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Proxy:
    """Proxy configuration."""
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"
    country: Optional[str] = None
    is_healthy: bool = True
    last_used: Optional[datetime] = None
    failure_count: int = 0
    success_count: int = 0
    
    @property
    def url(self) -> str:
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"
    
    @property
    def health_score(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total


@dataclass
class UserAgent:
    """User-Agent string with metadata."""
    string: str
    browser: str
    os: str
    device_type: str
    weight: float = 1.0


class ProxyManager:
    """Manages proxy pool with health tracking and rotation."""
    
    def __init__(self, proxies: List[Proxy] = None, config=None):
        self.proxies = proxies or []
        self.config = config
        self._index = 0
        self._lock = asyncio.Lock()
        self._health_check_interval = 300  # 5 minutes
        self._last_health_check = datetime.utcnow()
    
    def add_proxy(self, proxy: Proxy):
        """Add a proxy to the pool."""
        self.proxies.append(proxy)
    
    def load_from_file(self, filepath: str):
        """Load proxies from file (one per line: protocol://user:pass@host:port)."""
        import json
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Parse proxy URL
                self.proxies.append(self._parse_proxy_url(line))
    
    def _parse_proxy_url(self, url: str) -> Proxy:
        """Parse proxy URL into Proxy object."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return Proxy(
            host=parsed.hostname,
            port=parsed.port or 8080,
            username=parsed.username,
            password=parsed.password,
            protocol=parsed.scheme
        )
    
    async def get_proxy(self) -> Optional[Proxy]:
        """Get next healthy proxy using weighted round-robin."""
        async with self._lock:
            if not self.proxies:
                return None
            
            # Filter healthy proxies
            healthy = [p for p in self.proxies if p.is_healthy and p.health_score > 0.3]
            if not healthy:
                # Fallback to any proxy
                healthy = self.proxies
            
            if not healthy:
                return None
            
            # Weighted selection based on health score
            weights = [p.health_score for p in healthy]
            total = sum(weights)
            r = random.random() * total
            
            for i, w in enumerate(weights):
                r -= w
                if r <= 0:
                    proxy = healthy[i]
                    proxy.last_used = datetime.utcnow()
                    return proxy
            
            return healthy[0]
    
    async def mark_success(self, proxy: Proxy):
        """Mark proxy as successful."""
        proxy.success_count += 1
        proxy.failure_count = max(0, proxy.failure_count - 1)
    
    async def mark_failure(self, proxy: Proxy, error: Exception = None):
        """Mark proxy as failed."""
        proxy.failure_count += 1
        if proxy.failure_count >= 5:
            proxy.is_healthy = False
            # Schedule health check
            asyncio.create_task(self._recheck_proxy(proxy))
    
    async def _recheck_proxy(self, proxy: Proxy):
        """Recheck proxy health after cooldown."""
        await asyncio.sleep(300)  # 5 minute cooldown
        proxy.is_healthy = True
        proxy.failure_count = 0


class UserAgentRotator:
    """Rotates User-Agent strings with realistic distributions."""
    
    # Realistic browser/OS combinations with weights
    USER_AGENTS = [
        # Chrome on Windows
        UserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Chrome", "Windows", "desktop", 0.35),
        UserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36", "Chrome", "Windows", "desktop", 0.15),
        # Chrome on Mac
        UserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Chrome", "macOS", "desktop", 0.20),
        # Firefox on Windows
        UserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0", "Firefox", "Windows", "desktop", 0.10),
        # Firefox on Mac
        UserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0", "Firefox", "macOS", "desktop", 0.05),
        # Safari on Mac
        UserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15", "Safari", "macOS", "desktop", 0.08),
        # Edge on Windows
        UserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0", "Edge", "Windows", "desktop", 0.05),
        # Mobile Chrome
        UserAgent("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36", "Chrome", "Android", "mobile", 0.02),
        # Mobile Safari
        UserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1", "Safari", "iOS", "mobile", 0.02),
    ]
    
    def __init__(self, custom_agents: List[UserAgent] = None):
        self.agents = custom_agents or self.USER_AGENTS
        self._weights = [ua.weight for ua in self.agents]
        self._total_weight = sum(self._weights)
    
    def get_random(self) -> UserAgent:
        """Get random User-Agent weighted by probability."""
        r = random.random() * sum(self._weights)
        for i, w in enumerate(self._weights):
            r -= w
            if r <= 0:
                return self.agents[i]
        return self.agents[0]
    
    def get_for_device(self, device_type: str) -> UserAgent:
        """Get User-Agent for specific device type."""
        matching = [ua for ua in self.agents if ua.device_type == device_type]
        if not matching:
            return self.get_random()
        weights = [ua.weight for ua in matching]
        total = sum(weights)
        r = random.random() * total
        for i, w in enumerate(weights):
            r -= w
            if r <= 0:
                return matching[i]
        return matching[0]


class HeaderManager:
    """Manages request headers with rotation."""
    
    def __init__(self, ua_rotator: UserAgentRotator = None, proxy_manager: ProxyManager = None):
        self.ua_rotator = ua_rotator or UserAgentRotator()
        self.proxy_manager = proxy_manager
    
    def get_headers(self, url: str = None, referer: str = None, device_type: str = "desktop") -> Dict[str, str]:
        """Generate request headers."""
        ua = self.ua_rotator.get_for_device(device_type)
        
        headers = {
            "User-Agent": ua.string,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        
        # Add Chrome-specific headers
        if "Chrome" in ua.string:
            headers["sec-ch-ua"] = '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
            headers["sec-ch-ua-mobile"] = "?0" if ua.device_type == "desktop" else "?1"
            headers["sec-ch-ua-platform"] = f'"{ua.os}"'
        
        return headers