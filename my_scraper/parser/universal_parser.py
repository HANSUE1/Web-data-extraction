"""Parser for multiple content types."""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from parsel import Selector

from my_scraper.fetcher.discovery import is_platform_api
from my_scraper.parser import documents
from my_scraper.parser.auto_extractor import extract_items
from my_scraper.parser.embedded_state import find_record_lists, records_to_items

MULTI_VALUE_SUFFIX = "[]"  # a selector named tags[] collects every match, not just the first
ALL_TEXT = " ::text"  # with the space: all text inside the element, including its children

BINARY_TYPES = ("image/", "video/", "audio/", "font/", "application/octet-stream", "application/zip")
XML_OPENINGS = ("<?xml", "<rss", "<feed", "<rdf:rdf", "<urlset", "<sitemapindex")
HEAD_SAMPLE = 600

# Guards some APIs put before their JSON, and callback(...) wrappers.
JSON_GUARD = re.compile(r"""^\s*(?:\)\]\}'?,?|while\(1\);|for\(;;\);)\s*""")
JSONP_WRAPPER = re.compile(r"^\s*[\w$.]+\(\s*(.*?)\s*\)\s*;?\s*$", re.DOTALL)


def load_json(text: str) -> Any:
    """Parse JSON, including guarded, JSONP and one-record-per-line forms."""
    text = JSON_GUARD.sub("", text, count=1)
    try:
        return json.loads(text)
    except ValueError:
        wrapped = JSONP_WRAPPER.match(text)
        if wrapped:
            try:
                return json.loads(wrapped.group(1))
            except ValueError:
                pass
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@dataclass
class ParsedPage:
    """Everything extracted from one page."""
    items: List[Dict[str, Any]]
    url: str
    errors: List[str] = field(default_factory=list)


class ContentTypeDetector:
    """Work out what a response really is. Labels are often wrong, so bytes get a say too.

    Header names must be lowercase, as fetchers provide them.
    """

    @staticmethod
    def detect(content: str, headers: Dict[str, str] = None, url: str = "", body: Optional[bytes] = None) -> str:
        content_type = headers.get("content-type", "").lower() if headers else ""
        path = urlsplit(url).path.lower()
        start = (body or b"")[:8]

        if start.startswith(b"%PDF-") or "pdf" in content_type:
            return "pdf"
        if "spreadsheet" in content_type or path.endswith(".xlsx"):
            return "xlsx"
        if "wordprocessing" in content_type or path.endswith(".docx"):
            return "docx"
        if "json" in content_type or path.endswith((".json", ".jsonl", ".ndjson")):
            return "json"

        head = content.lstrip()[:HEAD_SAMPLE].lower()
        if "<html" in head or "<!doctype html" in head:
            return "html"
        if "csv" in content_type or path.endswith((".csv", ".tsv")):
            return "csv"
        if ("xml" in content_type and "html" not in content_type) or head.startswith(XML_OPENINGS):
            return "xml"
        if head.startswith(("{", "[")):
            try:
                json.loads(content)
                return "json"
            except ValueError:
                pass
        if content_type.startswith("text/plain") or path.endswith(".txt"):
            return "text"
        if content_type.startswith(BINARY_TYPES) or start.startswith(b"PK\x03\x04"):
            return "binary"
        return "html"


class HTMLParser:
    """CSS/XPath selector based HTML parser."""

    def __init__(self, schema: List[str] = None):
        self.schema = schema or []

    def parse(self, content: str, url: str, selectors: Dict[str, str]) -> ParsedPage:
        """One item from the whole page."""
        errors: List[str] = []
        return ParsedPage(items=[self._extract(Selector(text=content, type="html"), selectors, errors)], url=url, errors=errors)

    def parse_rows(self, content: str, url: str, item_selector: str, selectors: Dict[str, str]) -> ParsedPage:
        """One item per match of item_selector, with selectors relative to each match."""
        errors: List[str] = []
        try:
            rows = self._query(Selector(text=content, type="html"), item_selector)
        except Exception as e:  # parsel raises several types for a bad selector
            return ParsedPage(items=[], url=url, errors=[f"Error in item selector: {e}"])

        items = [self._extract(row, selectors, errors) for row in rows]
        return ParsedPage(items=items, url=url, errors=errors)

    @staticmethod
    def _query(sel: Selector, selector: str):
        """Support CSS and XPath. Use .// for an XPath relative to the current item."""
        return sel.xpath(selector) if selector.startswith(("/", "./", "(")) else sel.css(selector)

    @classmethod
    def first_value(cls, sel: Selector, selector: str) -> Optional[str]:
        """The value of a single-value field.

        For 'h4 ::text' this is the whole text of the first h4 with any, so
        '<b>Title:</b> Paper 1' gives 'Title: Paper 1', not just 'Title:'.
        Otherwise it is the first non-blank match: an icon before the text leaves a blank first.
        """
        if selector.endswith(ALL_TEXT):
            for element in cls._query(sel, selector[:-len(ALL_TEXT)] or "*"):
                text = " ".join(piece.strip() for piece in element.xpath(".//text()").getall() if piece.strip())
                if text:
                    return text
            return None
        return next((value.strip() for value in cls._query(sel, selector).getall() if value.strip()), None)

    def _extract(self, sel: Selector, selectors: Dict[str, str], errors: List[str]) -> Dict[str, Any]:
        data: Dict[str, Any] = {}

        for name, selector in selectors.items():
            multiple = name.endswith(MULTI_VALUE_SUFFIX)
            key = name[:-len(MULTI_VALUE_SUFFIX)] if multiple else name
            try:
                if multiple:
                    data[key] = [value.strip() for value in self._query(sel, selector).getall() if value.strip()]
                else:
                    data[key] = self.first_value(sel, selector)
            except Exception as e:  # parsel raises several types for a bad selector
                errors.append(f"Error parsing field '{key}': {e}")
                data[key] = None

        # Enforce schema - ensure all schema fields present
        for name in self.schema:
            data.setdefault(name, None)

        return data


