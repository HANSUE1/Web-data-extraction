"""Session and cookie management for persistent crawls."""
import asyncio
import json
import time
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from urllib.parse import urlparse
import aiohttp
from aiohttp import CookieJar


@dataclass
class SessionData:
    """Stored session data."""
    domain: str
    cookies: Dict[str, str]
    headers: Dict[str, str]
    local_storage: Dict[str, str]
    session_storage: Dict[str, str]
    created_at: float
    expires_at: float
    auth_token: Optional[str] = None
    csrf_token: Optional[str] = None


class SessionManager:
    """Manages persistent sessions with cookie storage."""
    
    def __init__(self, storage_dir: str = "./data/sessions"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: Dict[str, SessionData] = {}
        self._lock = asyncio.Lock()
    
    def _session_file(self, domain: str) -> Path:
        safe_domain = domain.replace(".", "_").replace(":", "_")
        return self.storage_dir / f"{safe_domain}.json"
    
    async def load_session(self, domain: str) -> Optional[SessionData]:
        """Load session for domain."""
        async with self._lock:
            if domain in self.sessions:
                session = self.sessions[domain]
                if session.expires_at > time.time():
                    return session
                else:
                    del self.sessions[domain]
            
            file_path = self._session_file(domain)
            if file_path.exists():
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                    session = SessionData(**data)
                    if session.expires_at > time.time():
                        self.sessions[domain] = session
                        return session
                except Exception:
                    pass
            return None
    
    async def save_session(self, domain: str, session: SessionData):
        """Save session for domain."""
        async with self._lock:
            self.sessions[domain] = session
            file_path = self._session_file(domain)
            with open(file_path, 'w') as f:
                json.dump(asdict(session), f)
    
    async def update_cookies(self, domain: str, cookies: Dict[str, str]):
        """Update cookies for domain."""
        session = await self.load_session(domain) or SessionData(
            domain=domain,
            cookies={},
            headers={},
            local_storage={},
            session_storage={},
            created_at=time.time(),
            expires_at=time.time() + 86400 * 30  # 30 days
        )
        session.cookies.update(cookies)
        await self.save_session(domain, session)
    
    async def get_cookies(self, domain: str) -> Dict[str, str]:
        """Get cookies for domain."""
        session = await self.load_session(domain)
        return session.cookies if session else {}
    
    async def clear_session(self, domain: str):
        """Clear session for domain."""
        async with self._lock:
            if domain in self.sessions:
                del self.sessions[domain]
            file_path = self._session_file(domain)
            if file_path.exists():
                file_path.unlink()
    
    def create_cookie_jar(self, domain: str) -> CookieJar:
        """Create aiohttp CookieJar with stored cookies."""
        jar = CookieJar(unsafe=True)
        # Note: Actual cookie loading happens async
        return jar


class BrowserSession:
    """Persistent browser session with Playwright."""
    
    def __init__(self, config):
        self.config = config
        self.browser = None
        self.context = None
        self.pages: Dict[str, Any] = {}
        self._initialized = False
    
    async def initialize(self):
        if self._initialized:
            return
        
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.config.browser.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        
        # Create persistent context
        self.context = await self.browser.new_context(
            viewport={
                "width": self.config.browser.viewport_width,
                "height": self.config.browser.viewport_height
            },
            user_agent=self.config.robots.user_agent,
            java_script_enabled=self.config.browser.javascript_enabled,
        )
        
        # Add stealth scripts
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        self._initialized = True
    
    async def get_page(self, url: str = None) -> Any:
        """Get or create a page."""
        await self.initialize()
        
        if url:
            domain = urlparse(url).netloc
            if domain in self.pages:
                return self.pages[domain]
        
        page = await self.context.new_page()
        
        if url:
            domain = urlparse(url).netloc
            self.pages[domain] = page
        
        return page
    
    async def navigate(self, url: str, wait_until: str = "networkidle") -> Any:
        """Navigate to URL with stealth."""
        page = await self.get_page(url)
        
        # Add random delays
        import random
        await page.wait_for_timeout(random.randint(100, 500))
        
        response = await page.goto(url, wait_until=wait_until, timeout=self.config.browser.timeout)
        
        # Handle common challenges
        await self._handle_challenges(page)
        
        return response
    
    async def _handle_challenges(self, page: Any):
        """Handle CAPTCHAs, cookie banners, etc."""
        try:
            # Common cookie banner selectors
            cookie_selectors = [
                'button:has-text("Accept")',
                'button:has-text("Allow")',
                'button:has-text("I agree")',
                '[id*="cookie"] button',
                '[class*="cookie"] button',
            ]
            
            for selector in cookie_selectors:
                try:
                    btn = await page.wait_for_selector(selector, timeout=2000)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await page.wait_for_timeout(500)
                        break
                except:
                    continue
        except:
            pass
    
    async def get_cookies(self, domain: str) -> list:
        """Get cookies for domain."""
        return await self.context.cookies()
    
    async def set_cookies(self, domain: str, cookies: list):
        """Set cookies for domain."""
        await self.context.add_cookies(cookies)
    
    async def close(self):
        if self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright'):
            await self.playwright.stop()
        self._initialized = False


class AuthManager:
    """Authentication state management."""
    
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self.auth_handlers: Dict[str, callable] = {}
    
    def register_auth_handler(self, domain: str, handler: callable):
        """Register custom auth handler for domain."""
        self.auth_handlers[domain] = handler
    
    async def ensure_authenticated(self, domain: str, page: Any = None) -> bool:
        """Ensure session is authenticated for domain."""
        session = await self.session_manager.load_session(domain)
        
        if not session or not session.auth_token:
            # Try custom handler
            if domain in self.auth_handlers:
                return await self.auth_handlers[domain](page)
            return False
        
        # Check if token is expired
        if session.expires_at < time.time():
            return await self._refresh_token(domain)
        
        return True
    
    async def _refresh_token(self, domain: str) -> bool:
        """Refresh authentication token."""
        # Implement OAuth refresh logic here
        return False
    
    async def store_auth(self, domain: str, auth_token: str, csrf_token: str = None, expires_in: int = 3600):
        """Store authentication tokens."""
        session = await self.session_manager.load_session(domain) or SessionData(
            domain=domain,
            cookies={},
            headers={},
            local_storage={},
            session_storage={},
            created_at=time.time(),
            expires_at=time.time() + expires_in
        )
        session.auth_token = auth_token
        session.csrf_token = csrf_token
        session.expires_at = time.time() + expires_in
        await self.session_manager.save_session(domain, session)


class SessionPool:
    """Pool of browser sessions for concurrent crawling."""
    
    def __init__(self, config, pool_size: int = 3):
        self.config = config
        self.pool_size = pool_size
        self.sessions: List[BrowserSession] = []
        self._available: asyncio.Queue = asyncio.Queue()
        self._in_use: Dict[int, BrowserSession] = {}
    
    async def initialize(self):
        for i in range(self.pool_size):
            session = BrowserSession(self.config)
            await session.initialize()
            self.sessions.append(session)
            await self._available.put(session)
    
    async def acquire(self) -> BrowserSession:
        return await self._available.get()
    
    async def release(self, session: BrowserSession):
        await self._available.put(session)
    
    async def close_all(self):
        for session in self.sessions:
            await session.close()
        self.sessions.clear()