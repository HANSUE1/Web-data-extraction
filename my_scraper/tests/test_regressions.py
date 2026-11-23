"""Bugs found in the November 2026 audit. Each test fails on the code before its fix."""
import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from my_scraper.benchmark import run as benchmark
from my_scraper.cli import main as cli
from my_scraper.compliance.manager import ComplianceManager
from my_scraper.config.settings import CrawlConfig, ExportFormat
from my_scraper.core.crawler import CrawlState, ProductionCrawler
from my_scraper.fetcher.smart_fetcher import StaticFetcher
from my_scraper.parser.documents import parse_xml
from my_scraper.parser.universal_parser import UniversalParser, load_json
from my_scraper.service.engine import ScrapeService
from my_scraper.service.http_api import _dumps, create_app
from my_scraper.storage.multi_format import CSVExporter, SQLiteExporter, StorageManager
from my_scraper.studio import sketch
from my_scraper.studio.jobs import InvalidJob, JobQueue, config_from_request
from my_scraper.studio.results import Library
from my_scraper.studio.server import create_app as create_studio
from my_scraper.tests.conftest import html_page
from my_scraper.utils.urls import normalize_url

LINE_SEPARATOR, NEXT_LINE = chr(0x2028), chr(0x85)  # str.splitlines() breaks on these, JSON does not


def crawl(config, **options):
    crawler = ProductionCrawler(config)
    asyncio.run(crawler.crawl(**options))
    return crawler


def parse(url, content, content_type):
    return UniversalParser().parse_page(
        {"url": url, "content": content, "headers": {"content-type": content_type}, "body": content.encode()}).items


def parse_json(url, data):
    return parse(url, json.dumps(data), "application/json")


async def hang_up(request):
    request.transport.close()
    return web.Response()


# --- Crawling -------------------------------------------------------------------

def test_a_broken_sitemap_does_not_stop_the_crawl(site, make_config):
    site.add("/", "Home")
    site.add_raw("/robots.txt", f"Sitemap: {site.url}/bad.xml.gz\nSitemap: {site.url}/good.xml\n", "text/plain")
    site.add_raw("/bad.xml.gz", b"\x1f\x8bnot really gzip", "application/gzip")
    site.add_raw("/good.xml", f"<urlset><url><loc>{site.url}/listed</loc></url></urlset>", "application/xml")
    site.add("/listed", "Listed")

    crawler = crawl(make_config(use_sitemap=True))

    assert sorted(item["title"] for item in crawler.results) == ["Home", "Listed"]


def test_an_unreachable_sitemap_does_not_stop_the_crawl(site, make_config):
    site.add("/", "Home")
    site.pages["/sitemap.xml"] = hang_up
    config = make_config(use_sitemap=True)
    config.retry.max_retries = 0

    assert [item["title"] for item in crawl(config).results] == ["Home"]


def test_redirects_do_not_use_up_the_page_budget(site, make_config):
    async def redirect(request):
        raise web.HTTPFound(request.path.replace("/go", "/page"))

    site.add("/", "Home", links=[f"/go{n}" for n in range(4)])
    for n in range(4):
        site.pages[f"/go{n}"] = redirect
        site.add(f"/page{n}", f"Page {n}")

    crawler = crawl(make_config(max_pages=5))

    assert len(crawler.results) == 5
    assert crawler.state.page_count == 5
    reloaded = CrawlState(str(crawler.state.state_file))
    assert reloaded.load() and reloaded.page_count == 5


def test_a_redirect_into_a_disallowed_path_is_not_kept(site, make_config):
    async def redirect(_request):
        raise web.HTTPFound("/private/secret")

    site.add("/", "Home", links=["/open"])
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /private/\n", "text/plain")
    site.pages["/open"] = redirect
    site.add("/private/secret", "Secret")

    crawler = crawl(make_config())

    assert [item["title"] for item in crawler.results] == ["Home"]
    assert crawler.compliance.status_counts["robots_disallowed"] == 1


def test_a_malformed_base_href_costs_neither_the_page_nor_its_links(site, make_config):
    site.add_raw("/", '<base href="http://[bad/">' + html_page("Home", ["/a"]), "text/html")
    site.add("/a", "A")

    crawler = crawl(make_config())

    assert sorted(item["title"] for item in crawler.results) == ["A", "Home"]
    assert "failed" not in crawler.compliance.status_counts


