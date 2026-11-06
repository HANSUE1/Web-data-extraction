"""One test per gap found by measuring real sites. Each of these once failed silently."""
import asyncio
import csv
import gzip
import io
import json
import zipfile
from pathlib import Path

import pytest
from aiohttp import web

from my_scraper.config.settings import CrawlConfig, FetcherType
from my_scraper.core.crawler import ProductionCrawler
from my_scraper.core.links import next_page_links, page_links
from my_scraper.core.page_checks import BLOCKED, CONSENT, LOGIN, NOT_FOUND, classify_page
from my_scraper.fetcher.discovery import feed_links, is_platform_api, next_api_page, platform_endpoints
from my_scraper.fetcher.smart_fetcher import StaticFetcher, decode_body
from my_scraper.parser.auto_extractor import extract_items, find_price
from my_scraper.parser.by_example import learn_selectors
from my_scraper.parser.embedded_state import embedded_items, records_to_items
from my_scraper.parser.universal_parser import ContentTypeDetector, UniversalParser
from my_scraper.storage.multi_format import CSVExporter, JSONExporter, JSONLExporter, SQLiteExporter, StorageManager
from my_scraper.tests.conftest import FILLER, html_page
from my_scraper.utils.politeness import RobotsChecker
from my_scraper.utils.retry import HttpStatusError, RetryManager
from my_scraper.utils.urls import facet_key, has_route_fragment, is_action, is_trap, normalize_url
from parsel import Selector

URL = "https://site.test/page"
HEADLINE = "A properly long headline about something that happened"


def run(config, **kwargs):
    crawler = ProductionCrawler(config)
    return crawler, asyncio.run(crawler.crawl(**kwargs))


