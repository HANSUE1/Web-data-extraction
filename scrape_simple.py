import argparse, csv, json, os, sys
from urllib.parse import urlparse
import requests
from parsel import Selector

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p

def save(items, outdir, base):
    if not items:
        print('Nothing extracted')
        return
    with open(os.path.join(outdir, f'{base}.json'), 'w', encoding='utf-8') as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    keys = list(items[0].keys())
    with open(os.path.join(outdir, f'{base}.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(items)
    print(f'Saved {len(items)} items -> {outdir}')

def scrape_quotes_api():
    items = []
    page = 1
    while True:
        r = requests.get(f'https://quotes.toscrape.com/api/quotes?page={page}', headers={'User-Agent': 'simple-scraper/1.0'}, timeout=30)
        if r.status_code != 200: break
        data = r.json()
        for q in data.get('quotes', []):
            items.append({'text': q.get('text'), 'author': q.get('author', {}).get('name'), 'tags': ', '.join(q.get('tags', []))})
        if not data.get('has_next'): break
        page += 1
    return items

def scrape_generic(url, item_sel, fields):
    r = requests.get(url, headers={'User-Agent': 'simple-scraper/1.0'}, timeout=30)
    r.raise_for_status()
    sel = Selector(text=r.text)
    items = []
    for row in sel.css(item_sel):
        obj = {}
        for key, s in fields.items():
            if '::attr(' in s:
                attr = s.split('::attr(')[-1].rstrip(')')
                base_sel = s.split('::attr(')[0]
                obj[key] = row.css(base_sel + f'::attr({attr})').get()
            else:
                val = row.css(s).get()
                obj[key] = val.strip() if isinstance(val, str) else val
        if any(v for v in obj.values()): items.append(obj)
    return items

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True)
    p.add_argument('--item')
    p.add_argument('--field', action='append')
    p.add_argument('--api-quotes', action='store_true')
    args = p.parse_args()
    parsed = urlparse(args.url)
    domain = parsed.netloc.replace(':', '_')
    outdir = ensure_dir(os.path.join('outputs', domain))
    base = os.path.basename(parsed.path).strip('/') or 'index'
    if args.api_quotes or 'quotes.toscrape.com' in args.url:
        items = scrape_quotes_api()
    else:
        if not args.item or not args.field:
            print('Need --item and --field'); sys.exit(1)
        fields = {}
        for f in args.field:
            if ':' in f: k,v=f.split(':',1); fields[k]=v
        items = scrape_generic(args.url, args.item, fields)
    save(items, outdir, base)

if __name__=='__main__': main()