def test_json_records_with_structured_titles_are_kept(site, make_config):
    records = [{"title": {"rendered": f"Post {n}"}, "url": {"href": f"/p/{n}"}} for n in range(4)]
    site.add_raw("/", json.dumps(records), "application/json")
    config = make_config()
    config.selectors = {}

    crawler = crawl(config)

    assert [item["title"]["rendered"] for item in crawler.results] == [f"Post {n}" for n in range(4)]


def test_resume_keeps_items_holding_unicode_line_breaks(tmp_path):
    item = {"text": f"one{LINE_SEPARATOR}two{NEXT_LINE}three"}
    state = CrawlState(str(tmp_path / "s.json"))
    state.reset_items()
    state.append_items("u", [item])
    state.visited = {"u"}
    assert state.load_items() == [item]


def test_a_second_resume_does_not_duplicate_or_lose_items(tmp_path):
    state = CrawlState(str(tmp_path / "s.json"))
    state.reset_items()
    state.append_items("done", [{"t": 1}])
    state.append_items("unfinished", [{"t": 2}, {"t": 3}])
    with open(state.items_file, "a", encoding="utf-8") as f:
        f.write('{"page": "cut sho')  # a crash mid-write leaves no newline
    state.visited = {"done"}

    assert state.load_items() == [{"t": 1}]
    state.append_items("unfinished", [{"t": 2}, {"t": 3}])  # fetched again by the resumed crawl
    state.visited.add("unfinished")

    assert state.load_items() == [{"t": 1}, {"t": 2}, {"t": 3}]


def test_login_cookies_stay_on_the_site_they_came_from(site, other_site):
    site.add("/in", "in")
    other_site.add("/out", "out")
    fetcher = StaticFetcher()
    fetcher.cookies = [{"name": "session", "value": "s3cret", "domain": "shop.example", "path": "/"},
                       {"name": "mine", "value": "1", "domain": "127.0.0.1", "path": "/"}]

    async def go():
        try:
            await fetcher.fetch(site.url + "/in")
            await fetcher.fetch(other_site.url.replace("127.0.0.1", "localhost") + "/out")
        finally:
            await fetcher.close()

    asyncio.run(go())
    assert site.headers_seen["/in"].get("Cookie") == "mine=1"
    assert "Cookie" not in other_site.headers_seen["/out"]


def test_urls_keep_query_bytes_that_are_not_utf8():
    assert normalize_url("http://x.com/s?q=caf%E9&a=1") == "http://x.com/s?a=1&q=caf%E9"
    assert normalize_url("http://x.com/s?q=日本") == "http://x.com/s?q=%E6%97%A5%E6%9C%AC"


def test_scanning_for_personal_data_is_fast_on_long_unbroken_text(make_config):
    started = time.time()
    found = ComplianceManager(make_config()).scan_for_personal_data({"blob": "a1" * 50_000, "note": "write to bob@a.com"})
    assert found == ["note"]
    assert time.time() - started < 5


def test_a_blocklist_saved_with_a_bom_still_blocks(make_config, tmp_path):
    config = make_config()
    config.compliance.blocklist_file = str(tmp_path / "block.txt")
    Path(config.compliance.blocklist_file).write_text("blocked.example\n", encoding="utf-8-sig")
    with pytest.raises(ValueError):
        ComplianceManager(config).validate_url("https://blocked.example/")


# --- Parsing --------------------------------------------------------------------

def test_the_last_short_page_of_a_platform_api_is_read():
    posts = [{"id": n, "title": {"rendered": f"Post {n}"}, "link": f"https://w.com/{n}"} for n in range(2)]
    items = parse_json("https://w.com/wp-json/wp/v2/posts?per_page=100&page=2", posts)
    assert [item["title"] for item in items] == ["Post 0", "Post 1"]


def test_shopify_products_are_read_not_their_variants():
    variants = [{"id": n, "title": f"Size {n}", "price": "5", "sku": "s", "created_at": "2026", "grams": 1}
                for n in range(6)]
    data = {"products": [{"id": n, "title": f"Chair {n}", "handle": f"chair-{n}", "variants": variants} for n in range(10)]}
    items = parse_json("https://s.com/products.json?limit=250&page=1", data)
    assert [item["title"] for item in items] == [f"Chair {n}" for n in range(10)]


