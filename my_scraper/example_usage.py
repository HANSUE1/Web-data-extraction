"""Example usage of production scraper."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import CrawlConfig, FetcherType, ExportFormat
from core.crawler import ProductionCrawler


async def main():
    # Create production config
    config = CrawlConfig(
        name="example_production_scraper",
        start_urls=["https://books.toscrape.com/"],
        allowed_domains=["books.toscrape.com"],
        max_depth=2,
        max_pages=100,
        fetcher=FetcherType.AUTO,
        export_formats=[ExportFormat.JSONL, ExportFormat.CSV],
        required_fields=["title", "price", "url"],
    )
    
    # Configure politeness
    config.rate_limit.requests_per_second = 1.0
    config.robots.respect = True
    
    # Configure concurrency
    config.concurrency.max_workers = 5
    config.concurrency.max_per_domain = 2
    
    # Configure retries
    config.retry.max_retries = 3
    
    # Create crawler
    crawler = ProductionCrawler(config)
    
    # Define extraction schema
    selectors = {
        "title": "h3 a::attr(title)",
        "price": ".price_color::text",
        "url": "h3 a::attr(href)"
    }
    
    # Run crawler
    results = await crawler.crawl(config.start_urls, selectors)
    
    print(f"Extracted {len(results)} items")
    print(f"Audit log: {config.logging.audit_log}")


if __name__ == "__main__":
    asyncio.run(main())
