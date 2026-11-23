"""A light copy of a fetched page for the live view: its text blocks, links and items.

Built from the HTML the crawler already has, so drawing the page costs the site nothing.
"""
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import lxml.html

from my_scraper.utils.urls import safe_join

MAX_HTML_CHARS = 400_000   # enough for the top of any page, and fast to parse on the event loop
MAX_BLOCKS = 70
MAX_BLOCK_CHARS = 220
MAX_IMAGES = 10
INLINE = {"a", "span", "b", "strong", "i", "em", "small", "code", "abbr", "sup", "sub", "label", "time",
          "mark", "u", "s", "cite", "q", "br", "font", "bdi", "data", "kbd", "var", "wbr"}
SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "select", "option", "button",
        "nav", "header", "footer", "form", "input", "textarea", "canvas"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SPACE = re.compile(r"\s+")

Segment = Tuple[str, Optional[str]]  # text, and the link it belongs to


def build(url: str, fetch_result: Dict[str, Any], items: List[Dict[str, Any]],
          follows: Callable[[str], bool]) -> Dict[str, Any]:
    """The page as blocks of text with inline links, flagged where an item or a followed link is."""
    sketch: Dict[str, Any] = {"url": url, "title": None, "nav": [], "blocks": [], "items": len(items)}
    names = [name for name in (_item_name(item) for item in items[:200]) if name]
    keys = [name.lower() for name in names]
    content = fetch_result.get("content") or ""
    content_type = (fetch_result.get("headers") or {}).get("content-type", "html").lower()
    if "html" not in content_type or not content.strip():
        sketch["blocks"] = [{"k": "row", "parts": [[name, None, False]], "item": True} for name in names[:MAX_BLOCKS]]
        return sketch

    try:
        doc = lxml.html.fromstring(content[:MAX_HTML_CHARS])
    except (ValueError, lxml.etree.ParserError):
        return sketch
    sketch["title"] = _clean(doc.findtext(".//title") or "")[:120] or None
    for link in doc.xpath("(//nav//a | //header//a)[normalize-space()]")[:8]:
        sketch["nav"].append(_clean(link.text_content())[:30])

    body = doc.find("body") if doc.find("body") is not None else doc
    blocks: List[Dict[str, Any]] = []
    images = 0
    for element in _walk(body):
        tag = element.tag
        if tag == "img":
            if images < MAX_IMAGES and (not blocks or blocks[-1]["k"] != "img"):
                blocks.append({"k": "img", "alt": _clean(element.get("alt") or "")[:60]})
                images += 1
            continue
        parts = _merge(_segments(element, url, None))
        text = "".join(part for part, _ in parts).strip()
        if len(text) < 3:
            continue
        block = {"k": "h" if tag in HEADINGS else "li" if tag == "li" else "p",
                 "parts": [[part, href, bool(href and follows(href))] for part, href in parts]}
        if _just_a_link(block) and blocks and blocks[-1]["k"] in ("links",) + (("li",) if _just_a_link(blocks[-1]) else ()):
            previous = blocks[-1]  # a menu or category list: one cloud of links, not fifty blocks
            previous["k"] = "links"
            previous["parts"] += [[" · ", None, False], *block["parts"]]
            continue
        lowered = text.lower()
        if any(key in lowered or (len(lowered) >= 12 and lowered in key) or key[:14] in lowered.replace("...", "")
               for key in keys if len(key) >= 14 or key in lowered):
            block["item"] = True
        blocks.append(block)
        if len(blocks) >= MAX_BLOCKS:
            break
    sketch["blocks"] = blocks
    return sketch


def _just_a_link(block: Dict[str, Any]) -> bool:
    """A short block that is nothing but one link, like a menu entry."""
    linked = [part for part in block["parts"] if part[1]]
    text = "".join(part[0] for part in block["parts"]).strip()
    return block["k"] in ("li", "links") and len(linked) >= 1 and len(text) < 60 and all(
        part[1] or not part[0].strip() or part[0] == " · " for part in block["parts"])


def _walk(root):
    """Block-level elements in document order, skipping page furniture."""
    stack = [root]
    while stack:
        element = stack.pop()
        tag = element.tag if isinstance(element.tag, str) else None
        if tag is None or tag in SKIP:
            continue
        if tag not in INLINE:
            yield element
        stack.extend(reversed([child for child in element if isinstance(child.tag, str)]))


def _segments(element, base: str, href: Optional[str]) -> List[Segment]:
    """The element's own text, with links kept, leaving out text of nested blocks."""
    parts: List[Segment] = []
    if element.text:
        parts.append((element.text, href))
    for child in element:
        tag = child.tag if isinstance(child.tag, str) else None
        if tag in INLINE:
            link = href
            if tag == "a" and child.get("href") and not child.get("href", "").startswith(("#", "javascript:", "mailto:")):
                link = safe_join(base, child.get("href"))
            parts.extend(_segments(child, base, link))
        if child.tail:
            parts.append((child.tail, href))
    return parts


def _merge(parts: List[Segment]) -> List[Segment]:
    """Join neighbours with the same link, squeeze whitespace, and cut to a readable length."""
    merged: List[List[Any]] = []
    for text, href in parts:
        text = SPACE.sub(" ", text)
        if merged and merged[-1][1] == href:
            merged[-1][0] += text
        elif text.strip() or merged:
            merged.append([text, href])
    out: List[Segment] = []
    room = MAX_BLOCK_CHARS
    for text, href in merged:
        if room <= 0:
            break
        if len(text) > room:
            text = text[:room].rstrip() + "…"
        room -= len(text)
        out.append((text, href))
    if out:
        out[0] = (out[0][0].lstrip(), out[0][1])
        out[-1] = (out[-1][0].rstrip(), out[-1][1])
    return [(text, href) for text, href in out if text]


def _item_name(item: Dict[str, Any]) -> Optional[str]:
    for field in ("title", "name", "headline", "text"):
        value = item.get(field)
        if isinstance(value, str) and len(value.strip()) >= 4:
            return _clean(value)[:40]
    return None


def _clean(text: str) -> str:
    return SPACE.sub(" ", text).strip()
