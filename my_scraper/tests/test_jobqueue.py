"""The durable queue, on both stores, and the workers and schedules that use it."""
import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from my_scraper.jobqueue.schedule import ScheduleError, cron_matches, due_slot, in_window, load_schedules, tick
from my_scraper.jobqueue.store import SQLiteQueue, open_queue
from my_scraper.jobqueue.worker import Worker
from my_scraper.ops.history import History

LISTING = "<html><body><main>" + "".join(
    f'<article class="p"><h3><a href="/p/{i}">Kettle number {i}</a></h3><p>£{i}.00</p></article>' for i in range(1, 6)
) + "</main></body></html>"


@pytest.fixture(params=["sqlite", "redis"])
def queue(request, tmp_path):
    if request.param == "sqlite":
        return SQLiteQueue(str(tmp_path / "queue.db"))
    from my_scraper.jobqueue.redis_store import RedisQueue

    if os.environ.get("SCRAPER_TEST_REDIS"):  # a real server, as in CI
        redis = pytest.importorskip("redis")
        client = redis.Redis.from_url(os.environ["SCRAPER_TEST_REDIS"], decode_responses=True)
        return RedisQueue(client=client, prefix=f"test:{uuid.uuid4().hex}:")
    fakeredis = pytest.importorskip("fakeredis", reason="pip install 'fakeredis[lua]'")
    pytest.importorskip("lupa", reason="pip install 'fakeredis[lua]'")
    return RedisQueue(client=fakeredis.FakeRedis(decode_responses=True))


# --- The queue -------------------------------------------------------------------


def test_urgent_jobs_go_first_then_the_oldest(queue):
    first, urgent, second = queue.enqueue({"n": 1}), queue.enqueue({"n": 2}, priority=5), queue.enqueue({"n": 3})
    assert [queue.claim("w", 60).id for _ in range(3)] == [urgent, first, second]
    assert queue.claim("w", 60) is None


def test_a_job_that_is_not_due_waits(queue):
    queue.enqueue({}, delay=60)
    assert queue.claim("w", 60) is None
    assert queue.counts()["queued"] == 1


def test_one_job_per_key_until_it_is_finished(queue):
    first = queue.enqueue({}, dedupe_key="site")
    assert queue.enqueue({}, dedupe_key="site") == first
    queue.complete(queue.claim("w", 60), {})
    assert queue.enqueue({}, dedupe_key="site") != first


def test_a_crashed_workers_job_goes_to_the_next_worker(queue):
    queue.enqueue({"n": 1})
    lost = queue.claim("dead-worker", 0.05)
    time.sleep(0.1)
    again = queue.claim("w2", 60)
    assert (again.id, again.attempts, again.worker) == (lost.id, 2, "w2")
    assert not queue.heartbeat(lost, 60)        # the first worker wakes up: it has lost the job
    assert not queue.complete(lost, {"stale": True})
    assert queue.complete(again, {"items": 3})
    assert queue.get(lost.id).result == {"items": 3}


def test_a_job_that_keeps_killing_workers_becomes_a_dead_letter(queue):
    job_id = queue.enqueue({}, max_attempts=1)
    queue.claim("w", 0.05)
    time.sleep(0.1)
    assert queue.claim("w", 60) is None
    assert queue.get(job_id).state == "dead"


def test_attempts_run_out_into_the_dead_letters_and_can_be_given_again(queue):
    job_id = queue.enqueue({}, max_attempts=2)
    assert queue.fail(queue.claim("w", 60), "outage", retry_in=0) == "queued"
    assert queue.fail(queue.claim("w", 60), "outage again", retry_in=0) == "dead"
    assert (queue.counts()["dead"], queue.get(job_id).error) == (1, "outage again")
    assert queue.retry(job_id)
    assert queue.claim("w", 60).attempts == 1


def test_a_permanent_failure_is_not_tried_again(queue):
    queue.enqueue({}, max_attempts=5)
    assert queue.fail(queue.claim("w", 60), "refused", retry_in=None) == "dead"


def test_a_retry_waits_its_time(queue):
    queue.enqueue({})
    queue.fail(queue.claim("w", 60), "later", retry_in=60)
    assert queue.claim("w", 60) is None


def test_releasing_costs_no_attempt_and_cancelling_ends_a_running_job(queue):
    job_id = queue.enqueue({})
    queue.release(queue.claim("w", 60))
    job = queue.claim("w", 60)
    assert job.attempts == 1
    assert queue.cancel(job_id)
    assert not queue.heartbeat(job, 60)
    assert queue.get(job_id).state == "cancelled"


