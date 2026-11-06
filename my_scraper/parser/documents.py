"""Items from anything that is not a web page: feeds, spreadsheets, PDFs and plain files."""
import csv
import io
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from lxml import etree
from lxml import html as lxml_html

Item = Dict[str, Any]

MIN_REPEATED_RECORDS = 2
SAMPLE_BYTES = 8192
DELIMITERS = ",;\t|"
MAX_CSV_FIELD = 10_000_000

csv.field_size_limit(MAX_CSV_FIELD)  # the default rejects one long cell, losing the whole file


def unique_headers(names: List[Any]) -> List[str]:
    """Column names that are all present and all different."""
    headers: List[str] = []
    seen: Dict[str, int] = {}
    for position, name in enumerate(names, 1):
        header = " ".join(str(name).split()) if name not in (None, "") else f"column_{position}"
        seen[header] = seen.get(header, 0) + 1
        headers.append(header if seen[header] == 1 else f"{header}_{seen[header]}")
    return headers


# --- Delimited text ----------------------------------------------------------

def parse_csv(text: str) -> List[Item]:
    text = text.lstrip("﻿")
    sample = text[:SAMPLE_BYTES]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=DELIMITERS)
    except csv.Error:
        dialect = csv.excel

    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        return []

    headers = unique_headers(rows[0])
    items = []
    for row in rows[1:]:
        item = {header: value.strip() for header, value in zip(headers, row, strict=False) if value.strip()}
        if item:
            items.append({**item, "source": "csv"})
    return items


# --- XML and feeds -----------------------------------------------------------

def _xml_root(body: bytes) -> Optional[Any]:
    # Same care as sitemaps: never fetch or expand anything a file refers to.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
    try:
        return etree.fromstring(body, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return None


def _local(element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _child(entry, *names: str) -> Optional[Any]:
    for name in names:
        for child in entry:
            if _local(child).lower() == name:
                return child
    return None


def _text_of(element) -> Optional[str]:
    if element is None:
        return None
    text = " ".join("".join(element.itertext()).split())
    if "<" in text:
        try:
            text = " ".join(lxml_html.fromstring(f"<div>{text}</div>").text_content().split())
        except (etree.ParserError, ValueError):
            pass
    return text or None


def _entry_link(entry) -> Optional[str]:
    for child in entry:
        if _local(child).lower() == "link":
            href = child.get("href")
            if href and child.get("rel", "alternate") == "alternate":
                return href
            if not href and child.text and child.text.strip():
                return child.text.strip()
    return _text_of(_child(entry, "guid", "id")) if _child(entry, "guid") is not None else None


def _entry_image(entry) -> Optional[str]:
    for child in entry.iter():
        if _local(child).lower() in ("thumbnail", "enclosure", "content") and str(child.get("type", "image")).startswith("image"):
            if child.get("url"):
                return child.get("url")
    return None


def _feed_entries(root) -> List[Any]:
    name = _local(root).lower()
    wanted = {"rss": "item", "rdf": "item", "feed": "entry"}.get(name)
    return [element for element in root.iter() if isinstance(element.tag, str) and _local(element).lower() == wanted]


def parse_feed(root, page_url: str) -> List[Item]:
    """Entries of an RSS or Atom feed."""
    items = []
    for entry in _feed_entries(root):
        author = _child(entry, "author", "creator")
        name = _child(author, "name") if author is not None and len(author) else None
        link = _entry_link(entry)
        item = {
            "title": _text_of(_child(entry, "title")),
            "url": urljoin(page_url, link) if link else None,
            "description": _text_of(_child(entry, "description", "summary", "content", "encoded")),
            "date": _text_of(_child(entry, "pubdate", "published", "updated", "date")),
            "author": _text_of(name if name is not None else author),
            "image": _entry_image(entry),
            "source": "feed",
        }
        items.append({key: value for key, value in item.items() if value})
    return items


def parse_xml(body: bytes, page_url: str) -> List[Item]:
    """Feeds, or the repeated records of any other XML file."""
    root = _xml_root(body)
    if root is None or not isinstance(root.tag, str):
        return []

    if _local(root).lower() in ("rss", "rdf", "feed"):
        return parse_feed(root, page_url)

    # Any other XML: the element that repeats most is the record.
    best: List[Any] = []
    for parent in root.iter():
        groups: Dict[str, List[Any]] = {}
        for child in parent:
            if isinstance(child.tag, str):
                groups.setdefault(_local(child), []).append(child)
        for group in groups.values():
            if len(group) >= MIN_REPEATED_RECORDS and len(group) > len(best):
                best = group

    items = []
    for record in best:
        fields = dict(record.attrib)
        for child in record:
            value = _text_of(child)
            if value and isinstance(child.tag, str):
                fields[_local(child)] = value
        if not fields and _text_of(record):
            fields["value"] = _text_of(record)
        if fields:
            items.append({**fields, "source": "xml"})
    return items


# --- Plain text --------------------------------------------------------------

def parse_text(text: str) -> List[Item]:
    stripped = text.strip()
    if not stripped:
        return []
    title = next(line.strip() for line in stripped.splitlines() if line.strip())
    return [{"title": title[:200], "text": stripped, "source": "text"}]


# --- Binary documents --------------------------------------------------------

def parse_pdf(body: bytes) -> List[Item]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    metadata = reader.metadata or {}
    item = {
        "title": str(metadata.get("/Title") or "").strip() or (text.splitlines()[0].strip()[:200] if text else None),
        "author": str(metadata.get("/Author") or "").strip() or None,
        "text": text,
        "pages": len(pages),
        "source": "pdf",
    }
    return [{key: value for key, value in item.items() if value}]


def parse_docx(body: bytes) -> List[Item]:
    from docx import Document

    document = Document(io.BytesIO(body))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            paragraphs.append(" | ".join(cell.text.strip() for cell in row.cells))
    text = "\n".join(paragraphs)
    title = document.core_properties.title or (paragraphs[0][:200] if paragraphs else None)
    return [{"title": title, "text": text, "source": "docx"}] if text else []


def parse_xlsx(body: bytes) -> List[Item]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    items = []
    for sheet in workbook.worksheets:
        rows = [row for row in sheet.iter_rows(values_only=True) if any(cell not in (None, "") for cell in row)]
        if len(rows) < 2:
            continue
        headers = unique_headers(list(rows[0]))
        for row in rows[1:]:
            item = {header: value for header, value in zip(headers, row, strict=False) if value not in (None, "")}
            if item:
                items.append({**item, "sheet": sheet.title, "source": "xlsx"})
    workbook.close()
    return items
