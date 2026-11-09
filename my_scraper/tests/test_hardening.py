"""Gaps found by reviewing each code path, not by a site failing. Each once went unnoticed."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from aiohttp import web

from my_scraper.config.settings import CrawlConfig, FetcherType
from my_scraper.core.crawler import ProductionCrawler
from my_scraper.core.page_checks import CONSENT, classify_page
from my_scraper.fetcher.browser import BrowserUnavailable
from my_scraper.fetcher.discovery import api_next_links
from my_scraper.fetcher.smart_fetcher import SmartFetcher
from my_scraper.parser.auto_extractor import extract_items
from my_scraper.parser.by_example import learn_selectors
from my_scraper.parser.documents import parse_csv
from my_scraper.parser.universal_parser import UniversalParser, load_json
from my_scraper.storage.multi_format import SQLiteExporter
from my_scraper.tests.conftest import FILLER, html_page
from my_scraper.utils.http import create_session
from my_scraper.utils.politeness import RobotsChecker
from my_scraper.utils.retry import RetryManager
from my_scraper.utils.urls import domain_key, is_trap, normalize_url, site_key

REPO_ROOT = Path(__file__).resolve().parents[2]
URL = "https://site.test/page"
HEADLINE = "A properly long headline about something that happened"


def run(config, **kwargs):
    crawler = ProductionCrawler(config)
    return crawler, asyncio.run(crawler.crawl(**kwargs))


def rendered_result(url, html):
    return {"url": url, "final_url": url, "status_code": 200, "content": html,
            "headers": {"content-type": "text/html"}, "fetcher_type": "browser"}


def auto_config(make_config, **overrides):
    config = make_config(**overrides)
    config.selectors = {}
    config.fetcher = FetcherType.AUTO
    return config


# --- Selectors on JavaScript sites -------------------------------------------

def test_selectors_that_match_nothing_try_the_browser(site, make_config):
    """A page with no matches still returns a row of empty fields, which must not count."""
    site.add_raw("/", f"<html><body>{'word ' * 60}</body></html>", "text/html")

    config = make_config(max_depth=0)
    config.fetcher = FetcherType.AUTO
    config.selectors = {"title": "h1::text"}
    crawler = ProductionCrawler(config)
    renders = []

    async def render(url):
        renders.append(url)
        return rendered_result(url, f"<html><body><h1>Rendered</h1>{FILLER}</body></html>")

    crawler.fetcher.browser_fetcher.fetch = render
    results = asyncio.run(crawler.crawl())

    assert len(renders) == 1
    assert results[0]["title"] == "Rendered"


def test_examples_are_learned_from_the_rendered_page(site, make_config):
    site.add_raw("/", f"<html><body>{'word ' * 60}</body></html>", "text/html")
    cards = "".join(f'<div class="c"><h3>Name {i}</h3><b>${i}.00</b></div>' for i in range(4))

    config = auto_config(make_config, max_depth=0, examples={"name": "Name 0", "price": "$0.00"})
    crawler = ProductionCrawler(config)

    async def render(url):
        return rendered_result(url, f"<html><body>{cards}</body></html>")

    crawler.fetcher.browser_fetcher.fetch = render
    results = asyncio.run(crawler.crawl())

    assert sorted(item["name"] for item in results) == [f"Name {i}" for i in range(4)]


# --- Domains -----------------------------------------------------------------

def test_international_domains_stay_in_scope():
    assert site_key("xn--mnchen-3ya.de") == domain_key("münchen.de")
    assert normalize_url("https://münchen.de/").startswith("https://xn--mnchen-3ya.de")


def test_domain_key_keeps_ports_and_drops_www():
    assert domain_key("www.Example.com") == "example.com"
    assert domain_key("127.0.0.1:8080") == "127.0.0.1:8080"


# --- Repeat runs -------------------------------------------------------------

def test_incremental_recognises_unchanged_pages_in_other_encodings(site, make_config):
    body = f'<html><head><meta charset="windows-1251"></head><body><h1>Новости</h1>{FILLER}</body></html>'
    site.add_raw("/", body.encode("windows-1251"), "text/html")

    run(make_config(incremental=True))
    _, second = run(make_config(incremental=True))

    assert second == []


# --- Traps -------------------------------------------------------------------

def test_calendars_far_in_the_future_are_traps():
    future = date.today().year + 10

    assert is_trap(f"https://x.com/events/{future}/03/")
    assert is_trap(f"https://x.com/cal?year={future}&month=2")
    assert not is_trap(f"https://x.com/events/{date.today().year}/03/")
    assert not is_trap("https://x.com/products/2099")


def test_pagination_stops_when_pages_add_nothing(site, make_config):
    async def endless(request):
        number = int(request.path.split("/")[-1])
        same = "".join(f'<div class="s"><h3><a href="/item/{i}/detail">{HEADLINE} {i}</a></h3></div>' for i in range(4))
        return web.Response(text=f"<html><body><!-- {number} -->{same}<a rel='next' href='/list/{number + 1}'>n</a></body></html>",
                            content_type="text/html")

    for number in range(1, 40):
        site.pages[f"/list/{number}"] = endless

    config = make_config(max_depth=0)
    config.selectors = {}
    config.start_urls = [site.url + "/list/1"]
    run(config)

    assert sum(count for path, count in site.hits.items() if path.startswith("/list/")) == 2


# --- Reading APIs ------------------------------------------------------------

@pytest.mark.parametrize("text", [
    ')]}\',\n{"a": 1}',
    'while(1);{"a": 1}',
    'callback({"a": 1});',
    'window.handle({"a": 1})',
])
def test_guarded_and_wrapped_json(text):
    assert load_json(text) == {"a": 1}


def test_one_record_per_line_json():
    page = UniversalParser().parse_page({"url": "https://x.com/d.ndjson", "content": '{"a": 1}\n{"a": 2}\n{"a": 3}\n'})

    assert [item["a"] for item in page.items] == [1, 2, 3]


@pytest.mark.parametrize("headers, body, expected", [
    ({"link": '<https://x.com/p?page=2>; rel="next", <https://x.com/p?page=9>; rel="last"'}, "", ["https://x.com/p?page=2"]),
    ({"content-type": "application/json"}, '{"next": "https://x.com/p2", "results": []}', ["https://x.com/p2"]),
    ({"content-type": "application/json"}, '{"links": {"next": "/p2"}}', ["/p2"]),
    ({"content-type": "application/json"}, '{"_links": {"next": {"href": "/p2"}}}', ["/p2"]),
    ({"content-type": "application/json"}, '{"paging": {"next": null}}', []),
    ({"content-type": "application/json"}, '{"next": true}', []),
])
def test_api_next_links(headers, body, expected):
    assert api_next_links(headers, body) == expected


def test_api_pagination_through_the_crawler(site, make_config):
    async def first(request):
        return web.json_response({"next": site.url + "/api?page=2", "results": [{"id": i, "name": f"a{i}"} for i in range(3)]})

    async def second(request):
        return web.json_response({"next": None, "results": [{"id": i, "name": f"b{i}"} for i in range(3)]})

    site.pages["/api"] = first
    site.pages["/api?page=2"] = second

    config = make_config(max_depth=0)
    config.selectors = {}
    config.start_urls = [site.url + "/api"]
    _, results = run(config)

    assert len(results) == 6


def test_json_response_does_not_break_the_crawl(site, make_config):
    site.add_raw("/", '{"a": 1}', "application/json")
    config = make_config()
    config.selectors = {}

    run(config)

    statuses = [json.loads(line)["status"] for line in Path("logs/audit.log").read_text(encoding="utf-8").splitlines()]
    assert statuses == ["success"]


# --- Limits ------------------------------------------------------------------

def test_huge_robots_txt_is_cut_off_safely(site):
    rules = "User-agent: *\nDisallow: /private\n" + "# padding\n" * 100_000
    site.add_raw("/robots.txt", rules, "text/plain")

    checker = RobotsChecker("TestBot/1.0")

    assert asyncio.run(checker.is_allowed(site.url + "/private")) is False
    assert asyncio.run(checker.is_allowed(site.url + "/open")) is True


def test_csv_with_a_very_long_cell():
    assert len(parse_csv("a,b\n" + "x" * 500_000 + ",1\n")[0]["a"]) == 500_000


def test_spreadsheet_dates_export_to_sqlite():
    path = SQLiteExporter("out").export([{"when": datetime(2026, 1, 2)}])

    assert Path(path).stat().st_size > 0


def test_proxies_from_the_environment_are_honoured():
    async def check():
        session = create_session()
        try:
            return session.trust_env
        finally:
            await session.close()

    assert asyncio.run(check()) is True


# --- A missing browser -------------------------------------------------------

def test_missing_browser_is_not_retried_and_the_crawl_carries_on(site, make_config):
    site.add("/", "home", links=["/a", "/b"])
    site.add_raw("/a", f"<html><body>{'first ' * 60}</body></html>", "text/html")
    site.add_raw("/b", f"<html><body>{'second ' * 60}</body></html>", "text/html")

    config = auto_config(make_config)
    crawler = ProductionCrawler(config)
    attempts = []

    async def broken(url):
        attempts.append(url)
        raise BrowserUnavailable("no browser here")

    crawler.fetcher.browser_fetcher.fetch = broken
    results = asyncio.run(crawler.crawl())

    assert len(attempts) == 1
    assert crawler.fetcher.fallback_enabled is False
    assert len(results) == 3


def test_browser_unavailable_is_a_permanent_error():
    calls = []

    async def fetch(url):
        calls.append(url)
        raise BrowserUnavailable("no")

    config = CrawlConfig()
    config.retry.base_delay = 0.001
    with pytest.raises(BrowserUnavailable):
        asyncio.run(RetryManager(config).retry_http(fetch, "http://x/"))

    assert len(calls) == 1


def test_forced_browser_mode_reports_a_missing_browser():
    fetcher = SmartFetcher(CrawlConfig(fetcher=FetcherType.BROWSER))

    async def broken(url):
        raise BrowserUnavailable("no browser here")

    fetcher.browser_fetcher.fetch = broken

    with pytest.raises(BrowserUnavailable):
        asyncio.run(fetcher.fetch("http://x/"))


# --- Odd pages ---------------------------------------------------------------

def test_picture_only_cards_use_their_alt_text():
    cards = "".join(f'<div class="c"><a href="/gallery/photo-{i}"><img src="/{i}.jpg" alt="Sunset over the bay {i}"></a></div>'
                    for i in range(4))

    items = extract_items(f"<html><body>{cards}</body></html>", URL)

    assert items[0]["title"] == "Sunset over the bay 0"
    assert items[0]["url"] == "https://site.test/gallery/photo-0"


def test_a_story_in_two_sections_appears_once():
    def card(css, number):
        return f'<div class="{css}"><h3><a href="/news/2026/{number}">{HEADLINE} {number}</a></h3><p>Details here for you.</p></div>'

    top = "".join(card("top", i) for i in range(4))
    more = "".join(card("more", i) for i in range(2, 7))

    assert len(extract_items(f"<html><body><section>{top}</section><section>{more}</section></body></html>", URL)) == 7


def test_age_gate_is_recognised():
    html = "<html><body><h1>Are you over 18?</h1><p>Please confirm your age to continue.</p></body></html>"

    assert classify_page({"url": URL, "final_url": URL, "content": html, "headers": {"content-type": "text/html"}}) == CONSENT


# --- Learning from examples on awkward pages ---------------------------------

def bootstrap_page():
    cards = "".join(
        f'<div class="col-md-4 col-xl-4 col-lg-4"><div class="card thumbnail"><div class="product-wrapper card-body">'
        f'<div class="caption"><h4 class="price">${i}.99</h4><h4><a class="title" href="/p/{i}">Laptop {i}</a></h4></div>'
        f"</div></div></div>" for i in range(6))
    return f'<html><body><div class="container"><div class="row">{cards}</div></div></body></html>'


def test_learning_keeps_classes_with_digits_when_all_rows_share_them():
    item, fields = learn_selectors(bootstrap_page(), {"title": "Laptop 0", "price": "$0.99"})

    page = UniversalParser().parse_page({"url": URL, "content": bootstrap_page()}, fields, item)

    assert len(page.items) == 6
    assert page.items[5] == {"title": "Laptop 5", "price": "$5.99"}


def test_blank_text_before_an_icon_is_skipped():
    html = '<div class="c"><h3 class="n">\n  <i class="flag"></i> Andorra </h3></div>'

    page = UniversalParser().parse_page({"url": URL, "content": html}, {"name": "h3.n::text", "all[]": "h3.n::text"}, ".c")

    assert page.items == [{"name": "Andorra", "all": ["Andorra"]}]


def test_learning_a_value_split_by_inline_tags():
    rows = "".join(f'<div class="r"><span class="label"><b>Title:</b> Paper {i}</span><i>{i}</i></div>' for i in range(4))
    html = f"<html><body>{rows}</body></html>"

    item, fields = learn_selectors(html, {"title": "Title: Paper 0"})

    items = UniversalParser().parse_page({"url": URL, "content": html}, fields, item).items
    assert len(items) == 4
    assert items[3] == {"title": "Title: Paper 3"}


def test_a_spaced_text_selector_keeps_the_whole_text_of_the_first_match():
    html = '<div class="c"><h4 class="p">$1</h4><h4><a href="/x">Laptop <b>Pro</b></a></h4></div>'

    page = UniversalParser().parse_page({"url": URL, "content": html},
                                        {"first": "h4 ::text", "name": "h4 a ::text", "own": "h4 a::text"}, ".c")

    assert page.items == [{"first": "$1", "name": "Laptop Pro", "own": "Laptop"}]


def test_learning_refuses_an_empty_example():
    assert learn_selectors(bootstrap_page(), {"title": "Laptop 0", "price": ""}) is None


def test_learning_refuses_examples_that_are_not_in_one_repeating_block():
    html = ("<html><body><div class='a'><p>Alpha</p></div><div class='b'><p>Beta</p></div>"
            "<div class='c'><p>Gamma</p></div></body></html>")

    assert learn_selectors(html, {"first": "Alpha", "second": "Gamma"}) is None


def test_learning_picks_the_selector_that_fills_the_most_rows():
    rows = "".join(f'<li class="row"><span class="n">Name {i}</span><span class="v">{i}</span></li>' for i in range(5))
    html = f'<html><body><ul class="outer"><li class="wrap"><ul>{rows}</ul></li><li class="wrap">x</li></ul></body></html>'

    item, fields = learn_selectors(html, {"name": "Name 0", "value": "0"})

    assert len(UniversalParser().parse_page({"url": URL, "content": html}, fields, item).items) == 5


# --- The command line --------------------------------------------------------

def cli(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONIOENCODING", None)
    return subprocess.run([sys.executable, "-m", "my_scraper.cli.main", *map(str, args)], env=env,
                          capture_output=True, timeout=60)


def test_summary_survives_a_console_that_cannot_show_the_text(site):
    site.add_raw("/", "<html><head><title>こんにちは</title></head><body><h1>こんにちは世界</h1>" + FILLER + "</body></html>", "text/html")

    result = cli("--urls", site.url + "/", "--no-browser", "--explain")

    assert result.returncode == 0, result.stderr[-400:]
    assert b"Plain HTTP: HTTP 200" in result.stdout


def test_explain_shows_each_method(site):
    cards = "".join(f'<div class="c"><h3><a href="/p/{i}/x">{HEADLINE} {i}</a></h3></div>' for i in range(4))
    site.add_raw("/", f"<html><body>{cards}</body></html>", "text/html")

    output = cli("--urls", site.url + "/", "--no-browser", "--explain").stdout.decode("utf-8", "replace")

    for method in ("structured", "list", "embedded", "document"):
        assert method in output
    assert "chosen: 4 item(s)" in output


def test_explain_reports_a_refusal(site):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /\n", "text/plain")

    output = cli("--urls", site.url + "/", "--no-browser", "--explain").stdout.decode("utf-8", "replace")

    assert "robots_disallowed" in output


def test_quiet_keeps_logs_off_the_console(site):
    site.add("/", "home")

    output = cli("--urls", site.url + "/", "--no-browser", "--quiet", "--selector", "t=h1::text").stdout.decode("utf-8", "replace")

    assert '"timestamp"' not in output
    assert "Extracted 1 items" in output


def test_summary_suggests_explain_when_nothing_came_back(site):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /\n", "text/plain")

    output = cli("--urls", site.url + "/", "--no-browser", "--quiet").stdout.decode("utf-8", "replace")

    assert "--explain" in output


def test_contradictory_browser_options():
    assert b"contradict" in cli("--urls", "http://x.test/", "--use-browser", "--no-browser").stderr


def test_learned_selectors_are_printed_for_reuse(site):
    cards = "".join(f'<div class="c"><h3>Name {i}</h3><b>${i}.00</b></div>' for i in range(4))
    site.add_raw("/", f"<html><body>{cards}</body></html>", "text/html")

    output = cli("--urls", site.url + "/", "--no-browser", "--quiet", "--max-depth", "0", "--example", "name=Name 0",
                 "--example", "price=$0.00").stdout.decode("utf-8", "replace")

    assert "Extracted 4 items" in output
    assert "learned, reuse with:" in output


def test_unwritable_output_folder_is_a_clear_error(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x", encoding="utf-8")

    result = cli("--urls", "http://x.test/", "--output-dir", blocker / "out")

    assert result.returncode == 1
    assert b"Error:" in result.stderr
    assert b"Traceback" not in result.stderr


def test_html_page_helper_still_used():
    assert "lorem" in html_page("x")


def test_one_long_item_does_not_hide_a_list():
    # quotes.toscrape.com/page/2/: one quote is a third of the page, and the list vanished.
    from my_scraper.parser.auto_extractor import extract_items

    lengths = [40, 45, 50, 55, 60, 70, 90, 200, 450, 1300]
    quotes = "".join(
        f'<div class="quote"><span class="text">{"word " * (n // 5)}</span>'
        f'<small class="author">Author {i}</small> <a href="/author/{i}">(about)</a>'
        f'<div class="tags"><a class="tag" href="/tag/t{i}/">t{i}</a></div></div>'
        for i, n in enumerate(lengths))
    html = f"<html><body><h1>Quotes</h1>{quotes}</body></html>"

    items = extract_items(html, "https://quotes.example/page/2/")

    assert len(items) == 10
    assert {item["source"] for item in items} == {"list"}