@pytest.mark.parametrize(("kind", "content"), [
    ("text/csv", "name,source\nA,web\nB,tv\n"),
    ("application/json", '[{"name": "A", "source": "web"}, {"name": "B", "source": "tv"}]'),
    ("text/xml", "<r><i><name>A</name><source>web</source></i><i><name>B</name><source>tv</source></i></r>"),
])
def test_a_column_called_source_is_not_overwritten(kind, content):
    assert [item["source_column"] for item in parse("http://x/a", content, kind)] == ["web", "tv"]


def test_one_record_per_line_json_may_hold_unicode_line_breaks():
    assert load_json('{"a":"x' + LINE_SEPARATOR + 'y"}\n{"a":"z"}\n') == [{"a": f"x{LINE_SEPARATOR}y"}, {"a": "z"}]


def test_json_nan_is_read_as_missing():
    assert load_json('{"a": NaN, "b": -Infinity, "c": 1}') == {"a": None, "b": None, "c": 1}


def test_one_malformed_link_does_not_lose_the_feed_or_the_sketch():
    feed = (b"<rss><channel><item><title>A</title><link>http://[x</link></item>"
            b"<item><title>B</title><link>http://ok.test/</link></item></channel></rss>")
    assert [item["title"] for item in parse_xml(feed, "http://x.test/")] == ["A", "B"]
    page = {"content": '<p>Hello <a href="http://[x">broken</a> world</p>'}
    assert "broken" in str(sketch.build("http://x.test/", page, [], lambda href: False)["blocks"])


def test_many_selector_rows_are_deduplicated_quickly():
    rows = "".join(f"<li><a>{n}</a></li>" for n in range(12_000))
    started = time.time()
    page = UniversalParser().parse_page({"url": "http://x/", "content": f"<ul>{rows}</ul>", "headers": {}},
                                        {"n": "a::text"}, "li")
    assert len(page.items) == 12_000
    assert time.time() - started < 20


# --- Exports --------------------------------------------------------------------

def sqlite_rows(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM items")]
    finally:
        connection.close()


def test_sqlite_keeps_names_that_differ_only_in_case(tmp_path):
    path = SQLiteExporter(str(tmp_path)).export([{"title": "a", "attributes": {"Title": "b", "TITLE": "c"}}])
    assert sorted(sqlite_rows(path)[0].values()) == ["a", "b", "c"]


def test_sqlite_takes_numbers_of_any_size(tmp_path):
    path = SQLiteExporter(str(tmp_path)).export([{"id": 2 ** 70, "n": 5}])
    assert sqlite_rows(path) == [{"id": str(2 ** 70), "n": "5"}]


def test_sqlite_takes_more_fields_than_it_has_columns(tmp_path):
    items = [{"title": f"t{n}", "attributes": {f"k{n}": "v"}} for n in range(2500)]
    rows = sqlite_rows(SQLiteExporter(str(tmp_path)).export(items))
    assert len(rows) == 2500 and json.loads(rows[7]["attributes"]) == {"k7": "v"}


def test_csv_headers_cannot_be_formulas(tmp_path):
    path = CSVExporter(str(tmp_path)).export([{"=cmd()": "x"}])
    assert Path(path).read_text(encoding="utf-8").splitlines()[0] == "'=cmd()"


def test_an_output_folder_that_cannot_be_made_is_an_export_error(make_config, tmp_path):
    (tmp_path / "file").write_text("in the way")
    manager = StorageManager(make_config(output_dir=str(tmp_path / "file" / "results"),
                                         export_formats=[ExportFormat.JSONL]))
    assert manager.export([{"a": 1}]) == {"jsonl": None}
    assert "jsonl" in manager.errors


# --- Command line ---------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://[bad/", "http://x.test:port/", "https:///no-host"])
def test_an_unreadable_start_url_is_a_usage_error(url, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--urls", url])
    assert stop.value.code == 2 and "start URL" in capsys.readouterr().err
    with pytest.raises(InvalidJob):
        config_from_request({"urls": [url]})


def test_a_config_file_may_start_with_a_bom(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"start_urls": ["https://a.test/"], "max_pages": 7}), encoding="utf-8-sig")
    parser = cli.build_parser()
    assert cli.build_config(parser.parse_args(["--config", str(path)]), parser).max_pages == 7


