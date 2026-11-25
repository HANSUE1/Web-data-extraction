"""Real Chromium against a local site. Skipped when no browser is installed."""
import asyncio
import json

import pytest
from aiohttp import web

from my_scraper.config.settings import CrawlConfig, FetcherType
from my_scraper.core.crawler import ProductionCrawler
from my_scraper.fetcher.browser import BrowserFetcher
from my_scraper.service.engine import ScrapeService
from my_scraper.tests.conftest import FILLER

UA = "TestBot/1.0"


def chromium_available() -> bool:
    async def check():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            await browser.close()

    try:
        asyncio.run(check())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not chromium_available(), reason="Chromium is not installed")


def render(url, **options):
    async def go():
        fetcher = BrowserFetcher(user_agent=UA, **options)
        try:
            return await fetcher.fetch(url)
        finally:
            await fetcher.close()

    return asyncio.run(go())


def page(script="", body=""):
    return f"<html><body>{body}<script>{script}</script></body></html>"


def test_reports_the_real_status_and_identity(site):
    site.add("/gone", "gone", status=404)

    result = render(site.url + "/gone")

    assert result["status_code"] == 404
    assert site.headers_seen["/gone"]["User-Agent"] == UA


def test_shadow_dom_content_is_read(site):
    script = """
      const host = document.getElementById('host');
      const root = host.attachShadow({mode: 'open'});
      const inner = document.createElement('h2');
      inner.textContent = 'From ' + 'the shadow';
      root.appendChild(inner);
      const nested = document.createElement('div');
      root.appendChild(nested);
      nested.attachShadow({mode: 'open'}).innerHTML = '<p>Nested ' + 'too</p>';
    """
    site.add_raw("/", page(script, '<div id="host"></div>' + FILLER), "text/html")

    content = render(site.url + "/")["content"]

    assert "<h2>From the shadow</h2>" in content
    assert "<p>Nested too</p>" in content


def test_same_site_iframes_are_read(site):
    site.add_raw("/", f'<html><body>{FILLER}<iframe src="/inner"></iframe></body></html>', "text/html")
    site.add_raw("/inner", "<html><body><table><tr><td>Inside the frame</td></tr></table></body></html>", "text/html")

    assert "Inside the frame" in render(site.url + "/")["content"]


def test_slow_content_is_waited_for(site):
    script = "setTimeout(() => { document.getElementById('c').textContent = 'Arrived late '.repeat(30); }, 3000);"
    site.add_raw("/", page(script, '<div id="c"></div>'), "text/html")

    assert "Arrived late" in render(site.url + "/")["content"]


def test_infinite_scroll_is_followed(site):
    script = """
      let batch = 0;
      const add = () => { for (let i = 0; i < 5; i++) {
        const row = document.createElement('div');
        row.className = 'row'; row.style.height = '400px'; row.textContent = 'Row ' + (batch * 5 + i);
        document.body.appendChild(row); } batch++; };
      add();
      window.addEventListener('scroll', () => {
        if (batch < 4 && window.innerHeight + window.scrollY >= document.body.scrollHeight - 10) add();
      });
    """
    site.add_raw("/", page(script), "text/html")

    content = render(site.url + "/")["content"]

    assert "Row 19" in content


def test_load_more_button_is_pressed(site):
    script = """
      let shown = 0;
      document.getElementById('more').onclick = () => {
        fetch('/more').then(r => r.text()).then(t => {
          const d = document.createElement('div'); d.textContent = t + ' ' + (++shown);
          document.body.appendChild(d);
          if (shown === 3) document.getElementById('more').remove();
        });
      };
    """
    site.add_raw("/", page(script, f'{FILLER}<button id="more">Load more</button>'), "text/html")
    site.add_raw("/more", "Extra batch", "text/plain")

    content = render(site.url + "/")["content"]

    assert "Extra batch 3" in content


def test_clicking_waits_for_the_response(site):
    """networkidle only fires once per load, so this needs real request tracking."""
    async def slow(request):
        await asyncio.sleep(0.7)
        return web.json_response([{"title": f"Film {i}", "year": 2015, "awards": i, "nominations": i} for i in range(4)])

    script = """
      document.querySelectorAll('.year').forEach(link => link.onclick = (event) => {
        event.preventDefault();
        fetch('/data?year=' + link.textContent).then(r => r.json()).then(rows => {
          document.getElementById('out').textContent = rows.map(r => r.title).join(', ');
        });
      });
    """
    links = "".join(f'<a href="#" class="year">{year}</a> ' for year in (2013, 2014, 2015))
    site.add_raw("/", page(script, f'{FILLER}{links}<div id="out"></div>'), "text/html")
    for year in (2013, 2014, 2015):
        site.pages[f"/data?year={year}"] = slow

    result = render(site.url + "/")

    assert any("Film 3" in snapshot for snapshot in result["snapshots"])
    assert len(result["json_payloads"]) == 3


