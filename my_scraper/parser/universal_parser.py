"""Parser for multiple content types."""
import json
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional

from parsel import Selector


@dataclass
class ParsedItem:
    """Parsed item with schema enforcement."""
    data: Dict[str, Any]
    url: str
    errors: List[str] = field(default_factory=list)


class ContentTypeDetector:
    """Detect content type. Header names must be lowercase, as fetchers provide them."""

    @staticmethod
    def detect(content: str, headers: Dict[str, str] = None) -> str:
        content_type = headers.get("content-type", "").lower() if headers else ""

        if "json" in content_type:
            return "json"
        if "pdf" in content_type:
            return "pdf"

        stripped = content.strip()
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return "json"
            except ValueError:
                pass

        return "html"


class HTMLParser:
    """CSS/XPath selector based HTML parser."""

    def __init__(self, schema: List[str] = None):
        self.schema = schema or []

    def parse(self, content: str, url: str, selectors: Dict[str, str]) -> ParsedItem:
        """Parse HTML with CSS/XPath selectors."""
        sel = Selector(text=content)
        data = {}
        errors = []

        for name, selector in selectors.items():
            try:
                # Support CSS and XPath
                query = sel.xpath if selector.startswith("/") else sel.css
                result = query(selector).get()
                data[name] = result.strip() if result is not None else None
            except Exception as e:  # parsel raises several types for a bad selector
                errors.append(f"Error parsing field '{name}': {e}")
                data[name] = None

        # Enforce schema - ensure all schema fields present
        for name in self.schema:
            data.setdefault(name, None)

        return ParsedItem(data=data, url=url, errors=errors)

    def extract_json_ld(self, content: str) -> List[Dict[str, Any]]:
        """Extract JSON-LD structured data."""
        results = []
        for script in Selector(text=content).css('script[type="application/ld+json"]::text').getall():
            try:
                results.append(json.loads(script))
            except ValueError:
                continue
        return results

    def extract_script_json(self, content: str, pattern: str = None) -> Optional[Dict[str, Any]]:
        """Extract JSON from script tags, trying pattern first and then common shapes."""
        patterns = [
            r"var\s+\w+\s*=\s*({.*?});",
            r"window\.\w+\s*=\s*({.*?});",
            r"__NEXT_DATA__.*?>(.*?)</script>",
        ]
        if pattern:
            patterns.insert(0, pattern)

        for candidate in patterns:
            match = re.search(candidate, content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except ValueError:
                    continue

        return None


class PDFParser:
    """PDF text extraction (requires PyPDF2)."""

    def parse(self, content: bytes) -> str:
        """Parse PDF content."""
        try:
            import PyPDF2
        except ImportError as e:
            raise RuntimeError("PyPDF2 not installed. Use 'pip install PyPDF2'") from e

        reader = PyPDF2.PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)


class UniversalParser:
    """Universal parser for all content types."""

    def __init__(self, schema: List[str] = None):
        self.html_parser = HTMLParser(schema)
        self.pdf_parser = PDFParser()
        self.detector = ContentTypeDetector()

    def parse(self, fetch_result: Dict[str, Any], selectors: Dict[str, str] = None) -> ParsedItem:
        """Parse based on content type."""
        content = fetch_result["content"]
        url = fetch_result["url"]

        content_type = self.detector.detect(content, fetch_result.get("headers"))

        if content_type == "json":
            try:
                data = json.loads(content)
                return ParsedItem(data=data if isinstance(data, dict) else {"data": data}, url=url)
            except ValueError:
                pass  # labelled JSON but isn't; treat as HTML below

        elif content_type == "pdf":
            body = fetch_result.get("body")
            if body is None:
                return ParsedItem(data={}, url=url, errors=["PDF response has no binary body"])
            try:
                return ParsedItem(data={"text": self.pdf_parser.parse(body)}, url=url)
            except Exception as e:  # PyPDF2 raises many error types on damaged files
                return ParsedItem(data={}, url=url, errors=[f"PDF parsing failed: {e}"])

        if selectors:
            return self.html_parser.parse(content, url, selectors)
        return ParsedItem(data={"html": content}, url=url)
