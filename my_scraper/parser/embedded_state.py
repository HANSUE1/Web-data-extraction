"""Find the data modern sites hand to their own JavaScript as JSON.

A React or Vue page often arrives with its whole catalogue inside a script tag, ready
to be drawn. The same search runs over JSON the page fetches while rendering. Either
way the records are cleaner than anything recoverable from the finished layout.
"""
import json
import re
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urljoin

from parsel import Selector

Item = Dict[str, Any]

MIN_RECORDS = 3
MAX_SCRIPT_BYTES = 8 * 1024 * 1024
MAX_NODES = 300_000
MAX_DEPTH = 14
MAX_EXTRA_FIELDS = 20
MIN_TITLE_COVERAGE = 0.7

TITLE_KEYS = ("title", "name", "headline", "displayname", "display_name", "productname", "product_name",
              "heading", "label", "subject")
URL_KEYS = ("url", "href", "link", "permalink", "canonicalurl", "canonical_url", "pageurl", "weburl",
            "html_url", "htmlurl", "pdpurl", "producturl", "path")
IMAGE_KEYS = ("image", "imageurl", "image_url", "img", "imagesrc", "thumbnail", "thumbnailurl", "thumbnail_url",
              "primaryimage", "featuredimage", "featured_image", "poster", "images", "photo")
PRICE_KEYS = ("price", "saleprice", "sale_price", "currentprice", "current_price", "finalprice", "pricevalue",
              "displayprice", "price_amount")
DESCRIPTION_KEYS = ("description", "summary", "excerpt", "subtitle", "snippet", "teaser", "abstract", "dek")
DATE_KEYS = ("date", "publishedat", "published_at", "published", "datepublished", "publishdate", "createdat",
             "created_at", "firstpublished", "updatedat", "updated_at")
AUTHOR_KEYS = ("author", "authorname", "author_name", "byline", "creator", "user", "username")

NESTED_VALUE_KEYS = ("rendered", "name", "title", "value", "amount", "text", "displayname", "formatted", "url",
                     "src", "href")

JSON_SCRIPT_TYPES = ("application/json", "text/json")
ASSIGNMENT = re.compile(r"=\s*(?=[{\[])")
JSON_PARSE_CALL = re.compile(r"""JSON\.parse\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*\)""")
JSON_ATTRIBUTE = "//@*[starts-with(normalize-space(.), '{\"') or starts-with(normalize-space(.), '[{\"')]"

MARKUP = re.compile(r"<[^>]+>")

_DECODER = json.JSONDecoder()


def _decode_js_string(literal: str) -> Optional[str]:
    if literal.startswith("'"):
        literal = '"' + literal[1:-1].replace('\\"', '"').replace('"', '\\"').replace("\\'", "'") + '"'
    try:
        return json.loads(literal)
    except ValueError:
        return None


def _parse(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _values_in_script(script: str) -> Iterator[Any]:
    """JSON assigned to a variable or passed to JSON.parse inside a script."""
    for match in JSON_PARSE_CALL.finditer(script):
        decoded = _decode_js_string(match.group(1))
        value = _parse(decoded) if decoded else None
        if isinstance(value, (dict, list)):
            yield value

    position = 0
    for match in ASSIGNMENT.finditer(script):
        if match.end() < position:
            continue  # inside a value already read
        try:
            value, end = _DECODER.raw_decode(script, match.end())
        except ValueError:
            continue
        position = end
        if isinstance(value, (dict, list)) and value:
            yield value


def embedded_json(html: str) -> Iterator[Any]:
    """Every JSON value embedded in a page's scripts and data attributes."""
    sel = Selector(text=html, type="html")
    budget = MAX_SCRIPT_BYTES

    for script in sel.xpath("//script[not(@src)]"):
        text = script.xpath("string(.)").get(default="")
        script_type = (script.attrib.get("type") or "").lower()
        if not text.strip() or "ld+json" in script_type or len(text) > budget:
            continue
        budget -= len(text)

        if any(json_type in script_type for json_type in JSON_SCRIPT_TYPES):
            value = _parse(text)
            if value is not None:
                yield value
        elif not script_type or "javascript" in script_type or script_type == "module":
            yield from _values_in_script(text)

    for attribute in sel.xpath(JSON_ATTRIBUTE).getall():
        value = _parse(attribute)
        if value is not None:
            yield value


def _unwrap(record: Dict[str, Any]) -> Dict[str, Any]:
    """GraphQL lists wrap each record, as in {"node": {...}}."""
    for key in ("node", "item", "product", "article", "data"):
        if isinstance(record.get(key), dict) and len(record) <= 3:
            return record[key]
    return record


def find_record_lists(data: Any) -> Iterator[List[Dict[str, Any]]]:
    """Every list of similar objects inside a JSON value, including id-keyed collections."""
    stack = [(data, 0)]
    nodes = 0

    while stack and nodes < MAX_NODES:
        node, depth = stack.pop()
        nodes += 1
        if depth > MAX_DEPTH:
            continue

        if isinstance(node, list):
            records = [_unwrap(entry) for entry in node if isinstance(entry, dict)]
            if len(records) >= MIN_RECORDS and len(records) >= 0.8 * len(node):
                yield records
            stack.extend((entry, depth + 1) for entry in node if isinstance(entry, (dict, list)))

        elif isinstance(node, dict):
            children = [value for value in node.values() if isinstance(value, dict)]
            if len(children) >= MIN_RECORDS and len(children) >= 0.6 * len(node):
                by_type: Dict[Any, List[Dict[str, Any]]] = {}
                for child in children:
                    by_type.setdefault(child.get("__typename"), []).append(child)
                yield from (group for group in by_type.values() if len(group) >= MIN_RECORDS)
            stack.extend((value, depth + 1) for value in node.values() if isinstance(value, (dict, list)))


def _lowered(record: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key).lower(): value for key, value in record.items()}


