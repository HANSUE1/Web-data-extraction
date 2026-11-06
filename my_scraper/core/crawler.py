"""Production-grade crawler orchestrator."""
import asyncio
import hashlib
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from parsel import Selector

from my_scraper.compliance.manager import ComplianceManager
from my_scraper.core.links import base_url_of, forbids_following, next_page_links, page_links
from my_scraper.core.page_checks import BLOCKED, CONSENT, LOGIN, classify_page
from my_scraper.fetcher.discovery import (
    SITEMAP_LOCATIONS,
    api_next_links,
    feed_links,
    is_platform_api,
    next_api_page,
    platform_endpoints,
)
from my_scraper.fetcher.sitemap import discover_urls
from my_scraper.fetcher.smart_fetcher import RawHtmlStorage, SmartFetcher
from my_scraper.logutil import StructuredLogger, log_parsing, log_request
from my_scraper.parser.by_example import learn_selectors
from my_scraper.parser.universal_parser import ParsedPage, UniversalParser
from my_scraper.queueing.persistent_queue import ETagCache
from my_scraper.storage.multi_format import StorageManager
from my_scraper.utils.politeness import PolitenessManager
from my_scraper.utils.retry import HttpStatusError, RetryManager
from my_scraper.utils.urls import domain_key, facet_key, is_action, is_asset, is_trap, normalize_url, site_key

CHECKPOINT_EVERY_PAGES = 10

MAX_CONSECUTIVE_REFUSALS = 5   # after this, stop asking a site that keeps saying no
REFUSAL_STATUSES = {401, 403, 407, 429, 451, 503}

MAX_FILTER_VARIANTS = 200      # filter combinations per path, as faceted search never ends
MAX_PARSE_SECONDS = 60

MAX_RENDER_ATTEMPTS = 3        # rendering that keeps not helping a site is not worth more tries
WEAK_DOCUMENT_TEXT = 800
SLOW_PACING_SECONDS = 5        # worth a mention, or a slow crawl looks like a hang
RENDER_GAIN = 1.2              # a rendered copy must beat the static one by this much to be used

RECORD_SOURCES = {"list", "table", "csv", "xml", "xlsx", "json", "embedded", "network"}

QueuedUrl = Tuple[str, int]  # (url, depth)


class CrawlState:
    """Persist crawl state for checkpointing."""

    def __init__(self, state_file: str = "./data/crawl_state.json"):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.items_file = self.state_file.with_name(f"{self.state_file.stem}_items.jsonl")
        self.visited: Set[str] = set()
        self.frontier: deque = deque()
        self.extracted_count = 0
        self.start_time: Optional[float] = None

    def save(self, in_flight: Iterable[QueuedUrl] = ()):
        """Save state to disk. URLs still in flight are stored as not yet done."""
        in_flight = list(in_flight)
        busy = {url for url, _ in in_flight}
        snapshot = {
            "visited": sorted(self.visited - busy),
            "frontier": in_flight + list(self.frontier),
            "extracted_count": self.extracted_count,
            "start_time": self.start_time,
            "last_checkpoint": time.time(),
        }
        # Write then swap, so a crash mid-save cannot corrupt the previous checkpoint.
        temp_file = self.state_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(snapshot), encoding="utf-8")
        os.replace(temp_file, self.state_file)

    def load(self) -> bool:
        """Load state from disk. Returns False if there is no usable checkpoint."""
        try:
            snapshot = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.visited = set(snapshot["visited"])
            self.frontier = deque((url, depth) for url, depth in snapshot["frontier"])
            self.extracted_count = snapshot["extracted_count"]
            self.start_time = snapshot["start_time"]
            return True
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return False

    def reset_items(self):
        self.items_file.write_text("", encoding="utf-8")

    def append_items(self, page: str, items: List[Dict[str, Any]]):
        """Record items the moment they are found, so a hard crash cannot lose them."""
        with open(self.items_file, "a", encoding="utf-8", errors="replace") as f:
            for item in items:
                f.write(json.dumps({"page": page, "item": item}, ensure_ascii=False, default=str) + "\n")

    def load_items(self) -> List[Dict[str, Any]]:
        """Items from pages the checkpoint counts as finished.

        Any other page is fetched again on resume, so keeping its items would
        duplicate them. A line cut short by a crash is skipped.
        """
        items = []
        if self.items_file.exists():
            for line in self.items_file.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record["page"] in self.visited:
                    items.append(record["item"])
        return items


