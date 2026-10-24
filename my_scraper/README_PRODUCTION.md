# Production-Grade Web Scraper

A complete, compliant, and resilient web scraping framework meeting all production requirements.

## Features

### Functional Requirements
- **Hybrid Fetching**: Static HTTP requests with automatic fallback to headless browser (Playwright) for JS-rendered pages
- **Smart Retry Logic**: Exponential backoff with jitter, capped retries, dead-letter queue for failed URLs
- **Concurrent Fetching**: Async workers with semaphores, respecting per-domain rate limits
- **Multi-format Parsing**: CSS/XPath selectors, JSON-LD extraction, JSON in script tags, PDF support
- **Pagination & Depth Control**: Follow next links with configurable crawl depth
- **Multi-format Export**: JSON, JSONL, CSV, SQLite, PostgreSQL, MongoDB support
- **Raw HTML Storage**: Blob storage with metadata pointers for reprocessing

### Non-Functional Requirements
- **Robots.txt Compliance**: Parse and cache rules per domain
- **Rate Limiting**: Sliding window algorithm, default 1 req/s per domain
- **Error Backoff**: Automatic rate reduction on 429/503 responses
- **Jitter**: Random delays to prevent thundering herd
- **Pipeline Architecture**: Separate fetch/parse/storage stages
- **Checkpointing**: Resume interrupted crawls from saved state
- **Structured Logging**: JSON logs with request details, durations, retry counts
- **Horizontal Scaling**: Stateless workers with shared queue/database
- **Proxy & UA Rotation**: Support for proxy and User-Agent rotation

### Legal & Ethical Compliance
- **Public Data Only**: Blocks login/paywall URLs
- **Data Minimization**: Only collects specified schema fields
- **PII Detection**: Scans for personal data fields
- **Audit Trail**: Logs every request with full context
- **Takedown Support**: Process for handling complaints
- **Jurisdiction Awareness**: GDPR/CCPA/CPRA considerations

## Quick Start

```bash
# Install dependencies
pip install -r requirements_production.txt

# Run example
python example_usage.py

# Use CLI
python cli/main.py --urls https://example.com --max-pages 100 --export jsonl csv
```

## Configuration

See `config/settings.py` for all configuration options.

## Architecture

```
my_scraper/
├── config/         # Configuration
├── core/           # Crawler orchestrator
├── fetcher/        # Smart fetcher with fallback
├── parser/         # Multi-format parser
├── storage/        # Multi-format exporters
├── queue/          # Queue management
├── utils/          # Retry, politeness, etc.
├── logging/        # Structured logging
├── compliance/     # Legal/ethical enforcement
└── cli/            # Command-line interface
```

## Legal Notice

This scraper is designed for ethical use only. Always:
- Check and respect robots.txt
- Only scrape publicly accessible data
- Comply with website terms of service
- Consider GDPR/CCPA implications
- Obtain legal review when scraping personal data
- Have a takedown response process
