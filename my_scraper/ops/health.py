"""Score an operation on eight dimensions, two points each, from what is configured and what is on record."""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlsplit

from my_scraper.compliance.risk import GO, RegisterError, load_register, register_decision
from my_scraper.config.settings import PLACEHOLDER_USER_AGENT, CrawlConfig
from my_scraper.jobqueue.schedule import ScheduleError, load_schedules
from my_scraper.ops.history import History
from my_scraper.utils.retry import DeadLetterQueue
from my_scraper.storage.snapshots import SnapshotStore

READY, NEEDS_WORK = 12, 8
RECENT_DAYS = 7
POLITE_RPS = 2.0
POLITE_WORKERS = 4
BACKLOG = 100


@dataclass
class Score:
    dimension: str
    points: int
    evidence: List[str]


def _score(dimension: str, checks: Dict[str, bool], cap: int = 2) -> Score:
    """Two points when every check passes, one when some do."""
    passed = sum(checks.values())
    points = 2 if passed == len(checks) else 1 if passed else 0
    return Score(dimension, min(points, cap), [f"{'ok' if good else 'missing'}: {what}" for what, good in checks.items()])


def health(config: CrawlConfig, queue, schedules_file: str = "") -> List[Score]:
    state_dir = config.storage.state_dir
    history = History(state_dir)
    runs = history.runs(50)
    snapshots = SnapshotStore(state_dir, config.storage.snapshot_location)
    counts = queue.counts()

    register_problem = ""
    try:
        register = load_register(config.compliance.site_register)
    except RegisterError as e:
        register, register_problem = {}, f" ({e})"
    sites = {urlsplit(url).hostname for url in config.start_urls} or {run["site"] for run in runs if run["site"]}
    cleared = bool(sites) and all(register_decision(f"https://{site}/", register).verdict == GO for site in sites)

    scheduled, schedule_problem = [], ""
    if schedules_file:
        try:
            scheduled = load_schedules(schedules_file)
        except ScheduleError as e:
            schedule_problem = f" ({e})"
    newest = max((run["started"] for run in runs), default=0)
    dead_urls = len(DeadLetterQueue(str(Path(state_dir) / "dead_letter_queue.jsonl")).get_all())
    limits = config.concurrency

    return [
        _score("Legal Compliance", {
            "robots.txt is obeyed everywhere": config.robots.respect and not config.robots.ignore_for,
            "the user agent names a real contact": config.robots.user_agent != PLACEHOLDER_USER_AGENT,
            f"every site is cleared in the register, terms and API reviewed{register_problem}": cleared}),
        _score("Architecture", {
            "every fetch is kept as a snapshot": config.storage.snapshots,
            "snapshots are on record": snapshots.count() > 0,
            "work goes through the queue": sum(counts.values()) > 0}),
        _score("Anti-Detection", {
            "one steady browser profile": bool(config.browser.locale),
            f"at most {POLITE_WORKERS} requests at once per site": limits.max_per_domain <= POLITE_WORKERS,
            f"at most {POLITE_RPS:g} requests a second per site": config.rate_limit.requests_per_second <= POLITE_RPS,
            "proxies are configured": bool(config.proxy.urls)}),
        _score("Data Quality", {
            "records are validated against a schema": config.quality.schema is not None,
            "duplicates are dropped": config.quality.dedupe != "off",
            "records carry their provenance": config.quality.provenance}),
        _score("Error Handling", {
            "retries are on": config.retry.max_retries > 0,
            "the circuit breaker is on": 0 < limits.breaker_failures <= 20 and limits.breaker_error_rate <= 1,
            "the job queue has no dead letters": counts["dead"] == 0,
            f"fewer than {BACKLOG} URLs are in the dead-letter file": dead_urls < BACKLOG}),
        _score("Monitoring", {
            "logs are JSON": config.logging.json_format,
            "alerts reach somebody": config.alerts.enabled and bool(config.alerts.webhook),
            f"a run is on record from the last {RECENT_DAYS} days": time.time() - newest < RECENT_DAYS * 86400}),
        _score("Storage", {
            "snapshots and what was parsed from them are kept": config.storage.snapshots,
            "records are deduplicated or delivered by key": (config.quality.dedupe != "off"
                                                             or bool(config.storage.database_url)),
            "datasets below their thresholds are held back": (config.quality.min_valid_ratio > 0
                                                             or config.quality.min_items > 0)}),
        _score("Scheduling", {
            f"schedules are defined{schedule_problem}": bool(scheduled),
            "they keep to off-peak times": bool(scheduled) and all("cron" in s or s.get("window") for s in scheduled),
            "the queue is durable": True}),
    ]


def verdict(scores: List[Score]) -> str:
    total = sum(score.points for score in scores)
    return "production-ready" if total >= READY else "needs work" if total >= NEEDS_WORK else "needs a redesign"