def test_explain_reports_a_failed_fetch_without_a_traceback(site, tmp_path, capsys):
    site.pages["/"] = hang_up
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"retry": {"max_retries": 0}}), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        cli.main(["--urls", site.url + "/", "--no-browser", "--explain", "--config", str(config)])
    assert stop.value.code == 1 and "Error:" in capsys.readouterr().err


def test_the_benchmark_finds_the_logs_and_survives_a_cut_line(tmp_path):
    assert (benchmark.LOGS / "audit.log").as_posix() == Path(CrawlConfig().logging.audit_log).as_posix()
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": "x' + LINE_SEPARATOR + 'y"}\n{"a": 1}\n{"a": "cut', encoding="utf-8")
    assert benchmark.read_jsonl(path) == [{"a": f"x{LINE_SEPARATOR}y"}, {"a": 1}]


# --- Service and studio -----------------------------------------------------------

def with_client(app, steps):
    async def go():
        async with TestClient(TestServer(app)) as client:
            return await steps(client)
    return asyncio.run(go())


def test_api_reports_badly_typed_fields_as_client_errors(site, tmp_path):
    site.add_raw("/", "<html><head><title>T</title></head><body><h1>Chair</h1></body></html>", "text/html")

    async def steps(client):
        url = site.url + "/"
        assert (await client.post("/v1/crawl", json={"url": url, "max_pages": "many"})).status == 400
        assert (await client.post("/v1/map", json={"url": url, "limit": "x"})).status == 400
        assert (await client.post("/v1/extract", json={"url": url, "fields": 5})).status == 400
        found = await (await client.post("/v1/extract", json={"url": url, "fields": "title, price"})).json()
        assert set(found["data"]) | set(found["missing"]) == {"title", "price"}

    with_client(create_app(ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)), steps)


def test_a_key_with_odd_characters_is_refused_not_an_error(tmp_path):
    async def steps(client):
        assert (await client.get("/v1", headers={"Authorization": "Bearer clé"})).status == 401

    with_client(create_app(ScrapeService(home=str(tmp_path / "home")), api_key="key"), steps)


def test_answers_are_valid_json_even_with_nan():
    answer = json.loads(_dumps({"a": float("nan"), "b": [float("inf"), 1.5], "c": "x"}))
    assert answer == {"a": None, "b": [None, 1.5], "c": "x"}


def test_one_oddly_named_folder_does_not_break_the_library(tmp_path):
    run = tmp_path / "report_20261345_000000"
    run.mkdir()
    (run / "a.csv").write_text("a\n1\n")
    library = Library(str(tmp_path))
    assert [entry["site"] for entry in library.runs()] == ["report_20261345_000000"]
    assert library.stats()["runs"] == 1


def test_a_damaged_results_file_gives_a_readable_error(tmp_path):
    run = tmp_path / "results" / "a.test_20261118_120000"
    run.mkdir(parents=True)
    (run / "a.jsonl").write_text('{"a": 1}\n{"a": cut', encoding="utf-8")
    (run / "a.db").write_bytes(b"not a database, whatever its name says")

    async def steps(client):
        for name in ("a.jsonl", "a.db"):
            response = await client.get("/api/file", params={"path": f"{run.name}/{name}"})
            assert response.status == 422
            assert "could not be read" in (await response.json())["message"]

    service = ScrapeService(home=str(tmp_path / "home"), use_browser=False)
    with_client(create_studio(str(tmp_path / "results"), service), steps)


def test_a_finished_job_keeps_its_numbers_but_not_its_crawler(site):
    site.add("/", "Home", links=["/a"])
    site.add("/a", "A")

    async def go():
        queue = JobQueue()
        queue.start()
        job = queue.submit({"urls": [site.url + "/"], "mode": "static", "rate_limit": 1000,
                            "selectors": {"title": "h1::text"}, "export": ["jsonl"]})
        while not job.finished:
            await asyncio.sleep(0.02)
        await queue.stop()
        return job

    job = asyncio.run(go())
    assert job.crawler is None
    progress = job.snapshot()["progress"]
    assert (progress["pages"], progress["items"], progress["statuses"]) == (2, 2, {"success": 2})
