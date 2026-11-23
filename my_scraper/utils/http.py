"""HTTP session setup shared by everything that talks to the network."""
import ssl
from functools import lru_cache
from http.cookies import CookieError, Morsel
from typing import Any, Dict, List, Optional

import aiohttp
import certifi
from yarl import URL

# What any ordinary client sends. Some servers refuse requests that leave these out.
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_HEADER_SIZE = 64 * 1024


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Verify certificates against Mozilla's maintained bundle.

    Python's default on Windows reads the system store, where expired root
    certificates can make valid sites fail with "certificate has expired".
    """
    return ssl.create_default_context(cafile=certifi.where())


def login_cookie_jar(cookies: List[Dict[str, Any]]) -> aiohttp.CookieJar:
    """A jar holding browser cookies, each sent only to the site that set it, as the browser would."""
    jar = aiohttp.CookieJar(unsafe=True, quote_cookie=False)  # unsafe: also for sites addressed by IP
    for cookie in cookies:
        host, path = cookie["domain"].lstrip("."), cookie.get("path") or "/"
        morsel: Morsel = Morsel()
        try:
            morsel.set(cookie["name"], cookie["value"], cookie["value"])
        except CookieError:
            continue  # a name no server should send
        morsel["path"] = path
        if cookie["domain"].startswith("."):  # with the dot it covers subdomains, without it only this host
            morsel["domain"] = host
        if cookie.get("secure"):
            morsel["secure"] = True
        jar.update_cookies({cookie["name"]: morsel}, URL.build(scheme="https", host=host, path=path))
    return jar


def create_session(headers: Optional[Dict[str, str]] = None,
                   timeout: Optional[aiohttp.ClientTimeout] = None,
                   cookies: Optional[List[Dict[str, Any]]] = None) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(ssl=ssl_context())
    options = {"timeout": timeout} if timeout else {}
    if cookies:
        options["cookie_jar"] = login_cookie_jar(cookies)  # from --login
    # trust_env honours HTTP_PROXY and friends, which company networks often require.
    # Some sites send a Content-Security-Policy header far over aiohttp's 8 KB default; browsers accept it.
    return aiohttp.ClientSession(connector=connector, headers={**DEFAULT_HEADERS, **(headers or {})},
                                 trust_env=True, max_line_size=MAX_HEADER_SIZE,
                                 max_field_size=MAX_HEADER_SIZE, **options)
