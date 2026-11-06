"""Shared fixtures: a local test website, so the suite never touches the network."""
import asyncio
import threading
from collections import Counter

import pytest
from aiohttp import web

from my_scraper.config.settings import CrawlConfig, FetcherType

FILLER = "<p>" + "lorem ipsum " * 40 + "</p>"


def html_page(title, links=()):
    anchors = "".join(f'<a href="{href}">link</a>' for href in links)
    return f"<html><body><h1>{title}</h1>{FILLER}{anchors}</body></html>"


class TestSite:
    """Serves pages from a dict: path -> (status, body, content_type), or a callable."""

    __test__ = False

    def __init__(self):
        self.pages = {}
        self.hits = Counter()
        self.headers_seen = {}
        self.url = None

    def add(self, path, title=None, links=(), status=200):
        self.pages[path] = (status, html_page(title or path, links), "text/html")

    def add_raw(self, path, body, content_type, status=200):
        self.pages[path] = (status, body, content_type)

    async def handle(self, request):
        path = request.path_qs
        self.hits[path] += 1
        self.headers_seen[path] = dict(request.headers)

        page = self.pages.get(path)
        if callable(page):
            return await page(request)
        if page is None:
            if path == "/robots.txt":
                return web.Response(status=404)
            return web.Response(status=404, text=html_page("Not Found"), content_type="text/html")

        status, body, content_type = page
        if isinstance(body, bytes):
            return web.Response(status=status, body=body, headers={"Content-Type": content_type})
        return web.Response(status=status, text=body, content_type=content_type)


@pytest.fixture
def site():
    test_site = TestSite()
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state = {}

    def run():
        asyncio.set_event_loop(loop)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", test_site.handle)
        runner = web.AppRunner(app, shutdown_timeout=0.2)
        loop.run_until_complete(runner.setup())
        tcp = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(tcp.start())
        state["runner"] = runner
        test_site.url = "http://127.0.0.1:{}".format(tcp._server.sockets[0].getsockname()[1])
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(5)
    yield test_site

    asyncio.run_coroutine_threadsafe(state["runner"].cleanup(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


@pytest.fixture(autouse=True)
def isolated_workdir(tmp_path, monkeypatch):
    """Logs, state and exports are relative paths, so keep them out of the repository."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def make_config(site):
    def build(**overrides):
        host = site.url.split("//")[1]
        config = CrawlConfig(
            name="test",
            start_urls=[site.url + "/"],
            allowed_domains=[host],
            fetcher=FetcherType.STATIC,
            selectors={"title": "h1::text"},
            **overrides,
        )
        config.rate_limit.requests_per_second = 1000
        config.retry.base_delay = 0.001
        return config

    return build
