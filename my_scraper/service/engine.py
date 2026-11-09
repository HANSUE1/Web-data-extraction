"""Scraping as a service: a page, a site, a site's map, or chosen fields, returned as plain data.

The MCP server and the HTTP API both use this. Every request goes through the same
robots.txt, pacing and compliance checks as the crawler, and requests share them, so
two requests to one site are still paced as one visitor.
"""
import asyncio
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from parsel import Selector

from my_scraper.config.settings import CrawlConfig, FetcherType
from my_scraper.core.crawler import CrawlState, ProductionCrawler
from my_scraper.core.links import base_url_of, page_links
from my_scraper.fetcher.smart_fetcher import SmartFetcher
from my_scraper.parser.auto_extractor import page_markdown
from my_scraper.parser.universal_parser import ContentTypeDetector, ParsedPage
from my_scraper.utils.politeness import PolitenessManager
from my_scraper.utils.urls import normalize_url

DEFAULT_MAX_CHARS = 20_000
MAX_ITEMS = 100
MAX_LINKS = 300
MAX_CRAWL_PAGES = 200
MAX_MAP_URLS = 5000
DEFAULT_TIMEOUT = 120
RENDER_MODES = ("auto", "always", "never")

DOCUMENT_SOURCES = ("json-ld", "microdata", "document")
METADATA_FIELDS = ("description", "author", "date", "language", "image", "type")

# What each refusal means to someone who just asked for a page.
REFUSALS = {
    "robots_disallowed": ("The site's robots.txt does not allow this page to be fetched.", 403),
    "robots_unreachable": ("The site's robots.txt could not be read, so the site is not fetched (RFC 9309).", 503),
    "blocked": ("This address is not fetched: it is a login or paywall page, or on the takedown list.", 403),
    "blocked_by_site": ("The site answered with a block page instead of content.", 403),
    "login_required": ("The page needs a login. Only public pages are fetched.", 403),
    "consent_wall": ("The page only shows a consent wall.", 422),
    "soft_404": ("The page does not exist (the site returned a 'not found' page).", 404),
    "off_site_redirect": ("The page redirected to another site.", 422),
    "http_error": ("The site returned an error.", 502),
    "failed": ("The page could not be fetched.", 502),
}

# Names a field goes by in structured data, so "price" also finds "offers.price" or "cost".
SYNONYMS = [
    {"title", "name", "headline", "heading"},
    {"price", "cost", "amount", "offerprice", "lowprice", "saleprice"},
    {"currency", "pricecurrency"},
    {"author", "creator", "byline", "writer", "by"},
    {"date", "published", "datepublished", "publishdate", "pubdate", "created", "datecreated", "publishedat"},
    {"updated", "datemodified", "modified", "lastmodified"},
    {"description", "summary", "excerpt", "abstract", "subtitle"},
    {"image", "thumbnail", "photo", "picture", "img"},
    {"rating", "ratingvalue", "stars", "score"},
    {"reviews", "reviewcount", "ratingcount"},
    {"url", "link", "href"},
    {"text", "body", "content", "articlebody"},
    {"brand", "manufacturer", "maker"},
    {"sku", "mpn", "productid", "gtin"},
    {"availability", "stock", "instock"},
]


