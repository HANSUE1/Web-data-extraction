"""Find the feeds and public APIs a site offers, which beat scraping its pages.

A WordPress or Shopify site publishes its whole catalogue as clean JSON, and most blogs
and news sites publish a feed. These are ordinary public endpoints, fetched through the
same robots.txt and rate limit checks as any page.
"""
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from parsel import Selector

SITEMAP_LOCATIONS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml", "/sitemap.xml.gz")

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json")
WORDPRESS_API_LINK = "//link[@rel='https://api.w.org/']/@href"
SHOPIFY_MARKERS = re.compile(r"cdn\.shopify\.com|Shopify\.shop\b|shopify-digital-wallet")
WOOCOMMERCE_MARKER = "woocommerce"
COLLECTION_PATH = re.compile(r"^(/(?:[a-z]{2}(?:-[a-z]{2})?/)?collections/[^/]+)")

PLATFORM_API_PATHS = re.compile(r"/wp-json/|/products\.json$")
PAGE_SIZE_PARAMS = ("per_page", "limit")

LINK_HEADER_NEXT = re.compile(r"""<([^>]+)>\s*;[^,]*\brel="?next"?""", re.IGNORECASE)
NEXT_KEYS = ("next", "next_url", "nexturl", "next_page_url", "nextpageurl", "next_link", "nextlink", "next_href")
NEXT_CONTAINERS = ("links", "_links", "paging", "pagination", "meta", "page_info", "pageinfo")

WORDPRESS_POSTS = "wp/v2/posts?per_page=100&page=1"
WOOCOMMERCE_PRODUCTS = "wc/store/v1/products?per_page=100&page=1"
SHOPIFY_PRODUCTS = "products.json?limit=250&page=1"


def feed_links(html: str, base_url: str) -> List[str]:
    """Feeds a page announces in its head."""
    links = []
    for link in Selector(text=html, type="html").xpath("//link[@rel='alternate'][@href]"):
        if link.attrib.get("type", "").lower() in FEED_TYPES:
            links.append(urljoin(base_url, link.attrib["href"]))
    return links


def platform_endpoints(html: str, base_url: str) -> List[str]:
    """Public content APIs of the platform a page was built with."""
    endpoints = []

    api_root = Selector(text=html, type="html").xpath(WORDPRESS_API_LINK).get()
    if api_root:
        api_root = urljoin(base_url, api_root.rstrip("/") + "/")
        endpoints.append(urljoin(api_root, WORDPRESS_POSTS))
        if WOOCOMMERCE_MARKER in html:
            endpoints.append(urljoin(api_root, WOOCOMMERCE_PRODUCTS))

    if SHOPIFY_MARKERS.search(html):
        collection = COLLECTION_PATH.match(urlsplit(base_url).path)
        prefix = f"{collection.group(1)}/" if collection else "/"
        endpoints.append(urljoin(base_url, prefix + SHOPIFY_PRODUCTS))

    return endpoints


def _next_in(record: Dict[str, Any]) -> Optional[str]:
    lowered = {str(key).lower(): value for key, value in record.items()}
    for key in NEXT_KEYS:
        value = lowered.get(key)
        if isinstance(value, dict):
            value = value.get("href")
        if isinstance(value, str) and value.startswith(("http://", "https://", "/")):
            return value
    return None


def api_next_links(headers: Dict[str, str], content: str) -> List[str]:
    """The next page of an API, from a Link header or a "next" field in the JSON body."""
    links = LINK_HEADER_NEXT.findall(headers.get("link", ""))
    if "json" in headers.get("content-type", "").lower():
        try:
            data = json.loads(content)
        except ValueError:
            return links
        if isinstance(data, dict):
            lowered = {str(key).lower(): value for key, value in data.items()}
            records = [data] + [lowered[name] for name in NEXT_CONTAINERS if isinstance(lowered.get(name), dict)]
            links.extend(found for found in map(_next_in, records) if found)
    return links


def is_platform_api(url: str) -> bool:
    return bool(PLATFORM_API_PATHS.search(urlsplit(url).path))


def next_api_page(url: str, item_count: int) -> Optional[str]:
    """The following page of a platform API, if this one came back full."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    page_size = next((int(query[name]) for name in PAGE_SIZE_PARAMS if query.get(name, "").isdigit()), None)
    if page_size is None or item_count < page_size:
        return None
    query["page"] = str(int(query.get("page", "1")) + 1)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