class UniversalParser:
    """Universal parser for all content types."""

    def __init__(self, schema: List[str] = None):
        self.html_parser = HTMLParser(schema)
        self.detector = ContentTypeDetector()

    def parse_page(self, fetch_result: Dict[str, Any], selectors: Dict[str, str] = None,
                   item_selector: str = None, include_markdown: bool = False) -> ParsedPage:
        """Extract every item from a response, whatever kind of file it is.

        HTML uses the given selectors, or works items out automatically when there are none.
        """
        content = fetch_result["content"]
        url = fetch_result["url"]
        body = fetch_result.get("body")
        kind = self.detector.detect(content, fetch_result.get("headers"), url, body)

        if kind == "html":
            return self._parse_html(fetch_result, selectors, item_selector, include_markdown)
        if kind == "binary":
            return ParsedPage(items=[], url=url, errors=["Binary file with no readable content"])
        return self._parse_file(kind, content, body, url)

    def _parse_html(self, fetch_result: Dict[str, Any], selectors: Optional[Dict[str, str]],
                    item_selector: Optional[str], include_markdown: bool) -> ParsedPage:
        content = fetch_result["content"]
        url = fetch_result["url"]
        snapshots = fetch_result.get("snapshots") or []

        if not selectors and not item_selector:
            errors: List[str] = []
            items = extract_items(content, url, include_markdown, snapshots,
                                  fetch_result.get("json_payloads") or [], errors)
            return ParsedPage(items=items, url=url, errors=errors)

        pages = []
        for html in [content, *snapshots]:
            if item_selector:
                pages.append(self.html_parser.parse_rows(html, url, item_selector, selectors or {}))
            else:
                pages.append(self.html_parser.parse(html, url, selectors))

        items: List[Dict[str, Any]] = []
        for page in pages:
            items.extend(item for item in page.items if item not in items)
        return ParsedPage(items=items, url=url, errors=[error for page in pages for error in page.errors])

    def _parse_file(self, kind: str, content: str, body: Optional[bytes], url: str) -> ParsedPage:
        try:
            if kind == "json":
                items = self._json_items(content, url)
            elif kind == "csv":
                items = documents.parse_csv(content)
            elif kind == "xml":
                items = documents.parse_xml(body if body is not None else content.encode("utf-8"), url)
            elif kind == "text":
                items = documents.parse_text(content)
            elif body is None:
                return ParsedPage(items=[], url=url, errors=[f"{kind.upper()} response has no binary body"])
            else:
                items = {"pdf": documents.parse_pdf, "docx": documents.parse_docx,
                         "xlsx": documents.parse_xlsx}[kind](body)
        except Exception as e:  # document libraries raise many error types on damaged files
            return ParsedPage(items=[], url=url, errors=[f"{kind.upper()} parsing failed: {e}"])
        return ParsedPage(items=items, url=url)

    @staticmethod
    def _json_items(content: str, url: str) -> List[Dict[str, Any]]:
        """Records from a JSON response, kept as they are unless a known platform API sent them."""
        data = load_json(content)
        if is_platform_api(url):
            return records_to_items([data], url, "api")

        lists = list(find_record_lists(data))
        if lists:
            records = max(lists, key=len)
            return [{**record, "source": "json"} for record in records]
        if isinstance(data, dict):
            return [{**data, "source": "json"}]
        if isinstance(data, list) and data and all(isinstance(entry, dict) for entry in data):
            return [{**entry, "source": "json"} for entry in data]
        return [{"data": data, "source": "json"}] if data else []