def test_third_party_json_is_not_captured(site):
    """Same machine, other hostname: treated as someone else's server."""
    other = site.url.replace("127.0.0.1", "localhost")
    site.pages["/vendors"] = lambda request: _cors_json()
    script = f"fetch('{other}/vendors').then(r => r.json());"
    site.add_raw("/", page(script, FILLER), "text/html")

    assert render(site.url + "/")["json_payloads"] == []


async def _cors_json():
    return web.json_response([{"name": "_fbp"}], headers={"Access-Control-Allow-Origin": "*"})


def test_button_pagination_keeps_every_state(site):
    script = """
      let number = 1;
      document.getElementById('next').onclick = () => {
        number++;
        document.getElementById('list').textContent = 'Page number ' + number;
        if (number === 3) document.getElementById('next').remove();
      };
    """
    site.add_raw("/", page(script, f'{FILLER}<div id="list">Page number 1</div><button id="next">Next</button>'), "text/html")

    result = render(site.url + "/")
    states = " ".join(result["snapshots"]) + result["content"]

    assert all(f"Page number {number}" in states for number in (1, 2, 3))


def test_page_that_never_stops_loading_still_returns(site):
    async def endless(request):
        await asyncio.sleep(60)
        return web.Response(text="x")

    site.pages["/forever"] = endless
    site.add_raw("/", page("fetch('/forever');", FILLER), "text/html")

    result = render(site.url + "/", expand_rounds=0)

    assert "lorem ipsum" in result["content"]


def test_browser_is_shared_between_pages(site):
    site.add("/a", "page a")
    site.add("/b", "page b")

    async def go():
        fetcher = BrowserFetcher(user_agent=UA, expand_rounds=0)
        first = await fetcher.fetch(site.url + "/a")
        browser = fetcher._browser
        second = await fetcher.fetch(site.url + "/b")
        same = fetcher._browser is browser
        await fetcher.close()
        return first, second, same

    first, second, same = asyncio.run(go())

    assert "page a" in first["content"]
    assert "page b" in second["content"]
    assert same


def test_whole_crawl_on_a_javascript_site(site):
    rows = json.dumps([{"name": f"Product number {i}", "price": f"${i}.99"} for i in range(6)])
    script = f"""
      const rows = {rows};
      document.getElementById('app').innerHTML = rows.map(r =>
        `<div class="card"><h3><a href="/product/${{r.name.replaceAll(' ', '-')}}">${{r.name}}</a></h3>
         <span>${{r.price}}</span></div>`).join('');
    """
    site.add_raw("/", page(script, '<div id="app"></div>'), "text/html")

    config = CrawlConfig(name="t", start_urls=[site.url + "/"], allowed_domains=[site.url.split("//")[1]],
                         fetcher=FetcherType.AUTO, max_depth=0)
    config.rate_limit.requests_per_second = 100
    crawler = ProductionCrawler(config)

    results = asyncio.run(crawler.crawl())

    assert len(results) == 6
    assert results[0]["price"] == "$0.99"
    assert results[0]["url"].endswith("/product/Product-number-0")


# --- Actions, and the profile ----------------------------------------------------------

GROWING = """
  let n = 0;
  function more() {
    for (let i = 0; i < 2; i++) list.insertAdjacentHTML('beforeend', `<li>Row ${++n}</li>`);
    if (n >= 6) { btn.remove(); list.insertAdjacentHTML('afterend', '<p class="end">That is all</p>'); }
  }
  btn.onclick = more;
"""


def act(site, steps, body, script=""):
    site.add_raw("/", page(script, FILLER + body), "text/html")
    return render(site.url + "/", actions=steps, expand_rounds=0)


def test_clicks_until_a_condition_holds(site):
    result = act(site, [{"click": "role=button[name='Again']", "until": ".end"}],
                 '<ul id="list"></ul><button id="btn">Again</button>', GROWING)
    assert result["actions"][0]["times"] == 3 and "Row 6" in result["content"]


