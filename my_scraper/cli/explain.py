"""--explain: show what each method finds on a page, so a bad result has a visible cause."""
from typing import Any, Dict, List

from my_scraper.core.crawler import ProductionCrawler
from my_scraper.core.page_checks import classify_page
from my_scraper.parser.auto_extractor import compare_methods
from my_scraper.parser.universal_parser import ContentTypeDetector

SAMPLE_ITEMS = 3
SAMPLE_WIDTH = 70


def _show_method(name: str, items: List[Dict[str, Any]]):
    print(f"  {name:12} {len(items)} item(s)")
    for item in items[:SAMPLE_ITEMS]:
        sample = item.get("title") or next((value for value in item.values() if isinstance(value, str)), "")
        print(f"      {str(sample)[:SAMPLE_WIDTH]}")


async def _report(crawler: ProductionCrawler, url: str, render: bool):
    result = await crawler.fetch_page(url, render=render)
    label = "Browser" if render else "Plain HTTP"
    if result is None:
        statuses = dict(crawler.compliance.status_counts)
        print(f"\n{label}: no page. {statuses} {crawler.compliance.first_errors}")
        return

    kind = ContentTypeDetector.detect(result["content"], result.get("headers"), url, result.get("body"))
    print(f"\n{label}: HTTP {result['status_code']}, {kind}, {len(result['content'])} characters")
    if result.get("final_url") != url:
        print(f"  redirected to {result.get('final_url')}")

    verdict = classify_page(result)
    if verdict:
        print(f"  recognised as: {verdict}")

    if kind == "html":
        methods = compare_methods(result["content"], url, result.get("json_payloads") or [])
        for name, items in methods.items():
            _show_method(name, items)

    page = crawler.parser.parse_page(result, crawler._selectors, crawler._item_selector)
    print(f"  chosen: {len(page.items)} item(s) from {sorted({str(item.get('source')) for item in page.items})}")
    for error in page.errors[:3]:
        print(f"  problem: {error}")


async def explain(config):
    """Fetch the first start URL both ways and print what every method makes of it."""
    url = config.start_urls[0]
    crawler = ProductionCrawler(config)
    try:
        print(f"Explaining {url}")
        await _report(crawler, url, render=False)
        if crawler.fetcher.fallback_enabled:
            try:
                await _report(crawler, url, render=True)
            except Exception as e:
                print(f"\nBrowser: failed: {e}")
    finally:
        await crawler.close()
