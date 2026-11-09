"""Open-web crawling: following links to any site, fairly and within budgets."""
import asyncio
import time

from my_scraper.cli.main import OPEN_WEB_PAGES_PER_SITE, build_config, build_parser
from my_scraper.core.crawler import CrawlState, ProductionCrawler, site_of
from my_scraper.core.frontier import SiteFrontier
from my_scraper.utils.urls import is_action


def run(config):
    crawler = ProductionCrawler(config)
    results = asyncio.run(crawler.crawl())
    return crawler, results


def titles(results):
    return sorted(item["title"] for item in results)


def open_config(make_config, **overrides):
    config = make_config(open_web=True, **overrides)
    config.concurrency.max_workers = 1  # one page at a time, so the order is predictable
    return config


# --- The frontier -----------------------------------------------------------

def test_frontier_takes_sites_in_turn():
    frontier = SiteFrontier(site_of)
    frontier.extend([("http://a.test/1", 0), ("http://a.test/2", 0), ("http://a.test/3", 0),
                     ("http://b.test/1", 1), ("http://c.test/1", 1)])

    order = [frontier.popleft()[0] for _ in range(len(frontier))]

    assert order == ["http://a.test/1", "http://b.test/1", "http://c.test/1", "http://a.test/2", "http://a.test/3"]
    assert not frontier


def test_frontier_put_back_page_is_served_first():
    frontier = SiteFrontier(site_of, [("http://a.test/1", 0), ("http://b.test/1", 0)])
    frontier.appendleft(("http://b.test/0", 0))

    assert frontier.popleft()[0] == "http://b.test/0"
    assert len(frontier) == 2


def test_frontier_drops_a_whole_site():
    frontier = SiteFrontier(site_of, [("http://a.test/1", 0), ("http://b.test/1", 0), ("http://a.test/2", 0)])

    dropped = frontier.drop_site("a.test")

    assert [url for url, _ in dropped] == ["http://a.test/1", "http://a.test/2"]
    assert list(frontier) == [("http://b.test/1", 0)]
    assert frontier.queued_for("a.test") == 0


def test_frontier_skips_sites_that_are_not_ready():
    frontier = SiteFrontier(site_of, [("http://slow.test/1", 0), ("http://fast.test/1", 0), ("http://fast.test/2", 0)])

    def ready(url):
        return "slow" not in url

    assert frontier.popleft_ready(ready)[0] == "http://fast.test/1"
    assert frontier.popleft_ready(ready)[0] == "http://fast.test/2"
    assert frontier.popleft_ready(ready) is None
    assert list(frontier.heads()) == ["http://slow.test/1"]


def test_frontier_survives_a_checkpoint(tmp_path):
    state = CrawlState(str(tmp_path / "state.json"))
    state.frontier.extend([("http://a.test/1", 0), ("http://a.test/2", 0), ("http://b.test/1", 1)])
    state.save()

    loaded = CrawlState(str(tmp_path / "state.json"))
    assert loaded.load()
    assert [loaded.frontier.popleft()[0] for _ in range(3)] == ["http://a.test/1", "http://b.test/1", "http://a.test/2"]


# --- Crawling ---------------------------------------------------------------

def test_follows_links_to_other_sites(site, other_site, make_config):
    site.add("/", "home", links=[other_site.url + "/"])
    other_site.add("/", "elsewhere", links=["/more"])
    other_site.add("/more", "more")

    crawler, results = run(open_config(make_config, max_depth=1))

    assert titles(results) == ["elsewhere", "home", "more"]


def test_depth_counts_hops_between_sites(site, other_site, make_config):
    # Five pages deep on one site, but zero hops away from the seed.
    site.add("/", "home", links=["/1", other_site.url + "/"])
    for n in range(1, 5):
        site.add(f"/{n}", f"page {n}", links=[f"/{n + 1}"])
    site.add("/5", "page 5")
    other_site.add("/", "elsewhere")

    _, results = run(open_config(make_config, max_depth=0))

    assert "page 5" in titles(results)
    assert "elsewhere" not in titles(results)
    assert other_site.hits["/"] == 0


def test_page_budget_per_site(site, other_site, make_config):
    site.add("/", "home", links=[f"/{n}" for n in range(20)] + [other_site.url + "/"])
    for n in range(20):
        site.add(f"/{n}", f"page {n}")
    other_site.add("/", "elsewhere")

    crawler, results = run(open_config(make_config, max_depth=1, max_pages_per_site=3))

    assert len([item for item in results if item["url"].startswith(site.url)]) == 3
    assert "elsewhere" in titles(results)
    assert len(crawler.state.frontier) == 0  # the rest of the busy site was dropped, not kept waiting


def test_a_big_site_cannot_crowd_out_the_others(site, other_site, make_config):
    # The seed has far more links than max_pages, but the other site still gets its turn.
    site.add("/", "home", links=[f"/{n}" for n in range(50)] + [other_site.url + "/"])
    for n in range(50):
        site.add(f"/{n}", f"page {n}")
    other_site.add("/", "elsewhere")

    _, results = run(open_config(make_config, max_depth=1, max_pages=5))

    assert "elsewhere" in titles(results)


def test_a_slow_site_does_not_hold_up_the_crawl(site, other_site, make_config):
    # The seed asks for 30 seconds between requests. Waiting for it would stall every worker.
    site.add_raw("/robots.txt", "User-agent: *\nCrawl-delay: 30\n", "text/plain")
    site.add("/", "home", links=[f"/{n}" for n in range(5)] + [other_site.url + "/"])
    for n in range(5):
        site.add(f"/{n}", f"page {n}")
    other_site.add("/", "elsewhere", links=[f"/{n}" for n in range(5)])
    for n in range(5):
        other_site.add(f"/{n}", f"other {n}")
    config = make_config(open_web=True, max_depth=1)
    config.concurrency.max_workers = 4

    started = time.monotonic()
    crawler, results = run(config)

    assert time.monotonic() - started < 15
    assert titles(results) == sorted(["home", "elsewhere"] + [f"other {n}" for n in range(5)])
    assert len(crawler.state.frontier) == 5  # the slow site's pages, kept for --resume
    assert site.hits["/0"] == 0


def test_frontier_limit(site, make_config):
    site.add("/", "home", links=[f"/{n}" for n in range(30)])

    crawler, _ = run(open_config(make_config, max_frontier=10, max_pages=1))

    assert len(crawler.state.frontier) == 10


def test_closed_crawls_still_stay_home(site, other_site, make_config):
    site.add("/", "home", links=[other_site.url + "/"])
    other_site.add("/", "elsewhere")

    _, results = run(make_config())

    assert titles(results) == ["home"]
    assert other_site.hits["/"] == 0


# --- Command line -----------------------------------------------------------

def test_open_web_option():
    parser = build_parser()
    config = build_config(parser.parse_args(["--urls", "https://example.com/", "--open-web"]), parser)

    assert config.open_web
    assert config.allowed_domains == []
    assert config.max_pages_per_site == OPEN_WEB_PAGES_PER_SITE


def test_page_budget_option():
    parser = build_parser()
    args = parser.parse_args(["--urls", "https://example.com/", "--open-web", "--max-pages-per-site", "7"])

    assert build_config(args, parser).max_pages_per_site == 7


def test_share_buttons_are_not_followed():
    assert is_action("https://www.facebook.com/sharer/sharer.php?u=https://example.com/")
    assert is_action("https://twitter.com/intent/tweet?text=hello")
    assert is_action("https://www.linkedin.com/shareArticle?url=x")
    assert not is_action("https://example.com/articles/sharing-economy")
