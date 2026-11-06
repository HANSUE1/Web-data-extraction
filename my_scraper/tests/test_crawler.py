"""End-to-end crawler behaviour against the local test site."""
import asyncio
import json
import sqlite3
from pathlib import Path

from aiohttp import web

from my_scraper.config.settings import ExportFormat
from my_scraper.core.crawler import CrawlState, ProductionCrawler


def run(config, **kwargs):
    crawler = ProductionCrawler(config)
    results = asyncio.run(crawler.crawl(**kwargs))
    return crawler, results


def titles(results):
    return sorted(item["title"] for item in results)


def audit_statuses():
    lines = Path("logs/audit.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["status"] for line in lines]


def test_follows_links_beyond_the_first_page(site, make_config):
    site.add("/", "home", links=["/a", "/b"])
    site.add("/a", "a", links=["/c"])
    site.add("/b", "b")
    site.add("/c", "c")

    _, results = run(make_config())

    assert titles(results) == ["a", "b", "c", "home"]


def test_items_record_their_source_url(site, make_config):
    site.add("/", "home")

    _, results = run(make_config())

    assert results == [{"title": "home", "url": site.url + "/"}]


def test_each_page_is_fetched_once_despite_duplicate_links(site, make_config):
    site.add("/", "home", links=["/a", "/a", "/a#top", "/b"])
    site.add("/a", "a", links=["/b", "/"])
    site.add("/b", "b", links=["/a"])

    _, results = run(make_config())

    assert len(results) == 3
    assert site.hits["/a"] == site.hits["/b"] == site.hits["/"] == 1


def test_max_pages_counts_pages_not_items(site, make_config):
    links = [f"/p{i}" for i in range(20)]
    site.add("/", "home", links=links)
    for link in links:
        site.add_raw(link, "<html><body>" + "no heading " * 50 + "</body></html>", "text/html")

    config = make_config(max_pages=5)
    run(config)

    requested = sum(count for path, count in site.hits.items() if path != "/robots.txt")
    assert requested == 5


def test_max_depth_is_respected(site, make_config):
    site.add("/", "home", links=["/one"])
    site.add("/one", "one", links=["/two"])
    site.add("/two", "two")

    _, results = run(make_config(max_depth=1))

    assert titles(results) == ["home", "one"]


def test_stays_on_allowed_domains(site, make_config):
    site.add("/", "home", links=["http://other.invalid/x", "mailto:a@b.c"])

    _, results = run(make_config())

    assert titles(results) == ["home"]


def test_pages_without_matches_are_not_stored(site, make_config):
    site.add("/", "home", links=["/empty"])
    site.add_raw("/empty", "<html><body>" + "words " * 80 + "</body></html>", "text/html")

    _, results = run(make_config())

    assert titles(results) == ["home"]
    assert "no_data_extracted" in audit_statuses()


def test_error_pages_are_not_stored_as_data(site, make_config):
    site.add("/", "home", links=["/gone"])
    site.add("/gone", "Not Found", status=404)

    _, results = run(make_config())

    assert titles(results) == ["home"]
    assert "http_error" in audit_statuses()
    assert site.hits["/gone"] == 1  # a 404 is final, no retries


def test_retryable_status_fails_cleanly_after_all_retries(site, make_config):
    site.add("/", "home", links=["/down"])
    site.add("/down", "Service Unavailable", status=503)

    _, results = run(make_config())

    assert titles(results) == ["home"]
    assert site.hits["/down"] == 4  # first attempt + max_retries
    assert "http_error" in audit_statuses()


def test_recovers_when_a_retry_succeeds(site, make_config):
    attempts = []

    async def flaky(request):
        attempts.append(1)
        status = 503 if len(attempts) < 3 else 200
        return web.Response(status=status, text="<html><body><h1>ok</h1>" + "x " * 200 + "</body></html>",
                            content_type="text/html")

    site.pages["/"] = flaky

    _, results = run(make_config())

    assert titles(results) == ["ok"]
    assert len(attempts) == 3


def test_robots_txt_is_obeyed(site, make_config):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /private\n", "text/plain")
    site.add("/", "home", links=["/private/x", "/public"])
    site.add("/private/x", "secret")
    site.add("/public", "public")

    _, results = run(make_config())

    assert titles(results) == ["home", "public"]
    assert site.hits["/private/x"] == 0
    assert site.hits["/robots.txt"] == 1
    assert "robots_disallowed" in audit_statuses()


def test_blocked_url_is_skipped_without_a_request(site, make_config):
    site.add("/", "home", links=["/login", "/author/jane"])
    site.add("/login", "login")
    site.add("/author/jane", "jane")

    _, results = run(make_config())

    assert titles(results) == ["home", "jane"]
    assert site.hits["/login"] == 0
    assert "blocked" in audit_statuses()


def test_requests_identify_with_the_audited_user_agent(site, make_config):
    site.add("/", "home")
    config = make_config()

    run(config)

    assert site.headers_seen["/"]["User-Agent"] == config.robots.user_agent


def test_relative_links_resolve_against_the_redirect_target(site, make_config):
    async def redirect(request):
        raise web.HTTPFound("/section/")

    site.pages["/"] = redirect
    site.add("/section/", "section", links=["child"])
    site.add("/section/child", "child")

    _, results = run(make_config())

    assert "child" in titles(results)


def test_per_domain_concurrency_limit(site, make_config):
    running = {"now": 0, "peak": 0}

    async def slow(request):
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.03)
        running["now"] -= 1
        return web.Response(text="<html><body><h1>p</h1>" + "x " * 200 + "</body></html>", content_type="text/html")

    links = [f"/p{i}" for i in range(8)]
    site.add("/", "home", links=links)
    for link in links:
        site.pages[link] = slow

    config = make_config()
    config.concurrency.max_per_domain = 2
    run(config)

    assert running["peak"] == 2


