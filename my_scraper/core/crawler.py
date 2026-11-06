"""Production-grade crawler orchestrator."""
import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from parsel import Selector

from my_scraper.compliance.manager import ComplianceManager
from my_scraper.fetcher.smart_fetcher import RawHtmlStorage, SmartFetcher
from my_scraper.logutil import StructuredLogger, log_parsing, log_request
from my_scraper.parser.universal_parser import ParsedItem, UniversalParser
from my_scraper.storage.multi_format import StorageManager
from my_scraper.utils.politeness import PolitenessManager
from my_scraper.utils.retry import HttpStatusError, RetryManager

CHECKPOINT_EVERY_PAGES = 100

QueuedUrl = Tuple[str, int]  # (url, depth)


class CrawlState:
    """Persist crawl state for checkpointing."""

    def __init__(self, state_file: str = "./data/crawl_state.json"):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
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


class ProductionCrawler:
    """Production-grade crawler orchestrator."""

    def __init__(self, config):
        self.config = config
        self.state = CrawlState()
        self.results: List[Dict[str, Any]] = []
        self.exports: Dict[str, Optional[str]] = {}
        self._queued: Set[str] = set()
        self._domain_limits: Dict[str, asyncio.Semaphore] = {}

        self.politeness = PolitenessManager(config)
        self.retry_manager = RetryManager(config)
        self.fetcher = SmartFetcher(config)
        self.parser = UniversalParser(config.required_fields)
        self.storage = StorageManager(config)
        self.compliance = ComplianceManager(config)
        self.raw_storage = RawHtmlStorage(config.storage.blob_storage_path)
        self.logger = StructuredLogger(
            config.name,
            log_file=config.logging.log_file,
            json_format=config.logging.json_format,
            level=config.logging.level,
        )

    async def crawl(self, start_urls: List[str] = None, selectors: Dict[str, str] = None,
                    resume: bool = False) -> List[Dict[str, Any]]:
        """Crawl from start_urls, or continue the saved crawl when resume is True.

        Results are exported and state saved even if the crawl is interrupted.
        """
        if start_urls is None:
            start_urls = self.config.start_urls
        if selectors is None:
            selectors = self.config.selectors

        started = time.time()
        if resume and self.state.load():
            self._queued = {url for url, _ in self.state.frontier}
            self.logger.info("crawl_resumed", visited=len(self.state.visited), queued=len(self._queued))
        if self.state.start_time is None:
            self.state.start_time = started

        self.logger.info("crawl_started", start_urls=start_urls, max_depth=self.config.max_depth)
        for url in start_urls:
            self._enqueue(url, 0)

        try:
            await self._run_frontier(selectors)
        finally:
            self.state.save()
            self.exports = self.storage.export(self.results)
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

    async def _run_frontier(self, selectors: Dict[str, str]):
        """Keep up to max_workers pages in flight until no work is left.

        A finished page can add new links, so the crawl is only over once the
        frontier is empty AND nothing is still running.
        """
        in_flight: Dict[asyncio.Task, QueuedUrl] = {}
        pages_since_checkpoint = 0

        try:
            while True:
                self._schedule(in_flight, selectors)
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

    def _schedule(self, in_flight: Dict[asyncio.Task, QueuedUrl], selectors: Dict[str, str]):
        """Start tasks from the frontier up to the concurrency and page limits."""
        frontier = self.state.frontier
        visited = self.state.visited

        while (frontier and len(in_flight) < self.config.concurrency.max_workers
               and len(visited) < self.config.max_pages):
            url, depth = frontier.popleft()
            self._queued.discard(url)

            if url in visited or depth > self.config.max_depth or not self._in_scope(url):
                continue

            visited.add(url)  # marked now so no other worker can pick up the same page
            in_flight[asyncio.create_task(self.process_url(url, depth, selectors))] = (url, depth)

    def _in_scope(self, url: str) -> bool:
        allowed = self.config.allowed_domains
        return not allowed or urlparse(url).netloc in allowed

    def _enqueue(self, url: str, depth: int):
        if url not in self.state.visited and url not in self._queued:
            self._queued.add(url)
            self.state.frontier.append((url, depth))

    async def fetch_page(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch one URL through the full compliance, politeness and retry pipeline.

        Every outcome other than a usable page is logged and audited here, and
        returns None.
        """
        try:
            self.compliance.validate_url(url)
        except ValueError as e:
            self.logger.warning("url_blocked", url=url, reason=str(e))
            self.compliance.log_scrape_event(url, "blocked", error=str(e))
            return None

        if not await self.politeness.can_fetch(url):
            self.logger.warning("robots_disallowed", url=url)
            self.compliance.log_scrape_event(url, "robots_disallowed")
            return None

        await self.politeness.wait_for_slot(url)

        start_time = time.time()
        try:
            fetch_result = await self.retry_manager.retry_http(
                self.fetcher.fetch, url,
                on_status=lambda status: self.politeness.handle_status(url, status),
            )
        except HttpStatusError as e:
            self.logger.error("http_error", url=url, status=e.status)
            self.compliance.log_scrape_event(url, "http_error", error=str(e))
            return None

        status = fetch_result["status_code"]
        log_request(self.logger, url, "GET", status, (time.time() - start_time) * 1000,
                    fetcher_type=fetch_result.get("fetcher_type"))

        if status >= 400:
            self.compliance.log_scrape_event(url, "http_error", error=f"HTTP {status}")
            return None

        if self.config.storage.save_raw_html:
            self.raw_storage.save(url, fetch_result["content"])

        return fetch_result

    async def process_url(self, url: str, depth: int, selectors: Dict[str, str] = None):
        """Fetch a page, store what it yields and queue its links."""
        domain = urlparse(url).netloc
        limit = self._domain_limits.setdefault(
            domain, asyncio.Semaphore(self.config.concurrency.max_per_domain)
        )

        async with limit:
            try:
                fetch_result = await self.fetch_page(url)
                if fetch_result is None:
                    return

                self._store(url, self.parser.parse(fetch_result, selectors))

                if depth < self.config.max_depth and selectors:
                    self.extract_links(fetch_result["content"], fetch_result.get("final_url", url), depth)

            except Exception as e:
                self.logger.error("url_processing_failed", url=url, error=str(e))
                self.compliance.log_scrape_event(url, "failed", error=str(e))

    def _store(self, url: str, parsed: ParsedItem):
        """Keep a parsed item if it holds real data, and audit the outcome."""
        has_data = set(parsed.data) != {"html"} and any(value not in (None, "") for value in parsed.data.values())
        if not has_data:
            log_parsing(self.logger, url, 0, len(parsed.errors))
            self.compliance.log_scrape_event(url, "no_data_extracted")
            return

        data = {**parsed.data, "url": url}
        personal_data = self.compliance.scan_for_personal_data(parsed.data)
        if self.config.required_fields:
            data = self.compliance.check_data_minimization(data, self.config.required_fields)

        self.results.append(data)
        self.state.extracted_count += 1
        log_parsing(self.logger, url, 1, len(parsed.errors))
        self.compliance.log_scrape_event(url, "success", 1, personal_data)

    def extract_links(self, content: str, base_url: str, depth: int):
        """Extract and enqueue links."""
        for link in Selector(text=content).css("a::attr(href)").getall():
            try:
                full_url = urldefrag(urljoin(base_url, link)).url
            except ValueError:  # malformed href
                continue

            if urlparse(full_url).scheme in ("http", "https") and self._in_scope(full_url):
                self._enqueue(full_url, depth + 1)