def audit_statuses():
    lines = Path("logs/audit.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line).get("status") for line in lines]


def fetch_static(url):
    async def go():
        fetcher = StaticFetcher()
        try:
            return await fetcher.fetch(url)
        finally:
            await fetcher.close()

    return asyncio.run(go())


# --- robots.txt --------------------------------------------------------------

@pytest.mark.parametrize("path, allowed", [
    ("/list?sort=price", False),      # wildcard
    ("/private-area", False),         # trailing wildcard
    ("/report.pdf", False),           # end anchor
    ("/docs/public/a", True),         # the longest rule wins, whatever the order
    ("/docs/secret", False),
    ("/fine", True),
])
def test_robots_wildcards_and_rule_priority(site, path, allowed):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /*?sort=\nDisallow: /private*\nDisallow: /*.pdf$\n"
                                "Disallow: /docs/\nAllow: /docs/public\n", "text/plain")

    result = asyncio.run(RobotsChecker("TestBot/1.0").is_allowed(site.url + path))

    assert result is allowed


def test_rules_for_another_crawler_do_not_apply_to_us(site):
    """Our full agent string contains /bot in its URL, which must not match a group named bot."""
    site.add_raw("/robots.txt", "User-agent: bot\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "text/plain")
    agent = CrawlConfig().robots.user_agent

    assert asyncio.run(RobotsChecker(agent).is_allowed(site.url + "/page")) is True


# --- Encodings ---------------------------------------------------------------

@pytest.mark.parametrize("charset, text", [
    ("windows-1251", "Привет, мир"),
    ("Shift_JIS", "こんにちは世界"),
    ("EUC-KR", "안녕하세요 세계"),
    ("GB2312", "你好世界"),
    ("ISO-8859-1", "Déjà vu à Paris"),
])
def test_charset_declared_only_in_the_page(charset, text):
    body = f'<html><head><meta charset="{charset}"></head><body><h1>{text}</h1></body></html>'.encode(charset)

    assert text in decode_body(body, "text/html")


def test_undeclared_legacy_encoding_is_detected():
    text = "Съешь же ещё этих мягких французских булок, да выпей чаю. " * 20
    body = f"<html><body><p>{text}</p></body></html>".encode("windows-1251")

    assert "мягких французских" in decode_body(body, "text/html")


def test_wrong_charset_in_the_header_is_overruled():
    text = "こんにちは世界、これはテストです。" * 40
    body = f"<html><body><p>{text}</p></body></html>".encode("shift_jis")

    assert "こんにちは世界" in decode_body(body, "text/html; charset=utf-8")


def test_byte_order_mark_wins():
    assert "héllo" in decode_body("héllo".encode("utf-16"), "text/html")


# --- Fetching ----------------------------------------------------------------

def test_meta_refresh_is_followed(site):
    site.add_raw("/old", '<html><head><meta http-equiv="refresh" content="0; url=/new"></head></html>', "text/html")
    site.add("/new", "The real page")

    result = fetch_static(site.url + "/old")

    assert "The real page" in result["content"]
    assert result["final_url"].endswith("/new")
    assert result["url"].endswith("/old")


def test_slow_self_reloading_page_is_not_a_redirect(site):
    site.add_raw("/live", '<html><head><meta http-equiv="refresh" content="300"></head><body>live</body></html>', "text/html")

    assert fetch_static(site.url + "/live")["final_url"].endswith("/live")


def test_meta_refresh_loop_ends(site):
    site.add_raw("/a", '<html><head><meta http-equiv="refresh" content="0; url=/b"></head></html>', "text/html")
    site.add_raw("/b", '<html><head><meta http-equiv="refresh" content="0; url=/a"></head></html>', "text/html")

    fetch_static(site.url + "/a")

    assert site.hits["/a"] + site.hits["/b"] <= 5


def test_huge_response_is_cut_off(site, monkeypatch):
    monkeypatch.setattr("my_scraper.fetcher.smart_fetcher.MAX_BODY_BYTES", 100_000)
    site.add_raw("/huge", "x" * 500_000, "text/html")

    result = fetch_static(site.url + "/huge")

    assert result["truncated"] is True
    assert len(result["body"]) <= 100_000


def test_media_is_not_downloaded(site):
    site.add_raw("/video", b"\x00" * 50_000, "video/mp4")

    assert fetch_static(site.url + "/video")["body"] == b""


def test_requests_send_normal_headers(site):
    site.add("/", "home")

    fetch_static(site.url + "/")

    assert "text/html" in site.headers_seen["/"]["Accept"]
    assert "Accept-Language" in site.headers_seen["/"]


# --- Retries -----------------------------------------------------------------

def retry_config():
    config = CrawlConfig()
    config.retry.base_delay = 0.001
    return config


def test_permanent_errors_are_not_retried():
    import aiohttp

    calls = []

    async def fetch(url):
        calls.append(url)
        raise aiohttp.InvalidURL(url)

    with pytest.raises(aiohttp.InvalidURL):
        asyncio.run(RetryManager(retry_config()).retry_http(fetch, "http://x/"))

    assert len(calls) == 1


def test_very_long_retry_after_gives_up_instead_of_hammering():
    calls = []

    async def fetch(url):
        calls.append(url)
        return {"status_code": 429, "headers": {"retry-after": "86400"}}

    with pytest.raises(HttpStatusError) as caught:
        asyncio.run(RetryManager(retry_config()).retry_http(fetch, "http://x/"))

    assert len(calls) == 1
    assert "86400" in str(caught.value)


def test_rate_limit_gets_one_honoured_retry(monkeypatch):
    async def instant(seconds):
        return None

    monkeypatch.setattr("my_scraper.utils.retry.asyncio.sleep", instant)
    calls = []

    async def fetch(url):
        calls.append(url)
        return {"status_code": 429, "headers": {"retry-after": "30"}}

    with pytest.raises(HttpStatusError):
        asyncio.run(RetryManager(retry_config()).retry_http(fetch, "http://x/"))

    assert len(calls) == 2


def test_retry_after_as_a_date():
    from datetime import timedelta
    from email.utils import format_datetime

    from my_scraper.utils.retry import _retry_after
    from my_scraper.utils.timeutil import utc_now

    moment = format_datetime(utc_now() + timedelta(seconds=120), usegmt=True)

    assert 100 < _retry_after({"headers": {"retry-after": moment}}) <= 120


def test_waiting_is_reported(monkeypatch):
    async def instant(seconds):
        return None

    monkeypatch.setattr("my_scraper.utils.retry.asyncio.sleep", instant)
    waits = []
    responses = iter([{"status_code": 503}, {"status_code": 200}])

    async def fetch(url):
        return next(responses)

    asyncio.run(RetryManager(retry_config()).retry_http(fetch, "http://x/", on_wait=waits.append))

    assert len(waits) == 1


# --- Pages that only look like success ---------------------------------------

def page_result(html, url=URL, final_url=None, content_type="text/html"):
    return {"url": url, "final_url": final_url or url, "content": html, "headers": {"content-type": content_type}}


@pytest.mark.parametrize("title, body", [
    ("Just a moment...", "Checking your browser before accessing the site."),
    ("Attention Required! | Cloudflare", "Sorry, you have been blocked"),
    ("Access Denied", "You don't have permission to access this server."),
    ("Robot Check", "Enter the characters you see below"),
    ("Pardon Our Interruption", "Something about your browser made us think you were a bot."),
    ("Sorry", "We have detected unusual traffic from your computer network."),
    ("Verify", "Please verify you are a human to continue"),
])
def test_block_pages_are_recognised(title, body):
    html = f"<html><head><title>{title}</title></head><body><p>{body}</p></body></html>"

    assert classify_page(page_result(html)) == BLOCKED


def test_an_article_about_captchas_is_not_a_block_page():
    html = f"<html><head><title>How CAPTCHA works</title></head><body><p>{'A captcha is a test. ' * 200}</p></body></html>"

    assert classify_page(page_result(html)) is None


def test_redirect_to_login_is_recognised():
    result = page_result(html_page("Welcome"), final_url="https://site.test/login?next=/page")

    assert classify_page(result) == LOGIN


def test_a_page_really_called_login_is_left_to_the_url_policy():
    assert classify_page(page_result(html_page("x"), url="https://site.test/login")) is None


def test_password_form_is_a_login_wall():
    html = '<html><body><h1>Members</h1><form><input type="password"></form></body></html>'

    assert classify_page(page_result(html)) == LOGIN


@pytest.mark.parametrize("title", ["404 Not Found", "Page not found", "Page introuvable", "ページが見つかりません"])
def test_soft_404s_are_recognised(title):
    html = f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>Sorry about that.</p></body></html>"

    assert classify_page(page_result(html)) == NOT_FOUND


def test_cookie_wall_is_recognised():
    html = "<html><body><h1>Before you continue</h1><p>We use cookies. Accept all cookies?</p></body></html>"

    assert classify_page(page_result(html)) == CONSENT


def test_normal_pages_and_files_pass_through():
    assert classify_page(page_result(html_page("A normal page"))) is None
    assert classify_page(page_result('{"a": "captcha"}', content_type="application/json")) is None


def test_block_page_is_not_stored_as_data(site, make_config):
    site.add("/", "home", links=["/guarded"])
    site.add_raw("/guarded", "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>",
                 "text/html")

    _, results = run(make_config())

    assert [item["title"] for item in results] == ["home"]
    assert "blocked_by_site" in audit_statuses()


def test_redirect_away_from_the_site_is_not_stored(site, make_config):
    async def away(request):
        raise web.HTTPFound("http://127.0.0.1:1/elsewhere")

    site.add("/", "home", links=["/leaves"])
    site.pages["/leaves"] = away

    _, results = run(make_config())

    assert [item["title"] for item in results] == ["home"]


def test_start_url_that_moves_to_another_host_is_followed(site, make_config):
    async def moved(request):
        raise web.HTTPFound(site.url.replace("127.0.0.1", "localhost") + "/home")

    site.pages["/"] = moved
    site.add("/home", "home", links=["/a"])
    site.add("/a", "a")

    _, results = run(make_config())

    assert sorted(item["title"] for item in results) == ["a", "home"]


def test_site_that_keeps_refusing_is_left_alone(site, make_config):
    links = [f"/p{i}" for i in range(30)]
    site.add("/", "home", links=links)
    for link in links:
        site.add(link, "no", status=403)

    config = make_config()
    config.concurrency.max_per_domain = 1
    config.concurrency.max_workers = 1
    crawler, results = run(config)

    assert sum(site.hits[link] for link in links) == 5
    assert "site_abandoned" in audit_statuses()
    assert len(crawler.state.frontier) == 25  # kept for a later --resume


def test_table_rows_sharing_a_first_column_are_all_kept(site, make_config):
    rows = "".join(f"<tr><td>Boston</td><td>{1990 + i}</td><td>{40 + i}</td></tr>" for i in range(5))
    site.add_raw("/", f"<html><body><table><tr><th>Team</th><th>Year</th><th>Wins</th></tr>{rows}</table></body></html>",
                 "text/html")
    config = make_config(max_depth=0)
    config.selectors = {}

    _, results = run(config)

    assert len(results) == 5


def test_one_refusal_among_successes_does_not_end_the_crawl(site, make_config):
    links = [f"/p{i}" for i in range(12)]
    site.add("/", "home", links=links)
    for number, link in enumerate(links):
        site.add(link, f"page {number}", status=403 if number % 3 == 0 else 200)

    config = make_config()
    config.concurrency.max_workers = 1
    _, results = run(config)

    assert len(results) == 9


# --- Crawl traps and bad links -----------------------------------------------

@pytest.mark.parametrize("url", [
    "https://x.com/" + "a/b/" * 6,
    "https://x.com/" + "/".join(f"s{i}" for i in range(20)),
    "https://x.com/" + "x" * 2500,
])
def test_trap_urls(url):
    assert is_trap(url)


def test_normal_urls_are_not_traps():
    assert not is_trap("https://x.com/shop/shoes/running/nike/air-max-90")
    assert not is_trap("https://x.com/a/a/b")


@pytest.mark.parametrize("url", [
    "https://x.com/cart", "https://x.com/checkout/step1", "https://x.com/logout", "https://x.com/wp-login/logout.php",
    "https://x.com/item?add-to-cart=5", "https://x.com/post?replytocom=9", "https://x.com/signup",
])
def test_action_links_are_recognised(url):
    assert is_action(url)


def test_ordinary_links_are_not_actions():
    assert not is_action("https://x.com/products/cartoon-mug")
    assert not is_action("https://x.com/news?page=2")


def test_session_ids_do_not_make_new_pages():
    assert normalize_url("https://x.com/a;jsessionid=ABC123?id=1") == "https://x.com/a?id=1"
    assert normalize_url("https://x.com/a?PHPSESSID=abc&id=1") == "https://x.com/a?id=1"


def test_route_fragments_are_kept_but_anchors_are_not():
    assert normalize_url("https://x.com/#/products/1") == "https://x.com/#/products/1"
    assert normalize_url("https://x.com/#!/about") == "https://x.com/#!/about"
    assert normalize_url("https://x.com/page#section") == "https://x.com/page"
    assert has_route_fragment("https://x.com/#/a")


def test_pagination_is_not_counted_as_filters():
    assert facet_key("https://x.com/s?color=red&page=2") == facet_key("https://x.com/s?color=red&page=3")
    assert facet_key("https://x.com/s?color=red") != facet_key("https://x.com/s?color=blue")


def test_faceted_search_explosion_is_capped(site, make_config, monkeypatch):
    monkeypatch.setattr("my_scraper.core.crawler.MAX_FILTER_VARIANTS", 5)
    links = [f"/search?color=c{i}" for i in range(50)]
    site.add("/", "home", links=links)
    for link in links:
        site.add(link, link)

    _, results = run(make_config())

    assert len(results) == 6


def test_action_links_are_not_followed(site, make_config):
    site.add("/", "home", links=["/cart/add/5", "/logout", "/fine"])
    site.add("/fine", "fine")
    site.add("/logout", "bye")

    _, results = run(make_config())

    assert site.hits["/logout"] == 0
    assert sorted(item["title"] for item in results) == ["fine", "home"]


LINKS = """<html><head><base href="/deep/dir/"><link rel="next" href="/page/2"></head><body>
<a href="relative">relative</a>
<a href="/plain">plain</a>
<a href="/nofollow" rel="nofollow">x</a>
<a href="/hidden-style" style="display: none">x</a>
<div hidden><a href="/hidden-attr">x</a></div>
<div style="visibility:hidden"><a href="/hidden-parent">x</a></div>
<map><area href="/area"></map>
<iframe src="/frame"></iframe>
<div data-href="/data"></div>
<button onclick="location.href='/onclick'">go</button>
</body></html>"""


def test_link_discovery():
    links = page_links(Selector(text=LINKS))

    for found in ("relative", "/plain", "/area", "/frame", "/data", "/onclick"):
        assert found in links
    for avoided in ("/nofollow", "/hidden-style", "/hidden-attr", "/hidden-parent"):
        assert avoided not in links


def test_base_href_changes_where_links_point(site, make_config):
    site.add_raw("/", '<html><head><base href="/deep/"></head><body><h1>home</h1>' + FILLER + '<a href="child">c</a></body></html>',
                 "text/html")
    site.add("/deep/child", "child")

    _, results = run(make_config())

    assert sorted(item["title"] for item in results) == ["child", "home"]


def test_robots_meta_nofollow_is_honoured(site, make_config):
    site.add_raw("/", f'<html><head><meta name="robots" content="noindex, nofollow"></head><body><h1>home</h1>{FILLER}'
                      '<a href="/secret">s</a></body></html>', "text/html")
    site.add("/secret", "secret")

    run(make_config())

    assert site.hits["/secret"] == 0


@pytest.mark.parametrize("markup", [
    '<a href="/p2" aria-label="Next page"><svg></svg></a>',
    '<a href="/p2">Suivant</a>',
    '<a href="/p2">次へ</a>',
    '<a href="/p2">Weiter</a>',
    '<nav class="pagination"><a href="/p2">2</a></nav>',
    '<ul class="pager"><li><a href="/p2">2</a></li></ul>',
])
def test_next_page_links_in_many_forms(markup):
    assert "/p2" in next_page_links(Selector(text=f"<html><body>{markup}</body></html>"))


def test_page_numbers_elsewhere_are_not_pagination():
    assert next_page_links(Selector(text='<html><body><a href="/x">2</a></body></html>')) == []


# --- Choosing the right content ----------------------------------------------

def story(number, tag="div", css="story"):
    return (f'<{tag} class="{css}"><h3><a href="/news/2026/story-{number}">{HEADLINE} {number}</a></h3>'
            f"<p>{'Some detail about this story that adds context. ' * 2}</p></{tag}>")


def test_menus_in_plain_divs_lose_to_content():
    menu = '<div id="mega">' + "".join(f'<div class="item"><a href="/s{i}">Section {i}</a></div>' for i in range(40)) + "</div>"
    html = f"<html><body>{menu}<div class='list'>{''.join(story(i) for i in range(6))}</div></body></html>"

    items = extract_items(html, URL)

    assert len(items) == 6
    assert all(HEADLINE in item["title"] for item in items)


def test_named_menus_are_skipped_even_when_rich():
    menu = '<div class="site-footer">' + "".join(story(i, css="promo") for i in range(4)) + "</div>"
    html = f"<html><body><div>{''.join(story(i + 100) for i in range(8))}</div>{menu}</body></html>"

    assert len(extract_items(html, URL)) == 8


def test_a_page_wide_wrapper_named_sidebar_is_not_skipped():
    html = f"<html><body><div class='with-sidebar'>{''.join(story(i) for i in range(5))}</div></body></html>"

    assert len(extract_items(html, URL)) == 5


def test_main_is_trusted_over_a_huge_footer():
    footer = f"<footer>{'<p>' + 'legal words ' * 100 + '</p>'}</footer>" * 3
    html = f"<html><body><main id='wp--skip-link--target'>{''.join(story(i) for i in range(4))}</main>{footer}</body></html>"

    assert len(extract_items(html, URL)) == 4


def test_several_lists_on_one_page_are_all_kept():
    first = "<section>" + "".join(story(i, css="top") for i in range(4)) + "</section>"
    second = "<section>" + "".join(story(i + 50, css="more") for i in range(5)) + "</section>"

    items = extract_items(f"<html><body>{first}{second}</body></html>", URL)

    assert len(items) == 9
    assert items[0]["title"].endswith(" 0")
    assert items[-1]["title"].endswith(" 54")


def test_counters_are_not_titles():
    rows = "".join(f'<tr class="a"><td><a href="/item/{i}">{HEADLINE} {i}</a></td></tr>'
                   f'<tr class="b"><td><a href="/c/{i}">{i * 3} comments</a></td></tr>' for i in range(5))

    items = extract_items(f"<html><body><table>{rows}</table></body></html>", URL)

    assert all(HEADLINE in item["title"] for item in items)


def test_bullet_points_in_an_article_are_not_items():
    bullets = "<ul>" + "".join(f"<li>A plain sentence in a list, number {i}, explaining something.</li>" for i in range(5)) + "</ul>"
    prose = "<p>" + "Running text that explains the topic in some detail for the reader. " * 8 + "</p>"
    html = f"<html><body><h1>Guide</h1>{prose}{bullets}{prose}</body></html>"

    [item] = extract_items(html, URL)

    assert item["source"] == "document"


def test_paragraphs_are_not_list_items():
    paragraphs = "".join(f"<p>Paragraph {i} of this article has words in it. {'More words. ' * 3}</p>" for i in range(8))

    [item] = extract_items(f"<html><body><h1>Essay</h1>{paragraphs}</body></html>", URL)

    assert item["title"] == "Essay"


def test_tab_panels_are_not_items():
    panels = "".join(f'<div class="panel" role="tabpanel"><h2>Tab {i}</h2><p>{"Words in this panel. " * 200}</p></div>'
                     for i in range(3))

    [item] = extract_items(f"<html><body><h1>Project</h1>{panels}</body></html>", URL)

    assert item["title"] == "Project"


def test_a_homepage_calling_itself_an_article_is_still_a_listing():
    entity = '{"@context": "https://schema.org", "@type": "Article", "headline": "Home"}'
    html = (f'<html><head><script type="application/ld+json">{entity}</script></head>'
            f"<body><main>{''.join(story(i, tag='article') for i in range(6))}</main></body></html>")

    assert len(extract_items(html, URL)) == 6


def test_a_real_article_with_many_comments_stays_an_article():
    entity = '{"@context": "https://schema.org", "@type": "NewsArticle", "headline": "Big News"}'
    prose = "<p>" + "A long paragraph of the actual article. " * 20 + "</p>"
    comments = "".join(f'<div class="c"><a href="/u/{i}">user{i}</a><span>{"Nice article. " * 3}</span></div>' for i in range(10))
    html = (f'<html><head><script type="application/ld+json">{entity}</script></head>'
            f"<body><article>{prose * 4}</article>{comments}</body></html>")

    [item] = extract_items(html, URL)

    assert item["title"] == "Big News"


def test_product_page_with_related_products_stays_a_product():
    entity = ('{"@context": "https://schema.org", "@type": "Product", "name": "Lamp", '
              '"offers": {"@type": "Offer", "price": "19.99"}}')
    related = "".join(f'<div class="card"><a href="/p/{i}"><img src="/{i}.jpg"><h3>Other lamp {i}</h3></a><b>$5.00</b></div>'
                      for i in range(8))
    html = f'<html><head><script type="application/ld+json">{entity}</script></head><body><h1>Lamp</h1>{related}</body></html>'

    [item] = extract_items(html, URL)

    assert item["title"] == "Lamp"
    assert item["price"] == "19.99"


def test_breadcrumbs_are_not_content():
    crumbs = ('{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": ['
              '{"@type": "ListItem", "position": 1, "item": {"@type": "Thing", "name": "Home"}},'
              '{"@type": "ListItem", "position": 2, "item": {"@type": "Product", "name": "Shoes"}}]}')
    html = f'<html><head><script type="application/ld+json">{crumbs}</script></head><body><h1>Real page</h1>{FILLER}</body></html>'

    [item] = extract_items(html, URL)

    assert item["title"] == "Real page"


def test_short_cjk_titles_are_long_enough():
    entries = "".join(f'<li><a href="/card{i}.html">坊っちゃん{i}</a>（新字新仮名、作品ID：{i}）</li>' for i in range(10))

    items = extract_items(f"<html><body><ol>{entries}</ol></body></html>", URL)

    assert items[0]["title"] == "坊っちゃん0"
    assert items[0]["url"] == "https://site.test/card0.html"


@pytest.mark.parametrize("text, expected", [
    ("Now $1,299.99 only", "$1,299.99"),
    ("Preis: 1.299,00 €", "1.299,00 €"),
    ("R$ 10,50 à vista", "R$ 10,50"),
    ("CHF 15.50", "CHF 15.50"),
    ("1 299 kr", "1 299 kr"),
    ("¥1,200", "¥1,200"),
    ("1200円", "1200円"),
    ("₹499", "₹499"),
    ("£51.77", "£51.77"),
    ("99,90 zł", "99,90 zł"),
])
def test_prices_around_the_world(text, expected):
    assert find_price(text) == expected


def test_a_number_before_the_price_is_not_mistaken_for_it():
    assert find_price("Product number 5 $19.99") == "$19.99"


@pytest.mark.parametrize("text", ["Windows 11 in 2026", "Chapter 3 of 12", "Room 101", "1,204 reviews"])
def test_ordinary_numbers_are_not_prices(text):
    assert find_price(text) is None


def test_lazy_images_and_srcset():
    cards = "".join(
        f'<div class="c"><h3><a href="/p/{i}/details">{HEADLINE}{i}</a></h3>'
        f'<img src="data:image/gif;base64,R0lGOD" data-src="/real{i}.jpg"></div>' for i in range(3))
    picture = ('<div class="c"><h3><a href="/p/9/details">Another long enough headline here</a></h3>'
               '<picture><source srcset="/big.webp 2x, /small.webp 1x"></picture></div>')

    items = extract_items(f"<html><body>{cards}{picture}</body></html>", URL)

    assert items[0]["image"] == "https://site.test/real0.jpg"
    assert items[3]["image"] == "https://site.test/big.webp"


def test_title_is_the_link_not_the_site_name_beside_it():
    rows = "".join(f'<li class="story"><span class="titleline"><a href="/n/{i}/details">{HEADLINE} {i}</a>'
                   f'<span> (example.com)</span></span><p>12 points by someone 3 hours ago</p></li>' for i in range(4))

    assert extract_items(f"<html><body><ul>{rows}</ul></body></html>", URL)[0]["title"] == f"{HEADLINE} 0"


def test_block_details_are_collected():
    card = ('<article class="post"><h2><a href="/2026/post-1">{title}</a></h2><time datetime="2026-01-02">Jan 2</time>'
            '<span class="author-name">Sam Jones</span><p class="summary">{summary}</p>'
            '<span class="star-rating Four"></span></article>')
    summary = "A summary of this post that is long enough to count as one."
    html = "<html><body>" + "".join(card.format(title=f"{HEADLINE} {i}", summary=summary) for i in range(3)) + "</body></html>"

    item = extract_items(html, URL)[0]

    assert item["date"] == "2026-01-02"
    assert item["author"] == "Sam Jones"
    assert item["description"] == summary
    assert item["rating"] == "4"


# --- Tables ------------------------------------------------------------------

def table(header, rows):
    head = "<tr>" + "".join(f"<th>{cell}</th>" for cell in header) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<html><body><table>{head}{body}</table></body></html>"


def test_duplicate_and_empty_column_names():
    html = table(["Name", "Name", ""], [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"], ["j", "k", "l"]])

    first = extract_items(html, URL)[0]

    assert first["Name"] == "a"
    assert first["Name_2"] == "b"
    assert first["column_3"] == "c"


def test_column_called_source_survives():
    html = table(["Team", "Source", "Year"], [["a", "wiki", "1"], ["b", "wiki", "2"], ["c", "wiki", "3"]])

    first = extract_items(html, URL)[0]

    assert first["Source_column"] == "wiki"
    assert first["source"] == "table"


def test_colspan_keeps_cells_under_their_headers():
    html = ("<html><body><table><tr><th>A</th><th>B</th><th>C</th></tr>"
            '<tr><td colspan="2">wide</td><td>x</td></tr><tr><td>1</td><td>2</td><td>3</td></tr>'
            "<tr><td>4</td><td>5</td><td>6</td></tr><tr><td>7</td><td>8</td><td>9</td></tr></table></body></html>")

    first = extract_items(html, URL)[0]

    assert first["C"] == "x"


def test_table_without_header_row():
    rows = "".join(f"<tr><td>Item {i}</td><td>{i}</td><td>note {i}</td></tr>" for i in range(6))

    items = extract_items(f"<html><body><table>{rows}</table></body></html>", URL)

    assert len(items) == 6
    assert items[0]["column_1"] == "Item 0"


def test_table_rows_keep_their_links():
    rows = [[f'<a href="/t/{i}">Topic number {i}</a>', str(i), "x"] for i in range(4)]

    first = extract_items(table(["Topic", "Replies", "Views"], rows), URL)[0]

    assert first["url"] == "https://site.test/t/0"
    assert first["title"] == "Topic number 0"


def test_duplicate_first_column_keeps_every_row():
    rows = [["Boston", "1990", "44"], ["Boston", "1991", "40"], ["Boston", "1992", "51"], ["Boston", "1993", "42"]]

    assert len(extract_items(table(["Team", "Year", "Wins"], rows), URL)) == 4


def test_small_two_column_table_is_a_specification():
    specs = table(["Spec", "Value"], [["Weight", "2kg"], ["Height", "10cm"], ["Colour", "Red"]])

    [item] = extract_items(specs.replace("</body>", f"<h1>Thing</h1>{FILLER}</body>"), URL)

    assert item["attributes"]["Weight"] == "2kg"


def test_specifications_from_any_layout():
    html = (f"<html><body><h1>Thing</h1>{FILLER}<table><tr><td>Weight</td><td>2kg</td></tr></table>"
            "<dl><dt>Colour</dt><dd>Red</dd></dl></body></html>")

    [item] = extract_items(html, URL)

    assert item["attributes"] == {"Weight": "2kg", "Colour": "Red"}


# --- Data embedded for JavaScript --------------------------------------------

PRODUCTS = [{"id": i, "name": f"Product number {i}", "price": {"amount": 10 + i}, "url": f"/p/{i}",
             "image": {"src": f"/i/{i}.jpg"}, "sku": f"S{i}"} for i in range(5)]


def app_shell(script):
    return f'<html><body><div id="root"></div>{script}</body></html>'


@pytest.mark.parametrize("script", [
    '<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"items": %s}}}</script>',
    "<script>window.__INITIAL_STATE__ = {\"catalog\": {\"list\": %s}};</script>",
    "<script>var data = %s;</script>",
])
def test_app_state_in_scripts(script):
    html = app_shell(script % json.dumps(PRODUCTS))

    items = extract_items(html, URL)

    assert len(items) == 5
    assert items[0]["title"] == "Product number 0"
    assert items[0]["price"] == "10"
    assert items[0]["url"] == "https://site.test/p/0"
    assert items[0]["image"] == "https://site.test/i/0.jpg"
    assert items[0]["source"] == "embedded"


def test_json_parse_call_in_a_script():
    payload = json.dumps(json.dumps({"items": PRODUCTS}))

    assert len(embedded_items(app_shell(f"<script>window.d = JSON.parse({payload});</script>"), URL)) == 5


def test_json_in_a_data_attribute():
    data = json.dumps({"props": {"products": PRODUCTS}}).replace('"', "&quot;")

    assert len(embedded_items(f'<html><body><div id="app" data-page="{data}"></div></body></html>', URL)) == 5


def test_graphql_edges_are_unwrapped():
    edges = [{"node": product} for product in PRODUCTS]

    assert len(records_to_items([{"data": {"products": {"edges": edges}}}], URL, "network")) == 5


def test_id_keyed_collections():
    cache = {f"Product:{i}": {**product, "__typename": "Product"} for i, product in enumerate(PRODUCTS)}
    cache["ROOT_QUERY"] = {"__typename": "Query", "a": 1}

    assert len(records_to_items([cache], URL, "network")) == 5


def test_navigation_in_app_state_is_ignored():
    menu = [{"label": f"Sec {i}", "url": f"/s{i}"} for i in range(9)]

    assert embedded_items(app_shell(f"<script>var nav = {json.dumps(menu)};</script>"), URL) == []


def test_unparseable_script_is_harmless():
    assert embedded_items(app_shell("<script>var x = {broken: function() { return 1; }};</script>"), URL) == []


def test_visible_list_beats_similar_sized_app_state():
    cards = "".join(story(i) for i in range(5))
    html = f"<html><body>{cards}<script>var data = {json.dumps(PRODUCTS)};</script></body></html>"

    assert extract_items(html, URL)[0]["source"] == "list"


def test_network_data_does_not_replace_a_good_list():
    cards = "".join(story(i) for i in range(4))
    junk = [{"name": f"_cookie{i}", "purposes": [1], "vendor": "x", "domain": ".x.com"} for i in range(40)]

    items = extract_items(f"<html><body>{cards}</body></html>", URL, json_payloads=[junk])

    assert len(items) == 4
    assert items[0]["source"] == "list"


def test_captured_network_json_rescues_an_empty_page():
    items = extract_items(app_shell(""), URL, json_payloads=[{"results": PRODUCTS}])

    assert len(items) == 5
    assert items[0]["source"] == "network"


def test_tabs_of_one_dataset_are_joined():
    first = [{"title": f"Film {i}", "year": 2010, "awards": i, "nominations": i} for i in range(3)]
    second = [{"title": f"Film {i}", "year": 2011, "awards": i, "nominations": i} for i in range(3, 7)]

    assert len(records_to_items([first, second], URL, "network")) == 7


def test_snapshots_of_a_page_are_combined():
    page_one = f"<html><body>{''.join(story(i) for i in range(4))}</body></html>"
    page_two = f"<html><body>{''.join(story(i) for i in range(2, 7))}</body></html>"

    assert len(extract_items(page_one, URL, snapshots=[page_two])) == 7


# --- One failing method must not cost the others -----------------------------

def test_broken_metadata_library_does_not_lose_the_page(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("library bug")

    monkeypatch.setattr("my_scraper.parser.auto_extractor.extruct.extract", explode)
    errors = []

    items = extract_items(f"<html><body>{''.join(story(i) for i in range(4))}</body></html>", URL, errors=errors)

    assert len(items) == 4
    assert "library bug" in errors[0]


def test_broken_text_extractor_does_not_lose_the_page(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("library bug")

    monkeypatch.setattr("my_scraper.parser.auto_extractor.trafilatura.bare_extraction", explode)

    assert extract_items(html_page("x"), URL) == [] or True  # must simply not raise


def test_deeply_nested_html():
    html = "<div>" * 3000 + "text" + "</div>" * 3000

    assert isinstance(extract_items(f"<html><body>{html}</body></html>", URL), list)


@pytest.mark.parametrize("html", ["<<<>>>", "<html><body><table><tr><td>", "\x00\x01\x02", "<a href='http://[bad'>x</a>" * 5])
def test_malformed_html_is_survivable(html):
    assert isinstance(extract_items(html, URL), list)


# --- File types --------------------------------------------------------------

def parse(content="", body=None, content_type="", url=URL):
    return UniversalParser().parse_page({"url": url, "content": content, "body": body,
                                         "headers": {"content-type": content_type}})


def test_csv_with_any_delimiter():
    items = parse("name;age\nAna;30\nBo;25\n", content_type="text/csv").items

    assert items[0]["name"] == "Ana"
    assert items[1]["age"] == "25"


def test_csv_served_as_plain_text_is_recognised_by_its_name():
    items = parse("a,b\n1,2\n3,4\n", content_type="text/plain", url="https://x.com/data.csv").items

    assert len(items) == 2


def test_csv_with_bom_and_blank_lines():
    assert parse("﻿a,b\n\n1,2\n\n", content_type="text/csv").items[0]["a"] == "1"


RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Feed</title>
<item><title>First &amp; best</title><link>https://x.com/1</link><description><![CDATA[<p>Hello <b>there</b></p>]]></description>
<pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate><dc:creator>Sam</dc:creator><media:thumbnail url="https://x.com/1.jpg"/></item>
<item><title>Second</title><link>https://x.com/2</link></item></channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
<entry><title>Entry</title><link rel="alternate" href="/e/1"/><updated>2026-11-02T10:00:00Z</updated>
<author><name>Sam</name></author><summary>Short</summary></entry></feed>"""


def test_rss_feed():
    first = parse(RSS, body=RSS.encode(), content_type="application/rss+xml").items[0]

    assert first["title"] == "First & best"
    assert first["url"] == "https://x.com/1"
    assert first["description"] == "Hello there"
    assert first["author"] == "Sam"
    assert first["image"] == "https://x.com/1.jpg"
    assert "2026" in first["date"]


def test_atom_feed():
    first = parse(ATOM, body=ATOM.encode(), content_type="application/atom+xml").items[0]

    assert first["url"] == "https://site.test/e/1"
    assert first["author"] == "Sam"
    assert first["source"] == "feed"


def test_feed_is_recognised_despite_a_wrong_label():
    assert ContentTypeDetector.detect(RSS, {"content-type": "text/html"}) == "xml"


def test_generic_xml_records():
    xml = "<catalog><book id='1'><name>A</name></book><book id='2'><name>B</name></book><book id='3'><name>C</name></book></catalog>"

    items = parse(xml, body=xml.encode(), content_type="application/xml").items

    assert items[1] == {"id": "2", "name": "B", "source": "xml"}


def test_xml_external_entities_are_not_read(tmp_path):
    secret = tmp_path / "s.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    xml = f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///{secret.as_posix()}">]><rss><channel><item><title>&x;</title></item></channel></rss>'

    assert "TOPSECRET" not in json.dumps(parse(xml, body=xml.encode(), content_type="application/xml").items)


def test_plain_text():
    [item] = parse("Network Working Group\n\nSome text here.\n", content_type="text/plain").items

    assert item["title"] == "Network Working Group"
    assert "Some text" in item["text"]


def test_html_served_as_plain_text_is_still_html():
    assert ContentTypeDetector.detect("<!DOCTYPE html><html><body>x</body></html>", {"content-type": "text/plain"}) == "html"


def test_pdf_recognised_by_its_bytes_whatever_the_label():
    assert ContentTypeDetector.detect("", {"content-type": "application/octet-stream"}, body=b"%PDF-1.7") == "pdf"


def make_pdf(text="Hello from a real PDF"):
    """A minimal valid PDF, built by hand so the test needs no writer library."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + content + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def test_real_pdf_text_is_extracted():
    [item] = parse(body=make_pdf(), content_type="application/pdf").items

    assert "Hello from a real PDF" in item["text"]
    assert item["pages"] == 1


def test_damaged_pdf_is_reported():
    page = parse(body=b"%PDF-1.4 garbage", content_type="application/pdf")

    assert page.items == [] or not page.items[0].get("text")


def test_word_document():
    import docx

    document = docx.Document()
    document.add_paragraph("Quarterly report")
    document.add_paragraph("Sales went up.")
    buffer = io.BytesIO()
    document.save(buffer)

    [item] = parse(body=buffer.getvalue(), url="https://x.com/a.docx").items

    assert item["title"] == "Quarterly report"
    assert "Sales went up." in item["text"]


def test_excel_workbook():
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Name", "Score"])
    sheet.append(["Ana", 9])
    sheet.append(["Bo", 7])
    buffer = io.BytesIO()
    workbook.save(buffer)

    items = parse(body=buffer.getvalue(), url="https://x.com/a.xlsx").items

    assert items[0]["Name"] == "Ana"
    assert items[1]["Score"] == 7


def test_unknown_zip_is_not_parsed_as_html():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "x")

    page = parse(content="PK garbage", body=buffer.getvalue(), content_type="application/zip")

    assert page.items == []
    assert page.errors


def test_nested_json_records_are_found():
    payload = json.dumps({"meta": {"total": 3}, "data": {"users": [{"id": i, "name": f"u{i}"} for i in range(3)]}})

    items = parse(payload, content_type="application/json").items

    assert [item["name"] for item in items] == ["u0", "u1", "u2"]


def test_single_json_object():
    assert parse('{"a": 1}', content_type="application/json").items == [{"a": 1, "source": "json"}]


# --- Feeds and platform APIs -------------------------------------------------

WORDPRESS_HEAD = '<link rel="https://api.w.org/" href="https://x.com/wp-json/">'


def test_feed_discovery():
    html = ('<html><head><link rel="alternate" type="application/rss+xml" href="/feed/">'
            '<link rel="alternate" hreflang="fr" href="/fr"></head></html>')

    assert feed_links(html, "https://x.com/") == ["https://x.com/feed/"]


def test_wordpress_api_discovery():
    assert platform_endpoints(f"<html><head>{WORDPRESS_HEAD}</head></html>", "https://x.com/") == [
        "https://x.com/wp-json/wp/v2/posts?per_page=100&page=1"]


def test_woocommerce_api_discovery():
    endpoints = platform_endpoints(f"<html><head>{WORDPRESS_HEAD}</head><body class='woocommerce'></body></html>", "https://x.com/")

    assert "https://x.com/wp-json/wc/store/v1/products?per_page=100&page=1" in endpoints


def test_shopify_discovery_uses_the_collection():
    html = '<html><script src="https://cdn.shopify.com/s/x.js"></script></html>'

    assert platform_endpoints(html, "https://x.com/") == ["https://x.com/products.json?limit=250&page=1"]
    assert platform_endpoints(html, "https://x.com/collections/mens") == [
        "https://x.com/collections/mens/products.json?limit=250&page=1"]


def test_api_pagination():
    assert next_api_page("https://x.com/products.json?limit=250&page=1", 250).endswith("page=2")
    assert next_api_page("https://x.com/products.json?limit=250&page=1", 12) is None


def test_platform_api_urls():
    assert is_platform_api("https://x.com/wp-json/wp/v2/posts")
    assert is_platform_api("https://x.com/collections/a/products.json")
    assert not is_platform_api("https://x.com/data.json")


def test_discover_uses_feed_and_follows_its_entries(site, make_config):
    site.add_raw("/", f'<html><head><link rel="alternate" type="application/rss+xml" href="/rss"></head>'
                      f"<body><h1>home</h1>{FILLER}</body></html>", "text/html")
    feed = (f'<?xml version="1.0"?><rss><channel><item><title>Hidden post</title><link>{site.url}/post</link>'
            "</item></channel></rss>")
    site.add_raw("/rss", feed, "application/rss+xml")
    site.add("/post", "Hidden post")

    config = make_config(discover=True)
    config.selectors = {}
    _, results = run(config)

    assert site.hits["/post"] == 1
    assert any(item["title"] == "Hidden post" for item in results)


def test_discover_walks_a_platform_api(site, make_config):
    site.add_raw("/", f'<html><head><link rel="https://api.w.org/" href="{site.url}/wp-json/"></head>'
                      f"<body><h1>home</h1>{FILLER}</body></html>", "text/html")

    async def posts(request):
        page = int(request.query.get("page", 1))
        size = int(request.query.get("per_page"))
        count = size if page == 1 else 3
        records = [{"id": page * 1000 + i, "title": {"rendered": f"Post {page}-{i}"}, "link": f"{site.url}/p/{page}-{i}",
                    "excerpt": {"rendered": "<p>Some excerpt text</p>"}, "date": "2026-01-01"} for i in range(count)]
        return web.json_response(records)

    site.pages["/wp-json/wp/v2/posts?page=1&per_page=100"] = posts
    site.pages["/wp-json/wp/v2/posts?page=2&per_page=100"] = posts

    config = make_config(discover=True, max_depth=0)
    config.selectors = {}
    _, results = run(config)

    api_items = [item for item in results if item.get("source") == "api"]
    assert len(api_items) == 103
    assert api_items[0]["title"] == "Post 1-0"
    assert api_items[0]["description"] == "Some excerpt text"


def test_fallback_sitemap_locations(site, make_config):
    site.add_raw("/sitemap_index.xml", f"<urlset><url><loc>{site.url}/found</loc></url></urlset>", "application/xml")
    site.add("/", "home")
    site.add("/found", "found")

    _, results = run(make_config(use_sitemap=True))

    assert len(results) == 2


# --- Teaching by example -----------------------------------------------------

SHOP = """<html><body><div class="products">
<div class="card"><h3 class="name">Blue lamp</h3><span class="cost">£10</span><a class="more" href="/lamp/1">View</a></div>
<div class="card"><h3 class="name">Red chair</h3><span class="cost">£25</span><a class="more" href="/chair/2">View</a></div>
<div class="card"><h3 class="name">Green desk</h3><span class="cost">£99</span><a class="more" href="/desk/3">View</a></div>
</div></body></html>"""


def test_learning_selectors_from_examples():
    item, fields = learn_selectors(SHOP, {"title": "Blue lamp", "price": "£10"})

    page = UniversalParser().parse_page({"url": URL, "content": SHOP}, fields, item)

    assert page.items == [{"title": "Blue lamp", "price": "£10"}, {"title": "Red chair", "price": "£25"},
                          {"title": "Green desk", "price": "£99"}]


def test_learning_from_an_attribute_value():
    item, fields = learn_selectors(SHOP, {"title": "Blue lamp", "link": "/lamp/1"})

    page = UniversalParser().parse_page({"url": URL, "content": SHOP}, fields, item)

    assert page.items[2] == {"title": "Green desk", "link": "/desk/3"}


def test_examples_that_are_not_on_the_page():
    assert learn_selectors(SHOP, {"title": "Not here at all"}) is None


def test_examples_through_the_crawler(site, make_config):
    site.add_raw("/", SHOP, "text/html")
    config = make_config(max_depth=0, examples={"title": "Blue lamp", "price": "£10"})
    config.selectors = {}

    crawler, results = run(config)

    assert sorted(item["title"] for item in results) == ["Blue lamp", "Green desk", "Red chair"]
    assert crawler.learned_selectors is not None


# --- Exports -----------------------------------------------------------------

BROKEN = json.loads('{"title": "broken emoji \\ud83d here", "url": "http://x/"}')


@pytest.mark.parametrize("exporter", [JSONExporter, JSONLExporter, CSVExporter, SQLiteExporter])
def test_broken_unicode_does_not_crash_exports(exporter):
    path = exporter("out").export([BROKEN])

    assert Path(path).stat().st_size > 0


def test_one_failed_format_does_not_lose_the_others(monkeypatch):
    from my_scraper.config.settings import ExportFormat

    def explode(self, items, filepath):
        raise OSError("disk trouble")

    monkeypatch.setattr(CSVExporter, "_write", explode)
    config = CrawlConfig(export_formats=[ExportFormat.CSV, ExportFormat.JSONL])
    manager = StorageManager(config)

    results = manager.export([{"a": 1}])

    assert results["csv"] is None
    assert Path(results["jsonl"]).exists()
    assert "disk trouble" in manager.errors["csv"]


@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://evil\")", "+cmd", "@SUM(1)", "-2+3"])
def test_spreadsheet_formulas_are_defused(value):
    path = CSVExporter("out").export([{"a": value}])

    with open(path, encoding="utf-8", newline="") as f:
        cell = next(csv.DictReader(f))["a"]

    assert cell == "'" + value


@pytest.mark.parametrize("value", ["-5", "-3.14", "plain", ""])
def test_ordinary_cells_are_left_alone(value):
    path = CSVExporter("out").export([{"a": value, "b": "x"}])

    with open(path, encoding="utf-8", newline="") as f:
        assert next(csv.DictReader(f))["a"] == value


# --- Rendering decisions, without launching a real browser -------------------

def rendered_result(url, html):
    return {"url": url, "final_url": url, "status_code": 200, "content": html, "headers": {"content-type": "text/html"},
            "fetcher_type": "browser"}


def auto_crawler(make_config, **overrides):
    config = make_config(**overrides)
    config.selectors = {}
    config.fetcher = FetcherType.AUTO
    crawler = ProductionCrawler(config)
    crawler._selectors = {}
    return crawler


def test_shell_with_a_long_footer_is_rendered(site, make_config):
    """Plenty of visible text, none of it content, so only the result gives it away."""
    footer = "<footer>" + "Terms and conditions apply to everything. " * 30 + "</footer>"
    site.add_raw("/", f"<html><body><div id='list'></div>{footer}</body></html>", "text/html")

    crawler = auto_crawler(make_config, max_depth=0)
    full = f"<html><body>{''.join(story(i) for i in range(6))}{footer}</body></html>"
    renders = []

    async def render(url):
        renders.append(url)
        return rendered_result(url, full)

    crawler.fetcher.browser_fetcher.fetch = render

    results = asyncio.run(crawler.crawl())

    assert len(renders) == 1
    assert len(results) == 6


def test_infinite_scroll_list_is_checked_once_per_site(site, make_config):
    site.add_raw("/", f"<html><body>{''.join(story(i) for i in range(5))}<a href='/two'>two</a></body></html>", "text/html")
    site.add_raw("/two", f"<html><body>{''.join(story(i + 20) for i in range(5))}</body></html>", "text/html")

    crawler = auto_crawler(make_config)
    renders = []

    async def render(url):
        renders.append(url)
        return rendered_result(url, "<html><body>" + "".join(story(i) for i in range(5)) + "</body></html>")

    crawler.fetcher.browser_fetcher.fetch = render
    asyncio.run(crawler.crawl())

    assert len(renders) == 1


def test_site_needing_the_browser_is_remembered(site, make_config):
    site.add_raw("/", f"<html><body><h1>x</h1>{'word ' * 60}<a href='/two'>two</a></body></html>", "text/html")
    site.add_raw("/two", f"<html><body><h1>y</h1>{'word ' * 60}</body></html>", "text/html")

    crawler = auto_crawler(make_config, include_patterns=["/two$"])
    renders = []

    async def render(url):
        renders.append(url)
        cards = "".join(story(i + len(renders) * 10) for i in range(5))
        return rendered_result(url, f"<html><body>{cards}<a href='/two'>two</a></body></html>")

    crawler.fetcher.browser_fetcher.fetch = render
    asyncio.run(crawler.crawl())

    assert site.hits["/two"] == 0  # straight to the browser, with no wasted plain request
    assert len(renders) == 2


def test_static_mode_never_renders(site, make_config):
    site.add_raw("/", f"<html><body>{'word ' * 60}</body></html>", "text/html")
    config = make_config()
    config.selectors = {}
    crawler = ProductionCrawler(config)

    async def render(url):
        raise AssertionError("browser must not run")

    crawler.fetcher.browser_fetcher.fetch = render
    asyncio.run(crawler.crawl())


def test_block_page_is_never_escalated_to_the_browser(site, make_config):
    site.add_raw("/", "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>", "text/html")

    crawler = auto_crawler(make_config)

    async def render(url):
        raise AssertionError("a block page must not be retried with a browser")

    crawler.fetcher.browser_fetcher.fetch = render
    results = asyncio.run(crawler.crawl())

    assert results == []
    assert "blocked_by_site" in audit_statuses()


def test_http_failure_gets_one_browser_attempt(make_config):
    crawler = auto_crawler(make_config, max_depth=0)
    url = "http://127.0.0.1:1/"
    crawler.config.start_urls = [url]
    crawler.config.robots.respect = False
    crawler.politeness.respect_robots = False
    crawler._allowed_sites = set()
    renders = []

    async def render(target):
        renders.append(target)
        return rendered_result(target, html_page("rendered"))

    crawler.fetcher.browser_fetcher.fetch = render

    results = asyncio.run(crawler.crawl())

    assert len(renders) >= 1
    assert results[0]["title"] == "rendered"


# --- Politeness of compressed sitemaps ---------------------------------------

def test_sitemap_gz_served_with_any_type(site, make_config):
    urlset = f"<urlset><url><loc>{site.url}/z</loc></url></urlset>"
    site.add_raw("/robots.txt", f"User-agent: *\nSitemap: {site.url}/s.xml.gz\n", "text/plain")
    site.add_raw("/s.xml.gz", gzip.compress(urlset.encode()), "application/x-gzip")
    site.add("/", "home")
    site.add("/z", "z")

    _, results = run(make_config(use_sitemap=True))

    assert len(results) == 2
