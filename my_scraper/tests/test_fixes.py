"""One test per bug found in the November 2026 review, so none of them can come back."""
import asyncio
import json
from pathlib import Path

from aiohttp import web
from parsel import Selector

from my_scraper.cli import main as cli
from my_scraper.compliance.manager import ComplianceManager
from my_scraper.config.settings import CrawlConfig, ExportFormat
from my_scraper.core.crawler import ProductionCrawler
from my_scraper.core.links import forbids_following
from my_scraper.fetcher.browser import _registered_domain
from my_scraper.fetcher.discovery import next_api_page
from my_scraper.fetcher.smart_fetcher import StaticFetcher
from my_scraper.parser.documents import parse_xml
from my_scraper.parser.embedded_state import records_to_items
from my_scraper.storage.etag_cache import ETagCache
from my_scraper.storage.multi_format import StorageManager
from my_scraper.utils.politeness import RobotsChecker


def config_for(make_config, *start_urls, **overrides):
    config = make_config(**overrides)
    config.start_urls = list(start_urls)
    return config


def run(config):
    crawler = ProductionCrawler(config)
    return crawler, asyncio.run(crawler.crawl())


# --- Crawling ----------------------------------------------------------------

def test_items_after_a_redirect_link_to_where_the_page_really_is(site, make_config):
    # /shop answers with a redirect to /shop/, so "item-1" means /shop/item-1, not /item-1.
    async def redirect(request):
        raise web.HTTPFound("/shop/")

    cards = "".join(
        f'<div class="card"><h3><a href="item-{n}">Garden chair model {n}</a></h3>'
        f'<span class="price">${n}9.00</span><p>Weatherproof folding chair number {n}.</p></div>'
        for n in range(6))
    site.pages["/shop"] = redirect
    site.add_raw("/shop/", f"<html><body><main>{cards}</main></body></html>", "text/html")

    config = config_for(make_config, site.url + "/shop", max_depth=0)
    config.selectors = {}  # find the items automatically
    _, results = run(config)

    assert {item["url"] for item in results} == {f"{site.url}/shop/item-{n}" for n in range(6)}


def test_each_crawl_keeps_its_own_checkpoint(site, make_config):
    site.add("/a", "a")
    site.add("/b", "b")

    run(config_for(make_config, site.url + "/a"))
    run(config_for(make_config, site.url + "/b"))

    # Before, both used data/crawl_state.json, so --resume of /a continued the crawl of /b.
    assert len(list(Path("outputs/state/crawls").glob("*.json"))) == 2


def test_everything_the_crawler_writes_goes_under_outputs(site, make_config):
    site.add("/", "home")

    run(make_config(incremental=True))

    assert sorted(path.name for path in Path(".").iterdir()) == ["outputs"]
    assert any(Path("outputs/results").glob("*.jsonl"))
    assert Path("outputs/logs/audit.log").exists()
    assert Path("outputs/state/etag_cache.db").exists()


def test_service_writes_nothing_into_the_folder_it_runs_in(site, tmp_path):
    from my_scraper.service.engine import ScrapeService

    site.add("/", "home")
    service = ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)

    async def go():
        try:
            return await service.scrape(site.url + "/")
        finally:
            await service.close()

    asyncio.run(go())

    assert sorted(path.name for path in Path(".").iterdir()) == ["home"]


# --- Politeness --------------------------------------------------------------

def test_robots_txt_answering_429_is_not_read_as_no_rules(site):
    site.add_raw("/robots.txt", "slow down", "text/plain", status=429)
    checker = RobotsChecker("TestBot/1.0")

    allowed = asyncio.run(checker.is_allowed(site.url + "/page"))

    assert allowed is False
    assert checker.failure_for(site.url + "/page") == "robots.txt returned HTTP 429"


def test_meta_refresh_to_another_site_is_not_followed(site):
    other_host = site.url.replace("127.0.0.1", "localhost")  # same server, different site
    site.add_raw("/", f'<html><head><meta http-equiv="refresh" content="0; url={other_host}/away"></head></html>',
                 "text/html")
    site.add("/away", "away")
    fetcher = StaticFetcher()

    async def go():
        try:
            return await fetcher.fetch(site.url + "/")
        finally:
            await fetcher.close()

    result = asyncio.run(go())

    assert site.hits["/away"] == 0
    assert result["final_url"] == site.url + "/"


def test_meta_refresh_within_the_site_is_still_followed(site):
    site.add_raw("/", '<html><head><meta http-equiv="refresh" content="0; url=/moved"></head></html>', "text/html")
    site.add("/moved", "moved")
    fetcher = StaticFetcher()

    async def go():
        try:
            return await fetcher.fetch(site.url + "/")
        finally:
            await fetcher.close()

    assert asyncio.run(go())["final_url"] == site.url + "/moved"


