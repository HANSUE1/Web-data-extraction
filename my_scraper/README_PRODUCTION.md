# Production-Grade Web Scraper

An async crawler with robots.txt handling, per-domain rate limiting, retries, audit logging and multi-format export.

## Quick Start

Run these from the repository root.

```bash
pip install -r my_scraper/requirements_production.txt

# Crawl a site and choose the fields to extract
python -m my_scraper.cli.main --urls https://quotes.toscrape.com/ \
    --max-pages 20 --export jsonl csv \
    --selector "quote=.text::text" --selector "author=.author::text"

# Continue after pressing Ctrl+C
python -m my_scraper.cli.main --urls https://quotes.toscrape.com/ --resume

# Worked example: 50 products from books.toscrape.com
python my_scraper/example_usage.py

# Run the tests (no network needed)
python -m pytest my_scraper/tests
```

Results go to `./output`, logs to `./logs`, and crawl state to `./data`, all relative to the folder you run from.

## Command Line Options

| Option | Meaning |
| --- | --- |
| `--urls` | Start URLs. Crawling stays on these domains. |
| `--selector NAME=SELECTOR` | Field to extract, CSS or XPath. Repeat for more fields. Default: `h1` and the first `p`. |
| `--max-pages`, `--max-depth` | Crawl limits. Every page attempted counts towards `--max-pages`. |
| `--rate-limit` | Requests per second, per domain. Fractions such as `0.5` work. |
| `--export` | Any of `json`, `jsonl`, `csv`, `sqlite`. |
| `--output-dir` | Where exports are written. |
| `--use-browser` | Fetch every page with a headless browser. |
| `--resume` | Continue from the last saved crawl state. |
| `--config` | JSON file with any `CrawlConfig` setting. Command line options override it. |

Example config file:

```json
{
  "start_urls": ["https://quotes.toscrape.com/"],
  "selectors": {"quote": ".text::text"},
  "fetcher": "static",
  "export_formats": ["csv"],
  "rate_limit": {"requests_per_second": 0.5}
}
```

See `config/settings.py` for all settings.

## What the Crawler Does

**Fetching**
- Static HTTP first, falling back to headless Chromium when a page has almost no visible text (mode `auto`)
- Exponential backoff with jitter; URLs that still fail go to `data/dead_letter_queue.jsonl`
- Concurrent workers, capped overall and per domain

**Politeness**
- robots.txt is cached per site. If it can't be reached the site is treated as disallowed, per RFC 9309, and checked again after a minute
- Each domain is paced separately, and slowed down automatically on 429 and 503 responses
- Requests identify as `robots.user_agent`. Replace the placeholder contact details there before crawling real sites

**Parsing and export**
- HTML by CSS or XPath selector, JSON responses, and PDF text
- `extract_json_ld` and `extract_script_json` are available on `HTMLParser` for custom scrapers
- Export to JSON, JSONL, CSV and SQLite; raw HTML saved to `data/raw_html`

**Compliance**
- Blocks login and paywall URLs by whole path segment (`/login`, `/auth/x`, but not `/author/x`). This is a heuristic based on the URL only
- Takedowns: `ComplianceManager.handle_takedown_request` adds a domain to `logs/takedown_blocklist.txt`, which is enforced on all future crawls
- Flags personal data by field name and by value pattern (emails, phone numbers, SSNs)
- Writes every request outcome to `logs/audit.log`
- `required_fields` limits saved data to those fields

**Resuming**
- State is saved every 100 pages and at exit. After Ctrl+C, results so far are exported and `--resume` continues with the remaining pages, writing a new export file
- After a hard crash, `--resume` still skips finished pages, but items collected since the last export are lost

## Standalone Modules

The crawler does not use these yet. Import them directly if you need them. They have unit tests, but the Redis queue and browser sessions have not been run against a live Redis server or a real site.

| Module | Provides |
| --- | --- |
| `queueing/persistent_queue.py` | SQLite and Redis queues, ETag cache, content de-duplication |
| `session/manager.py` | Cookie sessions and persistent browser sessions |
| `utils/rotation.py` | Proxy pool and header generation |
| `monitoring/metrics.py` | Prometheus metrics, signed webhooks, tracing |
| `models/schemas.py` | Pydantic validation for scraped items |

## Architecture

```
my_scraper/
├── cli/            # Command-line interface
├── config/         # Configuration
├── core/           # Crawler orchestrator
├── fetcher/        # Static fetcher with browser fallback
├── parser/         # HTML, JSON and PDF parsing
├── storage/        # Exporters
├── compliance/     # Audit trail, URL policy, personal data checks
├── utils/          # Politeness, retry, rotation
├── logutil/        # Structured logging
├── queueing/       # Persistent queues (standalone)
├── session/        # Sessions (standalone)
├── monitoring/     # Metrics (standalone)
├── models/         # Item schemas (standalone)
└── tests/
```

`scrape_simple.py` in the repository root is a separate quick tool for one-page extraction. It does not check robots.txt.

## Legal Notice

This scraper is designed for ethical use only. Always:
- Check and respect robots.txt
- Only scrape publicly accessible data
- Comply with website terms of service
- Consider GDPR/CCPA implications
- Obtain legal review when scraping personal data
- Have a takedown response process
