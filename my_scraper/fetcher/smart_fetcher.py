"""Production-grade fetcher with static and browser fallback."""
import asyncio
import aiohttp
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from pathlib import Path
import hashlib
import os


class StaticFetcher:
    """Lightweight static HTTP fetcher using aiohttp."""
    
    def __init__(self, headers: Dict[str, str] = None, timeout: int = 30):
        self.headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout)
    
    async def fetch(self, url: str) -> Dict[str, Any]:
        """Fetch URL with static HTTP request."""
        start_time = time.time()
        
        async with aiohttp.ClientSession(headers=self.headers, timeout=self.timeout) as session:
            async with session.get(url) as response:
                content = await response.text()
                duration_ms = (time.time() - start_time) * 1000
                
                return {
                    'url': url,
                    'status_code': response.status,
                    'content': content,
                    'headers': dict(response.headers),
                    'duration_ms': duration_ms,
                    'fetcher_type': 'static',
                    'content_length': len(content)
                }


class BrowserFetcher:
    """Browser fetcher for JavaScript-rendered pages."""
    
    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
    
    async def fetch(self, url: str) -> Dict[str, Any]:
        """Fetch URL with headless browser."""
        start_time = time.time()
        
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                context = await browser.new_context()
                page = await context.new_page()
                
                await page.goto(url, wait_until='networkidle', timeout=self.timeout)
                content = await page.content()
                
                await browser.close()
                
                duration_ms = (time.time() - start_time) * 1000
                
                return {
                    'url': url,
                    'status_code': 200,
                    'content': content,
                    'headers': {},
                    'duration_ms': duration_ms,
                    'fetcher_type': 'browser',
                    'content_length': len(content)
                }
        except ImportError:
            # Fallback if playwright not installed
            raise Exception("Playwright not installed. Use 'pip install playwright' and 'playwright install'")
        except Exception as e:
            raise Exception(f"Browser fetch failed: {e}")


class SmartFetcher:
    """Smart fetcher with automatic fallback."""
    
    def __init__(self, config):
        self.config = config
        self.static_fetcher = StaticFetcher()
        self.browser_fetcher = BrowserFetcher(
            headless=config.browser.headless,
            timeout=config.browser.timeout
        )
    
    def is_content_sparse(self, content: str) -> bool:
        """Check if content is likely JS-dependent."""
        if not content:
            return True
        
        # Heuristics for JS dependency
        indicators = [
            'id="root"',
            'id="app"',
            '<noscript>',
            'loading...',
            'enable javascript',
        ]
        
        content_lower = content.lower()
        
        # If very little text content
        if len(content) < 1000:
            return True
        
        # Check for common SPA indicators
        for indicator in indicators:
            if indicator in content_lower:
                # Check if there's actual content
                if content.count('<p>') < 3 and content.count('<div>') < 10:
                    return True
        
        return False
    
    async def fetch(self, url: str, force_browser: bool = False) -> Dict[str, Any]:
        """Fetch with automatic fallback strategy."""
        if force_browser or self.config.fetcher.value == 'browser':
            return await self.browser_fetcher.fetch(url)
        
        # Try static first
        try:
            result = await self.static_fetcher.fetch(url)
            
            # Check if we need browser fallback
            if self.config.fetcher.value == 'auto' and self.is_content_sparse(result['content']):
                try:
                    browser_result = await self.browser_fetcher.fetch(url)
                    browser_result['fallback_used'] = True
                    return browser_result
                except:
                    # If browser fails, return static result
                    result['fallback_attempted'] = True
                    result['fallback_failed'] = True
                    return result
            
            return result
        except Exception as e:
            # On static failure, try browser
            if self.config.browser.enabled:
                try:
                    result = await self.browser_fetcher.fetch(url)
                    result['fallback_used'] = True
                    return result
                except:
                    raise e
            raise


class RawHtmlStorage:
    """Store raw HTML in blob storage."""
    
    def __init__(self, storage_path: str = "./data/raw_html"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
    
    def save(self, url: str, content: str) -> str:
        """Save raw HTML and return pointer."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        filename = f"{url_hash}.html"
        filepath = self.storage_path / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return str(filepath)
    
    def load(self, pointer: str) -> Optional[str]:
        """Load raw HTML from pointer."""
        try:
            with open(pointer, 'r', encoding='utf-8') as f:
                return f.read()
        except:
            return None