def test_robots_meta_none_counts_when_comma_separated():
    assert forbids_following(Selector(text='<meta name="robots" content="none,noarchive">'))
    assert not forbids_following(Selector(text='<meta name="robots" content="index,follow">'))


# --- Discovery and parsing ---------------------------------------------------

def test_rss_guid_that_is_not_a_permalink_is_not_used_as_a_link():
    feed = b"""<rss><channel>
      <item><title>Has link</title><link>https://site.test/a</link></item>
      <item><title>Id only</title><guid isPermaLink="false">abc-123</guid></item>
      <item><title>Permalink guid</title><guid>https://site.test/c</guid></item>
    </channel></rss>"""

    items = parse_xml(feed, "https://site.test/feed")

    assert [item.get("url") for item in items] == ["https://site.test/a", None, "https://site.test/c"]


def test_api_page_parameter_that_is_not_a_number_does_not_crash():
    assert next_api_page("https://shop.test/products.json?limit=2&page=abc", 2) is None
    assert next_api_page("https://shop.test/products.json?limit=2&page=1", 2).endswith("page=2")


def test_a_list_embedded_twice_gives_each_record_once():
    records = [{"title": f"Product {n} with a name", "price": n, "url": f"/p/{n}"} for n in range(4)]
    data = {"products": records}

    items = records_to_items([data, json.loads(json.dumps(data))], "https://shop.test/", "embedded")

    assert len(items) == 4


def test_registered_domain_understands_country_second_levels():
    assert _registered_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert _registered_domain("https://api.example.com/x") == "example.com"
    assert _registered_domain("https://www.abc.net.au/") == "abc.net.au"


# --- Compliance, storage and config ------------------------------------------

def test_compliance_report_counts_pages_read_not_every_event():
    config = CrawlConfig()
    config.compliance.audit_all_requests = False
    compliance = ComplianceManager(config)

    compliance.log_scrape_event("https://a.test/1", "success", 3)
    compliance.log_scrape_event("https://a.test/1", "success", 3)  # merged twice, read once
    compliance.log_scrape_event("https://a.test/2", "robots_disallowed")
    compliance.log_scrape_event("https://b.test/3", "failed", error="boom")

    report = compliance.generate_compliance_report()
    assert report["total_urls_scraped"] == 1
    assert report["unique_domains"] == 1


def test_takedown_blocklist_lives_with_the_state_not_the_logs():
    config = CrawlConfig()
    ComplianceManager(config).handle_takedown_request("blocked.test", "owner asked")

    assert Path("outputs/state/takedown_blocklist.txt").read_text(encoding="utf-8") == "blocked.test\n"
    assert ComplianceManager(config)._is_blocked_domain("www.blocked.test")


def test_one_run_names_all_its_files_alike_after_the_site():
    config = CrawlConfig(start_urls=["https://books.toscrape.com/"],
                         export_formats=[ExportFormat.JSONL, ExportFormat.CSV])

    exports = StorageManager(config).export([{"title": "a"}])

    stems = {Path(path).stem for path in exports.values()}
    assert len(stems) == 1
    assert stems.pop().startswith("books.toscrape.com_")
    again = StorageManager(config).export([{"title": "b"}])
    assert set(again.values()).isdisjoint(exports.values())  # never overwrites a run


def test_old_config_files_with_settings_that_never_worked_still_load():
    config = CrawlConfig.from_dict({
        "personal_data_fields": ["email"],
        "compliance": {"respect_paywalls": True, "takedown_email": "x@y.test"},
        "storage": {"use_s3": False},
        "browser": {"javascript_enabled": True},
    })

    assert config.compliance.public_only is True


def test_etag_cache_returns_conditional_headers(tmp_path):
    cache = ETagCache(str(tmp_path / "e.db"))
    cache.update("http://x", {"etag": '"v1"', "last-modified": "yesterday"}, "body")

    assert cache.get_headers("http://x") == {"If-None-Match": '"v1"', "If-Modified-Since": "yesterday"}
    assert cache.get_content_hash("http://x") is not None
    assert cache.get_headers("http://other") == {}


def test_cli_warns_when_the_shell_rewrote_a_pattern(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["--urls", "https://a.test/", "--include", "C:/Program Files/Git/shop/"])

    cli.build_config(args, parser)

    assert "rewritten by Git Bash" in capsys.readouterr().err


def test_cli_passes_ordinary_patterns_quietly(capsys):
    parser = cli.build_parser()
    cli.build_config(parser.parse_args(["--urls", "https://a.test/", "--include", "/shop/"]), parser)

    assert capsys.readouterr().err == ""
