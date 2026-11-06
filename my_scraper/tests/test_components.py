"""Unit tests for politeness, retry, fetching, parsing, compliance and storage."""
import asyncio
import csv
import json
import time
from pathlib import Path

import aiohttp
import pytest

from my_scraper.compliance.manager import ComplianceManager
from my_scraper.config.settings import CrawlConfig, ExportFormat, FetcherType
from my_scraper.fetcher.smart_fetcher import SmartFetcher, StaticFetcher
from my_scraper.parser.universal_parser import ContentTypeDetector, UniversalParser
from my_scraper.storage.multi_format import CSVExporter
from my_scraper.utils.politeness import RateLimiter, RobotsChecker
from my_scraper.utils.retry import HttpStatusError, RetryManager


# --- Rate limiting -----------------------------------------------------------

def test_rate_limiter_accepts_rates_below_one_per_second():
    asyncio.run(RateLimiter(0.5).acquire("example.com"))


def test_rate_limiter_spaces_requests():
    async def three_requests():
        limiter = RateLimiter(20)  # 50 ms apart
        start = time.monotonic()
        await asyncio.gather(*(limiter.acquire("d") for _ in range(3)))
        return time.monotonic() - start

    assert asyncio.run(three_requests()) >= 0.1


def test_rate_limiter_wait_on_one_domain_does_not_block_another():
    async def scenario():
        limiter = RateLimiter(1)
        await limiter.acquire("slow")
        blocked = asyncio.create_task(limiter.acquire("slow"))  # waits about 1 s
        await asyncio.sleep(0)
        start = time.monotonic()
        await limiter.acquire("fast")
        elapsed = time.monotonic() - start
        blocked.cancel()
        return elapsed

    assert asyncio.run(scenario()) < 0.2


def test_backoff_is_per_domain_and_recovers():
    limiter = RateLimiter(10)
    limiter.backoff_on_error("busy", 429)

    assert limiter.interval_for("busy") == pytest.approx(0.2)
    assert limiter.interval_for("other") == pytest.approx(0.1)

    for _ in range(10):
        limiter.recover("busy")
    assert limiter.interval_for("busy") == pytest.approx(0.1)


def test_backoff_ignores_other_errors():
    limiter = RateLimiter(10)
    limiter.backoff_on_error("d", 500)
    assert limiter.interval_for("d") == pytest.approx(0.1)


# --- robots.txt --------------------------------------------------------------

def check_robots(url):
    return asyncio.run(RobotsChecker("TestBot/1.0").is_allowed(url))


def test_missing_robots_allows_crawling(site):
    assert check_robots(site.url + "/page") is True


def test_robots_server_error_disallows_crawling(site):
    site.add_raw("/robots.txt", "oops", "text/plain", status=500)
    assert check_robots(site.url + "/page") is False


def test_unreachable_robots_disallows_crawling():
    assert check_robots("http://127.0.0.1:1/page") is False


def test_robots_is_fetched_once_for_concurrent_checks(site):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /x\n", "text/plain")

    async def many():
        checker = RobotsChecker("TestBot/1.0")
        return await asyncio.gather(*(checker.is_allowed(site.url + f"/p{i}") for i in range(10)))

    assert all(asyncio.run(many()))
    assert site.hits["/robots.txt"] == 1


# --- Retry -------------------------------------------------------------------

def retry_manager(**retry_settings):
    config = CrawlConfig()
    config.retry.base_delay = 0.001
    for key, value in retry_settings.items():
        setattr(config.retry, key, value)
    return RetryManager(config)


def test_exceptions_get_one_attempt_plus_max_retries():
    calls = []

    async def always_fails(url):
        calls.append(url)
        raise ConnectionError("down")

    manager = retry_manager(max_retries=3)
    with pytest.raises(ConnectionError):
        asyncio.run(manager.retry_http(always_fails, "http://x/"))

    assert len(calls) == 4
    entries = manager.dlq.get_all()
    assert len(entries) == 1
    assert entries[0]["attempts"] == 4
    assert entries[0]["reason"].startswith("Max retries exceeded")


def test_retry_on_exceptions_setting_is_honoured():
    calls = []

    async def always_fails(url):
        calls.append(url)
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        asyncio.run(retry_manager(retry_on_exceptions=False).retry_http(always_fails, "http://x/"))

    assert len(calls) == 1


def test_retryable_status_is_retried_and_logged_once():
    statuses = []

    async def unavailable(url):
        return {"status_code": 503}

    manager = retry_manager(max_retries=2)
    with pytest.raises(HttpStatusError) as caught:
        asyncio.run(manager.retry_http(unavailable, "http://x/", on_status=statuses.append))

    assert caught.value.status == 503
    assert statuses == [503, 503, 503]
    assert len(manager.dlq.get_all()) == 1


