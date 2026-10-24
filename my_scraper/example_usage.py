"""Quick test scraper - limited to 50 products for quick verification."""
import asyncio
import sys
from pathlib import Path
import re
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).parent))

from my_scraper.config.settings import CrawlConfig, FetcherType, ExportFormat
from my_scraper.core.crawler import ProductionCrawler


class QuickBooksScraper:
    """Fast test scraper for books.toscrape.com."""

    def __init__(self, config):
        self.crawler = ProductionCrawler(config)
        self.config = config

    async def crawl(self):
        """Crawl with proper pagination."""
        self.crawler.logger.info("crawl_started", start_url=self.config.start_urls[0])
        
        product_urls = await self._collect_product_urls()
        self.crawler.logger.info("collected_urls", count=len(product_urls))
        
        await self._scrape_product_details(product_urls)
        
        export_results = self.crawler.storage.export(self.crawler.results)
        
        self.crawler.logger.info("crawl_completed", total=len(self.crawler.results), exports=list(export_results.keys()))
        return self.crawler.results
    
    async def _collect_product_urls(self):
        product_urls = []
        current_url = self.config.start_urls[0]
        page = 1
        
        while current_url and len(product_urls) < self.config.max_pages:
            self.crawler.logger.info("fetching_listing", url=current_url, page=page)
            
            fetch_result = await self.crawler.retry_manager.retry_http(
                self.crawler.fetcher.fetch, current_url
            )
            
            if fetch_result['status_code'] != 200:
                break
            
            from parsel import Selector
            sel = Selector(text=fetch_result['content'])
            product_links = sel.css("article.product_pod h3 a::attr(href)").getall()
            
            if not product_links:
                break
            
            for link in product_links:
                product_urls.append(urljoin(current_url, link))
            
            self.crawler.logger.info("found_products", page=page, count=len(product_links))
            
            next_page = sel.css(".next a::attr(href)").get()
            if not next_page:
                break
            
            current_url = urljoin(current_url, next_page)
            page += 1
            await self.crawler.politeness.wait_for_slot(current_url)
        
        return product_urls[:self.config.max_pages]
    
    async def _scrape_product_details(self, product_urls):
        for url in product_urls:
            try:
                if not await self.crawler.politeness.can_fetch(url):
                    continue
                await self.crawler.politeness.wait_for_slot(url)
                
                fetch_result = await self.crawler.retry_manager.retry_http(
                    self.crawler.fetcher.fetch, url
                )
                
                if fetch_result['status_code'] != 200:
                    continue
                
                # Parse with comprehensive selectors
                from parsel import Selector
                sel = Selector(text=fetch_result['content'])
                
                title = sel.css(".product_main h1::text").get()
                price = sel.css(".product_main .price_color::text").get()
                availability = sel.css(".product_main .availability::text").get()
                rating_class = sel.css(".product_main .star-rating::attr(class)").get()
                description = sel.css("#product_description + p::text").get()
                upc = sel.css("table.table tr:nth-child(1) td::text").get()
                product_type = sel.css("table.table tr:nth-child(2) td::text").get()
                price_excl_tax = sel.css("table.table tr:nth-child(3) td::text").get()
                price_incl_tax = sel.css("table.table tr:nth-child(4) td::text").get()
                tax = sel.css("table.table tr:nth-child(5) td::text").get()
                availability_detail = sel.css("table.table tr:nth-child(6) td::text").get()
                num_reviews = sel.css("table.table tr:nth-child(7) td::text").get()
                image = sel.css("#product_gallery img::attr(src)").get()
                
                # Parse rating
                rating_num = 0
                if rating_class:
                    match = re.search(r'star-rating\s+(\w+)', rating_class)
                    if match:
                        rating_map = {'One': 1, 'Two': 2, 'Three': 3, 'Four': 4, 'Five': 5}
                        rating_num = rating_map.get(match.group(1), 0)
                
                # Clean availability
                avail_qty = None
                if availability_detail:
                    match = re.search(r'(\d+)\s+available', availability_detail)
                    if match:
                        avail_qty = int(match.group(1))
                
                data = {
                    "title": title.strip() if title else None,
                    "price": price.replace('£', '').strip() if price else None,
                    "availability": availability_detail.strip() if availability_detail else None,
                    "availability_qty": avail_qty,
                    "rating_num": rating_num,
                    "description": description.strip() if description else None,
                    "upc": upc.strip() if upc else None,
                    "product_type": product_type.strip() if product_type else None,
                    "price_excl_tax": price_excl_tax.replace('£', '').strip() if price_excl_tax else None,
                    "price_incl_tax": price_incl_tax.replace('£', '').strip() if price_incl_tax else None,
                    "tax": tax.replace('£', '').strip() if tax else None,
                    "num_reviews": int(num_reviews) if num_reviews and num_reviews.isdigit() else None,
                    "image": urljoin(url, image) if image else None,
                    "url": url,
                }
                
                self.crawler.results.append(data)
                self.crawler.logger.info("scraped", title=title, url=url)
                
            except Exception as e:
                self.crawler.logger.error("failed", url=url, error=str(e))
        
        return self.crawler.results


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
    
    print(f"\n✅ Extracted {len(results)} products")
    print(f"📁 Check my_scraper/output/ for JSONL/CSV files")


if __name__ == "__main__":
    asyncio.run(main())