def _flat(value: Any) -> Optional[str]:
    """Reduce a nested value such as {"amount": 5} or ["a.jpg"] to text."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        lowered = _lowered(value)
        value = next((lowered[key] for key in NESTED_VALUE_KEYS if lowered.get(key) not in (None, "", [], {})), None)
        if isinstance(value, (dict, list)):
            return _flat(value)
    if value is None or isinstance(value, bool) or value == "":
        return None
    return " ".join(str(value).split())


def _first(record: Dict[str, Any], keys: tuple) -> Optional[str]:
    for key in keys:
        value = _flat(record.get(key))
        if value:
            return value
    return None


def _web_address(value: Optional[str], page_url: str) -> Optional[str]:
    if value and (value.startswith(("http://", "https://", "//", "/"))):
        return urljoin(page_url, value)
    return None


def _coverage(records: List[Dict[str, Any]], keys: tuple) -> float:
    found = sum(1 for record in records if _first(_lowered(record), keys))
    return found / len(records)


def _score(records: List[Dict[str, Any]]) -> float:
    """How much a list looks like the page's content. Zero means it does not."""
    if _coverage(records, TITLE_KEYS) < MIN_TITLE_COVERAGE:
        return 0.0

    sample = [_lowered(record) for record in records[:50]]
    fields = sum(sum(1 for value in record.values() if _flat(value)) for record in sample) / len(sample)
    titles = [_first(record, TITLE_KEYS) or "" for record in sample]
    average_title = sum(len(title) for title in titles) / len(titles)
    has_content_signal = any(_first(record, PRICE_KEYS + IMAGE_KEYS + DATE_KEYS + DESCRIPTION_KEYS) for record in sample)
    has_links = any(_first(record, URL_KEYS) for record in sample)

    # Navigation menus in app state hold little beyond a short label and a link.
    if not has_content_signal and (fields <= 3 or (average_title < 15 and has_links)):
        return 0.0
    return len(records) * min(fields, 8.0)


def _without_markup(value: Optional[str]) -> Optional[str]:
    return " ".join(MARKUP.sub(" ", value).split()) if value and "<" in value else value


def _normalize(record: Dict[str, Any], page_url: str, source: str) -> Item:
    lowered = _lowered(record)
    item = {
        "title": _without_markup(_first(lowered, TITLE_KEYS)),
        "url": _web_address(_first(lowered, URL_KEYS), page_url),
        "image": _web_address(_first(lowered, IMAGE_KEYS), page_url),
        "price": _first(lowered, PRICE_KEYS),
        "description": _without_markup(_first(lowered, DESCRIPTION_KEYS)),
        "date": _first(lowered, DATE_KEYS),
        "author": _first(lowered, AUTHOR_KEYS),
    }
    used = set(TITLE_KEYS + URL_KEYS + IMAGE_KEYS + PRICE_KEYS + DESCRIPTION_KEYS + DATE_KEYS + AUTHOR_KEYS)
    extras = {key: value for key, value in record.items()
              if str(key).lower() not in used and isinstance(value, (str, int, float)) and value != ""
              and not str(key).startswith("__")}
    item["attributes"] = dict(list(extras.items())[:MAX_EXTRA_FIELDS])
    item["source"] = source
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


def records_to_items(values: Any, page_url: str, source: str) -> List[Item]:
    """Normalised items from the most content-like lists across some JSON values.

    Lists with the same fields are joined: tabs and pages of one dataset arrive separately.
    """
    by_shape: Dict[frozenset, List[Dict[str, Any]]] = {}
    for value in values:
        for records in find_record_lists(value):
            if _score(records) > 0:
                by_shape.setdefault(frozenset(records[0]), []).extend(records)

    if not by_shape:
        return []
    best = max(by_shape.values(), key=_score)
    return [_normalize(record, page_url, source) for record in best]


def embedded_items(html: str, page_url: str) -> List[Item]:
    return records_to_items(embedded_json(html), page_url, "embedded")
