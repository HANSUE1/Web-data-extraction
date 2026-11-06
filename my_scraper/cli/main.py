"""CLI interface for production scraper.

Run from the repository root with `python -m my_scraper.cli.main`, or directly
with `python my_scraper/cli/main.py`.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from my_scraper.config.settings import CrawlConfig, ExportFormat, FetcherType  # noqa: E402
from my_scraper.core.crawler import ProductionCrawler  # noqa: E402

DEFAULT_SELECTORS = {"title": "h1::text", "content": "p::text"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Production-grade Web Scraper")
    parser.add_argument("--config", type=str, help="Path to config JSON file")
    parser.add_argument("--urls", nargs="+", help="Start URLs (with http:// or https://)")
    parser.add_argument("--output-dir", type=str, help="Output directory (default: ./output)")
    parser.add_argument("--max-pages", type=int, help="Maximum pages to crawl")
    parser.add_argument("--max-depth", type=int, help="Maximum crawl depth")
    parser.add_argument("--respect-robots", action="store_true", default=None,
                        help="Respect robots.txt (the default; overrides a config file that disables it)")
    parser.add_argument("--rate-limit", type=float, help="Requests per second, per domain (default: 1.0)")
    parser.add_argument("--use-browser", action="store_true", help="Force browser fallback")
    parser.add_argument("--export", nargs="+", choices=[fmt.value for fmt in ExportFormat],
                        help="Export formats (default: jsonl)")
    parser.add_argument("--selector", action="append", metavar="NAME=SELECTOR",
                        help="Field to extract, as a CSS or XPath selector. Repeat for more fields. "
                             "Example: --selector \"title=h1::text\"")
    parser.add_argument("--resume", action="store_true", help="Continue the last interrupted crawl (pass the same --urls)")
    return parser


def parse_selectors(pairs: List[str], parser: argparse.ArgumentParser) -> Dict[str, str]:
    selectors = {}
    for pair in pairs:
        name, separator, selector = pair.partition("=")
        if not (name and separator and selector):
            parser.error(f"--selector must look like NAME=SELECTOR, got: {pair}")
        selectors[name] = selector
    return selectors


def domains_of(urls: List[str]) -> List[str]:
    return sorted({urlparse(url).netloc for url in urls})


def build_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> CrawlConfig:
    """Load the config file, if any, then apply command line overrides."""
    config = CrawlConfig()
    if args.config:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = CrawlConfig.from_dict(json.load(f))
        except (OSError, ValueError, TypeError) as e:
            parser.error(f"could not load config {args.config}: {e}")

    if args.urls:
        config.start_urls = args.urls
        config.allowed_domains = domains_of(args.urls)
    elif not config.allowed_domains:
        config.allowed_domains = domains_of(config.start_urls)

    for url in config.start_urls:
        if urlparse(url).scheme not in ("http", "https"):
            parser.error(f"start URL must begin with http:// or https://: {url}")
    if not config.start_urls:
        parser.error("no start URLs: pass --urls, or set start_urls in --config")

    if args.max_pages is not None:
        config.max_pages = args.max_pages
    if args.max_depth is not None:
        config.max_depth = args.max_depth
    if args.rate_limit is not None:
        config.rate_limit.requests_per_second = args.rate_limit
    if args.output_dir is not None:
        config.output_dir = args.output_dir
    if args.respect_robots:
        config.robots.respect = True
    if args.use_browser:
        config.fetcher = FetcherType.BROWSER
    if args.export:
        config.export_formats = [ExportFormat(fmt) for fmt in args.export]
    if args.selector:
        config.selectors = parse_selectors(args.selector, parser)
    elif not config.selectors:
        config.selectors = dict(DEFAULT_SELECTORS)

    return config


def print_summary(crawler: ProductionCrawler, heading: str):
    print(f"\n{heading} Extracted {len(crawler.results)} items from {len(crawler.state.visited)} pages.")
    for fmt, path in crawler.exports.items():
        print(f"  {fmt}: {path or 'nothing to export'}")
    print(f"  audit trail: {crawler.config.logging.audit_log}")


def main(argv: Optional[List[str]] = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    config = build_config(args, parser)
    crawler = ProductionCrawler(config)

    try:
        asyncio.run(crawler.crawl(resume=args.resume))
        print_summary(crawler, "Crawling completed.")
    except KeyboardInterrupt:
        print_summary(crawler, "Crawling interrupted, partial results saved. Continue with --resume.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
