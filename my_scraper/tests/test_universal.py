"""Selector-free extraction and the discovery features around it."""
import asyncio
import gzip
import json
import ssl
from pathlib import Path

import pytest
from aiohttp import web

from my_scraper.config.settings import CrawlConfig, FetcherType
from my_scraper.core.crawler import CrawlState, ProductionCrawler
from my_scraper.fetcher.sitemap import MAX_SITEMAP_BYTES, parse_sitemap
from my_scraper.parser.auto_extractor import extract_items
from my_scraper.parser.universal_parser import UniversalParser
from my_scraper.tests.conftest import FILLER, html_page
from my_scraper.utils.http import ssl_context
from my_scraper.utils.politeness import PolitenessManager, RateLimiter
from my_scraper.utils.retry import RetryManager
from my_scraper.utils.urls import is_asset, normalize_url, site_key

URL = "https://shop.test/list"


def extract(html, **kwargs):
    return extract_items(html, URL, **kwargs)


def card(number):
    return (f'<li class="card"><h3><a href="/p/{number}" title="Full Title {number}">Full Ti...</a></h3>'
            f'<img src="/img/{number}.jpg"><span>$1{number}.99</span></li>')


def listing(body):
    return f'<html><body><nav><ul>{"<li><a href=/x>menu</a></li>" * 9}</ul></nav>{body}</body></html>'


# --- Structured data ---------------------------------------------------------

def test_json_ld_product_is_normalized():
    html = """<html><head><script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Product", "name": "Lamp", "sku": "L1",
     "brand": {"@type": "Brand", "name": "Acme"}, "image": ["/lamp.jpg"],
     "offers": {"@type": "Offer", "price": "19.99", "priceCurrency": "USD",
                "availability": "https://schema.org/InStock"},
     "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.5"}}
    </script></head><body><h1>Lamp</h1></body></html>"""

    [item] = extract(html)

    assert item["title"] == "Lamp"
    assert item["type"] == "Product"
    assert item["price"] == "19.99"
    assert item["currency"] == "USD"
    assert item["availability"] == "InStock"
    assert item["rating"] == "4.5"
    assert item["brand"] == "Acme"
    assert item["image"] == "https://shop.test/lamp.jpg"
    assert item["source"] == "json-ld"


def test_json_ld_graph_skips_site_level_entities():
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": "Shop"},
        {"@type": "Organization", "name": "Acme"},
        {"@type": "BreadcrumbList", "itemListElement": []},
        {"@type": "NewsArticle", "headline": "Big News", "author": {"@type": "Person", "name": "Sam"},
         "datePublished": "2026-01-02"},
    ]}
    html = f'<html><head><script type="application/ld+json">{json.dumps(graph)}</script></head><body></body></html>'

    [item] = extract(html)

    assert (item["title"], item["author"], item["date"]) == ("Big News", "Sam", "2026-01-02")


def test_json_ld_item_list_gives_one_item_each():
    entries = [{"@type": "ListItem", "position": i, "item": {"@type": "Product", "name": f"P{i}"}} for i in range(3)]
    data = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": entries}
    html = f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>'

    assert [item["title"] for item in extract(html)] == ["P0", "P1", "P2"]


def test_broken_json_ld_does_not_stop_extraction():
    html = ('<html><head><title>T</title><script type="application/ld+json">{broken</script></head>'
            f"<body><h1>Heading</h1>{FILLER}</body></html>")

    [item] = extract(html)

    assert item["title"] == "Heading"


def test_url_in_name_field_falls_back_to_the_list():
    products = "".join(
        f'<li class="p" itemscope itemtype="https://schema.org/Product">'
        f'<h3><a itemprop="name" href="/p/{i}">Name {i}</a></h3><span>$5.00</span></li>' for i in range(4))

    items = extract(listing(f"<ul>{products}</ul>"))

    assert [item["title"] for item in items] == [f"Name {i}" for i in range(4)]


# --- Repeated layouts --------------------------------------------------------

def test_product_cards_become_items():
    items = extract(listing("<ul>" + "".join(card(i) for i in range(5)) + "</ul>"))

    assert len(items) == 5
    assert items[0] == {
        "title": "Full Title 0",
        "url": "https://shop.test/p/0",
        "image": "https://shop.test/img/0.jpg",
        "price": "$10.99",
        "text": "Full Ti... $10.99",
        "source": "list",
    }


