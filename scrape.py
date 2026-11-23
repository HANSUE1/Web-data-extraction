"""Simple, universal scraper.

Static requests first. If extraction yields zero items, fall back to
Playwright headless browser, then re-extract on the rendered HTML.

Usage examples:

    python scrape.py --url https://news.ycombinator.com/ \
        --item "tr.athing" \
        --field "rank:.rank::text" --field "title:.titleline > a::text" \
        --field "link:.titleline > a::attr(href)"

    python scrape.py --url https://quotes.toscrape.com/js/ --api-quotes
"""

import argparse
import csv
import json
import os
import sys
from urllib.parse import urlparse

import requests
from parsel import Selector


USER_AGENT = "simple-scraper/1.0 (+https://example.com/bot)"


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def fetch_requests(url, timeout=30):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.text


def fetch_playwright(url, timeout_ms=60000):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(f"Playwright not available: {exc}") from exc
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        html = page.content()
        browser.close()
        return html


def extract_items(html, item_selector, fields):
    """Extract items using CSS selectors. Returns list of dicts."""
    sel = Selector(text=html)
    items = []
    for row in sel.css(item_selector):
        obj = {}
        for key, spec in fields.items():
            if "::attr(" in spec:
                attr = spec.split("::attr(")[-1].rstrip(")")
                base_sel = spec.split("::attr(")[0]
                obj[key] = row.css(base_sel + f"::attr({attr})").get()
            elif spec.endswith("::text"):
                base_sel = spec[: -len("::text")]
                vals = row.css(base_sel + "::text").getall()
                obj[key] = " ".join(v.strip() for v in vals if v.strip()) if vals else None
            else:
                val = row.css(spec).get()
                obj[key] = val.strip() if isinstance(val, str) else val
        if obj and any(v for v in obj.values() if v):
            items.append(obj)
    return items


def save(items, outdir, base_name):
    if not items:
        print("Nothing extracted")
        return
    with open(os.path.join(outdir, f"{base_name}.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    keys = list(items[0].keys())
    with open(os.path.join(outdir, f"{base_name}.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(items)
    print(f"Saved {len(items)} items -> {outdir}\\{base_name}.json / .csv")


def scrape_quotes_api():
    items = []
    page = 1
    while True:
        r = requests.get(
            f"https://quotes.toscrape.com/api/quotes?page={page}",
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            break
        data = r.json()
        for q in data.get("quotes", []):
            items.append(
                {
                    "text": q.get("text"),
                    "author": q.get("author", {}).get("name"),
                    "tags": ", ".join(q.get("tags", [])),
                }
            )
        if not data.get("has_next"):
            break
        page += 1
    return items


def scrape(url, item_selector, fields, mode="auto"):
    if mode == "browser":
        html = fetch_playwright(url)
        return extract_items(html, item_selector, fields)

    # Static fetch
    html = fetch_requests(url)
    results = extract_items(html, item_selector, fields)

    # Post-extraction fallback: zero results -> browser -> re-extract
    if mode == "auto" and not results:
        print("Zero results from static fetch, trying browser fallback...")
        try:
            html = fetch_playwright(url)
            results = extract_items(html, item_selector, fields)
        except Exception as exc:
            print(f"Browser fallback failed: {exc}")
            return []
    return results


def main():
    parser = argparse.ArgumentParser(description="Simple universal scraper")
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", help="CSS selector for each item block")
    parser.add_argument(
        "--field", action="append", help="name:selector (e.g. title:.titleline::text)"
    )
    parser.add_argument("--api-quotes", action="store_true")
    parser.add_argument(
        "--mode", choices=["auto", "static", "browser"], default="auto"
    )
    args = parser.parse_args()

    parsed = urlparse(args.url)
    domain = parsed.netloc.replace(":", "_")
    outdir = ensure_dir(os.path.join("outputs", domain))
    base_name = os.path.basename(parsed.path).strip("/") or "index"

    if args.api_quotes or ("quotes.toscrape.com" in args.url and not args.item):  # your own selectors win
        print("Using quotes.toscrape.com API route...")
        items = scrape_quotes_api()
    else:
        if not args.item or not args.field:
            parser.error("generic scraping needs --item and at least one --field")
        fields = {}
        for f in args.field:
            name, _, selector = f.partition(":")
            if not (name and selector):
                parser.error(f"--field must look like name:selector, got: {f}")
            fields[name] = selector
        try:
            items = scrape(args.url, args.item, fields, args.mode)
        except (requests.RequestException, RuntimeError) as exc:
            sys.exit(f"Error: {exc}")

    save(items, outdir, base_name)
    if not items:
        sys.exit(1)


if __name__ == "__main__":
    main()
