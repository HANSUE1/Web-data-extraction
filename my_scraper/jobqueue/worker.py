"""Runs queued crawls. Any number of workers may share a queue.

A crawl that dies half way is resumed from its checkpoint by the next worker. What went wrong
decides what happens next: an outage is tried again later, a site that keeps failing gets a pause,
and a refusal or a bad configuration goes straight to the dead letters, as asking again would not help.
"""
import asyncio
import hashlib
import json
import logging
import os
import socket
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from my_scraper.cli.main import domains_of
from my_scraper.config.settings import CrawlConfig
from my_scraper.core.crawler import CrawlState, NotCleared, ProductionCrawler
from my_scraper.fetcher.actions import ActionError
from my_scraper.jobqueue.store import Job
from my_scraper.ops.alerts import Notifier, queue_alerts
from my_scraper.ops.history import DECLINED, OK, History
from my_scraper.ops.metrics import METRICS
from my_scraper.utils.urls import normalize_url

log = logging.getLogger("worker")

LEASE = 120            # seconds a worker may be silent before its job goes to another
POLL = 2.0             # seconds between looks at an empty queue
RETRY_BASE = 60        # seconds before the first retry, doubled each time
CIRCUIT_REST = 3600    # seconds before a site that kept failing is tried again
REFUSED = {"blocked_by_site", "login_required", "consent_wall", "soft_404", "off_site_redirect"}
BAD_JOB = (NotCleared, ActionError, ValueError, TypeError, KeyError, FileNotFoundError)


class BadJob(Exception):
    """A job that can never run as it is."""


def config_for(job: Job) -> CrawlConfig:
    """The crawl a job asks for. request is what the HTTP API accepts, which cannot lift any safety setting."""
    payload = job.payload
    try:
        if "request" in payload:
            from my_scraper.studio.jobs import config_from_request

            config = config_from_request(payload["request"])
        else:
            raw = payload["config"] if "config" in payload else \
                json.loads(Path(payload["config_file"]).read_text(encoding="utf-8-sig"))
            config = CrawlConfig.from_dict(raw)
    except BAD_JOB as e:
        raise BadJob(f"unusable job: {type(e).__name__}: {e}") from e
    if not config.start_urls:
        raise BadJob("the job has no start_urls")
    if config.open_web:
        config.allowed_domains = []
    elif not config.allowed_domains:
        config.allowed_domains = domains_of(config.start_urls)
    config.job_id = job.id
    config.name = job.id  # its own logger, so jobs never share log handlers
    config.logging.console = False
    return config


