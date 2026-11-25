r"""Selectors that survive a redesign, and a per-site memory of the ones that were learned.

Besides CSS and XPath a field can be addressed by what it is. Meaning outlives class names, so
write the sturdiest first and separate alternatives with ||:

    role=heading || label=Price || h1.title::text || //h1/text()

    role=button, role=link[Next]   an explicit role, or the elements that have it by nature
    label=Price                    the element a <label>, aria-label, <dt> or <th> names
    text=/SKU:\s*(\w+)/            a regular expression over the text (first group, or the whole match)
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from parsel import Selector

from my_scraper.utils.urls import site_key

ALTERNATIVE = re.compile(r"\s+\|\|\s+")
ROLE = re.compile(r"^role=([a-z]+)(?:\[(.+)\])?$")
IMPLICIT_ROLES = {
    "heading": "self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6",
    "link": "self::a[@href]",
    "button": "self::button or self::input[@type='button' or @type='submit']",
    "img": "self::img",
    "list": "self::ul or self::ol",
    "listitem": "self::li",
    "table": "self::table",
    "row": "self::tr",
    "cell": "self::td",
    "columnheader": "self::th",
    "article": "self::article",
    "main": "self::main",
    "navigation": "self::nav",
    "textbox": "self::textarea or self::input[not(@type) or @type='text']",
}
TEXT_NODES = ".//text()[not(ancestor::script or ancestor::style)]"


def alternatives(selector: str) -> List[str]:
    return ALTERNATIVE.split(selector.strip())


def _quoted(text: str) -> str:
    """An XPath string literal. XPath has no escapes, so a text with both quotes is joined from parts."""
    if "'" not in text:
        return f"'{text}'"
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in text.split("'")) + ")"


def _is_semantic(selector: str) -> bool:
    return selector.startswith(("role=", "label="))


def elements(sel: Selector, selector: str) -> List[Selector]:
    """The elements one selector (with no alternatives) matches."""
    role = ROLE.match(selector)
    if role:
        name, label = role.groups()
        natural = IMPLICIT_ROLES.get(name)
        condition = f"@role='{name}'" + (f" or (not(@role) and ({natural}))" if natural else "")
        found = list(sel.xpath(f".//*[{condition}]"))
        if label:
            wanted = label.strip().strip("\"'").lower()
            found = [element for element in found
                     if wanted in f"{_text(element)} {element.attrib.get('aria-label', '')}".lower()]
        return found
    if selector.startswith("label="):
        text = _quoted(selector[6:].strip())
        return list(sel.xpath(
            f".//*[@aria-label={text}] | .//*[@id = //label[normalize-space()={text}]/@for]"
            f" | .//label[normalize-space(text()[1])={text}]/input"
            f" | .//*[@aria-labelledby = //*[normalize-space()={text}]/@id]"
            f" | .//dt[normalize-space()={text}]/following-sibling::dd[1]"
            f" | .//th[normalize-space()={text}]/following-sibling::td[1]"))
    if selector.startswith(("/", "./", "(")):  # XPath. Use .// for one relative to the current item
        return list(sel.xpath(selector))
    return list(sel.css(selector))


def _text(element: Selector) -> str:
    return " ".join(" ".join(element.xpath(TEXT_NODES).getall()).split())


def values(sel: Selector, selector: str) -> List[str]:
    """Every value one selector (with no alternatives) gives, as text."""
    if selector.startswith("text=/") and selector.endswith("/") and len(selector) > 7:
        return [match.group(1) if match.re.groups else match.group()
                for match in re.finditer(selector[6:-1], _text(sel))]
    found = elements(sel, selector)
    if not _is_semantic(selector):
        return [element.get() for element in found]
    return [_text(element) or element.attrib.get("value") or element.attrib.get("alt")
            or element.attrib.get("content") or "" for element in found]


class SelectorCache:
    """Selectors learned from examples, kept per site, so a changed price does not lose the lesson."""

    def __init__(self, path: str):
        self.path = Path(path)

    @staticmethod
    def _key(url: str, fields: Dict[str, Any]) -> str:
        return f"{site_key(urlsplit(url).netloc)}|{','.join(sorted(fields))}"

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, url: str, fields: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, str]]]:
        entry = self._load().get(self._key(url, fields))
        return (entry["item"], entry["fields"]) if entry else None

    def put(self, url: str, fields: Dict[str, Any], item: str, selectors: Dict[str, str]) -> bool:
        """Remember selectors. Returns whether they replace different ones: the site's layout drifted."""
        data = self._load()
        key = self._key(url, fields)
        entry = {"item": item, "fields": selectors}
        before = data.get(key)
        if before == entry:
            return False
        data[key] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        return before is not None
