"""Print a quick summary of a JSONL export.

Usage: python check_output.py [file.jsonl]   (default: newest file in output/)
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        exports = sorted(Path('output').glob('*.jsonl'), key=lambda p: p.stat().st_mtime)
        if not exports:
            sys.exit('No .jsonl files found in output/')
        path = exports[-1]

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f'{path}: {len(lines)} lines')
    for number, line in enumerate(lines[:3], start=1):
        item = json.loads(line)
        title = str(item.get('title'))[:50]
        print(f"Item {number}: {title} ... | Price: {item.get('price')} | URL: {item.get('url')}")


if __name__ == '__main__':
    main()