def test_grid_split_into_rows_is_one_list():
    def row(start):
        cells = "".join(f'<div class="cell"><h3>Item {i}</h3><p>Detail {i}</p></div>' for i in range(start, start + 3))
        return f'<div class="row">{cells}</div>'

    items = extract(listing(row(0) + row(3) + row(6)))

    assert [item["title"] for item in items] == [f"Item {i}" for i in range(9)]


def test_menus_are_not_mistaken_for_content():
    links = "".join(f'<li class="m"><a href="/{i}">Section {i}</a></li>' for i in range(30))
    html = f"<html><body><ul>{links}</ul><h1>About us</h1>{FILLER}{FILLER}</body></html>"

    [item] = extract(html)

    assert item["title"] == "About us"
    assert item["source"] == "document"


def test_short_tag_links_are_not_titles():
    quotes = "".join(
        f'<div class="quote"><span>A long and thoughtful remark, number {i}, about the nature of things</span>'
        f'<a href="/tag/philosophy">philosophy</a><a href="/tag/deep-thoughts">deep-thoughts</a></div>'
        for i in range(4))

    items = extract(listing(quotes))

    assert items[0]["title"].startswith("A long and thoughtful")


def test_text_blocks_without_links_or_images_are_a_list():
    quotes = "".join(
        f'<div class="quote"><span class="t">Remark {i}, long enough to count as real content</span>'
        f'<span>by <small>Someone</small></span></div>' for i in range(5))

    items = extract(listing(quotes))

    assert len(items) == 5
    assert items[0]["title"].startswith("Remark 0")


def test_title_link_at_the_top_of_a_block_wins_over_longer_summary():
    entries = "".join(
        f'<li class="r"><a href="/doc/{i}">Short linked headline {i}</a>'
        "<p>" + f"A much longer summary sentence, {i}, that goes on for some time. " * 3 + "</p></li>"
        for i in range(4))

    items = extract(listing(f"<ul>{entries}</ul>"))

    assert items[0]["title"] == "Short linked headline 0"
    assert items[0]["url"] == "https://shop.test/doc/0"


def test_data_table_rows_use_column_names():
    rows = "".join(f"<tr><td>Team {i}</td><td>{i}</td><td>{i + 1}</td></tr>" for i in range(4))
    html = f"<html><body><table><tr><th>Name</th><th>Wins</th><th>Losses</th></tr>{rows}</table></body></html>"

    items = extract(html)

    assert items[1] == {"Name": "Team 1", "Wins": "1", "Losses": "2", "title": "Team 1", "source": "table"}
    assert len(items) == 4


def test_article_with_comments_stays_one_document():
    prose = "<p>" + "A real paragraph of article text that goes on for a while. " * 6 + "</p>"
    comments = "".join(f'<div class="c"><a href="/u/{i}">user{i}</a><span>nice one</span></div>' for i in range(6))
    html = f"<html><body><article><h1>Story</h1>{prose * 5}</article>{comments}</body></html>"

    [item] = extract(html)

    assert item["title"] == "Story"
    assert "real paragraph" in item["text"]


# --- Single documents --------------------------------------------------------

def test_document_collects_metadata_price_and_specs():
    html = f"""<html lang="en"><head><title>Page</title>
    <meta property="og:title" content="OG Title"><meta property="og:description" content="OG desc">
    <meta property="og:image" content="/og.png"></head>
    <body><h1>Heading</h1><p class="price_color">£51.77</p>{FILLER}
    <table><tr><th>UPC</th><td>abc</td></tr><tr><th>Tax</th><td>£0.00</td></tr></table></body></html>"""

    [item] = extract(html)

    assert item["title"] == "OG Title"
    assert item["description"] == "OG desc"
    assert item["image"] == "https://shop.test/og.png"
    assert item["price"] == "£51.77"
    assert item["attributes"] == {"UPC": "abc", "Tax": "£0.00"}


def test_markdown_is_added_on_request():
    html = f"<html><body><h1>Guide</h1>{FILLER}{FILLER}</body></html>"

    [plain] = extract(html)
    [with_markdown] = extract(html, include_markdown=True)

    assert "markdown" not in plain
    assert "lorem ipsum" in with_markdown["markdown"]


