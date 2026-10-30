"""CLI interface for production scraper."""
import asyncio
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from my_scraper.config.settings import CrawlConfig, DEFAULT_CONFIG
    from my_scraper.core.crawler import ProductionCrawler
except Exception:
    from config.settings import CrawlConfig, DEFAULT_CONFIG
    from core.crawler import ProductionCrawler


def main():
    parser = argparse.ArgumentParser(description='Production-grade Web Scraper')
    parser.add_argument('--config', type=str, help='Path to config JSON file')
    parser.add_argument('--urls', nargs='+', help='Start URLs')
    parser.add_argument('--output-dir', type=str, default='./output', help='Output directory')
    parser.add_argument('--max-pages', type=int, help='Maximum pages to crawl')
    parser.add_argument('--max-depth', type=int, help='Maximum crawl depth')
    parser.add_argument('--respect-robots', action='store_true', default=True, help='Respect robots.txt')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='Requests per second')
    parser.add_argument('--use-browser', action='store_true', help='Force browser fallback')
    parser.add_argument('--export', nargs='+', choices=['json', 'jsonl', 'csv'], default=['jsonl'], help='Export formats')
    
    args = parser.parse_args()
    
    # Load config
    if args.config:
        with open(args.config, 'r') as f:
            config_dict = json.load(f)
        config = CrawlConfig(**config_dict)
    else:
        config = CrawlConfig()
    
    # Override with CLI args
    if args.urls:
        config.start_urls = args.urls
        config.allowed_domains = list(set([__import__('urllib.parse').urlparse(u).netloc for u in args.urls]))
    if args.max_pages:
        config.max_pages = args.max_pages
    if args.max_depth:
        config.max_depth = args.max_depth
    if args.rate_limit:
        config.rate_limit.requests_per_second = args.rate_limit
    if args.use_browser:
        from config.settings import FetcherType
        config.fetcher = FetcherType.BROWSER
    
    # Create crawler
    crawler = ProductionCrawler(config)
    
    # Define selectors (example)
    selectors = {
        'title': 'h1::text',
        'content': 'p::text'
    }
    
    # Run crawler
    try:
        results = asyncio.run(crawler.crawl(config.start_urls, selectors))
        print(f"\nCrawling completed. Extracted {len(results)} items.")
        print(f"Check {config.logging.audit_log} for audit trail.")
    except KeyboardInterrupt:
        print("\nCrawling interrupted. State saved for resumption.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
