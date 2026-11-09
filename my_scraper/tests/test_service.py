"""The scraping service, and the MCP server and HTTP API built on it, against the local test site."""
import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from my_scraper.service import mcp_server
from my_scraper.service.engine import ScrapeService, ServiceError, map_fields
from my_scraper.service.http_api import create_app

ARTICLE = """<html><head><title>Tide tables explained</title>
<meta name="description" content="How tides are predicted.">
<script type="application/ld+json">{"@type": "Article", "headline": "Tide tables explained",
 "author": {"@type": "Person", "name": "Ada Wave"}, "datePublished": "2026-03-01"}</script></head>
<body><nav><a href="/">Home</a> <a href="/login">Log in</a></nav>
<main><h1>Tide tables explained</h1>
<p>Tides rise and fall twice a day because of the pull of the moon and the sun on the oceans of the earth.</p>
<p>A tide table lists the times and heights of high and low water for one place, worked out years ahead.</p>
<h2 id="reading">Reading one<a class="headerlink" href="#reading">¶</a></h2><p>Find your nearest port, then the date, then read across to the times of <a href="../glossary.html#high-water">high water</a>.</p>
<a href="/guide/two">Next chapter</a></main></body></html>"""

PRODUCT = """<html><head><title>Blue kettle</title>
<script type="application/ld+json">{"@type": "Product", "name": "Blue kettle", "brand": {"@type": "Brand",
 "name": "Boilwell"}, "sku": "BK-1", "offers": {"@type": "Offer", "price": "24.99", "priceCurrency": "EUR"}}</script>
</head><body><h1>Blue kettle</h1><p>A kettle that is blue and boils water quickly and quietly for tea.</p></body></html>"""


def listing(count=6):
    cards = "".join(
        f'<div class="card"><h3><a href="/item/{n}">Garden chair model {n}</a></h3>'
        f'<span class="price">${10 + n}.00</span><p>Weatherproof folding chair, number {n} in the range.</p></div>'
        for n in range(count))
    return f"<html><head><title>Chairs</title></head><body><main><h1>Chairs</h1>{cards}</main></body></html>"


@pytest.fixture
def service(tmp_path):
    return ScrapeService(home=str(tmp_path / "home"), rate_limit=1000, use_browser=False)


@pytest.fixture
def call(service):
    """Run one request, then close the service's connections on the same event loop."""
    def run(coroutine):
        async def go():
            try:
                return await coroutine
            finally:
                await service.close()
        return asyncio.run(go())
    return run


# --- Field matching ---------------------------------------------------------

def test_fields_match_by_name_and_by_common_alternatives():
    item = {"title": "Blue kettle", "offers": {"price": "24.99", "priceCurrency": "EUR"},
            "attributes": {"UPC": "123", "Number of reviews": "4"}, "brand": "Boilwell"}

    values, keys = map_fields(item, ["name", "price", "currency", "upc", "reviews", "colour"])

    assert values == {"name": "Blue kettle", "price": "24.99", "currency": "EUR", "upc": "123", "reviews": "4"}
    assert keys["price"] == "offers.price"


# --- Requests ---------------------------------------------------------------

def test_scrape_returns_markdown_and_metadata(site, service, call):
    site.add_raw("/guide/one", ARTICLE, "text/html")

    page = call(service.scrape(site.url + "/guide/one", include_links=True))

    assert page["title"] == "Tide tables explained"
    assert "pull of the moon" in page["markdown"]
    assert "Log in" not in page["markdown"]  # menus are not content
    assert page["metadata"]["author"] == "Ada Wave"
    assert site.url + "/guide/two" in page["links"]


def test_markdown_links_point_where_the_page_meant(site, service, call):
    site.add_raw("/docs/guide/one", ARTICLE, "text/html")

    markdown = call(service.scrape(site.url + "/docs/guide/one"))["markdown"]

    assert f"]({site.url}/docs/glossary.html#high-water)" in markdown  # relative to the page, not the site
    assert "¶" not in markdown  # no "link to this heading" marks


def test_scrape_returns_the_items_of_a_listing(site, service, call):
    site.add_raw("/chairs", listing(), "text/html")

    page = call(service.scrape(site.url + "/chairs"))

    assert page["items_total"] == 6
    assert page["items"][0]["title"] == "Garden chair model 0"
    assert page["items"][0]["price"] == "$10.00"


def test_markdown_is_cut_to_size(site, service, call):
    site.add_raw("/guide/one", ARTICLE, "text/html")

    page = call(service.scrape(site.url + "/guide/one", max_chars=50))

    assert len(page["markdown"]) == 50 and page["truncated"]


def test_refusals_say_why(site, service, call):
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /private\n", "text/plain")
    site.add("/private/page", "secret")

    with pytest.raises(ServiceError) as refused:
        call(service.scrape(site.url + "/private/page"))
    assert refused.value.code == "robots_disallowed"
    assert site.hits["/private/page"] == 0

    with pytest.raises(ServiceError) as login:
        call(service.scrape(site.url + "/login"))
    assert login.value.code == "blocked"

    with pytest.raises(ServiceError) as gone:
        call(service.scrape(site.url + "/missing"))
    assert gone.value.code == "http_error"

    with pytest.raises(ServiceError) as invalid:
        call(service.scrape("ftp://example.com/"))
    assert invalid.value.code == "invalid_url"


