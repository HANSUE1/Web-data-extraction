"""Find the links on a page worth following, and leave the ones that are not."""
import re
from typing import Dict, List, Optional, Set

from parsel import Selector

from my_scraper.utils.urls import safe_join

NEXT_LABELS = {
    "next", "next page", "next »", "next ›", "next >", "older", "older posts", "older entries", "›", "»", ">", "→",
    "suivant", "suivante", "page suivante", "weiter", "nächste", "nächste seite", "siguiente", "próximo", "próxima",
    "successivo", "avanti", "volgende", "następna", "следующая", "далее", "次へ", "次のページ", "下一页", "下页", "다음",
}
NEXT_ARIA = re.compile(r"\bnext\b", re.IGNORECASE)
PAGER_NAMES = re.compile(r"pagination|pager|paging|page-numbers", re.IGNORECASE)
PAGER_ANCESTORS = 3

HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
SCRIPTED_LINK = re.compile(r"""location(?:\.href)?\s*=\s*['"]([^'"]+)['"]|window\.open\(\s*['"]([^'"]+)['"]""")


def _label(element) -> str:
    return " ".join(element.text_content().split()).lower()


def _classes(element) -> List[str]:
    return (element.get("class") or "").lower().split()


def _is_hidden_link(element) -> bool:
    """Links nobody can see. Some exist only to catch crawlers, so they are never followed."""
    for node in (element, *element.iterancestors()):
        if node.get("hidden") is not None or HIDDEN_STYLE.search(node.get("style") or ""):
            return True
    return False


def base_url_of(sel: Selector, page_url: str) -> str:
    """The address relative links resolve against, which <base href> can change."""
    base = sel.xpath("//base/@href").get()
    return (safe_join(page_url, base) if base else None) or page_url


def forbids_following(sel: Selector) -> bool:
    """Whether the page's robots meta tag asks crawlers not to follow its links."""
    directives = " ".join(sel.xpath(
        "//meta[translate(@name, 'ROBOTS', 'robots') = 'robots']/@content").getall()).lower()
    return "nofollow" in directives or "none" in re.split(r"[\s,]+", directives)


HEAD_CHARS = 200_000


def robots_directives(content: str, headers: Dict[str, str], agent: str) -> Set[str]:
    """What a page asks of crawlers, in its robots meta tag and X-Robots-Tag header: {"noindex", "nofollow"}.

    Directives addressed to another crawler by name are not for us.
    """
    given: List[str] = []
    header_agent = None
    for part in (headers.get("x-robots-tag") or "").lower().split(","):
        name, separator, directive = part.partition(":")
        if separator and name.strip() not in ("unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"):
            header_agent, part = name.strip(), directive
        if header_agent in (None, "*", agent.lower()):
            given.append(part)
    # Reading the tags means parsing the page, so only when one is there.
    if re.search(rf"<meta[^>]+(?:robots|{re.escape(agent)})", content[:HEAD_CHARS], re.IGNORECASE):
        for tag in Selector(text=content, type="html").xpath("//meta[@name and @content]"):
            if tag.attrib["name"].strip().lower() in ("robots", agent.lower()):
                given.append(tag.attrib["content"].lower())
    directives = {word for text in given for word in re.split(r"[\s,]+", text.strip()) if word}
    return directives | {"noindex", "nofollow"} if "none" in directives else directives


def _in_pager(element) -> bool:
    node: Optional[object] = element.getparent()
    for _ in range(PAGER_ANCESTORS):
        if node is None:
            return False
        names = " ".join(filter(None, (node.get("class"), node.get("id"), node.get("aria-label"), node.get("role"))))
        if PAGER_NAMES.search(names):
            return True
        node = node.getparent()
    return False


def _is_next_link(element) -> bool:
    if "next" in (element.get("rel") or "").lower().split() or "next" in _classes(element):
        return True
    parent = element.getparent()
    if parent is not None and "next" in _classes(parent):
        return True
    if NEXT_ARIA.search(element.get("aria-label") or ""):
        return True
    label = _label(element)
    return label in NEXT_LABELS or (label.isdigit() and _in_pager(element))


def next_page_links(sel: Selector) -> List[str]:
    """Links to further pages of the same listing."""
    links = sel.xpath("//link[contains(concat(' ', normalize-space(@rel), ' '), ' next ')]/@href").getall()
    for element in sel.root.iter("a"):
        if element.get("href") and _is_next_link(element) and not _is_hidden_link(element):
            links.append(element.get("href"))
    return links


def page_links(sel: Selector) -> List[str]:
    """Every ordinary link on a page, wherever it is hiding."""
    links = []
    for element in sel.root.iter("a", "area"):
        href = element.get("href")
        if href and "nofollow" not in (element.get("rel") or "").lower() and not _is_hidden_link(element):
            links.append(href)

    # Old sites keep their content in frames, and some link with script instead of <a>.
    links.extend(element.get("src") for element in sel.root.iter("frame", "iframe") if element.get("src"))
    links.extend(sel.xpath("//@data-href | //@data-url").getall())
    for handler in sel.xpath("//@onclick").getall():
        match = SCRIPTED_LINK.search(handler)
        if match:
            links.append(match.group(1) or match.group(2))
    return links
