"""Operate the scraper: queue, workers, schedules, dead letters, replay, health and release checks.

    python -m my_scraper.ops assess https://example.com/       go or no-go for a site
    python -m my_scraper.ops enqueue --config config/books.json
    python -m my_scraper.ops worker --schedules config/schedules.json --port 9100
    python -m my_scraper.ops dlq list | retry
    python -m my_scraper.ops replay --host books.toscrape.com [--rev HEAD~1] [--strict]
    python -m my_scraper.ops health | gate | prune | search | rank | recrawl | proxies | serve
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

from aiohttp import web

from my_scraper.compliance.risk import GO, RegisterError, assess
from my_scraper.config.settings import CrawlConfig
from my_scraper.core.replay import ReplayError, replay
from my_scraper.fetcher.proxies import ProxyPool
from my_scraper.jobqueue.schedule import ScheduleError, load_schedules, tick
from my_scraper.jobqueue.store import STATES, open_queue
from my_scraper.ops import dashboard
from my_scraper.ops.gate import gate
from my_scraper.ops.health import health, verdict
from my_scraper.search.index import SearchIndex
from my_scraper.search.recrawl import recrawl_plan
from my_scraper.storage.snapshots import SnapshotStore
from my_scraper.utils.retry import DeadLetterQueue

TICK_SECONDS = 20
FINISHED_JOBS_KEPT_DAYS = 30
LOCAL = ("127.0.0.1", "localhost", "::1")


def _show(data: Any):
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _config(args) -> CrawlConfig:
    if not getattr(args, "config", None):
        return CrawlConfig()
    with open(args.config, encoding="utf-8-sig") as f:
        return CrawlConfig.from_dict(json.load(f))


def _store(config: CrawlConfig) -> SnapshotStore:
    return SnapshotStore(config.storage.state_dir, config.storage.snapshot_location)


def _dlq(config: CrawlConfig) -> DeadLetterQueue:
    return DeadLetterQueue(str(Path(config.storage.state_dir) / "dead_letter_queue.jsonl"))


def cmd_assess(args) -> int:
    config = _config(args)
    config.logging.console = False
    config.start_urls = [args.url]
    if args.register:
        config.compliance.site_register = args.register
    decision = asyncio.run(assess(config, args.url))
    _show({"site": decision.host, "verdict": decision.verdict, "reasons": decision.reasons, "facts": decision.facts})
    return 0 if decision.verdict == GO else 1


def cmd_enqueue(args) -> int:
    payload = {"fan_out": args.fan_out} if args.fan_out else {}
    if args.config:
        CrawlConfig.from_dict(json.loads(Path(args.config).read_text(encoding="utf-8-sig")))  # refuse a bad one now
        payload["config_file"] = str(Path(args.config).resolve())
    elif args.urls:
        payload["config"] = {"start_urls": args.urls, "max_pages": args.max_pages}
    else:
        print("give --config or --urls", file=sys.stderr)
        return 2
    key = args.key or json.dumps(payload, sort_keys=True)
    print(open_queue(args.queue).enqueue(payload, priority=args.priority, max_attempts=args.max_attempts, dedupe_key=key))
    return 0


def cmd_jobs(args) -> int:
    queue = open_queue(args.queue)
    if args.cancel:
        return 0 if queue.cancel(args.cancel) else 1
    if args.retry:
        return 0 if queue.retry(args.retry) else 1
    print(queue.counts())
    for job in queue.list(args.state, args.limit):
        print(f"{job.id}  {job.state:9}  attempt {job.attempts}/{job.max_attempts}  {job.error or ''}")
    return 0


async def _schedule_forever(queue, path: str, stopping: asyncio.Event):
    log = logging.getLogger("scheduler")
    warned = False
    while not stopping.is_set():
        try:
            missing = not Path(path).exists()
            if missing and not warned:
                log.warning("no schedules file at %s yet; copy config/schedules.example.json there to start", path)
            warned = missing
            # Read again each time, so editing or creating the file needs no restart.
            for name in await asyncio.to_thread(tick, queue, load_schedules(path, missing_ok=True)):
                log.info("queued %s", name)
        except Exception:  # a broken file or an unreachable queue must not end the scheduler
            log.exception("scheduling failed")
        try:
            await asyncio.wait_for(stopping.wait(), TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _work(args):
    from my_scraper.jobqueue.worker import Worker  # brings in the whole crawler

    queue = open_queue(args.queue)
    workers = [Worker(queue) for _ in range(args.concurrency)]
    stopping = asyncio.Event()

    def stop(*_):
        stopping.set()
        for worker in workers:
            worker.stop()

    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop)
        except NotImplementedError:  # Windows
            signal.signal(name, lambda *_: loop.call_soon_threadsafe(stop))

    tasks = [asyncio.create_task(worker.run(drain=args.drain)) for worker in workers]
    if args.schedules:
        load_schedules(args.schedules, missing_ok=True)  # a mistake should stop the start, not be logged every 20 seconds
        tasks.append(asyncio.create_task(_schedule_forever(queue, args.schedules, stopping)))
    runner = None
    if args.port:
        app = dashboard.create_app(lambda: queue, api_key=os.environ.get("SCRAPER_API_KEY"))
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
    try:
        await asyncio.gather(*tasks)
    finally:
        if runner:
            await runner.cleanup()


def _refuse_open_host(args) -> bool:
    if args.host in LOCAL or os.environ.get("SCRAPER_API_KEY"):
        return False
    print("listening beyond this computer needs SCRAPER_API_KEY", file=sys.stderr)
    return True


def cmd_worker(args) -> int:
    if args.port and _refuse_open_host(args):
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(_work(args))
    return 0


def cmd_scheduler(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_schedules(args.schedules, missing_ok=True)
    if args.once:
        print(tick(open_queue(args.queue), load_schedules(args.schedules, missing_ok=True)))
        return 0
    asyncio.run(_schedule_forever(open_queue(args.queue), args.schedules, asyncio.Event()))
    return 0


def cmd_serve(args) -> int:
    if _refuse_open_host(args):
        return 2
    queue = open_queue(args.queue)
    app = dashboard.create_app(lambda: queue, api_key=os.environ.get("SCRAPER_API_KEY"))
    web.run_app(app, host=args.host, port=args.port)
    return 0


def cmd_dlq(args) -> int:
    """Failed URLs. Those that were fetched but not parsed are parsed again from their snapshot, not fetched."""
    config = _config(args)
    dlq = _dlq(config)
    if args.action == "list":
        _show(dlq.get_all())
        return 0
    queue, store = open_queue(args.queue), _store(config)  # before taking entries, so a bad address loses none
    entries = dlq.take_all()
    if args.action == "clear":
        print(f"{len(entries)} entries set aside")
        return 0
    fetch = sorted({entry["url"] for entry in entries if entry.get("stage", "fetch") == "fetch"})
    parse = {entry["snapshot_id"] for entry in entries if entry.get("stage") == "parse" and entry.get("snapshot_id")}
    if fetch:
        raw = json.loads(Path(args.config).read_text(encoding="utf-8-sig")) if args.config else {}
        job = queue.enqueue({"config": {**raw, "start_urls": fetch, "allowed_domains": [], "max_depth": 0,
                                                         "max_pages": len(fetch), "follow_pagination": False}})
        print(f"{len(fetch)} URLs queued as {job}")
    if parse:
        report, _ = replay(store, [row for snapshot_id in parse for row in store.find(snapshot_id=snapshot_id)], save=True)
        _show(report.to_dict())
    return 0


def cmd_replay(args) -> int:
    config = _config(args)
    store = _store(config)
    since = time.time() - args.days * 86400 if args.days else None
    snapshots = store.find(url=args.url, host=args.host, run_id=args.run, since=since, latest=args.latest, limit=args.limit)
    try:
        report, records = replay(store, snapshots, args.rev, args.save)
    except ReplayError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    _show(report.to_dict())
    if args.export:
        Path(args.export).write_text("".join(json.dumps(record, ensure_ascii=False, default=str) + "\n"
                                             for record in records), encoding="utf-8")
        print(f"{len(records)} records written to {args.export}")
    return 1 if args.strict and (report.changed or report.errors) else 0


def cmd_health(args) -> int:
    scores = health(_config(args), open_queue(args.queue), args.schedules or "")
    for score in scores:
        print(f"{score.points}/2  {score.dimension}")
        for line in score.evidence:
            print(f"       {line}")
    total = sum(score.points for score in scores)
    print(f"{total}/16  {verdict(scores)}")
    return 0 if total >= args.min_score else 1


def cmd_gate(args) -> int:
    checks = gate(_config(args) if args.config else None, args.quick)
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'}  {check.name}: {check.detail}")
    return 0 if all(check.passed for check in checks) else 1


def cmd_prune(args) -> int:
    config = _config(args)
    days = args.days or config.storage.retention_days
    if not days:
        print("nothing pruned: set storage.retention_days or pass --days", file=sys.stderr)
        return 2
    print(f"{_store(config).prune(days)} snapshots deleted, "
          f"{open_queue(args.queue).prune(FINISHED_JOBS_KEPT_DAYS)} finished jobs forgotten")
    return 0


def _index(config: CrawlConfig) -> SearchIndex:
    return SearchIndex(str(Path(config.storage.state_dir) / "search.db"))


def cmd_search(args) -> int:
    config = _config(args)
    index = _index(config)
    index.build(_store(config))
    _show(index.search(args.query, args.limit))
    return 0


def cmd_rank(args) -> int:
    config = _config(args)
    index = _index(config)
    index.build(_store(config))
    _show(index.top(args.by, args.limit))
    return 0


def cmd_recrawl(args) -> int:
    _show(recrawl_plan(_store(_config(args)), args.limit))
    return 0


def cmd_proxies(args) -> int:
    config = _config(args)
    if not config.proxy.urls:
        print("no proxies configured")
        return 0
    results = asyncio.run(ProxyPool(config.proxy.urls).check(args.url or config.proxy.check_url or "https://example.com/"))
    _show(results)
    return 0 if all(results.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="my_scraper.ops", description="Operate the scraper",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, function, text: str, config: bool = True, queue: bool = False) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=text)
        command.set_defaults(function=function)
        if config:
            command.add_argument("--config", help="Crawl config (JSON)")
        if queue:
            command.add_argument("--queue", help="Queue: a file, or redis://... (default: $SCRAPER_QUEUE_URL)")
        return command

    command = add("assess", cmd_assess, "Go or no-go for a site, by the rules in compliance/risk.py")
    command.add_argument("url")
    command.add_argument("--register", help="Site register (JSON)")

    command = add("enqueue", cmd_enqueue, "Put a crawl on the queue", queue=True)
    command.add_argument("--urls", nargs="+")
    command.add_argument("--max-pages", type=int, default=100)
    command.add_argument("--priority", type=int, default=0, help="Higher goes first")
    command.add_argument("--max-attempts", type=int, default=3)
    command.add_argument("--fan-out", type=int, metavar="PAGES", help="Split the site into jobs of this many pages")
    command.add_argument("--key", help="Only one job with this key waits or runs at a time (default: the config)")

    command = add("jobs", cmd_jobs, "List, cancel or retry jobs", config=False, queue=True)
    command.add_argument("--state", choices=STATES)
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--cancel", metavar="JOB")
    command.add_argument("--retry", metavar="JOB")

    command = add("worker", cmd_worker, "Run queued crawls", config=False, queue=True)
    command.add_argument("--concurrency", type=int, default=1, help="Crawls at once in this process")
    command.add_argument("--schedules", help="Also queue what is due in this schedules file")
    command.add_argument("--drain", action="store_true", help="Stop when nothing is due")
    command.add_argument("--port", type=int, help="Also serve /dashboard, /metrics and /health")
    command.add_argument("--host", default="127.0.0.1")

    command = add("scheduler", cmd_scheduler, "Queue recurring crawls when they are due", config=False, queue=True)
    command.add_argument("--schedules", required=True)
    command.add_argument("--once", action="store_true", help="Look once and stop, for an outside cron")

    command = add("serve", cmd_serve, "Serve the dashboard and metrics", config=False, queue=True)
    command.add_argument("--port", type=int, default=9100)
    command.add_argument("--host", default="127.0.0.1")

    command = add("dlq", cmd_dlq, "URLs that used up their retries", queue=True)
    command.add_argument("action", choices=("list", "retry", "clear"))

    command = add("replay", cmd_replay, "Parse stored snapshots again and compare")
    command.add_argument("--url")
    command.add_argument("--host")
    command.add_argument("--run", help="Snapshots of one run")
    command.add_argument("--days", type=float, help="Snapshots from the last N days")
    command.add_argument("--latest", action="store_true", help="Only the newest snapshot of each URL")
    command.add_argument("--limit", type=int, default=1000)
    command.add_argument("--rev", help="Use the parser of this git revision")
    command.add_argument("--save", action="store_true", help="Record the new extraction beside the old one")
    command.add_argument("--export", metavar="FILE", help="Write the records as JSONL")
    command.add_argument("--strict", action="store_true", help="Exit 1 when anything came out differently")

    command = add("health", cmd_health, "Score the operation on eight dimensions", queue=True)
    command.add_argument("--schedules")
    command.add_argument("--min-score", type=int, default=0, help="Exit 1 below this")

    command = add("gate", cmd_gate, "Checks to pass before a release")
    command.add_argument("--quick", action="store_true", help="Leave out the test suites")

    command = add("prune", cmd_prune, "Delete snapshots past their retention", queue=True)
    command.add_argument("--days", type=float)

    command = add("search", cmd_search, "Search the crawled pages")
    command.add_argument("query")
    command.add_argument("--limit", type=int, default=10)

    command = add("rank", cmd_rank, "Pages by PageRank or HITS")
    command.add_argument("--by", choices=("pagerank", "hub", "authority"), default="pagerank")
    command.add_argument("--limit", type=int, default=20)

    command = add("recrawl", cmd_recrawl, "Pages most overdue for another read")
    command.add_argument("--limit", type=int, default=20)

    command = add("proxies", cmd_proxies, "Try every proxy")
    command.add_argument("--url")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.function(args)
    except (OSError, ValueError, TypeError, RegisterError, ScheduleError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
