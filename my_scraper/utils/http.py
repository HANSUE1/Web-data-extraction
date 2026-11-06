"""HTTP session setup shared by everything that talks to the network."""
import ssl
from functools import lru_cache
from typing import Dict, Optional

import aiohttp
import certifi

# What any ordinary client sends. Some servers refuse requests that leave these out.
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Verify certificates against Mozilla's maintained bundle.

    Python's default on Windows reads the system store, where expired root
    certificates can make valid sites fail with "certificate has expired".
    """
    return ssl.create_default_context(cafile=certifi.where())


def create_session(headers: Optional[Dict[str, str]] = None,
                   timeout: Optional[aiohttp.ClientTimeout] = None) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(ssl=ssl_context())
    options = {"timeout": timeout} if timeout else {}
    # trust_env honours HTTP_PROXY and friends, which company networks often require.
    return aiohttp.ClientSession(connector=connector, headers={**DEFAULT_HEADERS, **(headers or {})},
                                 trust_env=True, **options)