def test_fills_selects_and_reads_what_changed(site):
    result = act(site, [{"fill": "#q", "value": "kettle"}, {"select": "#s", "value": "price"}, {"wait": "#out b"}],
                 '<input id="q"><select id="s"><option value="name">n</option><option value="price">p</option></select>'
                 '<div id="out"></div>',
                 "s.onchange = () => { out.innerHTML = '<b>' + q.value + ' by ' + s.value + '</b>'; }")
    assert "kettle by price" in result["content"] and not any("error" in step for step in result["actions"])


def test_risky_steps_stop_the_run(site):
    body = '<button id="buy">Buy now</button><input id="pw" type="password"><p id="out"></p>'
    script = "buy.onclick = () => { out.textContent = 'ordered'; }"
    bought = act(site, [{"click": "#buy"}, {"wait": "#out"}], body, script)
    assert "may change something" in bought["actions"][0]["error"] and len(bought["actions"]) == 1
    assert '<p id="out"></p>' in bought["content"]
    assert "password" in act(site, [{"fill": "#pw", "value": "x"}], body, script)["actions"][0]["error"]


def test_actions_cannot_leave_the_site_post_a_form_or_enter_forbidden_pages(site, other_site):
    other_site.add("/", "Elsewhere")
    for path in ("/allowed", "/posted", "/secret"):
        site.add(path, path)
    site.add_raw("/robots.txt", "User-agent: *\nDisallow: /secret", "text/plain")
    links = (f'<a id="off" href="{other_site.url}/">away</a> <a id="no" href="/secret">s</a> <a id="yes" href="/allowed">a</a>'
             '<form method="post" action="/posted"><button id="go">Filter</button></form>')
    site.add_raw("/", page("", FILLER + links), "text/html")

    async def go(target):
        service = ScrapeService(rate_limit=1000, home="home")
        try:
            return await service.interact(site.url + "/", [{"click": target}])
        finally:
            await service.close()

    for target in ("#off", "#no", "#go"):
        result = asyncio.run(go(target))
        assert result["final_url"] == site.url + "/" and len(result["refused_navigations"]) == 1, target
    assert not other_site.hits["/"] and not site.hits["/secret"] and not site.hits["/posted"]
    assert asyncio.run(go("#yes"))["final_url"] == site.url + "/allowed"


def test_interact_lists_what_can_be_acted_on(site):
    site.add_raw("/", page("", FILLER * 4 + '<button>Show more</button><input type="password" aria-label="Secret">'
                                        '<select aria-label="Sort"><option value="a">A</option></select>'), "text/html")

    async def go():
        service = ScrapeService(rate_limit=1000, home="home")
        try:
            return await service.interact(site.url + "/")
        finally:
            await service.close()

    controls = asyncio.run(go())["controls"]
    assert any((c["role"], c["name"]) == ("button", "Show more") for c in controls)
    assert any(c["role"] == "combobox" and c["options"] == ["a"] for c in controls)
    assert "Secret" not in json.dumps(controls)


def test_the_browser_profile_is_steady_and_a_screenshot_is_kept(site):
    script = "document.title = [Intl.DateTimeFormat().resolvedOptions().timeZone, navigator.language].join(' ')"
    site.add_raw("/", page(script, FILLER), "text/html")
    result = render(site.url + "/", locale="de-DE", timezone="Europe/Berlin", screenshots=True, expand_rounds=0)
    assert "Europe/Berlin de-DE" in result["content"] and result["screenshot"].startswith(b"\x89PNG")


def test_a_crawl_with_actions_keeps_its_snapshot_and_screenshot(site):
    site.add_raw("/", page(GROWING, FILLER + '<ul id="list"></ul><button id="btn">Again</button>'), "text/html")
    config = CrawlConfig(name="t", start_urls=[site.url + "/"], allowed_domains=[site.url.split("//")[1]], max_depth=0,
                         selectors={"rows[]": "#list li::text"})
    config.rate_limit.requests_per_second = 100
    config.browser.actions = [{"click": "#btn", "until": ".end"}]
    config.browser.screenshots, config.browser.expand_rounds = True, 0
    crawler = ProductionCrawler(config)
    [record] = asyncio.run(crawler.crawl())
    assert len(record["rows"]) == 6
    [snapshot] = crawler.snapshots.find()
    assert snapshot["fetcher"] == "browser" and crawler.snapshots.blobs.get(snapshot["screenshot"]).startswith(b"\x89PNG")
