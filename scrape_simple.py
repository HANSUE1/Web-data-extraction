"""Quick one-page extractor: repeating items by CSS selector, saved as JSON and CSV.

Examples:
    python scrape_simple.py --url https://quotes.toscrape.com/
    python scrape_simple.py --url https://news.ycombinator.com/ --item .athing \
        --field "title:.titleline a::text" --field "link:.titleline a::attr(href)"
"""
import argparse
import csv
import json
import os
import sys
from pathlib import PurePosixPath
from urllib.parse import urlparse

import requests
from parsel import Selector

HEADERS = {'User-Agent': 'simple-scraper/1.0'}
TIMEOUT = 30
QUOTES_API = 'https://quotes.toscrape.com/api/quotes'


def save(items, outdir, base):
    if not items:
        print('Nothing extracted')
        return
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, f'{base}.json'), 'w', encoding='utf-8') as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    with open(os.path.join(outdir, f'{base}.csv'), 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(items[0]))
        writer.writeheader()
        writer.writerows(items)
    print(f'Saved {len(items)} items -> {outdir}')


def scrape_quotes_api():
    items = []
    page = 1
    with requests.Session() as session:
        session.headers.update(HEADERS)
        while True:
            response = session.get(QUOTES_API, params={'page': page}, timeout=TIMEOUT)
            if response.status_code != 200:
                print(f'Stopped at page {page}: HTTP {response.status_code}', file=sys.stderr)
                break
            data = response.json()
            for quote in data.get('quotes', []):
                items.append({
                    'text': quote.get('text'),
                    'author': quote.get('author', {}).get('name'),
                    'tags': ', '.join(quote.get('tags', [])),
                })
            if not data.get('has_next'):
                break
            page += 1
    return items


def scrape_generic(url, item_sel, fields):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    items = []
    for row in Selector(text=response.text).css(item_sel):
        obj = {}
        for key, selector in fields.items():
            value = row.css(selector).get()
            obj[key] = value.strip() if isinstance(value, str) else value
        if any(obj.values()):
            items.append(obj)
    return items


def parse_fields(pairs, parser):
    fields = {}
    for pair in pairs:
        key, separator, selector = pair.partition(':')
        if not (key and separator and selector):
            parser.error(f'--field must look like name:selector, got: {pair}')
        fields[key] = selector
    return fields


def output_location(url):
    """Return (directory, base filename) for a URL. /a/b/ and /a/b.html both give 'b'."""
    parsed = urlparse(url)
    outdir = os.path.join('outputs', parsed.netloc.replace(':', '_'))
    return outdir, PurePosixPath(parsed.path).stem or 'index'


def main():
    parser = argparse.ArgumentParser(description='Quick one-page extractor')
    parser.add_argument('--url', required=True)
    parser.add_argument('--item', help='CSS selector for each repeating item')
    parser.add_argument('--field', action='append', help='name:selector, repeat for each field')
    parser.add_argument('--api-quotes', action='store_true', help='Use the quotes.toscrape.com JSON API')
    args = parser.parse_args()

    # quotes.toscrape.com defaults to its API, unless selectors were given for the page itself.
    use_quotes_api = args.api_quotes or ('quotes.toscrape.com' in args.url and not args.item)

    if not use_quotes_api and not (args.item and args.field):
        parser.error('--item and --field are required for this URL')

    try:
        if use_quotes_api:
            items = scrape_quotes_api()
        else:
            items = scrape_generic(args.url, args.item, parse_fields(args.field, parser))
    except requests.RequestException as e:
        sys.exit(f'Request failed: {e}')

    save(items, *output_location(args.url))


if __name__ == '__main__':
    main()