def test_crawl_returns_each_page_as_markdown(site, other_site, service, call):
    site.add("/", "home", links=["/a", "/b", other_site.url + "/"])
    site.add("/a", "page a")
    site.add("/b", "page b")
    other_site.add("/", "elsewhere")

    result = call(service.crawl(site.url + "/", max_pages=10))

    assert sorted(page["url"] for page in result["pages"]) == [site.url + "/", site.url + "/a", site.url + "/b"]
    assert all("lorem ipsum" in page["markdown"] for page in result["pages"])
    assert other_site.hits["/"] == 0  # a crawl stays on its site


def test_crawl_reports_skipped_pages(site, service, call):
    site.add("/", "home", links=["/login", "/gone"])

    result = call(service.crawl(site.url + "/"))

    assert result["pages_crawled"] == 1
    assert set(result["skipped"]) == {"blocked", "http_error"}


def test_map_lists_pages_from_links_and_sitemap(site, service, call):
    site.add_raw("/sitemap.xml", f"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{site.url}/from-sitemap</loc></url></urlset>""", "application/xml")
    site.add("/", "home", links=["/about", "/docs/start", "/login"])

    result = call(service.map_site(site.url + "/"))

    assert set(result["urls"]) == {site.url + "/", site.url + "/about", site.url + "/docs/start",
                                   site.url + "/from-sitemap"}
    assert call(service.map_site(site.url + "/", search="docs"))["urls"] == [site.url + "/docs/start"]


def test_extract_fields_from_structured_data(site, service, call):
    site.add_raw("/kettle", PRODUCT, "text/html")

    result = call(service.extract(site.url + "/kettle", ["name", "price", "currency", "brand", "colour"]))

    assert result["data"] == {"name": "Blue kettle", "price": "24.99", "currency": "EUR", "brand": "Boilwell"}
    assert result["missing"] == ["colour"]
    assert "blue and boils water" in result["markdown"]  # so the caller can look for itself


def test_extract_one_record_per_item(site, service, call):
    site.add_raw("/chairs", listing(), "text/html")

    result = call(service.extract(site.url + "/chairs", ["name", "price", "link"], many=True))

    assert result["items_total"] == 6
    assert result["items"][1] == {"name": "Garden chair model 1", "price": "$11.00", "link": site.url + "/item/1"}


# --- MCP server -------------------------------------------------------------

def test_mcp_server_tools(site, service, monkeypatch, call):
    from mcp import Client

    monkeypatch.setattr(mcp_server, "_service", service)
    site.add_raw("/guide/one", ARTICLE, "text/html")
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /private\n", "text/plain")

    async def session():
        async with Client(mcp_server.mcp) as client:
            tools = sorted(tool.name for tool in (await client.list_tools()).tools)
            ok = await client.call_tool("scrape_url", {"url": site.url + "/guide/one"})
            refused = await client.call_tool("scrape_url", {"url": site.url + "/private/x"})
            return tools, ok, refused

    tools, ok, refused = call(session())

    assert tools == ["crawl_site", "extract", "map_site", "scrape_url"]
    assert not ok.is_error
    assert json.loads(ok.content[0].text)["title"] == "Tide tables explained"
    assert refused.is_error
    assert "robots_disallowed" in refused.content[0].text


# --- HTTP API ---------------------------------------------------------------

def http(make_app, method, path, body=None, headers=None):
    async def request():
        async with TestClient(TestServer(make_app())) as client:
            response = await client.request(method, path, json=body, headers=headers)
            return response.status, json.loads(await response.text())
    return asyncio.run(request())


def test_http_api(site, service, call):
    site.add_raw("/kettle", PRODUCT, "text/html")
    def app():
        return create_app(service)

    status, body = http(app, "POST", "/v1/extract", {"url": site.url + "/kettle", "fields": ["price"]})
    assert status == 200 and body["data"] == {"price": "24.99"}

    status, body = http(app, "POST", "/v1/scrape", {"url": site.url + "/login"})
    assert status == 403 and body["error"] == "blocked"

    status, body = http(app, "POST", "/v1/scrape", {"url": site.url + "/", "colour": "blue"})
    assert status == 400 and body["error"] == "unknown_fields"


def test_http_api_key(site, service, call):
    def app():
        return create_app(service, api_key="s3cret")

    assert http(app, "POST", "/v1/scrape", {"url": site.url + "/"})[0] == 401
    assert http(app, "GET", "/health")[0] == 200
    status, _ = http(app, "POST", "/v1/map", {"url": site.url + "/", "use_sitemap": False},
                     headers={"Authorization": "Bearer s3cret"})
    assert status != 401