def page_strength(page: ParsedPage) -> float:
    """How much a parsed page holds, for comparing a static copy with a rendered one."""
    filled = sum(1 for item in page.items if any(value not in (None, "", []) for value in item.values()))
    longest_text = max((len(str(item.get("text", ""))) for item in page.items), default=0)
    return filled + min(longest_text / 1000, 3.0)


class ProductionCrawler:
    """Production-grade crawler orchestrator."""

    def __init__(self, config):
        self.config = config
        self.state = CrawlState()
        self.results: List[Dict[str, Any]] = []
        self.exports: Dict[str, Optional[str]] = {}
        self._selectors: Dict[str, str] = dict(config.selectors)
        self._item_selector: Optional[str] = config.item_selector
        self._examples: Dict[str, str] = dict(config.examples)
        self.learned_selectors: Optional[Tuple[str, Dict[str, str]]] = None
        self._queued: Set[str] = set()
        self._start_urls: Set[str] = set()
        self._domain_limits: Dict[str, asyncio.Semaphore] = {}
        self._content_hashes: Set[str] = set()
        self._results_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._allowed_sites = {domain_key(domain) for domain in config.allowed_domains}
        self._include = [re.compile(pattern) for pattern in config.include_patterns]
        self._exclude = [re.compile(pattern) for pattern in config.exclude_patterns]
        self._filter_variants: Dict[str, Set[tuple]] = {}
        self._refusals: Dict[str, int] = {}
        self._parked: List[QueuedUrl] = []
        self._render_sites: Dict[str, bool] = {}   # learned per site: does it need the browser?
        self._render_attempts: Dict[str, int] = {}
        self._probed_sites: Set[str] = set()
        self._slow_sites: Set[str] = set()

        self.politeness = PolitenessManager(config)
        self.retry_manager = RetryManager(config)
        self.fetcher = SmartFetcher(config)
        self.parser = UniversalParser(config.required_fields)
        self.storage = StorageManager(config)
        self.compliance = ComplianceManager(config)
        self.raw_storage = RawHtmlStorage(config.storage.blob_storage_path)
        self.etag_cache = ETagCache() if config.incremental else None
        self.logger = StructuredLogger(
            config.name,
            log_file=config.logging.log_file,
            json_format=config.logging.json_format,
            level=config.logging.level,
            console=config.logging.console,
        )

    @property
    def _automatic(self) -> bool:
        return not self._selectors and not self._item_selector

    async def crawl(self, start_urls: List[str] = None, selectors: Dict[str, str] = None,
                    resume: bool = False) -> List[Dict[str, Any]]:
        """Crawl from start_urls, or continue the saved crawl when resume is True.

        Results are exported and state saved even if the crawl is interrupted.
        """
        if start_urls is None:
            start_urls = self.config.start_urls
        if selectors is not None:
            self._selectors = dict(selectors)

        started = time.time()
        if resume and self.state.load():
            self._queued = {url for url, _ in self.state.frontier}
            for item in self.state.load_items():
                self._add_result(item)
            self.logger.info("crawl_resumed", visited=len(self.state.visited), queued=len(self._queued),
                             items_restored=len(self.results))
        else:
            if resume:
                self.logger.warning("no_checkpoint_found", state_file=str(self.state.state_file))
            self.state.reset_items()
        if self.state.start_time is None:
            self.state.start_time = started

        self.logger.info("crawl_started", start_urls=start_urls, max_depth=self.config.max_depth)
        self._start_urls = {normalize_url(url) for url in start_urls}
        for url in self._start_urls:
            self._enqueue(url, 0)
        self.state.save()  # from here on, a crash at any moment can be resumed

        try:
            if self.config.use_sitemap:
                await self._seed_from_sitemaps(start_urls)
            await self._run_frontier()
        finally:
            self.state.frontier.extend(self._parked)
            self.state.save()
            self.exports = self.storage.export(self.results)
            for fmt, error in self.storage.errors.items():
                self.logger.error("export_failed", format=fmt, error=error)
            self.logger.info("crawl_completed",
                             total_items=len(self.results),
                             pages_visited=len(self.state.visited),
                             duration_seconds=round(time.time() - started, 2),
                             exports=self.exports)
            self.logger.info("compliance_report", **self.compliance.generate_compliance_report())
            await self.close()

        return self.results

    async def close(self):
        """Release network resources."""
        await self.fetcher.close()

    async def _seed_from_sitemaps(self, start_urls: List[str]):
        """Add the pages listed in each site's sitemaps to the frontier."""
        async def fetch_body(url: str) -> Optional[bytes]:
            result = await self.fetch_page(url, raw=True)
            return result["body"] if result else None

        for site in {f"{urlparse(url).scheme}://{urlparse(url).netloc}" for url in start_urls}:
            declared = await self.politeness.robots_checker.sitemaps(site + "/")
            attempts = [declared] if declared else [[site + location] for location in SITEMAP_LOCATIONS]

            urls: List[str] = []
            for sitemaps in attempts:
                urls = await discover_urls(sitemaps, fetch_body, self._accept_link, self.config.max_pages)
                if urls:
                    break

            for url in urls:
                self._enqueue(normalize_url(url), 0)
            self.logger.info("sitemap_discovery", site=site, sitemaps=declared, urls_found=len(urls))

    async def _run_frontier(self):
        """Keep up to max_workers pages in flight until no work is left.

        A finished page can add new links, so the crawl is only over once the
        frontier is empty AND nothing is still running.
        """
        in_flight: Dict[asyncio.Task, QueuedUrl] = {}
        pages_since_checkpoint = 0

        try:
            while True:
                self._schedule(in_flight)
                if not in_flight:
                    return

                done, _ = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    url, _ = in_flight.pop(task)
                    if not task.cancelled() and task.exception() is not None:
                        self.logger.error("worker_crashed", url=url, error=repr(task.exception()))

                pages_since_checkpoint += len(done)
                if pages_since_checkpoint >= CHECKPOINT_EVERY_PAGES:
                    pages_since_checkpoint = 0
                    self.state.save(in_flight.values())
                    self.logger.info("checkpoint_saved", extracted_count=len(self.results))
        finally:
            # Interrupted: put unfinished pages back so a resumed crawl picks them up.
            for task, (url, depth) in in_flight.items():
                task.cancel()
                self.state.visited.discard(url)
                self.state.frontier.appendleft((url, depth))
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)

    def _schedule(self, in_flight: Dict[asyncio.Task, QueuedUrl]):
        """Start tasks from the frontier up to the concurrency and page limits."""
        frontier = self.state.frontier
        visited = self.state.visited

        while (frontier and len(in_flight) < self.config.concurrency.max_workers
               and len(visited) < self.config.max_pages):
            url, depth = frontier.popleft()
            self._queued.discard(url)

            if url in visited or depth > self.config.max_depth or not self._in_scope(url):
                continue
            if self._gave_up_on(url):
                self._parked.append((url, depth))  # kept for a later --resume
                continue

            visited.add(url)  # marked now so no other worker can pick up the same page
            in_flight[asyncio.create_task(self.process_url(url, depth))] = (url, depth)

    def _in_scope(self, url: str) -> bool:
        return not self._allowed_sites or site_key(urlparse(url).netloc) in self._allowed_sites

    def _accept_link(self, url: str) -> bool:
        """Whether a discovered URL is worth visiting."""
        if urlparse(url).scheme not in ("http", "https") or is_asset(url) or not self._in_scope(url):
            return False
        if is_action(url) or is_trap(url):
            return False
        if any(pattern.search(url) for pattern in self._exclude):
            return False
        return not self._include or any(pattern.search(url) for pattern in self._include)

    def _within_filter_budget(self, url: str) -> bool:
        """Faceted search makes endless filter combinations of one page. Only so many are kept."""
        path, filters = facet_key(url)
        if not filters:
            return True
        seen = self._filter_variants.setdefault(path, set())
        if filters not in seen and len(seen) >= MAX_FILTER_VARIANTS:
            return False
        seen.add(filters)
        return True

    def _enqueue(self, url: str, depth: int):
        if url not in self.state.visited and url not in self._queued:
            self._queued.add(url)
            self.state.frontier.append((url, depth))

    def _site_of(self, url: str) -> str:
        return site_key(urlparse(url).netloc)

    def _gave_up_on(self, url: str) -> bool:
        return self._refusals.get(self._site_of(url), 0) >= MAX_CONSECUTIVE_REFUSALS

    def _note_refusal(self, url: str):
        site = self._site_of(url)
        self._refusals[site] = self._refusals.get(site, 0) + 1
        if self._refusals[site] == MAX_CONSECUTIVE_REFUSALS:
            self.logger.warning("site_abandoned", site=site, consecutive_refusals=MAX_CONSECUTIVE_REFUSALS)
            self.compliance.log_scrape_event(url, "site_abandoned",
                                             error=f"Stopped after {MAX_CONSECUTIVE_REFUSALS} refusals in a row")

    async def fetch_page(self, url: str, raw: bool = False, render: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch one URL through the full compliance, politeness and retry pipeline.

        Every outcome other than a usable page is logged and audited here, and
        returns None. raw is for non-page files such as sitemaps: no browser, no
        HTML archive. render goes straight to the browser. A page known to be
        unchanged comes back with 'unchanged' set and, when the archive has it,
        its previous content.
        """
        try:
            self.compliance.validate_url(url)
        except ValueError as e:
            self.logger.warning("url_blocked", url=url, reason=str(e))
            self.compliance.log_scrape_event(url, "blocked", error=str(e))
            return None

        if not await self.politeness.can_fetch(url):
            failure = self.politeness.robots_checker.failure_for(url)
            status = "robots_unreachable" if failure else "robots_disallowed"
            self.logger.warning(status, url=url, reason=failure)
            self.compliance.log_scrape_event(url, status, error=failure)
            return None

        self._mention_slow_pacing(url)
        await self.politeness.wait_for_slot(url)

        conditional = self.etag_cache.get_headers(url) if self.etag_cache and not raw and not render else None
        if raw:
            fetch, options = self.fetcher.static_fetcher.fetch, {"headers": conditional}
        elif render:
            fetch, options = self.fetcher.fetch, {"force_browser": True}
        else:
            fetch, options = self.fetcher.fetch, {"headers": conditional}

        def report_wait(seconds: float):
            self.logger.warning("waiting_to_retry", url=url, seconds=round(seconds, 1))

        start_time = time.time()
        try:
            fetch_result = await self.retry_manager.retry_http(
                fetch, url,
                on_status=lambda status: self.politeness.handle_status(url, status),
                on_wait=report_wait,
                **options,
            )
        except HttpStatusError as e:
            self.logger.error("http_error", url=url, status=e.status, reason=str(e))
            self.compliance.log_scrape_event(url, "http_error", error=str(e))
            if e.status in REFUSAL_STATUSES:
                self._note_refusal(url)
            return None
        except Exception as e:
            if raw or render or not self.fetcher.fallback_enabled:
                raise
            # Plain HTTP could not get a response at all: one browser attempt before giving up.
            try:
                fetch_result = await self.fetcher.fetch(url, force_browser=True)
            except Exception:
                raise e

        status = fetch_result["status_code"]
        log_request(self.logger, url, "GET", status, (time.time() - start_time) * 1000,
                    fetcher_type=fetch_result.get("fetcher_type"))

        if status == 304:
            previous = self.raw_storage.load(str(self.raw_storage.path_for(url)))
            return {**fetch_result, "content": previous or "", "unchanged": True}

        if status >= 400:
            self.compliance.log_scrape_event(url, "http_error", error=f"HTTP {status}")
            if status in REFUSAL_STATUSES:
                self._note_refusal(url)
            return None

        if fetch_result.get("truncated"):
            self.logger.warning("response_truncated", url=url)

        content_type = (fetch_result.get("headers") or {}).get("content-type", "").lower()
        if self.config.storage.save_raw_html and not raw and (not content_type or "html" in content_type):
            self.raw_storage.save(url, fetch_result["content"])

        return fetch_result

    def _mention_slow_pacing(self, url: str):
        domain = urlparse(url).netloc
        interval = self.politeness.rate_limiter.interval_for(domain)
        if interval >= SLOW_PACING_SECONDS and domain not in self._slow_sites:
            self._slow_sites.add(domain)
            self.logger.warning("slow_pacing", site=domain, seconds_between_requests=interval)

    async def process_url(self, url: str, depth: int, selectors: Dict[str, str] = None):
        """Fetch a page, store what it yields and queue its links."""
        domain = urlparse(url).netloc
        limit = self._domain_limits.setdefault(
            domain, asyncio.Semaphore(self.config.concurrency.max_per_domain)
        )

        async with limit:
            try:
                await self._process(url, depth)
            except Exception as e:
                self._note_refusal(url)
                self.logger.error("url_processing_failed", url=url, error=str(e) or repr(e))
                self.compliance.log_scrape_event(url, "failed", error=str(e) or repr(e))

    async def _process(self, url: str, depth: int):
        fetch_result = await self.fetch_page(url, render=self._render_sites.get(self._site_of(url), False))
        if fetch_result is None:
            return

        final_url = normalize_url(fetch_result.get("final_url", url))
        self.state.visited.add(final_url)  # a link to the redirect target is the same page

        if not fetch_result.get("unchanged") and not self._landed_well(url, final_url, fetch_result):
            return
        self._refusals.pop(self._site_of(url), None)

        # Hashing the decoded text matches what the ETag cache stores between runs.
        digest = hashlib.sha256(fetch_result["content"].encode("utf-8", "replace")
                                or fetch_result.get("body") or b"").hexdigest()

        page = None
        new_items = 1
        if fetch_result.get("unchanged") or self._same_as_last_run(url, digest):
            self.compliance.log_scrape_event(url, "unchanged")
        elif digest in self._content_hashes:
            self.compliance.log_scrape_event(url, "duplicate_content")
            return
        else:
            first_view = fetch_result
            fetch_result, page = await self._best_view(url, fetch_result)
            new_items = self._store(url, page)
            if self.etag_cache:
                self.etag_cache.update(url, first_view["headers"], first_view["content"])

        self._content_hashes.add(digest)
        if self._is_html(fetch_result):
            # A next-page link on a page that added nothing leads nowhere worth going.
            self.extract_links(fetch_result["content"], final_url, depth, follow_pagination=new_items > 0)
        if page is not None:
            self._follow_discoveries(url, final_url, depth, fetch_result, page)

    @staticmethod
    def _is_html(fetch_result: Dict[str, Any]) -> bool:
        content_type = (fetch_result.get("headers") or {}).get("content-type", "").lower()
        return not content_type or "html" in content_type

    def _landed_well(self, url: str, final_url: str, fetch_result: Dict[str, Any]) -> bool:
        """Check the response really is the page asked for, not a block page or a redirect away."""
        status = classify_page(fetch_result)
        reason = f"Landed on {final_url}"

        if status is None and not self._in_scope(final_url):
            if url in self._start_urls:
                self._allowed_sites.add(self._site_of(final_url))  # the site simply moved
            else:
                status = "off_site_redirect"

        if status is None:
            try:
                self.compliance.validate_url(final_url)
            except ValueError as e:
                status, reason = "blocked", str(e)

        if status is None:
            return True

        self.logger.warning(status, url=url, final_url=final_url)
        self.compliance.log_scrape_event(url, status, error=reason)
        if status in (BLOCKED, LOGIN, CONSENT):
            self._note_refusal(url)
        return False

    def _same_as_last_run(self, url: str, digest: str) -> bool:
        """For sites with no ETag: compare the body with what the last run saw."""
        return self.etag_cache is not None and self.etag_cache.get_content_hash(url) == digest

    async def _parse(self, fetch_result: Dict[str, Any]) -> ParsedPage:
        # Parsing a large page takes real time, so keep it off the event loop, and bound it.
        return await asyncio.wait_for(
            asyncio.to_thread(self.parser.parse_page, fetch_result, self._selectors,
                              self._item_selector, self.config.include_markdown),
            MAX_PARSE_SECONDS,
        )

    async def _best_view(self, url: str, fetch_result: Dict[str, Any]) -> Tuple[Dict[str, Any], ParsedPage]:
        """Parse a page, and try the browser when the result suggests more is there."""
        self._learn_from_examples(fetch_result)
        page = await self._parse(fetch_result)
        if not self._worth_rendering(url, fetch_result, page):
            return fetch_result, page
        self.logger.info("trying_browser", url=url)

        site = self._site_of(url)
        self._render_attempts[site] = self._render_attempts.get(site, 0) + 1
        try:
            rendered = await self.fetch_page(url, render=True)
        except Exception as e:  # the static copy is still good to use
            self.logger.warning("render_failed", url=url, error=str(e) or repr(e))
            return fetch_result, page

        if rendered is None or classify_page(rendered) is not None:
            return fetch_result, page

        self._learn_from_examples(rendered)
        rendered_page = await self._parse(rendered)
        if page_strength(rendered_page) <= page_strength(page) * RENDER_GAIN:
            return fetch_result, page

        self._render_sites[site] = True  # later pages of this site go straight to the browser
        self.logger.info("site_needs_browser", site=site)
        return rendered, rendered_page

    def _worth_rendering(self, url: str, fetch_result: Dict[str, Any], page: ParsedPage) -> bool:
        """Judge the result, not the page: a shell with a long footer still looks full of text."""
        if not self.fetcher.fallback_enabled or fetch_result.get("fetcher_type") != "static":
            return False

        site = self._site_of(url)
        if self._render_attempts.get(site, 0) >= MAX_RENDER_ATTEMPTS:
            return False
        if not self._is_html(fetch_result):
            return False

        items = [item for item in page.items if any(value not in (None, "", []) for value in item.values())]
        if not items or self._examples:
            return True
        if not self._automatic:
            return False
        if len(items) == 1:
            return len(str(items[0].get("text", ""))) < WEAK_DOCUMENT_TEXT

        # A list with no next-page link may be the first batch of an infinite scroll.
        # Checking costs a browser load, so it happens once per site.
        if site in self._probed_sites:
            return False
        self._probed_sites.add(site)
        return not next_page_links(Selector(text=fetch_result["content"], type="html"))

    def _learn_from_examples(self, fetch_result: Dict[str, Any]):
        """Turn example values into selectors, once, from the first page that has them."""
        if not self._examples:
            return

        learned = learn_selectors(fetch_result["content"], self._examples)
        if learned is None:
            self.logger.warning("examples_not_found", url=fetch_result["url"], examples=self._examples)
            return

        self._item_selector, self._selectors = learned
        self.learned_selectors = learned
        self._examples = {}
        self.logger.info("selectors_learned", item=self._item_selector, fields=self._selectors)

    def _store(self, url: str, page: ParsedPage) -> int:
        """Keep the parsed items that hold real data, and audit the outcome.

        Returns how many new items the page added.
        """
        items = [item for item in page.items if any(value not in (None, "", []) for value in item.values())]
        if not items:
            log_parsing(self.logger, url, 0, len(page.errors))
            self.compliance.log_scrape_event(url, "no_data_extracted", error=page.errors[0] if page.errors else None)
            return 0
        if page.errors:
            self.logger.warning("partial_extraction", url=url, errors=page.errors[:3])

        personal_data = sorted({name for item in items for name in self.compliance.scan_for_personal_data(item)})

        before = len(self.results)
        stored = []
        for item in items:
            data = {"url": url, **item}
            if data["url"] != url:
                data["source_url"] = url
            if self.config.required_fields:
                data = self.compliance.check_data_minimization(data, self.config.required_fields)
            self._add_result(data)
            stored.append(data)

        self.state.append_items(url, stored)
        self.state.extracted_count += len(stored)
        log_parsing(self.logger, url, len(stored), len(page.errors))
        self.compliance.log_scrape_event(url, "success", len(stored), personal_data)
        return len(self.results) - before

    def _add_result(self, data: Dict[str, Any]):
        """Add an item. Found automatically, one seen on a listing and on its own page is merged.

        Rows with no link of their own all share the page's URL. Those are separate records.
        """
        key = (data.get("url"), data.get("title"))
        mergeable = (self._automatic and data.get("title")
                     and (data.get("source_url") or data.get("source") not in RECORD_SOURCES))
        if mergeable and key in self._results_by_key:
            existing = self._results_by_key[key]
            existing.update({name: value for name, value in data.items() if value not in (None, "", [])})
            return

        self.results.append(data)
        if mergeable:
            self._results_by_key[key] = data

    def extract_links(self, content: str, page_url: str, depth: int, follow_pagination: bool = True):
        """Queue a page's links. Next-page links stay at this depth, so they never run out."""
        sel = Selector(text=content, type="html")
        if forbids_following(sel):
            return
        base_url = base_url_of(sel, page_url)

        if self.config.follow_pagination and follow_pagination:
            self._enqueue_links(next_page_links(sel), base_url, depth)

        if depth < self.config.max_depth:
            self._enqueue_links(page_links(sel), base_url, depth + 1)

    def _follow_discoveries(self, url: str, final_url: str, depth: int, fetch_result: Dict[str, Any], page: ParsedPage):
        """Follow what links can't show: API pagination, feed entries, and a site's own feeds."""
        if is_platform_api(url):
            following = next_api_page(url, len(page.items))
            if following:
                self._enqueue(normalize_url(following), depth)

        if self.config.follow_pagination and page.items:
            self._enqueue_links(api_next_links(fetch_result.get("headers") or {}, fetch_result["content"]), final_url, depth)

        if depth < self.config.max_depth:
            entries = [item["url"] for item in page.items if item.get("source") in ("feed", "api") and item.get("url")]
            self._enqueue_links(entries, final_url, depth + 1)

        if self.config.discover and url in self._start_urls:
            content = fetch_result["content"]
            found = feed_links(content, final_url) + platform_endpoints(content, final_url)
            self.logger.info("sources_discovered", url=url, found=found)
            self._enqueue_links(found, final_url, 0)

    def _enqueue_links(self, links: List[str], base_url: str, depth: int):
        for link in links:
            try:
                url = normalize_url(urljoin(base_url, link.strip()))
            except ValueError:  # malformed href
                continue
            if self._accept_link(url) and self._within_filter_budget(url):
                self._enqueue(url, depth)