class ServiceError(Exception):
    """A request that cannot be answered, with the reason in plain words."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> Dict[str, Any]:
        return {"error": self.code, "message": self.message}


class _ServiceCrawler(ProductionCrawler):
    """A crawler for one request, using the service's shared politeness and fetcher."""

    def __init__(self, config: CrawlConfig, service: "ScrapeService", job_dir: Path):
        super().__init__(config)
        self.state = CrawlState(str(job_dir / "state.json"))
        self.politeness = service.politeness
        self.fetcher = service.fetcher
        self.outcomes: Dict[str, Tuple[str, Optional[str]]] = {}  # url -> (status, reason), in order

        log_event = self.compliance.log_scrape_event

        def record(url, status, items_count=0, personal_data=None, error=None):
            self.outcomes[url] = (status, error)
            log_event(url, status, items_count, personal_data, error)

        self.compliance.log_scrape_event = record

    async def close(self):
        """The fetcher is shared, so it stays open for the next request."""


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _flatten(item: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Nested structured data as dotted keys: {"offers": {"price": 5}} -> {"offers.price": 5}."""
    flat: Dict[str, Any] = {}
    for key, value in item.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, path + "."))
        elif value not in (None, "", []):
            flat[path] = value
    return flat


def _match_score(field: str, key: str) -> int:
    wanted, last = _norm(field), _norm(key.rsplit(".", 1)[-1])
    whole = _norm(key)
    if wanted in (last, whole):
        return 4
    if any(wanted in group and last in group for group in SYNONYMS):
        return 3
    if len(wanted) >= 4 and (wanted in whole or (len(last) >= 4 and last in wanted)):
        return 1
    return 0


def map_fields(item: Dict[str, Any], fields: List[str]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Find each wanted field in an item. Returns (values, the key each came from)."""
    flat = _flatten(item)
    values, keys = {}, {}
    for field in fields:
        best = max(((score, key) for key in flat if (score := _match_score(field, key))), default=None)
        if best:
            values[field] = flat[best[1]]
            keys[field] = best[1]
    return values, keys


def _truncate(text: Optional[str], limit: int) -> Tuple[str, bool]:
    text = text or ""
    if limit and len(text) > limit:
        return text[:limit], True
    return text, False


def _html_title(content: str) -> Optional[str]:
    title = Selector(text=content, type="html").xpath("normalize-space(//title)").get()
    return title or None


class ScrapeService:
    """Scrape, crawl, map and extract, with one shared fetcher and politeness state."""

    def __init__(self, home: Optional[str] = None, user_agent: Optional[str] = None,
                 rate_limit: Optional[float] = None, use_browser: bool = True):
        self.home = Path(home or os.environ.get("SCRAPER_HOME") or Path.home() / ".web-data-extraction")
        (self.home / "logs").mkdir(parents=True, exist_ok=True)
        (self.home / "jobs").mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent or os.environ.get("SCRAPER_USER_AGENT")
        self.rate_limit = rate_limit
        self.use_browser = use_browser

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._renew_shared()

    def _renew_shared(self):
        base = self._config([])
        self.politeness = PolitenessManager(base)
        self.fetcher = SmartFetcher(base)

    def _bind_to_loop(self):
        """Connections and locks belong to one event loop. A caller on a new loop gets new ones."""
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            self._renew_shared()  # the old loop is gone, and its connections with it
        self._loop = loop

    async def close(self):
        if self._loop is asyncio.get_running_loop():
            await self.fetcher.close()

    # --- Building blocks ----------------------------------------------------

    def _config(self, start_urls: List[str], **overrides) -> CrawlConfig:
        config = CrawlConfig(name="scraper_service", start_urls=start_urls, export_formats=[], **overrides)
        config.fetcher = FetcherType.AUTO if self.use_browser else FetcherType.STATIC
        config.logging.console = False  # stdout belongs to the MCP connection
        config.logging.log_file = str(self.home / "logs" / "service.log")
        config.logging.audit_log = str(self.home / "logs" / "audit.log")
        if self.user_agent:
            config.robots.user_agent = self.user_agent
        if self.rate_limit:
            config.rate_limit.requests_per_second = self.rate_limit
        return config

    @contextmanager
    def _job(self) -> Iterator[Path]:
        """A scratch folder for one request's state and raw pages, removed afterwards."""
        path = Path(tempfile.mkdtemp(prefix=f"{uuid.uuid4().hex[:8]}_", dir=self.home / "jobs"))
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def _crawler(self, job: Path, start_urls: List[str], render: str, **overrides) -> _ServiceCrawler:
        self._bind_to_loop()
        config = self._config(start_urls, **overrides)
        config.storage.blob_storage_path = str(job / "raw_html")
        config.output_dir = str(job / "output")
        crawler = _ServiceCrawler(config, self, job)
        if render == "never":
            crawler._render_limit = 0
        return crawler

    @staticmethod
    def _check(url: str, render: str = "auto") -> str:
        if not isinstance(url, str) or urlparse(url.strip()).scheme not in ("http", "https"):
            raise ServiceError("invalid_url", f"Not an http(s) URL: {url!r}")
        if render not in RENDER_MODES:
            raise ServiceError("invalid_render", f"render must be one of {', '.join(RENDER_MODES)}")
        try:
            return normalize_url(url.strip())
        except ValueError as e:
            raise ServiceError("invalid_url", f"Could not read URL {url!r}: {e}")

    @staticmethod
    def _refusal(crawler: _ServiceCrawler, url: str) -> ServiceError:
        status, reason = crawler.outcomes.get(url) or next(reversed(crawler.outcomes.values()), ("failed", None))
        message, code = REFUSALS.get(status, REFUSALS["failed"])
        return ServiceError(status, f"{message}{f' ({reason})' if reason else ''}", code)

    async def _fetch(self, crawler: _ServiceCrawler, url: str, render: str) -> Tuple[Dict[str, Any], ParsedPage]:
        """One page through the full pipeline, with the browser when the result calls for it."""
        try:
            result = await crawler.fetch_page(url, render=render == "always")
        except Exception as e:
            raise ServiceError("failed", f"The page could not be fetched: {str(e) or repr(e)}", 502)
        if result is None:
            raise self._refusal(crawler, url)

        final_url = normalize_url(result.get("final_url") or url)
        if not crawler._landed_well(url, final_url, result):
            raise self._refusal(crawler, url)
        return await crawler._best_view(url, result)

    @staticmethod
    def _markdown(result: Dict[str, Any], page: ParsedPage) -> Optional[str]:
        content, url = result["content"], result.get("final_url") or result["url"]
        if ContentTypeDetector.detect(content, result.get("headers"), url, result.get("body")) == "html":
            markdown = page_markdown(content, url)
            if markdown:
                return markdown
        texts = [str(item["text"]) for item in page.items if item.get("text")]
        return "\n\n".join(texts) or None

    @staticmethod
    def _summary(result: Dict[str, Any], page: ParsedPage) -> Dict[str, Any]:
        """Title and metadata, from the page's own description of itself where it has one."""
        document = next((item for item in page.items if item.get("source") in DOCUMENT_SOURCES), {})
        title = document.get("title")
        if not title and "html" in (result.get("headers") or {}).get("content-type", "html"):
            title = _html_title(result["content"])
        metadata = {name: document[name] for name in METADATA_FIELDS if document.get(name)}
        if document.get("attributes"):
            metadata["attributes"] = document["attributes"]
        return {"title": title, "metadata": metadata}

    # --- Requests -------------------------------------------------------------

    async def scrape(self, url: str, render: str = "auto", max_chars: int = DEFAULT_MAX_CHARS,
                     include_items: bool = True, include_links: bool = False) -> Dict[str, Any]:
        """One page: its main content as Markdown, its metadata, and any items found on it."""
        url = self._check(url, render)
        with self._job() as job:
            crawler = self._crawler(job, [url], render, open_web=True, max_pages=1, max_depth=0)
            result, page = await self._fetch(crawler, url, render)
            markdown = await asyncio.to_thread(self._markdown, result, page)

        markdown, truncated = _truncate(markdown, max_chars)
        response = {
            "url": url,
            "final_url": result.get("final_url") or url,
            "status_code": result.get("status_code"),
            "fetched_with": result.get("fetcher_type"),
            **self._summary(result, page),
            "markdown": markdown,
            "truncated": truncated,
        }
        if include_items:
            items = [item for item in page.items if item.get("source") not in DOCUMENT_SOURCES]
            response["items"] = items[:MAX_ITEMS]
            response["items_total"] = len(items)
        if include_links and "html" in (result.get("headers") or {}).get("content-type", "html"):
            response["links"] = self._links(result)
        return response

    @staticmethod
    def _links(result: Dict[str, Any]) -> List[str]:
        sel = Selector(text=result["content"], type="html")
        base = base_url_of(sel, result.get("final_url") or result["url"])
        links: List[str] = []
        for href in page_links(sel):
            try:
                link = normalize_url(urljoin(base, href.strip()))
            except ValueError:
                continue
            if urlparse(link).scheme in ("http", "https") and link not in links:
                links.append(link)
                if len(links) >= MAX_LINKS:
                    break
        return links

    async def crawl(self, url: str, max_pages: int = 20, max_depth: int = 3, include: List[str] = None,
                    exclude: List[str] = None, render: str = "auto", use_sitemap: bool = False,
                    max_chars_per_page: int = 8000, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """Crawl one site and return every page as Markdown, plus the items found."""
        url = self._check(url, render)
        max_pages = max(1, min(int(max_pages), MAX_CRAWL_PAGES))
        for pattern in (include or []) + (exclude or []):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ServiceError("invalid_pattern", f"Invalid pattern {pattern!r}: {e}")

        with self._job() as job:
            crawler = self._crawler(job, [url], render, allowed_domains=[urlparse(url).netloc],
                                    max_pages=max_pages, max_depth=max(0, int(max_depth)),
                                    include_patterns=include or [], exclude_patterns=exclude or [],
                                    use_sitemap=use_sitemap)
            timed_out = False
            try:
                await asyncio.wait_for(crawler.crawl(), timeout)
            except asyncio.TimeoutError:
                timed_out = True
            pages = await asyncio.to_thread(self._crawled_pages, crawler, max_chars_per_page)

        skipped: Dict[str, Dict[str, Any]] = {}
        for page_url, (status, reason) in crawler.outcomes.items():
            if status not in ("success", "no_data_extracted"):
                entry = skipped.setdefault(status, {"count": 0, "example": page_url, "reason": reason})
                entry["count"] += 1

        if not pages and skipped and url in crawler.outcomes:
            raise self._refusal(crawler, url)
        return {
            "url": url,
            "pages_crawled": len(pages),
            "timed_out": timed_out,
            "pages": pages,
            "items": crawler.results[:MAX_ITEMS * 5],
            "items_total": len(crawler.results),
            "skipped": skipped,
        }

    @staticmethod
    def _crawled_pages(crawler: _ServiceCrawler, max_chars: int) -> List[Dict[str, Any]]:
        by_page: Dict[str, List[Dict[str, Any]]] = {}
        for item in crawler.results:
            by_page.setdefault(item.get("source_url") or item.get("url"), []).append(item)

        pages = []
        for page_url, (status, _) in crawler.outcomes.items():
            if status not in ("success", "no_data_extracted"):
                continue
            items = by_page.get(page_url, [])
            html = crawler.raw_storage.load(str(crawler.raw_storage.path_for(page_url)))
            markdown = page_markdown(html, page_url) if html else None
            if not markdown:
                markdown = "\n\n".join(str(item["text"]) for item in items if item.get("text")) or None
            markdown, truncated = _truncate(markdown, max_chars)
            document = next((item for item in items if item.get("source") in DOCUMENT_SOURCES), {})
            pages.append({
                "url": page_url,
                "title": document.get("title") or (_html_title(html) if html else None),
                "markdown": markdown,
                "truncated": truncated,
                "items": len(items),
            })
        return pages

    async def map_site(self, url: str, limit: int = 500, search: Optional[str] = None,
                       use_sitemap: bool = True) -> Dict[str, Any]:
        """The pages a site has: from its sitemaps and the links on the given page."""
        url = self._check(url)
        limit = max(1, min(int(limit), MAX_MAP_URLS))
        with self._job() as job:
            crawler = self._crawler(job, [url], "never", allowed_domains=[urlparse(url).netloc],
                                    max_pages=MAX_MAP_URLS, max_depth=1)
            from_sitemap = 0
            if use_sitemap:
                await crawler._seed_from_sitemaps([url])
                from_sitemap = len(crawler.state.frontier)

            try:
                result = await crawler.fetch_page(url)
            except Exception:
                result = None
            if result is not None:
                final_url = normalize_url(result.get("final_url") or url)
                crawler.extract_links(result["content"], final_url, 0)
            elif not from_sitemap:
                raise self._refusal(crawler, url)

        urls = [url] + [queued for queued, _ in crawler.state.frontier
                        if queued != url and self._fetchable(crawler, queued)]
        if search:
            urls = [found for found in urls if search.lower() in found.lower()]
        return {"url": url, "count": len(urls), "from_sitemap": from_sitemap, "urls": urls[:limit],
                "truncated": len(urls) > limit}

    @staticmethod
    def _fetchable(crawler: _ServiceCrawler, url: str) -> bool:
        """Leave out pages the scraper would refuse anyway, such as login pages."""
        try:
            crawler.compliance.validate_url(url)
            return True
        except ValueError:
            return False

    async def extract(self, url: str, fields: List[str], many: bool = False, render: str = "auto",
                      markdown_if_missing: bool = True, max_chars: int = 8000) -> Dict[str, Any]:
        """Chosen fields from a page, found in its structured data and the items on it.

        With many, one record per item on the page, as on a listing. Fields that can't be
        found are listed, with the page's Markdown so the caller can read them itself.
        """
        url = self._check(url, render)
        fields = [field.strip() for field in fields if isinstance(field, str) and field.strip()]
        if not fields:
            raise ServiceError("no_fields", "Name at least one field to extract.")

        with self._job() as job:
            crawler = self._crawler(job, [url], render, open_web=True, max_pages=1, max_depth=0)
            result, page = await self._fetch(crawler, url, render)
            markdown = await asyncio.to_thread(self._markdown, result, page) if markdown_if_missing else None

        if many:
            response = self._extract_many(page, fields)
        else:
            response = self._extract_one(page, fields)
        response["url"] = url
        if response["missing"] and markdown:
            response["markdown"], response["truncated"] = _truncate(markdown, max_chars)
        return response

    @staticmethod
    def _extract_one(page: ParsedPage, fields: List[str]) -> Dict[str, Any]:
        # The page's description of itself is the most reliable, then the items on it.
        ranked = sorted(page.items, key=lambda item: DOCUMENT_SOURCES.index(item["source"])
                        if item.get("source") in DOCUMENT_SOURCES else len(DOCUMENT_SOURCES))
        data: Dict[str, Any] = {}
        found_in: Dict[str, str] = {}
        for item in ranked:
            values, keys = map_fields(item, [field for field in fields if field not in data])
            for field, value in values.items():
                data[field] = value
                found_in[field] = f"{item.get('source')}:{keys[field]}"
        return {"data": data, "found_in": found_in, "missing": [field for field in fields if field not in data]}

    @staticmethod
    def _extract_many(page: ParsedPage, fields: List[str]) -> Dict[str, Any]:
        records = []
        for item in page.items:
            if item.get("source") == "document" and len(page.items) > 1:
                continue  # the page as a whole is not one of its items
            values, _ = map_fields(item, fields)
            if values:
                records.append(values)
        seen = {field for record in records for field in record}
        return {"items": records[:MAX_ITEMS * 5], "items_total": len(records),
                "missing": [field for field in fields if field not in seen]}
