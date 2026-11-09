# Production-Grade Web Scraper

Point it at a site and it works out what the data is. It tries several independent methods on every page, checks whether the result looks right, tries harder when it doesn't, and tells you honestly when a site can't be read.

No scraper reads every website. Some sites refuse automated access, some data is behind a login, and "what counts as an item" is sometimes a human judgement. See [What It Cannot Do](#what-it-cannot-do).

## Quick Start

Run these from the repository root.

```bash
pip install -r my_scraper/requirements_production.txt

# Point it at any site: items are found automatically, no selectors needed
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --max-pages 20 --export jsonl csv

# Only listing pages, following "next" links to the end
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --max-depth 0

# Find pages from the sitemap, and only keep part of the site
python -m my_scraper.cli.main --urls https://www.gov.uk/ --sitemap --include "guidance" --max-pages 50

# Use everything the site offers: sitemaps, RSS feeds, WordPress and Shopify APIs
python -m my_scraper.cli.main --urls https://wordpress.org/news/ --discover --max-pages 100

# Automatic result looks wrong? See what each method found
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --explain

# Or teach it: give values you can see on the page and it writes the selectors
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --max-depth 0 \
    --example "title=A Light in the Attic" --example "price=£51.77"

# Choose exactly what you want: one item per .quote, keeping every tag
python -m my_scraper.cli.main --urls https://quotes.toscrape.com/ --item .quote \
    --selector "text=.text::text" --selector "author=.author::text" --selector "tags[]=.tag::text"

# Run again later and only collect what changed
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --incremental

# Watch a site: list what is new, changed or gone since the last run
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --max-depth 0 --track

# Crawl the open web: follow links to any site, at most 50 pages per site
python -m my_scraper.cli.main --urls https://news.ycombinator.com/ --open-web --max-pages 500 --max-depth 2

# Continue after pressing Ctrl+C or a crash
python -m my_scraper.cli.main --urls https://books.toscrape.com/ --resume

# Worked example: 50 products from books.toscrape.com
python my_scraper/example_usage.py

# Run the tests (no network needed)
python -m pytest my_scraper/tests

# Measure it against real websites
python my_scraper/benchmark/run.py --out results.json
python my_scraper/benchmark/run.py --sites my_scraper/benchmark/sites_holdout.json
```

Results go to `./output`, logs to `./logs`, and crawl state to `./data`, all relative to the folder you run from.

## For AI Assistants and Other Programs

The scraper also runs as a service, so AI assistants and your own code can use it without the command line. Both versions offer the same four requests, with the same robots.txt and compliance rules as a crawl:

| Request | What it returns |
| --- | --- |
| `scrape_url` / `POST /v1/scrape` | One page: its main content as clean Markdown (no menus, ads or cookie banners), title, author, date, and the items on it |
| `crawl_site` / `POST /v1/crawl` | Several pages of one site, each as Markdown, plus the items found. For documentation, blogs and catalogues |
| `map_site` / `POST /v1/map` | The pages a site has, from its sitemaps and links, optionally filtered by `search` |
| `extract` / `POST /v1/extract` | Named fields, such as `["price", "brand", "rating"]`, from structured data and the items on a page. `many` gives one record per item on a listing. Fields it can't find are listed, with the page's Markdown attached |

A page that can't be fetched comes back as an error that says why, such as `robots_disallowed` or `login_required`, never as an empty result.

**MCP server**, for Claude Code, Claude Desktop, Cursor and other assistants:

```bash
pip install mcp
python -m my_scraper.service.mcp_server                       # stdio, for a local assistant
python -m my_scraper.service.mcp_server --transport streamable-http --port 8788
```

`.mcp.json` in the repository root registers it for Claude Code, which asks once before starting it. The path in it is absolute, so change it if the repository moves. For Claude Desktop, add the same entry to its `claude_desktop_config.json`.

**HTTP API:**

```bash
python -m my_scraper.service.http_api --port 8787
curl -X POST http://127.0.0.1:8787/v1/scrape -H "Content-Type: application/json" -d "{\"url\": \"https://example.com/\"}"
```

`GET /v1` lists each request's fields. The API listens on this computer only. To open it to others, pass `--host 0.0.0.0` with `--api-key` (or set `SCRAPER_API_KEY`), and send `Authorization: Bearer <key>`.

Both keep their logs and audit trail in `~/.web-data-extraction` (set `SCRAPER_HOME` to move it), and identify with `SCRAPER_USER_AGENT` when it is set. Requests share pacing, so two requests to one site are still paced as one visitor. A crawl reads at most 200 pages and stops after `timeout` seconds (default 120), returning what it has.

## Command Line Options

| Option | Meaning |
| --- | --- |
| `--urls` | Start URLs. Crawling stays on these domains. |
| `--selector NAME=SELECTOR` | Field to extract, CSS or XPath. Repeat for more. End the name with `[]` to keep every match. Without this, items are found automatically. |
| `--item SELECTOR` | One item per match, with `--selector` fields relative to it. In XPath, start them with `.//`. |
| `--example NAME=VALUE` | Teach by example: a value visible on the first page. Repeat for each field of one item. The learned selectors are printed for reuse. |
| `--explain` | Show what each method finds on the first URL, with plain HTTP and with the browser, then exit. |
| `--markdown` | Add a `markdown` field with the main content of single-item pages. |
| `--sitemap` | Also find pages from the site's sitemaps. |
| `--discover` | Also use sitemaps, RSS and Atom feeds, and public WordPress, WooCommerce and Shopify APIs. |
| `--include`, `--exclude` | Regular expressions choosing which links to follow. Repeatable. |
| `--max-pages`, `--max-depth` | Crawl limits. Every page attempted counts towards `--max-pages`. Next-page links don't use up depth, so `--max-depth 0` walks a whole listing. |
| `--open-web` | Follow links to any site, not only the start URLs' sites. See "Crawling the Open Web". |
| `--max-pages-per-site` | Most pages taken from one site. Defaults to 50 with `--open-web`, no limit otherwise. |
| `--no-pagination` | Treat next-page links like any other link. |
| `--incremental` | Skip pages that have not changed since the last run. |
| `--track` | Compare with the last run of the same crawl and report new, changed and removed items. See "Tracking changes" under What the Crawler Does. |
| `--rate-limit` | Requests per second, per domain. Fractions such as `0.5` work. |
| `--export` | Any of `json`, `jsonl`, `csv`, `sqlite`. |
| `--output-dir` | Where exports are written. |
| `--use-browser`, `--no-browser` | Force the browser for every page, or never use it. By default it is used when a page needs it. |
| `--quiet` | Keep logs to the log file and print only the summary. |
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

## How Items Are Found Automatically

With no selectors, independent methods look at each page and the best answer wins. The `source` field of every item says which one was used.

| `source` | What it means | Typical pages |
| --- | --- | --- |
| `json-ld`, `microdata` | Structured data the site publishes, with normalised fields such as `price`, `author` and `rating` | Products, articles, recipes, events |
| `list` | Repeated blocks in the main content: `title`, `url`, `image`, `price`, `date`, `author`, `rating`, `text` | Listings, search results, card grids |
| `table` | Rows of the main data table, keyed by column name | Statistics, directories |
| `embedded` | Data inside the page's scripts, ready for its own JavaScript | React, Vue and Next.js sites |
| `network` | JSON the page fetched from its own site while rendering | Pages that load data on demand |
| `document` | The page as one item: main text and metadata, plus `attributes` from specification tables | Articles, product detail pages |
| `feed`, `api` | RSS and Atom entries, and WordPress or Shopify records | With `--discover` |
| `csv`, `xlsx`, `pdf`, `docx`, `xml`, `text`, `json` | The contents of that kind of file | Direct links to files |

To tell content from page furniture, it finds the main area, skips menus, footers and hidden elements, and scores each repeated block on what real items have: a proper title, a specific link, an image, a price, a date. Pages with several lists keep them all. An item seen on a listing and again on its own page is merged into one.

**Results are judged, not just accepted.** A page that produces nothing, or a thin document, is fetched again with the browser, and a list with no next-page link is checked once per site in case it scrolls forever. If the browser does better, the rest of that site uses it directly.

**What the browser does:** waits for slow content, scrolls, presses "load more", steps through pagination buttons and tabs, and reads iframes and shadow DOM. It identifies itself honestly and does not try to avoid detection.

Automatic extraction goes by page structure, so it can pick the wrong block on unusual layouts. Check a sample of the results. When you need certainty, use `--example`, or `--item` and `--selector`.

## What the Crawler Does

**Fetching**
- Static HTTP first, with headless Chromium when the result shows a page needs it (mode `auto`)
- Character encodings are read from the BOM, HTTP header or page, detected when undeclared, and corrected when declared wrongly
- Meta refresh redirects are followed, and responses are capped at 30 MB
- Exponential backoff with jitter; URLs that still fail go to `data/dead_letter_queue.jsonl`
- Concurrent workers, capped overall and per domain

**Politeness**
- robots.txt rules are applied in full, including wildcards and rule priority. They are cached per site. If the file can't be reached the site is skipped, per RFC 9309, and checked again after a minute. That shows up as `robots_unreachable` with the reason, so it can't be confused with a real `robots_disallowed`
- Each domain is paced separately, slowed down automatically on 429 and 503 responses, and never faster than its `Crawl-delay`
- `Retry-After` is obeyed once. A server that keeps rate limiting, or asks for a very long wait, ends that URL with a clear message
- After 5 refusals in a row a site is left alone, and its remaining pages are kept for `--resume`
- Requests identify as `robots.user_agent`. Replace the placeholder contact details there before crawling real sites

**Parsing and export**
- Automatic extraction, CSS or XPath selectors, and files of many kinds, recognised by their content as well as their label
- Prices in many currencies and formats, lazy-loaded images, dates, authors and ratings
- Export to JSON, JSONL, CSV and SQLite; raw HTML saved to `data/raw_html`
- A format that fails to export doesn't affect the others, and CSV cells that would run as spreadsheet formulas are neutralised

**Compliance**
- Blocks login and paywall URLs by whole path segment (`/login`, `/auth/x`, but not `/author/x`). This is a heuristic based on the URL only
- Recognises pages that look successful but aren't, and never stores them as data: `blocked_by_site`, `login_required`, `soft_404`, `consent_wall` and `off_site_redirect`
- Takedowns: `ComplianceManager.handle_takedown_request` adds a domain to `logs/takedown_blocklist.txt`, which is enforced on all future crawls
- Flags personal data by field name and by value pattern (emails, phone numbers, SSNs)
- Writes every request outcome to `logs/audit.log`
- `required_fields` limits saved data to those fields

**Resuming**
- State is saved when a crawl starts, every 10 pages and at exit, and each item is recorded the moment it is found
- `--resume` continues with the remaining pages and writes one complete export, including items from before the interruption. After a hard crash, up to 10 recent pages are fetched again, with no duplicate items

**Repeat runs**
- `--incremental` asks the server whether each page changed (ETag, Last-Modified), and compares content when the server can't say
- Unchanged pages produce no items, so the export holds only what is new or changed. Their links are still followed, using the saved copy in `data/raw_html`, so `save_raw_html` should stay on

**Tracking changes**
- `--track` keeps a snapshot of each crawl's items in `data/snapshots`, one per set of start URLs and selectors
- The next run writes `output/changes_<time>.json` listing new items, removed items, and changed items with each field's `before` and `after`, and the summary shows the first few, such as `price £51.77 -> £45.00`
- Items are matched by their link plus their title, or the first column for table rows. Which method found an item is not counted as a change
- An item is only reported removed when its page was read again and no longer has it. Items on pages that failed, were unchanged under `--incremental`, or fell outside `--max-pages` are kept in the snapshot, so a partial run never reports half the site as deleted
- The first run only saves the baseline. Delete the snapshot file to start over

**URLs and links**
- Fragments, tracking parameters and session ids are removed, so one page is only crawled once. `#/` and `#!` routes are kept, as they are separate pages in single-page apps
- Images, stylesheets, archives and similar files are skipped
- `example.com` and `www.example.com` count as the same site, and a start URL that moves to another host takes the crawl with it
- Links that do something (`/cart`, `/logout`, `?add-to-cart=`) are never followed, nor are hidden links, `rel="nofollow"` links or anything on a page with a `nofollow` robots tag
- Crawler traps are avoided: repeating paths, absurdly long URLs, and endless filter combinations
- `<base href>`, frames, image maps and `onclick` links are all understood

## Crawling the Open Web

`--open-web` turns the crawler loose from its start sites. It follows links to any site, with the same robots.txt rules, pacing and compliance checks as a single-site crawl. The start URLs are only seeds.

- **Depth counts hops between sites.** A link within a site keeps its depth, a link to another site adds one. `--max-depth 0` crawls only the seed sites, `--max-depth 2` reaches sites two links away
- **Every site gets a turn.** Waiting pages are kept per site and taken from each in turn, so one large site can't fill the crawl, and newly found sites are reached soon after they turn up
- **Budgets keep it finite.** `--max-pages-per-site` (default 50) caps each site, which also bounds crawler traps. Each site keeps at most four times its budget waiting, and the whole frontier at most `max_frontier` pages (200,000). Links beyond that are dropped
- **Slow sites don't stall it.** A page is only started once its site is ready for another request, so a site asking for `Crawl-delay: 600` never ties up a worker. When only such sites are left, the crawl stops and keeps their pages for `--resume`
- **Dead sites are dropped quickly.** A site whose robots.txt can't be reached counts as a refusal, and after 5 refusals its remaining pages are set aside
- **The browser is used sparingly.** One attempt per site, and no check for infinite scrolling
- "Share this" links to social networks are not followed

The summary shows how many sites were visited and how many pages are still queued. `--resume` continues from there, so a long crawl can run in sessions.

Before a large open-web crawl, set `robots.user_agent` to a name and contact address of your own, so site owners can reach you. Each page is extracted automatically as usual, so results mix lists, tables, embedded data and one `document` item per article, and the `source` field tells them apart. All visited URLs are held in memory, which is fine for hundreds of thousands of pages but not for millions.

## Standalone Modules

The crawler does not use these yet. Import them directly if you need them. They have unit tests, but the Redis queue and browser sessions have not been run against a live Redis server or a real site.

| Module | Provides |
| --- | --- |
| `queueing/persistent_queue.py` | SQLite and Redis queues, content de-duplication (its ETag cache is used by `--incremental`) |
| `session/manager.py` | Cookie sessions and persistent browser sessions |
| `utils/rotation.py` | Proxy pool and header generation |
| `monitoring/metrics.py` | Prometheus metrics, signed webhooks, tracing |
| `models/schemas.py` | Pydantic validation for scraped items |

## Architecture

```
my_scraper/
├── cli/            # Command-line interface
├── service/        # MCP server and HTTP API
├── config/         # Configuration
├── core/           # Crawler, link discovery, page checks
├── fetcher/        # HTTP, browser, sitemaps, feeds and platform APIs
├── parser/         # Automatic extraction, app data, selectors by example, file formats
├── storage/        # Exporters
├── compliance/     # Audit trail, URL policy, personal data checks
├── utils/          # Politeness, retry, URLs, HTTP sessions, rotation
├── logutil/        # Structured logging
├── queueing/       # Persistent queues (standalone)
├── session/        # Sessions (standalone)
├── monitoring/     # Metrics (standalone)
├── models/         # Item schemas (standalone)
├── benchmark/      # Measures the scraper against real websites
└── tests/
```

`scrape_simple.py` in the repository root is a separate quick tool for one-page extraction. It also obeys robots.txt.

## What It Cannot Do

- **Sites that refuse.** Some sites block automated visitors outright, and many others disallow them in robots.txt. The scraper respects both and says which happened. It does not disguise itself, solve CAPTCHAs or bypass anti-bot systems, by design.
- **Pages behind a login or paywall**, also by design.
- **Text that isn't text.** Content drawn on a canvas or baked into images has nothing to read, and scanned PDFs have no text layer. There is no OCR.
- **Closed shadow DOM and cross-site iframes**, which browsers keep out of reach.
- **Knowing what you wanted.** Automatic extraction finds what looks like the main content. On a page with several plausible answers it may pick a different one than you meant, which is what `--example` is for.
- **Pages that change per visitor.** Personalised or A/B tested pages can differ from what you see yourself.

## Legal Notice

This scraper is designed for ethical use only. Always:
- Check and respect robots.txt
- Only scrape publicly accessible data
- Comply with website terms of service
- Consider GDPR/CCPA implications
- Obtain legal review when scraping personal data
- Have a takedown response process
