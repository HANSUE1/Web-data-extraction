"""Extraction jobs: crawls started from the app, run one at a time, with a live event feed.

One at a time on purpose: each crawler paces its own requests, so two crawls of one
site at once would visit it twice as fast as the rate limit says.
"""
import argparse
import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from my_scraper.cli.main import build_config, build_parser
from my_scraper.config.settings import CrawlConfig
from my_scraper.core.crawler import ProductionCrawler
from my_scraper.studio import sketch
from my_scraper.utils.urls import normalize_url

MAX_EVENTS = 4000
MAX_JOBS_KEPT = 50

# Request fields -> CLI options. Robots exemptions and logins are not here on purpose:
# they are for whoever runs the scraper, from the command line.
NUMBERS = {"max_pages": "--max-pages", "max_depth": "--max-depth", "rate_limit": "--rate-limit",
           "max_pages_per_site": "--max-pages-per-site"}
FLAGS = {"sitemap": "--sitemap", "discover": "--discover", "markdown": "--markdown",
         "incremental": "--incremental", "track": "--track", "open_web": "--open-web",
         "no_pagination": "--no-pagination"}
LISTS = {"include": "--include", "exclude": "--exclude"}
PAIRS = {"selectors": "--selector", "examples": "--example"}
MODES = {"auto": [], "browser": ["--use-browser"], "static": ["--no-browser"]}
ACCEPTED = {"urls", "mode", "export", "item", *NUMBERS, *FLAGS, *LISTS, *PAIRS}


class InvalidJob(ValueError):
    pass


def config_from_request(request: Dict[str, Any]) -> CrawlConfig:
    """Build the crawl config the same way the CLI does, so both validate alike."""
    if not isinstance(request, dict):
        raise InvalidJob("The body must be a JSON object.")
    unknown = sorted(set(request) - ACCEPTED)
    if unknown:
        raise InvalidJob(f"Unknown fields {unknown}.")

    urls = request.get("urls")
    if isinstance(urls, str):
        urls = urls.split()
    if not urls or not all(isinstance(url, str) and url.strip() for url in urls):
        raise InvalidJob("Give at least one start URL.")
    argv = ["--urls", *[url.strip() for url in urls]]

    for field, option in NUMBERS.items():
        value = request.get(field)
        if value not in (None, ""):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidJob(f"{field} must be a number.")
            argv += [option, str(value)]
    for field, option in FLAGS.items():
        if request.get(field):
            argv.append(option)
    for field, option in LISTS.items():
        for pattern in _strings(request.get(field), field):
            argv += [option, pattern]
    for field, option in PAIRS.items():
        pairs = request.get(field) or {}
        if not isinstance(pairs, dict):
            raise InvalidJob(f"{field} must map names to values.")
        for name, value in pairs.items():
            if str(name).strip() and str(value).strip():
                argv += [option, f"{str(name).strip()}={str(value).strip()}"]
    if request.get("item"):
        argv += ["--item", str(request["item"])]

    mode = request.get("mode") or "auto"
    if mode not in MODES:
        raise InvalidJob(f"mode must be one of {sorted(MODES)}.")
    argv += MODES[mode]
    export = _strings(request.get("export"), "export") or ["jsonl", "csv"]
    argv += ["--export", *export]

    parser = build_parser()

    def refuse(message: str):
        raise InvalidJob(message)

    parser.error = refuse  # argparse would otherwise exit the whole app
    try:
        args = parser.parse_args(argv + ["--quiet"])
        return build_config(args, parser)
    except argparse.ArgumentError as e:
        raise InvalidJob(str(e))


def _strings(value: Any, field: str) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [line for line in value.splitlines()]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidJob(f"{field} must be a list of text.")
    return [item.strip() for item in value if item.strip()]


class _Feed(logging.Handler):
    """Copies a job's log events into the job, for the live view."""

    def __init__(self, job: "Job"):
        super().__init__()
        self.job = job

    def emit(self, record: logging.LogRecord):
        entry = getattr(record, "structured", None) or {"level": record.levelname, "message": record.getMessage()}
        self.job.add_event(entry)


