"""Tests for modules the crawler does not use yet."""
import asyncio
import hashlib
import hmac
import json
import sqlite3
import warnings

import pytest
from aiohttp import web

from my_scraper.config.settings import CrawlConfig
from my_scraper.models.schemas import CrawlJob, ScrapedItem
from my_scraper.queueing.persistent_queue import (
    ContentDeduplicator,
    DiskQueue,
    ETagCache,
    HybridQueue,
    QueueItem,
    QueueManager,
    RedisQueue,
)
from my_scraper.session.manager import BrowserSession
from my_scraper.utils.rotation import Proxy, ProxyManager


# --- Queues ------------------------------------------------------------------

def test_disk_queue_orders_by_priority_and_rejects_duplicates():
    queue = DiskQueue("q.db")
    assert queue.push(QueueItem("http://low", priority=1)) is True
    assert queue.push(QueueItem("http://high", priority=9)) is True
    assert queue.push(QueueItem("http://low")) is False

    assert queue.pop().url == "http://high"
    assert queue.pop().url == "http://low"
    assert queue.pop() is None


def test_disk_queue_requeues_until_retries_run_out():
    queue = DiskQueue("q.db")
    queue.push(QueueItem("http://x"))
    queue.pop()

    queue.mark_failed("http://x", retries=1, max_retries=2)
    assert queue.size("pending") == 1
    queue.pop()
    queue.mark_failed("http://x", retries=2, max_retries=2)
    assert queue.size("failed") == 1


def test_disk_queue_closes_its_connections():
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        queue = DiskQueue("q.db")
        queue.push(QueueItem("http://x"))
        queue.pop()


def test_hybrid_queue_pop_respects_priority_and_status():
    queue = HybridQueue({"queue_db": "q.db"})
    queue.push(QueueItem("http://low", priority=1))
    queue.push(QueueItem("http://high", priority=9))

    assert queue.pop().url == "http://high"
    assert queue.disk_queue.size("processing") == 1


def test_queue_manager_uses_the_max_retries_it_is_given():
    manager = QueueManager({"queue_db": "q.db", "etag_db": "e.db", "dedup_db": "d.db", "max_retries": 99})
    manager.push(QueueItem("http://x"))
    manager.pop()

    manager.mark_failed("http://x", 1, 1)

    assert manager.queue.size("failed") == 1


class FakeRedis:
    def __init__(self):
        self.sets = {}
        self.zset = {}

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def zcard(self, key):
        return len(self.zset)

    async def zadd(self, key, mapping):
        self.zset.update(mapping)
        return 1

    async def eval(self, script, numkeys, key):
        if not self.zset:
            return None
        member = min(self.zset, key=self.zset.get)
        del self.zset[member]
        return member


def test_redis_queue_operations():
    async def scenario():
        queue = RedisQueue()
        queue._redis = FakeRedis()
        await queue.push(QueueItem("http://old-low", priority=1, added_at=1.0))
        await queue.push(QueueItem("http://new-high", priority=5, added_at=2_000_000_000.0))
        size = await queue.size()
        first = await queue.pop()
        await queue.mark_done("http://new-high")
        await queue.mark_failed("http://gone", retries=3, max_retries=3)
        return size, first, queue._redis.sets

    size, first, sets = asyncio.run(scenario())

    assert size == 2
    assert first.url == "http://new-high"
    assert first.priority == 5
    assert sets == {"crawler:done": {"http://new-high"}, "crawler:failed": {"http://gone"}}


def test_content_deduplicator_remembers_first_url():
    dedup = ContentDeduplicator("d.db")

    assert dedup.is_duplicate("body", "http://first") is False
    assert dedup.is_duplicate("body", "http://second") is True
    assert dedup.get_stats() == {"unique": 1, "duplicates": 1}
    with sqlite3.connect("d.db") as conn:
        assert conn.execute("SELECT first_url FROM content_hashes").fetchone() == ("http://first",)


