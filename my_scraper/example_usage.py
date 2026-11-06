"""Quick test scraper - limited to 50 products for quick verification."""
import asyncio
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from parsel import Selector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from my_scraper.config.settings import CrawlConfig, ExportFormat, FetcherType  # noqa: E402
from my_scraper.core.crawler import ProductionCrawler  # noqa: E402

RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def clean_text(value: Optional[str]) -> Optional[str]:
    return value.strip() if value else None


def clean_price(value: Optional[str]) -> Optional[str]:
    return value.replace("£", "").strip() if value else None


def parse_product(html: str, url: str) -> Dict[str, Any]:
    """Extract one book from a books.toscrape.com product page."""
    sel = Selector(text=html)

    def table_row(number: int) -> Optional[str]:
        return sel.css(f"table.table tr:nth-child({number}) td::text").get()

    rating_class = sel.css(".product_main .star-rating::attr(class)").get() or ""
    rating = re.search(r"star-rating\s+(\w+)", rating_class)

    availability = table_row(6)
    quantity = re.search(r"(\d+)\s+available", availability or "")

    num_reviews = table_row(7)
    image = sel.css("#product_gallery img::attr(src)").get()

    return {
        "title": clean_text(sel.css(".product_main h1::text").get()),
        "price": clean_price(sel.css(".product_main .price_color::text").get()),
        "availability": clean_text(availability),
        "availability_qty": int(quantity.group(1)) if quantity else None,
        "rating_num": RATINGS.get(rating.group(1), 0) if rating else 0,
        "description": clean_text(sel.css("#product_description + p::text").get()),
        "upc": clean_text(table_row(1)),
        "product_type": clean_text(table_row(2)),
        "price_excl_tax": clean_price(table_row(3)),
        "price_incl_tax": clean_price(table_row(4)),
        "tax": clean_price(table_row(5)),
        "num_reviews": int(num_reviews) if num_reviews and num_reviews.isdigit() else None,
        "image": urljoin(url, image) if image else None,
        "url": url,
    }


class QuickBooksScraper:
    """Fast test scraper for books.toscrape.com."""

    def __init__(self, config):
        self.crawler = ProductionCrawler(config)
        self.config = config

    async def crawl(self) -> List[Dict[str, Any]]:
        """Crawl with proper pagination."""
        logger = self.crawler.logger
        logger.info("crawl_started", start_url=self.config.start_urls[0])

        try:
            product_urls = await self._collect_product_urls()
            logger.info("collected_urls", count=len(product_urls))
            await self._scrape_product_details(product_urls)
        finally:
            await self.crawler.close()

        self.crawler.exports = self.crawler.storage.export(self.crawler.results)
        logger.info("crawl_completed", total=len(self.crawler.results), exports=self.crawler.exports)
        return self.crawler.results

    async def _collect_product_urls(self) -> List[str]:
        product_urls = []
        current_url = self.config.start_urls[0]
        page = 1

        while current_url and len(product_urls) < self.config.max_pages:
            self.crawler.logger.info("fetching_listing", url=current_url, page=page)

            fetch_result = await self.crawler.fetch_page(current_url)
            if fetch_result is None:
                break

            sel = Selector(text=fetch_result["content"])
            product_links = sel.css("article.product_pod h3 a::attr(href)").getall()
            if not product_links:
                break

            product_urls.extend(urljoin(current_url, link) for link in product_links)
            self.crawler.logger.info("found_products", page=page, count=len(product_links))

            next_page = sel.css(".next a::attr(href)").get()
            current_url = urljoin(current_url, next_page) if next_page else None
            page += 1

        return product_urls[:self.config.max_pages]

    async def _scrape_product_details(self, product_urls: List[str]):
        crawler = self.crawler
        for url in product_urls:
            try:
                fetch_result = await crawler.fetch_page(url)
                if fetch_result is None:
                    continue

                data = parse_product(fetch_result["content"], url)
                crawler.results.append(data)
                crawler.compliance.log_scrape_event(url, "success", 1)
                crawler.logger.info("scraped", title=data["title"], url=url)
            except Exception as e:
                crawler.logger.error("failed", url=url, error=str(e))
                crawler.compliance.log_scrape_event(url, "failed", error=str(e))


async def main():
    config = CrawlConfig(
        name="books_test",
        start_urls=["https://books.toscrape.com/catalogue/page-1.html"],
        allowed_domains=["books.toscrape.com"],
        max_depth=1,
        max_pages=50,
        fetcher=FetcherType.AUTO,
        export_formats=[ExportFormat.JSONL, ExportFormat.CSV],
        required_fields=["title", "price", "url", "availability", "rating_num", "description", "upc", "image"],
    )

    config.rate_limit.requests_per_second = 10.0
    config.robots.respect = True
    config.concurrency.max_workers = 15
    config.concurrency.max_per_domain = 8
    config.retry.max_retries = 3

    scraper = QuickBooksScraper(config)
    results = await scraper.crawl()

    # Plain ASCII: emoji crash print() on Windows when output is piped to a file.
    print(f"\nExtracted {len(results)} products")
    for fmt, path in scraper.crawler.exports.items():
        print(f"  {fmt}: {path}")


if __name__ == "__main__":
    asyncio.run(main())
