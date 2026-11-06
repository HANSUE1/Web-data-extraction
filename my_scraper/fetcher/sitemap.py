"""Find a site's pages from its sitemaps."""
import zlib
from typing import Awaitable, Callable, List, Optional, Tuple

from lxml import etree

MAX_SITEMAP_BYTES = 50 * 1024 * 1024  # the sitemap protocol's own limit
MAX_NESTING = 3  # sitemap index -> sitemap index -> sitemap

GZIP_MAGIC = b"\x1f\x8b"
ANY_NAMESPACE_URL_LOC = "//*[local-name()='url']/*[local-name()='loc']/text()"
ANY_NAMESPACE_SITEMAP_LOC = "//*[local-name()='sitemap']/*[local-name()='loc']/text()"


def _decompress(body: bytes) -> bytes:
    """Unpack .xml.gz files, refusing to expand past the size limit."""
    if body[:2] != GZIP_MAGIC:
        return body
    return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, MAX_SITEMAP_BYTES)


def parse_sitemap(body: bytes) -> Tuple[List[str], List[str]]:
    """Return (page URLs, nested sitemap URLs). Handles XML, gzip and plain-text sitemaps."""
    body = _decompress(body)

    if not body.lstrip()[:1] == b"<":
        lines = body.decode("utf-8", errors="replace").splitlines()
        return [line.strip() for line in lines if line.strip().startswith("http")], []

    # Sitemaps come from the open web: never fetch or expand anything they reference.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
    root = etree.fromstring(body, parser=parser)
    if root is None:
        return [], []

    pages = [url.strip() for url in root.xpath(ANY_NAMESPACE_URL_LOC)]
    children = [url.strip() for url in root.xpath(ANY_NAMESPACE_SITEMAP_LOC)]
    return pages, children


async def discover_urls(sitemaps: List[str], fetch_body: Callable[[str], Awaitable[Optional[bytes]]],
                        accept: Callable[[str], bool], limit: int) -> List[str]:
    """Walk sitemaps breadth-first and collect up to limit acceptable page URLs."""
    found: List[str] = []
    seen = set()
    queue = [(url, 0) for url in sitemaps]

    while queue and len(found) < limit:
        sitemap_url, level = queue.pop(0)
        if sitemap_url in seen or level > MAX_NESTING:
            continue
        seen.add(sitemap_url)

        body = await fetch_body(sitemap_url)
        if body is None:
            continue

        pages, children = parse_sitemap(body)
        found.extend(page for page in pages if accept(page))
        queue.extend((child, level + 1) for child in children)

    return found[:limit]