def test_exports_all_formats_to_the_configured_directory(site, make_config):
    site.add("/", "home")
    config = make_config(output_dir="custom_out", export_formats=list(ExportFormat))

    crawler, _ = run(config)

    assert set(crawler.exports) == {"json", "jsonl", "csv", "sqlite"}
    for path in crawler.exports.values():
        assert Path(path).parent.name == "custom_out"
        assert Path(path).stat().st_size > 0

    with sqlite3.connect(crawler.exports["sqlite"]) as conn:
        assert conn.execute("SELECT title FROM items").fetchall() == [("home",)]


def test_interrupted_crawl_saves_results_and_resumes(site, make_config):
    async def hang(request):
        await asyncio.sleep(0.3)  # outlasts the client, which is cancelled long before this
        return web.Response(text="<html><body><h1>slow</h1>" + "x " * 200 + "</body></html>", content_type="text/html")

    site.add("/", "home", links=["/slow", "/later"])
    site.add("/later", "later")
    site.pages["/slow"] = hang

    async def interrupted():
        crawler = ProductionCrawler(config)
        task = asyncio.create_task(crawler.crawl())
        while site.hits["/slow"] == 0:
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return crawler

    config = make_config()
    config.concurrency.max_workers = 1
    first = asyncio.run(interrupted())

    assert titles(first.results) == ["home"]
    assert Path(first.exports["jsonl"]).read_text(encoding="utf-8").count("\n") == 1

    site.add("/slow", "slow")
    _, results = run(make_config(), resume=True)

    assert titles(results) == ["later", "slow"]
    assert site.hits["/"] == 1  # finished pages are not fetched again
    assert len(list(Path("output").glob("*.jsonl"))) == 2  # the resume keeps the first export


def test_corrupt_checkpoint_is_ignored(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{not json", encoding="utf-8")

    assert CrawlState(str(state_file)).load() is False