def test_non_retryable_status_is_returned_immediately():
    calls = []

    async def not_found(url):
        calls.append(url)
        return {"status_code": 404}

    response = asyncio.run(retry_manager().retry_http(not_found, "http://x/"))

    assert response["status_code"] == 404
    assert len(calls) == 1


# --- Fetching ----------------------------------------------------------------

def fetcher_for(mode):
    config = CrawlConfig(fetcher=mode)
    fetcher = SmartFetcher(config)
    browser_calls = []

    async def fake_browser(url):
        browser_calls.append(url)
        return {"url": url, "status_code": 200, "content": "rendered", "headers": {}}

    fetcher.browser_fetcher.fetch = fake_browser
    return fetcher, browser_calls


def fetch(fetcher, url):
    async def go():
        try:
            return await fetcher.fetch(url)
        finally:
            await fetcher.close()

    return asyncio.run(go())


def test_response_header_names_are_lowercased(site):
    site.add_raw("/api", '{"k": "v"}', "application/json")

    async def go():
        fetcher = StaticFetcher()
        result = await fetcher.fetch(site.url + "/api")
        await fetcher.close()
        return result

    assert "content-type" in asyncio.run(go())["headers"]


def test_undecodable_bytes_do_not_fail_the_fetch(site):
    site.add_raw("/latin", "café".encode("latin-1"), "text/html; charset=utf-8")

    async def go():
        fetcher = StaticFetcher()
        result = await fetcher.fetch(site.url + "/latin")
        await fetcher.close()
        return result

    assert asyncio.run(go())["status_code"] == 200


def test_static_mode_never_uses_the_browser():
    fetcher, browser_calls = fetcher_for(FetcherType.STATIC)

    with pytest.raises(aiohttp.ClientError):
        fetch(fetcher, "http://127.0.0.1:1/")

    assert browser_calls == []


def test_auto_mode_renders_sparse_html(site):
    site.add_raw("/spa", '<html><body><div id="root"></div></body></html>', "text/html")
    fetcher, browser_calls = fetcher_for(FetcherType.AUTO)

    result = fetch(fetcher, site.url + "/spa")

    assert result["content"] == "rendered"
    assert browser_calls == [site.url + "/spa"]


def test_auto_mode_skips_browser_when_disabled(site):
    site.add_raw("/spa", "<html><body></body></html>", "text/html")
    fetcher, browser_calls = fetcher_for(FetcherType.AUTO)
    fetcher.config.browser.enabled = False

    fetch(fetcher, site.url + "/spa")

    assert browser_calls == []


def test_auto_mode_leaves_json_and_error_pages_alone(site):
    site.add_raw("/api", '{"k": "v"}', "application/json")
    site.add("/missing", status=404)
    site.add_raw("/tiny404", "<html></html>", "text/html", status=404)
    fetcher, browser_calls = fetcher_for(FetcherType.AUTO)

    fetch(fetcher, site.url + "/api")
    fetch(fetcher, site.url + "/tiny404")

    assert browser_calls == []


@pytest.mark.parametrize("content, sparse", [
    ("", True),
    ('<html><body><div id="root"></div><script>' + "x" * 5000 + "</script></body></html>", True),
    ('{"k": "v"}', False),
    ("<html><body><noscript>enable javascript</noscript><p class='a'>" + "real text " * 50 + "</p></body></html>", False),
])
def test_is_content_sparse(content, sparse):
    assert SmartFetcher(CrawlConfig()).is_content_sparse(content) is sparse


# --- Parsing -----------------------------------------------------------------

def test_detects_content_type_from_header():
    assert ContentTypeDetector.detect("x", {"content-type": "application/json"}) == "json"
    assert ContentTypeDetector.detect("x", {"content-type": "application/pdf"}) == "pdf"
    assert ContentTypeDetector.detect("<p>x</p>", {"content-type": "text/html"}) == "html"


def test_parses_json_responses():
    parsed = UniversalParser().parse(
        {"url": "u", "content": "[1, 2]", "headers": {"content-type": "application/json"}})
    assert parsed.data == {"data": [1, 2]}


def test_parses_css_and_xpath_selectors():
    parsed = UniversalParser().parse(
        {"url": "u", "content": "<h1> Hi </h1><p>there</p>"},
        {"css": "h1::text", "xpath": "//p/text()", "missing": ".nope::text"},
    )
    assert parsed.data == {"css": "Hi", "xpath": "there", "missing": None}


def test_bad_selector_is_reported_not_raised():
    parsed = UniversalParser().parse({"url": "u", "content": "<p>x</p>"}, {"bad": "a[[["})
    assert parsed.data == {"bad": None}
    assert len(parsed.errors) == 1