def test_etag_cache_returns_conditional_headers():
    cache = ETagCache("e.db")
    cache.update("http://x", {"etag": '"v1"', "last-modified": "yesterday"}, "body")

    assert cache.get_headers("http://x") == {"If-None-Match": '"v1"', "If-Modified-Since": "yesterday"}


# --- Sessions ----------------------------------------------------------------

def test_browser_config_has_every_setting_browser_session_reads():
    config = CrawlConfig()
    assert config.browser.javascript_enabled is True


def test_browser_session_only_returns_cookies_for_the_domain():
    class FakeContext:
        async def cookies(self):
            return [{"name": "a", "domain": ".example.com"}, {"name": "b", "domain": "other.com"}]

    session = BrowserSession(CrawlConfig())
    session.context = FakeContext()

    cookies = asyncio.run(session.get_cookies("www.example.com"))

    assert [c["name"] for c in cookies] == ["a"]


def test_cookie_banner_handling_can_be_cancelled():
    class FakePage:
        async def wait_for_selector(self, selector, timeout):
            await asyncio.sleep(10)

    async def scenario():
        session = BrowserSession(CrawlConfig())
        task = asyncio.create_task(session._handle_challenges(FakePage()))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.wait_for(task, 1)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())


# --- Rotation ----------------------------------------------------------------

def test_repeated_failures_schedule_a_single_recheck():
    rechecks = []

    async def scenario():
        manager = ProxyManager()

        async def fake_recheck(proxy):
            rechecks.append(proxy)

        manager._recheck_proxy = fake_recheck
        proxy = Proxy("h", 1)
        for _ in range(8):
            await manager.mark_failure(proxy)
        await asyncio.sleep(0)
        return proxy

    assert asyncio.run(scenario()).is_healthy is False
    assert len(rechecks) == 1


# --- Item models -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [("£51.77", 51.77), (12, 12.0), ("n/a", None), (None, None)])
def test_scraped_item_cleans_price(raw, expected):
    assert ScrapedItem(url="https://x.com/", price=raw).price == expected


@pytest.mark.parametrize("raw, expected", [(3, 3), ("4", 4), ("bad", None), (9, None), (None, None)])
def test_scraped_item_validates_rating(raw, expected):
    assert ScrapedItem(url="https://x.com/", rating_num=raw).rating_num == expected


def test_crawl_job_accepts_the_real_config():
    job = CrawlJob(id="1", config=CrawlConfig(name="job"))
    assert job.config.name == "job"


# --- Metrics (needs prometheus_client) ---------------------------------------

def test_metrics_server_serves_metrics(site):
    metrics = pytest.importorskip("my_scraper.monitoring.metrics", exc_type=ImportError)

    async def scenario():
        collector = metrics.MetricsCollector()
        collector.record_request("GET", 200, "x.com", 0.1)
        server = metrics.MetricsServer(collector, port=0)
        response = await server.metrics_handler(None)
        return response.status, response.body.decode(), collector

    status, body, collector = asyncio.run(scenario())

    assert status == 200
    assert 'crawler_requests_total{domain="x.com",method="GET",status="200"} 1.0' in body
    assert "crawler_queue_size{" not in body  # requests must not inflate the queue gauge


def test_webhook_signature_matches_the_bytes_sent(site):
    metrics = pytest.importorskip("my_scraper.monitoring.metrics", exc_type=ImportError)
    received = {}

    async def hook(request):
        received["body"] = await request.read()
        received["signature"] = request.headers["X-Signature"]
        return web.Response()

    site.pages["/hook"] = hook

    async def scenario():
        notifier = metrics.WebhookNotifier(site.url + "/hook", secret="s3cret")
        await notifier.notify("crawl.done", {"b": 1, "a": 2})
        await notifier.close()

    asyncio.run(scenario())

    expected = hmac.new(b"s3cret", received["body"], hashlib.sha256).hexdigest()
    assert received["signature"] == expected
    assert json.loads(received["body"])["event"] == "crawl.done"