class Job:
    def __init__(self, request: Dict[str, Any], config: CrawlConfig):
        self.id = uuid.uuid4().hex[:10]
        self.request = request
        self.config = config
        config.name = f"studio_{self.id}"  # its own logger, so jobs never share log handlers
        self.status = "queued"            # queued, running, done, failed, cancelled
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.error: Optional[str] = None
        self.crawler: Optional[ProductionCrawler] = None
        self.task: Optional[asyncio.Task] = None
        self.events: Deque[Tuple[int, Dict[str, Any]]] = deque(maxlen=MAX_EVENTS)
        self._seq = 0
        self.summary: Dict[str, Any] = {}

    def add_event(self, entry: Dict[str, Any]):
        self._seq += 1
        self.events.append((self._seq, entry))

    def snapshot(self, since: Optional[int] = None) -> Dict[str, Any]:
        crawler = self.crawler
        progress = {"pages": 0, "items": 0, "queued": 0, "statuses": {}}
        if crawler:
            progress = {"pages": len(crawler.state.visited), "items": len(crawler.results),
                        "queued": len(crawler.state.frontier),
                        "statuses": dict(crawler.compliance.status_counts)}
        data = {
            "id": self.id, "status": self.status, "urls": self.config.start_urls,
            "request": self.request, "created": self.created, "started": self.started,
            "finished": self.finished, "error": self.error, "progress": progress,
            "max_pages": self.config.max_pages, "summary": self.summary, "last_event": self._seq,
        }
        if since is not None:
            data["events"] = [{"seq": seq, **entry} for seq, entry in self.events if seq > since]
        return data


def _sketch(job: Job, url: str, fetch_result: Dict[str, Any], page):
    """Send the live view a light copy of the page, marking items and the links the crawl will visit next."""
    crawler = job.crawler

    def follows(href: str) -> bool:
        try:
            key = normalize_url(href)
        except ValueError:
            return False
        return key in crawler._queued and key not in crawler.state.visited  # queued, still to come

    try:
        drawn = sketch.build(url, fetch_result, page.items, follows)
    except Exception as e:  # drawing the page must never stop the crawl
        job.add_event({"level": "DEBUG", "message": "sketch_failed", "url": url, "error": str(e)})
        return
    job.add_event({"level": "INFO", "message": "page_sketch", "url": url, "sketch": drawn})


class JobQueue:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self._queue: "asyncio.Queue[Job]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None

    def start(self):
        self._worker = asyncio.create_task(self._work())

    async def stop(self):
        for job in self.jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    def submit(self, request: Dict[str, Any]) -> Job:
        job = Job(request, config_from_request(request))
        self.jobs[job.id] = job
        self._forget_old()
        self._queue.put_nowait(job)
        return job

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.jobs.get(job_id)
        if not job:
            return None
        if job.status == "queued":
            job.status, job.finished = "cancelled", time.time()
        elif job.status == "running" and job.task:
            job.task.cancel()  # the crawler still exports what it found so far
        return job

    def _forget_old(self):
        finished = [job for job in self.jobs.values() if job.finished]
        for job in sorted(finished, key=lambda job: job.created)[:max(0, len(self.jobs) - MAX_JOBS_KEPT)]:
            del self.jobs[job.id]

    async def _work(self):
        while True:
            job = await self._queue.get()
            if job.status == "queued":
                await self._run(job)

    async def _run(self, job: Job):
        job.status, job.started = "running", time.time()
        logger = logging.getLogger(job.config.name)
        try:
            job.crawler = ProductionCrawler(job.config)
            job.crawler.on_page = lambda url, fetch_result, page: _sketch(job, url, fetch_result, page)
            logger.addHandler(_Feed(job))  # after the crawler set up its logger, which clears handlers
            job.task = asyncio.create_task(job.crawler.crawl())
            await job.task
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            if asyncio.current_task().cancelling():  # the app is shutting down, not just this job
                raise
        except Exception as e:  # one bad job must not stop the queue
            job.status, job.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()
            crawler = job.crawler
            if crawler:
                job.summary = {
                    "exports": crawler.exports,
                    "run_dir": crawler.storage.last_run_dir,
                    "first_errors": dict(crawler.compliance.first_errors),
                    "learned": crawler.learned_selectors,
                    "export_errors": dict(crawler.storage.errors),
                }
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            logging.Logger.manager.loggerDict.pop(job.config.name, None)