def test_pdf_receives_raw_bytes_and_failure_is_reported():
    parser = UniversalParser()
    received = []

    def fake_parse(body):
        received.append(body)
        raise ValueError("damaged")

    parser.pdf_parser.parse = fake_parse
    parsed = parser.parse({
        "url": "u", "content": "garbled", "body": b"%PDF-\xff",
        "headers": {"content-type": "application/pdf"},
    })

    assert received == [b"%PDF-\xff"]
    assert parsed.data == {}
    assert "damaged" in parsed.errors[0]


# --- Compliance --------------------------------------------------------------

@pytest.fixture
def compliance():
    return ComplianceManager(CrawlConfig())


@pytest.mark.parametrize("url", [
    "https://quotes.toscrape.com/author/Albert-Einstein",
    "https://x.com/accounting/report",
    "https://x.com/premium-coffee",
    "https://x.com/page?next=/login",
])
def test_public_urls_are_allowed(compliance, url):
    assert compliance.validate_url(url) is True


@pytest.mark.parametrize("url", [
    "https://x.com/login",
    "https://x.com/LOGIN/",
    "https://x.com/login.php",
    "https://x.com/auth/callback",
    "https://x.com/oauth/token",
    "https://x.com/users/account/settings",
    "https://x.com/accounts",
    "https://x.com/subscribe",
    "https://x.com/premium/article",
])
def test_private_urls_are_blocked(compliance, url):
    with pytest.raises(ValueError):
        compliance.validate_url(url)


def test_takedown_blocks_domain_and_subdomains_across_restarts(compliance):
    compliance.handle_takedown_request("Blocked.example", "complaint")

    restarted = ComplianceManager(CrawlConfig())
    for url in ("https://blocked.example/x", "https://www.blocked.example/x"):
        with pytest.raises(ValueError):
            restarted.validate_url(url)
    assert restarted.validate_url("https://notblocked.example/x") is True


def test_takedown_applies_even_when_public_only_is_off(compliance):
    compliance.handle_takedown_request("blocked.example", "complaint")
    compliance.config.compliance.public_only = False

    with pytest.raises(ValueError):
        compliance.validate_url("https://blocked.example/x")


def test_pii_scan_does_not_flag_ordinary_text(compliance):
    data = {"title": "x", "price": "12.99", "upc": "a897fe39b1053632",
            "description": "a story of birth and the zip code of the heart"}
    assert compliance.scan_for_personal_data(data) == []


@pytest.mark.parametrize("data, expected", [
    ({"contact": "jane@corp.com", "title": "x"}, ["contact"]),
    ({"note": "call (555) 123-4567"}, ["note"]),
    ({"note": "call +44 20 7946 0958"}, ["note"]),
    ({"note": "SSN 123-45-6789"}, ["note"]),
    ({"email": None, "title": "x"}, ["email"]),
    ({"nested": {"who": "jane@corp.com"}}, ["nested"]),
])
def test_pii_scan_flags_only_the_fields_involved(compliance, data, expected):
    assert compliance.scan_for_personal_data(data) == expected


def test_audit_events_keep_errors_and_have_unique_ids(compliance):
    compliance.log_scrape_event("http://x/", "failed", error="boom")
    compliance.log_scrape_event("http://x/", "failed", error="boom")

    events = [json.loads(line) for line in Path("logs/audit.log").read_text(encoding="utf-8").splitlines()]
    assert events[0]["error"] == "boom"
    assert events[0]["event_id"] != events[1]["event_id"]


# --- Config and storage ------------------------------------------------------

def test_config_from_dict_builds_sections_and_enums():
    config = CrawlConfig.from_dict({
        "rate_limit": {"requests_per_second": 2},
        "fetcher": "static",
        "export_formats": ["csv", "sqlite"],
    })

    assert config.rate_limit.requests_per_second == 2
    assert config.fetcher is FetcherType.STATIC
    assert config.export_formats == [ExportFormat.CSV, ExportFormat.SQLITE]
    assert config.retry.max_retries == 3


def test_config_from_dict_rejects_typos():
    with pytest.raises(TypeError):
        CrawlConfig.from_dict({"max_page": 5})


def test_csv_keeps_nested_values_as_json_and_handles_uneven_items():
    path = CSVExporter("out").export([{"a": {"x": 1}}, {"b": [1, 2]}])

    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert json.loads(rows[0]["a"]) == {"x": 1}
    assert json.loads(rows[1]["b"]) == [1, 2]


def test_csv_with_no_items_writes_nothing():
    assert CSVExporter("out").export([]) is None
