"""Parser for multiple content types."""
from parsel import Selector
import json
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class ParsedItem:
    """Parsed item with schema enforcement."""
    data: Dict[str, Any]
    url: str
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class ContentTypeDetector:
    """Detect content type."""
    
    @staticmethod
    def detect(content: str, headers: Dict[str, str] = None) -> str:
        content_type = headers.get('content-type', '').lower() if headers else ''
        
        if 'json' in content_type:
            return 'json'
        elif 'pdf' in content_type:
            return 'pdf'
        elif content.strip().startswith('{') or content.strip().startswith('['):
            try:
                json.loads(content.strip())
                return 'json'
            except:
                pass
        
        return 'html'


class HTMLParser:
    """CSS/XPath selector based HTML parser."""
    
    def __init__(self, schema: List[str] = None):
        self.schema = schema or []
    
    def parse(self, content: str, url: str, selectors: Dict[str, str]) -> ParsedItem:
        """Parse HTML with CSS/XPath selectors."""
        sel = Selector(text=content)
        data = {}
        errors = []
        
        for field, selector in selectors.items():
            try:
                # Support CSS and XPath
                if selector.startswith('//') or selector.startswith('/'):
                    result = sel.xpath(selector).get()
                else:
                    result = sel.css(selector).get()
                
                if result is None:
                    data[field] = None
                else:
                    data[field] = result.strip()
            except Exception as e:
                errors.append(f"Error parsing field '{field}': {e}")
                data[field] = None
        
        # Enforce schema - ensure all schema fields present
        if self.schema:
            for field in self.schema:
                if field not in data:
                    data[field] = None
        
        return ParsedItem(data=data, url=url, errors=errors)
    
    def extract_json_ld(self, content: str) -> List[Dict[str, Any]]:
        """Extract JSON-LD structured data."""
        sel = Selector(text=content)
        json_ld_scripts = sel.css('script[type="application/ld+json"]::text').getall()
        
        results = []
        for script in json_ld_scripts:
            try:
                data = json.loads(script)
                results.append(data)
            except:
                continue
        
        return results
    
    def extract_script_json(self, content: str, pattern: str = None) -> Optional[Dict[str, Any]]:
        """Extract JSON from script tags."""
        if pattern:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except:
                    pass
        
        # Try common patterns
        patterns = [
            r'var\s+\w+\s*=\s*({.*?});',
            r'window\.\w+\s*=\s*({.*?});',
            r'__NEXT_DATA__.*?>(.*?)</script>',
        ]
        
        for p in patterns:
            match = re.search(p, content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except:
                    continue
        
        return None


class PDFParser:
    """PDF parser (placeholder - would need PyPDF2/pdfplumber)."""
    
    def parse(self, content: bytes) -> str:
        """Parse PDF content."""
        try:
            import PyPDF2
            from io import BytesIO
            
            pdf_file = BytesIO(content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            return text
        except ImportError:
            raise Exception("PyPDF2 not installed. Use 'pip install PyPDF2'")
        except Exception as e:
            raise Exception(f"PDF parsing failed: {e}")


class UniversalParser:
    """Universal parser for all content types."""
    
    def __init__(self, schema: List[str] = None):
        self.html_parser = HTMLParser(schema)
        self.pdf_parser = PDFParser()
        self.detector = ContentTypeDetector()
    
    def parse(self, fetch_result: Dict[str, Any], selectors: Dict[str, str] = None) -> ParsedItem:
        """Parse based on content type."""
        content = fetch_result['content']
        url = fetch_result['url']
        headers = fetch_result.get('headers', {})
        
        content_type = self.detector.detect(content, headers)
        
        if content_type == 'json':
            try:
                data = json.loads(content)
                return ParsedItem(data=data if isinstance(data, dict) else {'data': data}, url=url)
            except:
                pass
        
        elif content_type == 'pdf':
            try:
                if isinstance(content, str):
                    content_bytes = content.encode('latin-1')
                else:
                    content_bytes = content
                text = self.pdf_parser.parse(content_bytes)
                return ParsedItem(data={'text': text}, url=url)
            except:
                pass
        
        # Default to HTML
        if selectors:
            return self.html_parser.parse(content, url, selectors)
        else:
            return ParsedItem(data={'html': content}, url=url)