@pytest.mark.parametrize("html", ["<html></html>", "<html><body></body></html>", "not html at all"])
def test_empty_pages_do_not_crash(html):
    assert isinstance(extract(html), list)


# --- Selector rows -----------------------------------------------------------

ROWS = """<div class="q"><b>one</b><i>a</i><i>b</i></div>
<div class="q"><b>two</b><i>c</i></div>"""


def parse_rows(selectors, item=".q"):
    return UniversalParser().parse_page({"url": "u", "content": ROWS}, selectors, item)


def test_item_selector_gives_one_item_per_match():
    assert parse_rows({"name": "b::text"}).items == [{"name": "one"}, {"name": "two"}]


def test_multi_value_fields_keep_every_match():
    assert parse_rows({"tags[]": "i::text"}).items == [{"tags": ["a", "b"]}, {"tags": ["c"]}]


def test_relative_xpath_inside_rows():
    assert parse_rows({"name": ".//b/text()"}).items[1] == {"name": "two"}


def test_bad_item_selector_is_reported():
    page = parse_rows({"name": "b::text"}, item="a[[[")

    assert page.items == []
    assert len(page.errors) == 1


def test_json_array_gives_one_item_per_record():
    page = UniversalParser().parse_page({
        "url": "u", "content": '[{"a": 1}, {"a": 2}]', "headers": {"content-type": "application/json"},
    })

    assert page.items == [{"a": 1, "source": "json"}, {"a": 2, "source": "json"}]


