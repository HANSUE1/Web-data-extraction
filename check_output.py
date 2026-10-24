import json

f = 'output/output_20261024_212923.jsonl'
with open(f, 'r') as fh:
    lines = fh.readlines()
    print('Lines:', len(lines))
    for i, line in enumerate(lines[:3]):
        d = json.loads(line)
        print('Item', i+1, ':', d['title'][:50], '... | Price:', d['price'], '| URL:', d['url'])