def test_two_workers_never_get_the_same_job(queue):
    for n in range(30):
        queue.enqueue({"n": n})
    claimed = []

    def work(name):
        while (job := queue.claim(name, 60)) is not None:
            claimed.append(job.id)

    threads = [threading.Thread(target=work, args=(f"w{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == len(set(claimed)) == 30


def test_pacing_is_agreed_between_workers(queue):
    waits = [queue.reserve("example.com", 2.0) for _ in range(3)]
    assert waits[0] == pytest.approx(0, abs=0.2) and waits[2] == pytest.approx(4, abs=0.3)
    assert queue.reserve("other.com", 2.0) == pytest.approx(0, abs=0.2)


def test_a_key_is_only_new_once(queue):
    assert queue.seen_add("k") and not queue.seen_add("k")


def test_pruning_keeps_dead_letters(queue):
    queue.enqueue({})
    queue.complete(queue.claim("w", 60), {})
    queue.enqueue({})
    queue.fail(queue.claim("w", 60), "x", None)
    assert queue.prune(-1) == 1
    assert queue.counts()["dead"] == 1


def test_open_queue_takes_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_QUEUE_URL", str(tmp_path / "q.db"))
    assert isinstance(open_queue(), SQLiteQueue)


# --- Workers -----------------------------------------------------------------------


def crawl_payload(site, **extra):
    return {"config": {"start_urls": [site.url + "/"], "max_depth": 0, "fetcher": "static",
                       "rate_limit": {"requests_per_second": 1000},
                       "retry": {"base_delay": 0.001, "max_retries": 1}, **extra}}


def drain(queue, **options):
    asyncio.run(Worker(queue, **options).run(drain=True))


def test_a_worker_runs_a_queued_crawl(site, queue):
    site.add_raw("/", LISTING, "text/html")
    job_id = queue.enqueue(crawl_payload(site))
    drain(queue)
    job = queue.get(job_id)
    assert (job.state, job.result["items"]) == ("done", 5)
    [run] = History("outputs/state").runs()
    assert (run["job_id"], run["state"], run["items"]) == (job_id, "completed", 5)
    assert not list(Path("outputs/state/crawls").glob("*"))  # a finished job leaves no checkpoint


def test_a_refusal_is_not_asked_again_and_an_outage_is(site, queue):
    site.add_raw("/", "<html><title>Just a moment...</title><body>Verify you are human</body></html>", "text/html")
    refused = queue.enqueue(crawl_payload(site))
    drain(queue)
    assert queue.get(refused).state == "dead" and "blocked_by_site" in queue.get(refused).error

    site.add("/down", status=500)
    failing = queue.enqueue(crawl_payload(site, start_urls=[site.url + "/down"]))
    drain(queue)
    job = queue.get(failing)
    assert (job.state, job.attempts) == ("queued", 1) and job.run_after > time.time() + 30


def test_a_job_that_cannot_run_is_dead_at_once(queue):
    for payload in ({"config": {"start_url": "typo"}}, {"config": {}}, {"config_file": "missing.json"}, {}):
        job_id = queue.enqueue(payload)
        drain(queue)
        assert queue.get(job_id).state == "dead", payload


def test_stopping_a_worker_hands_its_crawl_back_and_the_next_one_resumes(site, queue):
    release = threading.Event()
    site.add("/", "Home", links=["/a", "/slow"])
    site.add("/a", "A")

    async def slow(_request):
        from aiohttp import web

        while not release.is_set():
            await asyncio.sleep(0.02)
        return web.Response(text="<html><body><h1>Slow</h1>" + "<p>words</p>" * 60 + "</body></html>", content_type="text/html")

    site.pages["/slow"] = slow
    job_id = queue.enqueue(crawl_payload(site, max_depth=1, selectors={"title": "h1::text"}))

    async def stop_half_way():
        worker = Worker(queue)
        task = asyncio.create_task(worker.run())
        while site.hits["/slow"] == 0 or site.hits["/a"] == 0:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        worker.stop()
        await task

    asyncio.run(stop_half_way())
    job = queue.get(job_id)
    assert (job.state, job.attempts) == ("queued", 0)

    release.set()
    queue.claim("crashed", 0.01)  # a worker that dies at once, so the next attempt is a resume
    time.sleep(0.05)
    before = site.hits["/"]
    drain(queue)
    job = queue.get(job_id)
    assert (job.state, job.result["items"]) == ("done", 3)
    assert site.hits["/"] == before  # finished pages are not fetched again


def test_a_cancelled_job_stops_its_crawl(site, queue):
    async def forever(_request):
        await asyncio.sleep(60)

    site.pages["/"] = forever
    job_id = queue.enqueue(crawl_payload(site))

    async def cancel_it():
        worker = Worker(queue, lease=0.3)
        task = asyncio.create_task(worker.run(drain=True))
        while not site.hits["/"]:
            await asyncio.sleep(0.02)
        queue.cancel(job_id)
        await asyncio.wait_for(task, 10)

    asyncio.run(cancel_it())
    assert queue.get(job_id).state == "cancelled"


def test_fan_out_gives_each_page_to_one_job(site, queue):
    site.add("/", "Home", links=[f"/p{n}" for n in range(5)])
    for n in range(5):
        site.add(f"/p{n}", f"Page {n}")
    parent = queue.enqueue({**crawl_payload(site, selectors={"title": "h1::text"}, max_depth=1), "fan_out": 2})
    drain(queue)
    result = queue.get(parent).result
    assert result["pages"] == 6 and len(result["children"]) == 3
    assert all(queue.get(child).state == "done" for child in result["children"])
    assert sum(queue.get(child).result["items"] for child in result["children"]) == 6
    assert all(site.hits[f"/p{n}"] == 1 for n in range(5))


def test_a_request_from_the_api_cannot_lift_a_safety_setting(queue, site):
    job_id = queue.enqueue({"request": {"urls": [site.url + "/"], "ignore_robots_for": "x"}})
    drain(queue)
    assert queue.get(job_id).state == "dead"


# --- Schedules ------------------------------------------------------------------------


@pytest.mark.parametrize("expression, moment, expected", [
    ("30 2 * * *", datetime(2026, 11, 26, 2, 30), True),
    ("30 2 * * *", datetime(2026, 11, 26, 2, 31), False),
    ("*/15 1-5 * * 1-5", datetime(2026, 11, 26, 3, 45), True),   # a Thursday
    ("*/15 1-5 * * 1-5", datetime(2026, 11, 29, 3, 45), False),  # a Sunday
    ("0 0 * * 0", datetime(2026, 11, 29, 0, 0), True),
    ("0 0 * * 7", datetime(2026, 11, 29, 0, 0), True),
    ("0 0 1 * 1", datetime(2026, 11, 30, 0, 0), True),           # the first, or a Monday
    ("0 0 1 * 1", datetime(2026, 12, 1, 0, 0), True),
    ("0 0 1,15 2 *", datetime(2026, 2, 15, 0, 0), True),
])
def test_cron(expression, moment, expected):
    assert cron_matches(expression, moment) is expected


@pytest.mark.parametrize("expression", ["* * * *", "61 * * * *", "a * * * *", "5-1 * * * *", "*/0 * * * *"])
def test_a_bad_cron_is_refused(expression):
    with pytest.raises(ScheduleError):
        cron_matches(expression, datetime(2026, 1, 1))


def test_windows_may_pass_midnight():
    assert in_window("22:00-06:00", datetime(2026, 1, 1, 23)) and in_window("22:00-06:00", datetime(2026, 1, 1, 5))
    assert not in_window("22:00-06:00", datetime(2026, 1, 1, 12))
    with pytest.raises(ScheduleError):
        in_window("late", datetime(2026, 1, 1))


def test_two_schedulers_queue_a_due_crawl_once(queue):
    schedules = [{"name": "nightly", "cron": "0 2 * * *", "timezone": "UTC", "config": {"start_urls": ["https://x.test/"]}}]
    moment = datetime(2026, 11, 26, 2, 10, tzinfo=timezone.utc).timestamp()  # ten minutes late is still caught
    assert tick(queue, schedules, moment) == ["nightly"]
    assert tick(queue, schedules, moment + 30) == []
    queue.complete(queue.claim("w", 60), {})
    assert tick(queue, schedules, moment + 60) == []
    assert tick(queue, schedules, moment + 86400) == ["nightly"]


def test_a_run_still_going_is_not_started_twice(queue):
    schedules = [{"name": "often", "every": "1m", "config": {"start_urls": ["https://x.test/"]}}]
    tick(queue, schedules, 1_000_000)
    tick(queue, schedules, 1_000_060)
    assert queue.counts()["queued"] == 1


def test_every_keeps_to_its_window():
    schedule = {"name": "s", "every": "1h", "window": "01:00-05:00", "timezone": "UTC", "config": {}}
    assert due_slot(schedule, datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp()) is None
    assert due_slot(schedule, datetime(2026, 1, 1, 2, tzinfo=timezone.utc).timestamp()) is not None


@pytest.mark.parametrize("schedules", [
    {}, [{}], [{"name": "a", "config": {}}], [{"name": "a", "every": "soon", "config": {}}],
    [{"name": "a", "every": "1h", "cron": "* * * * *", "config": {}}], [{"name": "a", "every": "1h"}],
    [{"name": "a", "every": "1h", "config": {}}, {"name": "a", "every": "1h", "config": {}}],
    [{"name": "a", "cron": "* * * * *", "timezone": "Mars/Olympus", "config": {}}],
])
def test_a_bad_schedules_file_is_refused(tmp_path, schedules):
    path = tmp_path / "schedules.json"
    path.write_text(json.dumps(schedules), encoding="utf-8")
    with pytest.raises(ScheduleError):
        load_schedules(str(path))


def test_a_missing_schedules_file_waits_instead_of_failing(tmp_path, caplog):
    from my_scraper.ops.__main__ import _schedule_forever

    path = str(tmp_path / "schedules.json")
    with pytest.raises(ScheduleError):
        load_schedules(path)  # still an error where a file is required
    assert load_schedules(path, missing_ok=True) == []

    async def one_round():
        stopping = asyncio.Event()
        task = asyncio.create_task(_schedule_forever(SQLiteQueue(str(tmp_path / "queue.db")), path, stopping))
        await asyncio.sleep(0.2)
        stopping.set()
        await asyncio.wait_for(task, 5)

    asyncio.run(one_round())
    assert "no schedules file" in caplog.text and "scheduling failed" not in caplog.text