# --- URLs --------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("http://EXAMPLE.com:80/a?b=2&a=1#frag", "http://example.com/a?a=1&b=2"),
    ("https://x.com:443/p", "https://x.com/p"),
    ("https://x.com:8443/p", "https://x.com:8443/p"),
    ("https://x.com/p?utm_source=mail&id=3&fbclid=abc", "https://x.com/p?id=3"),
    ("https://x.com", "https://x.com/"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_www_counts_as_the_same_site():
    assert site_key("WWW.Example.com") == site_key("example.com")


def test_asset_detection():
    assert is_asset("https://x.com/a/photo.JPG?size=2")
    assert not is_asset("https://x.com/report.pdf")
    assert not is_asset("https://x.com/page.html")


# --- Sitemap parsing ---------------------------------------------------------

URLSET = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
 xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
<url><loc> https://x.com/a </loc><image:image><image:loc>https://x.com/i.jpg</image:loc></image:image></url>
<url><loc>https://x.com/b</loc></url></urlset>"""


def test_parse_urlset_ignores_image_entries():
    assert parse_sitemap(URLSET) == (["https://x.com/a", "https://x.com/b"], [])


def test_parse_sitemap_index():
    body = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://x.com/s1.xml</loc></sitemap></sitemapindex>'

    assert parse_sitemap(body) == ([], ["https://x.com/s1.xml"])


def test_parse_gzipped_and_plain_text_sitemaps():
    assert parse_sitemap(gzip.compress(URLSET))[0] == ["https://x.com/a", "https://x.com/b"]
    assert parse_sitemap(b"https://x.com/1\nnot a url\nhttps://x.com/2\n")[0] == ["https://x.com/1", "https://x.com/2"]


def test_gzip_bomb_is_cut_off():
    bomb = gzip.compress(b"https://x.com/a\n" * (MAX_SITEMAP_BYTES // 8))

    pages, _ = parse_sitemap(bomb)

    assert len(pages) * len("https://x.com/a\n") <= MAX_SITEMAP_BYTES


def test_external_entities_are_not_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("https://leaked.example/", encoding="utf-8")
    body = (f'<?xml version="1.0"?><!DOCTYPE u [<!ENTITY x SYSTEM "file:///{secret.as_posix()}">]>'
            "<urlset><url><loc>&x;</loc></url></urlset>").encode()

    pages, _ = parse_sitemap(body)

    assert not any("leaked" in page for page in pages)


def test_garbage_sitemap_gives_nothing():
    assert parse_sitemap(b"<<<not xml") == ([], [])


# --- Politeness additions ----------------------------------------------------

def test_crawl_delay_floor_survives_recovery():
    limiter = RateLimiter(100)
    limiter.set_floor("d", 5.0)
    limiter.backoff_on_error("d", 429)
    assert limiter.interval_for("d") == 10.0

    for _ in range(100):
        limiter.recover("d")

    assert limiter.interval_for("d") == 5.0
    assert limiter.interval_for("other") == pytest.approx(0.01)


def test_crawl_delay_from_robots_is_applied(site):
    site.add_raw("/robots.txt", "User-agent: *\nCrawl-delay: 7\n", "text/plain")

    async def scenario():
        config = CrawlConfig()
        manager = PolitenessManager(config)
        await manager.can_fetch(site.url + "/x")
        return manager.rate_limiter.interval_for(site.url.split("//")[1])

    assert asyncio.run(scenario()) == 7.0


def test_retry_after_header_extends_the_wait(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("my_scraper.utils.retry.asyncio.sleep", fake_sleep)
    responses = iter([{"status_code": 429, "headers": {"retry-after": "9"}}, {"status_code": 200}])

    async def fetch(url):
        return next(responses)

    config = CrawlConfig()
    config.retry.base_delay = 0.001
    asyncio.run(RetryManager(config).retry_http(fetch, "http://x/"))

    assert waits == [9.0]


def test_certificates_are_always_verified():
    context = ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()  # the bundle really loaded


def test_robots_failure_is_not_reported_as_disallowed(site, make_config):
    site.add_raw("/robots.txt", "down", "text/plain", status=503)
    site.add("/", "home")

    crawler, results = run(make_config())

    assert results == []
    assert audit_statuses() == ["robots_unreachable"]
    assert crawler.compliance.first_errors["robots_unreachable"] == "robots.txt returned HTTP 503"


def test_real_disallow_has_no_failure_reason(site, make_config):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /\n", "text/plain")

    crawler, _ = run(make_config())

    assert audit_statuses() == ["robots_disallowed"]
    assert crawler.compliance.first_errors == {}


# --- Crawler integration -----------------------------------------------------

def run(config, **kwargs):
    crawler = ProductionCrawler(config)
    return crawler, asyncio.run(crawler.crawl(**kwargs))


def auto_config(make_config, **overrides):
    config = make_config(**overrides)
    config.selectors = {}
    return config


def audit_statuses():
    lines = Path("logs/audit.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["status"] for line in lines]


def test_automatic_crawl_needs_no_selectors(site, make_config):
    site.add_raw("/", listing("<ul>" + "".join(card(i) for i in range(4)) + "</ul>"), "text/html")

    crawler, results = run(auto_config(make_config, max_depth=0))

    assert len(results) == 4
    assert results[0]["title"] == "Full Title 0"
    assert results[0]["source_url"] == site.url + "/"
    assert results[0]["url"] == site.url + "/p/0"


def test_listing_and_detail_page_merge_into_one_item(site, make_config):
    site.add_raw("/", listing("<ul>" + "".join(card(i) for i in range(3)) + "</ul>"), "text/html")
    for i in range(3):
        site.add_raw(f"/p/{i}", f'<html><body><h1>Full Title {i}</h1><p class="price">$1{i}.99</p>{FILLER}'
                     f"<table><tr><th>SKU</th><td>S{i}</td></tr></table></body></html>", "text/html")

    _, results = run(auto_config(make_config))

    assert len(results) == 3
    assert results[0]["attributes"] == {"SKU": "S0"}
    assert results[0]["image"].endswith("/img/0.jpg")


def test_same_author_link_does_not_merge_different_quotes(site, make_config):
    quotes = "".join(f'<div class="q"><span>{"Quote number %d goes here " % i * 3}</span>'
                     f'<a href="/author/x">about</a></div>' for i in range(4))
    site.add_raw("/", listing(quotes), "text/html")

    _, results = run(auto_config(make_config, max_depth=0))

    assert len(results) == 4


def test_item_selector_through_the_crawler(site, make_config):
    site.add_raw("/", ROWS, "text/html")
    config = make_config(max_depth=0)
    config.item_selector = ".q"
    config.selectors = {"name": "b::text"}

    _, results = run(config)

    assert sorted(item["name"] for item in results) == ["one", "two"]


def test_pagination_goes_past_max_depth(site, make_config):
    for page in range(1, 6):
        site.add_raw(f"/list/{page}", html_page(f"page {page}").replace(
            "</body>", f'<a rel="next" href="/list/{page + 1}">more</a></body>'), "text/html")
    site.add_raw("/list/6", html_page("page 6"), "text/html")

    config = make_config(max_depth=0)
    config.start_urls = [site.url + "/list/1"]
    _, results = run(config)

    assert len(results) == 6


@pytest.mark.parametrize("markup", [
    '<a rel="next" href="{}">x</a>',
    '<li class="next"><a href="{}">go</a></li>',
    '<a class="pager next" href="{}">go</a>',
    '<a href="{}"> Next </a>',
    '<a href="{}">›</a>',
])
def test_recognises_next_page_links(site, make_config, markup):
    site.add_raw("/", html_page("one").replace("</body>", markup.format("/two") + "</body>"), "text/html")
    site.add("/two", "two")

    _, results = run(make_config(max_depth=0))

    assert len(results) == 2


def test_pagination_can_be_turned_off(site, make_config):
    site.add_raw("/", html_page("one", ["/two"]).replace("</body>", '<a rel="next" href="/two">n</a></body>'),
                 "text/html")
    site.add("/two", "two")

    _, results = run(make_config(max_depth=0, follow_pagination=False))

    assert len(results) == 1


def test_include_and_exclude_patterns(site, make_config):
    site.add("/", "home", links=["/shop/a", "/shop/b/print", "/blog/c"])
    for path in ("/shop/a", "/shop/b/print", "/blog/c"):
        site.add(path, path)

    _, results = run(make_config(include_patterns=["/shop/"], exclude_patterns=["/print$"]))

    assert sorted(item["title"] for item in results) == ["/shop/a", "home"]


def test_assets_and_tracking_variants_are_not_fetched(site, make_config):
    site.add("/", "home", links=["/photo.jpg", "/a?utm_source=x", "/a?utm_source=y", "/a"])
    site.add("/a", "a")
    site.add("/photo.jpg", "photo")

    _, results = run(make_config())

    assert len(results) == 2
    assert site.hits["/photo.jpg"] == 0
    assert site.hits["/a"] == 1


def test_www_redirect_does_not_end_the_crawl(site, make_config):
    site.add("/", "home", links=["/a"])
    site.add("/a", "a")
    config = make_config()
    host = site.url.split("//")[1]
    config.allowed_domains = [f"www.{host}"]  # the crawl began on the bare host

    _, results = run(config)

    assert len(results) == 2


def test_redirect_target_is_not_fetched_twice(site, make_config):
    async def redirect(request):
        raise web.HTTPFound("/real")

    site.pages["/"] = redirect
    site.add("/real", "real", links=["/real"])

    _, results = run(make_config())

    assert len(results) == 1
    assert site.hits["/real"] == 1


def test_identical_pages_are_stored_once(site, make_config):
    body = html_page("same page")
    site.add_raw("/", html_page("home", ["/copy1", "/copy2"]), "text/html")
    site.add_raw("/copy1", body, "text/html")
    site.add_raw("/copy2", body, "text/html")

    _, results = run(make_config())

    assert len(results) == 2
    assert "duplicate_content" in audit_statuses()


def test_sitemap_finds_pages_nothing_links_to(site, make_config):
    site.add_raw("/robots.txt", f"User-agent: *\nSitemap: {site.url}/index.xml\n", "text/plain")
    site.add_raw("/index.xml", f"<sitemapindex><sitemap><loc>{site.url}/pages.xml.gz</loc></sitemap></sitemapindex>",
                 "application/xml")
    urlset = f"<urlset><url><loc>{site.url}/hidden</loc></url><url><loc>http://other.test/x</loc></url></urlset>"
    site.add_raw("/pages.xml.gz", gzip.compress(urlset.encode()), "application/octet-stream")
    site.add("/", "home")
    site.add("/hidden", "hidden")

    _, results = run(make_config(use_sitemap=True))

    assert sorted(item["title"] for item in results) == ["hidden", "home"]
    assert site.hits["/index.xml"] == 1


def test_sitemap_falls_back_to_the_standard_location(site, make_config):
    site.add_raw("/sitemap.xml", f"<urlset><url><loc>{site.url}/found</loc></url></urlset>", "application/xml")
    site.add("/", "home")
    site.add("/found", "found")

    _, results = run(make_config(use_sitemap=True))

    assert len(results) == 2


def test_missing_sitemap_is_harmless(site, make_config):
    site.add("/", "home")

    _, results = run(make_config(use_sitemap=True))

    assert len(results) == 1


def test_sitemap_cycle_terminates(site, make_config):
    site.add_raw("/sitemap.xml", f"<sitemapindex><sitemap><loc>{site.url}/sitemap.xml</loc></sitemap></sitemapindex>",
                 "application/xml")
    site.add("/", "home")

    _, results = run(make_config(use_sitemap=True))

    assert len(results) == 1
    assert site.hits["/sitemap.xml"] == 1


def etag_page(body, etag):
    async def handler(request):
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304)
        return web.Response(text=body, content_type="text/html", headers={"ETag": etag})

    return handler


def test_incremental_run_skips_304_pages_but_still_finds_new_links(site, make_config):
    site.pages["/"] = etag_page(html_page("home", ["/a"]), '"v1"')
    site.add("/a", "a", links=["/b"])
    site.add("/b", "b")

    _, first = run(make_config(incremental=True))
    assert len(first) == 3

    # /a changes and now links to a brand new page. Home stays the same, answering 304.
    site.add("/a", "a2", links=["/b", "/new"])
    site.add("/new", "new")
    _, second = run(make_config(incremental=True))

    assert sorted(item["title"] for item in second) == ["a2", "new"]
    assert audit_statuses().count("unchanged") == 2  # home by ETag, /b by content hash


def test_non_incremental_runs_always_extract(site, make_config):
    site.add("/", "home")

    run(make_config())
    _, second = run(make_config())

    assert len(second) == 1


def test_failed_extraction_is_retried_on_the_next_incremental_run(site, make_config, monkeypatch):
    site.add("/", "home")
    original = UniversalParser.parse_page

    def explode(*args, **kwargs):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(UniversalParser, "parse_page", explode)
    _, first = run(make_config(incremental=True))
    assert first == []

    monkeypatch.setattr(UniversalParser, "parse_page", original)
    _, second = run(make_config(incremental=True))
    assert len(second) == 1


def test_items_survive_a_hard_crash(site, make_config):
    site.add("/", "home", links=["/a"])
    site.add("/a", "a")
    config = make_config()

    crawler = ProductionCrawler(config)
    asyncio.run(crawler.crawl())

    # A crash leaves a half-written last line and nothing exported for this run.
    with open(crawler.state.items_file, "a", encoding="utf-8") as f:
        f.write('{"title": "cut of')

    site.add("/b", "b")
    state = json.loads(crawler.state.state_file.read_text(encoding="utf-8"))
    state["frontier"] = [[site.url + "/b", 1]]
    crawler.state.state_file.write_text(json.dumps(state), encoding="utf-8")

    _, results = run(make_config(), resume=True)

    assert sorted(item["title"] for item in results) == ["a", "b", "home"]


def test_a_fresh_crawl_starts_with_no_old_items(site, make_config):
    site.add("/", "home")

    run(make_config())
    _, results = run(make_config())

    assert len(results) == 1
    state = CrawlState()
    state.load()
    assert len(state.load_items()) == 1


def test_resume_drops_items_from_pages_it_will_fetch_again(tmp_path):
    state = CrawlState(str(tmp_path / "state.json"))
    state.reset_items()
    state.append_items("http://x/done", [{"title": "kept"}])
    state.append_items("http://x/unfinished", [{"title": "dropped"}])

    state.visited = {"http://x/done"}

    assert state.load_items() == [{"title": "kept"}]


def test_state_exists_from_the_start_of_a_crawl(site, make_config):
    states = []

    async def check(request):
        states.append(Path("data/crawl_state.json").exists())
        return web.Response(text=html_page("home"), content_type="text/html")

    site.pages["/"] = check

    run(make_config())

    assert states == [True]


def test_browser_is_not_used_for_a_304(site):
    from my_scraper.fetcher.smart_fetcher import SmartFetcher

    async def not_modified(request):
        return web.Response(status=304)

    site.pages["/"] = not_modified
    fetcher = SmartFetcher(CrawlConfig(fetcher=FetcherType.AUTO))

    async def fail(url):
        raise AssertionError("browser should not run")

    fetcher.browser_fetcher.fetch = fail

    async def go():
        result = await fetcher.fetch(site.url + "/")
        await fetcher.close()
        return result

    assert asyncio.run(go())["status_code"] == 304
