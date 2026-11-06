"""CLI interface for production scraper.

Run from the repository root with `python -m my_scraper.cli.main`, or directly
with `python my_scraper/cli/main.py`.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from my_scraper.cli.explain import explain  # noqa: E402
from my_scraper.config.settings import CrawlConfig, ExportFormat, FetcherType  # noqa: E402
from my_scraper.core.crawler import ProductionCrawler  # noqa: E402


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
    parser.add_argument("--use-browser", action="store_true", help="Fetch every page with the browser")
    parser.add_argument("--no-browser", action="store_true", help="Never use the browser, plain HTTP only")
    parser.add_argument("--export", nargs="+", choices=[fmt.value for fmt in ExportFormat],
                        help="Export formats (default: jsonl)")
    parser.add_argument("--selector", action="append", metavar="NAME=SELECTOR",
                        help="Field to extract, as a CSS or XPath selector. Repeat for more fields. "
                             "End the name with [] to keep every match. Without this option, items "
                             "are found automatically. Example: --selector \"title=h1::text\"")
    parser.add_argument("--item", metavar="SELECTOR",
                        help="One item per match, with --selector fields relative to it. "
                             "Example: --item .product --selector \"name=h3::text\"")
    parser.add_argument("--example", action="append", metavar="NAME=VALUE",
                        help="Teach by example: a value you can see on the first page. The scraper "
                             "works out the selectors. Repeat for each field of one item. "
                             "Example: --example \"title=A Light in the Attic\" --example \"price=£51.77\"")
    parser.add_argument("--markdown", action="store_true",
                        help="Add a markdown field with the main content of single-item pages")
    parser.add_argument("--sitemap", action="store_true", help="Also find pages from the site's sitemaps")
    parser.add_argument("--discover", action="store_true",
                        help="Also use the site's sitemaps, feeds and public APIs (RSS, WordPress, Shopify)")
    parser.add_argument("--no-pagination", action="store_true",
                        help="Count next-page links against --max-depth like any other link")
    parser.add_argument("--include", action="append", metavar="REGEX",
                        help="Only follow links matching this pattern. Repeat to allow several")
    parser.add_argument("--exclude", action="append", metavar="REGEX",
                        help="Never follow links matching this pattern. Repeat for several")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip pages that have not changed since the last run")
    parser.add_argument("--resume", action="store_true", help="Continue the last interrupted crawl (pass the same --urls)")
    parser.add_argument("--quiet", action="store_true", help="Keep logs to the log file and show only the summary")
    parser.add_argument("--explain", action="store_true",
                        help="Show what each extraction method finds on the first URL, and exit")
    return parser


def parse_pairs(pairs: List[str], option: str, parser: argparse.ArgumentParser) -> Dict[str, str]:
    values = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not (name and separator and value):
            parser.error(f"{option} must look like NAME=VALUE, got: {pair}")
        values[name] = value
    return values


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
    if args.use_browser and args.no_browser:
        parser.error("--use-browser and --no-browser contradict each other")
    if args.use_browser:
        config.fetcher = FetcherType.BROWSER
    if args.no_browser:
        config.fetcher = FetcherType.STATIC
    if args.export:
        config.export_formats = [ExportFormat(fmt) for fmt in args.export]
    if args.selector:
        config.selectors = parse_pairs(args.selector, "--selector", parser)
    if args.example:
        config.examples = parse_pairs(args.example, "--example", parser)
    if args.item:
        config.item_selector = args.item
    if config.item_selector and not config.selectors:
        parser.error("--item needs at least one --selector to say which fields to take")
    if args.markdown:
        config.include_markdown = True
    if args.sitemap:
        config.use_sitemap = True
    if args.discover:
        config.discover = True
        config.use_sitemap = True
    if args.no_pagination:
        config.follow_pagination = False
    if args.incremental:
        config.incremental = True
    if args.include:
        config.include_patterns = args.include
    if args.exclude:
        config.exclude_patterns = args.exclude
    if args.quiet:
        config.logging.console = False

    for pattern in config.include_patterns + config.exclude_patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            parser.error(f"invalid pattern {pattern!r}: {e}")

    return config


def print_summary(crawler: ProductionCrawler, heading: str):
    print(f"\n{heading} Extracted {len(crawler.results)} items from {len(crawler.state.visited)} pages.")
    compliance = crawler.compliance
    for status, count in compliance.status_counts.most_common():
        reason = compliance.first_errors.get(status)
        print(f"  {status}: {count}" + (f" (first: {reason[:150]})" if reason else ""))
    for fmt, path in crawler.exports.items():
        print(f"  {fmt}: {path or 'nothing to export'}")
    for fmt, error in crawler.storage.errors.items():
        print(f"  {fmt} export failed: {error}")
    if not crawler.results:
        print("  nothing extracted: --explain shows what each method found on the page")
    if crawler.learned_selectors:
        item, fields = crawler.learned_selectors
        flags = " ".join(f'--selector "{name}={selector}"' for name, selector in fields.items())
        print(f'  learned, reuse with: --item "{item}" {flags}')
    print(f"  audit trail: {crawler.config.logging.audit_log}")


def main(argv: Optional[List[str]] = None):
    # Scraped text can hold any character, and a Windows console or pipe cannot show them all.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)
    config = build_config(args, parser)

    if args.explain:
        config.logging.console = False
        asyncio.run(explain(config))
        return

    try:
        crawler = ProductionCrawler(config)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

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