class Worker:
    def __init__(self, queue, lease: float = LEASE, poll: float = POLL, name: Optional[str] = None):
        self.queue, self.lease, self.poll = queue, lease, poll
        self.name = name or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:4]}"
        self.stopping = asyncio.Event()

    def stop(self):
        """Finish cleanly: the running crawl saves its state and goes back to the queue."""
        self.stopping.set()

    async def run(self, drain: bool = False):
        """Work until stopped, or with drain until nothing is due."""
        while not self.stopping.is_set():
            try:
                job = await asyncio.to_thread(self.queue.claim, self.name, self.lease)
                if job is not None:
                    log.info("running %s (attempt %s)", job.id, job.attempts)
                    await self._work(job)
                    continue
            except Exception:  # an unreachable queue: the job's lease runs out, and work resumes when it is back
                if drain:
                    raise
                log.exception("the queue could not be used")
            if drain:
                return
            try:
                await asyncio.wait_for(self.stopping.wait(), self.poll)
            except asyncio.TimeoutError:
                pass

    async def _work(self, job: Job):
        crawl = asyncio.create_task(self._execute(job))
        watch = asyncio.create_task(self._keep_lease(job, crawl))
        stop = asyncio.create_task(self.stopping.wait())
        try:
            await asyncio.wait({crawl, stop}, return_when=asyncio.FIRST_COMPLETED)
            if not crawl.done():
                crawl.cancel()
            outcome = await asyncio.gather(crawl, return_exceptions=True)
            if isinstance(outcome[0], Exception):
                raise outcome[0]
            if self.stopping.is_set() and crawl.cancelled():
                await asyncio.to_thread(self.queue.release, job)
        finally:
            watch.cancel()
            stop.cancel()
            await asyncio.gather(watch, stop, return_exceptions=True)
        for state, count in (await asyncio.to_thread(self.queue.counts)).items():
            METRICS.set("scraper_jobs", count, state=state)

    async def _keep_lease(self, job: Job, crawl: asyncio.Task):
        while True:
            await asyncio.sleep(self.lease / 3)
            if not await asyncio.to_thread(self.queue.heartbeat, job, self.lease):
                crawl.cancel()  # cancelled by a person, or another worker has it
                return

    async def _execute(self, job: Job):
        try:
            config = config_for(job)
            if job.payload.get("fan_out"):
                result = await self._fan_out(job, config, int(job.payload["fan_out"]))
                await asyncio.to_thread(self.queue.complete, job, result)
                return
            crawler = ProductionCrawler(config)
        except (BadJob, *BAD_JOB) as e:
            await asyncio.to_thread(self.queue.fail, job, str(e), None)
            return
        except Exception as e:
            await asyncio.to_thread(self.queue.fail, job, f"{type(e).__name__}: {e}", self._backoff(job))
            return

        crawler.politeness.rate_limiter.shared = self.queue.reserve
        resume = job.attempts > 1 and crawler.state.load() and bool(crawler.state.frontier)
        crawler.state = CrawlState(str(crawler.state.state_file))
        try:
            await crawler.crawl(resume=resume)
        except NotCleared as e:
            await asyncio.to_thread(self.queue.fail, job, str(e), None)
        except Exception as e:
            await asyncio.to_thread(self.queue.fail, job, f"{type(e).__name__}: {e}", self._backoff(job),
                                    self._result(crawler))
        else:
            await self._judge(job, crawler)
        await self._raise_alerts(crawler)

    @staticmethod
    def _backoff(job: Job) -> float:
        return RETRY_BASE * 2 ** (job.attempts - 1)

    @staticmethod
    def _result(crawler: ProductionCrawler) -> Dict[str, Any]:
        return {"run_id": crawler.run_id, "pages": crawler.state.page_count, "items": len(crawler.results),
                "statuses": dict(crawler.compliance.status_counts), "quality": crawler.quality,
                "quarantined": crawler.storage.quarantined, "exports": crawler.exports,
                "errors": dict(crawler.storage.errors)}

    async def _judge(self, job: Job, crawler: ProductionCrawler):
        result, statuses = self._result(crawler), crawler.compliance.status_counts
        failed = {status for status in statuses if status not in OK | DECLINED}
        read_any = any(statuses[status] for status in OK)
        waiting = len(crawler.state.frontier)

        if crawler._open_circuits and waiting and job.attempts < job.max_attempts:
            verdict = (f"left {', '.join(sorted(crawler._open_circuits))} alone with {waiting} pages to go", CIRCUIT_REST)
        elif not read_any and failed and failed <= REFUSED:
            verdict = (f"refused: {', '.join(sorted(failed))}", None)
        elif not read_any and failed:
            verdict = (f"nothing could be read: {', '.join(sorted(failed))}", self._backoff(job))
        elif crawler.storage.errors:
            verdict = (f"not saved: {crawler.storage.errors}", self._backoff(job))
        else:
            await asyncio.to_thread(self.queue.complete, job, result)
            for path in (crawler.state.state_file, crawler.state.items_file):
                path.unlink(missing_ok=True)
            return
        await asyncio.to_thread(self.queue.fail, job, verdict[0], verdict[1], result)

    async def _raise_alerts(self, crawler: ProductionCrawler):
        settings = crawler.config.alerts
        if not settings.enabled:
            return
        counts = await asyncio.to_thread(self.queue.counts)
        notifier = Notifier(History(str(crawler.state_dir)),
                            str(Path(crawler.config.logging.audit_log).parent / "alerts.jsonl"), settings.webhook)
        for alert in queue_alerts(counts, settings.max_queue_depth):
            await notifier.send(alert)

    async def _fan_out(self, job: Job, config: CrawlConfig, size: int) -> Dict[str, Any]:
        """Split a site into jobs of `size` pages for other workers, each page going to one of them only."""
        crawler = ProductionCrawler(config)
        crawler._require_clearance(config.start_urls)
        crawler.politeness.rate_limiter.shared = self.queue.reserve
        try:
            await crawler._seed_from_sitemaps(config.start_urls)
            for url in config.start_urls:
                url = normalize_url(url)
                crawler._enqueue(url, 0)
                result = await crawler.fetch_page(url)
                if result is not None:
                    crawler.extract_links(result["content"], normalize_url(result.get("final_url") or url), 0)
        finally:
            await crawler.close()
        pages = [url for url, _ in crawler.state.frontier if crawler._in_scope(url)][:config.max_pages]
        raw = dict(job.payload.get("config") or json.loads(
            Path(job.payload["config_file"]).read_text(encoding="utf-8-sig")))
        children: List[str] = []
        for start in range(0, len(pages), max(size, 1)):
            part_urls = pages[start:start + size]
            # Named by its pages: a parent that crashed half way queues the same parts again, not copies of them.
            name = hashlib.sha256("\n".join(part_urls).encode()).hexdigest()[:16]
            part = {**raw, "start_urls": part_urls, "allowed_domains": config.allowed_domains,
                    "max_depth": 0, "max_pages": size, "follow_pagination": False, "use_sitemap": False}
            children.append(self.queue.enqueue({"config": part, "parent": job.id}, priority=job.priority,
                                               max_attempts=job.max_attempts, dedupe_key=f"{job.id}:{name}"))
        return {"pages": len(pages), "children": children}
