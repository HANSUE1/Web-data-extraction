"""Selector-free extraction: turn any HTML page into items without site-specific rules.

Several independent methods look at each page:

1. Structured data the site publishes (JSON-LD, microdata)
2. Repeated layouts in the page's main content: cards, rows, data tables
3. Data embedded for the site's own JavaScript, or fetched by it while rendering
4. The page as a single document: main text plus its metadata

Methods that find a list compete, and a method that fails never costs the others.
"""
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import extruct
import trafilatura
from lxml import html as lxml_html
from parsel import Selector

from my_scraper.parser.documents import unique_headers
from my_scraper.parser.embedded_state import embedded_items, records_to_items

Item = Dict[str, Any]

MAX_HTML_CHARS = 6_000_000
SNAPSHOT_SECONDS = 20
MIN_LIST_ITEMS = 3
MIN_BLOCK_QUALITY = 0.3
SECOND_LIST_QUALITY = 0.4       # further lists on the page must be convincing in their own right
SECOND_LIST_SHARE = 0.2         # and not trivial next to the main one
DIRECTORY_MIN_ITEMS = 8
DIRECTORY_TEXT_SHARE = 0.5      # a page that is mostly one list of links is a directory, not a menu
SCORED_BLOCKS_PER_GROUP = 30
MAX_SCORED_GROUPS = 60
MIN_TITLE_COVERAGE = 0.6
MIN_UNIQUE_TITLES = 0.6
MIN_PROSE_PARAGRAPH = 80
MIN_MARKED_MAIN_SHARE = 0.15    # <main> is the page's own word for it
MIN_GUESSED_MAIN_SHARE = 0.4    # an id that merely sounds right has to prove itself
MAX_ITEM_SHARE = 0.35           # no single item holds a big part of a page
MIN_LIST_COVERAGE = 0.4         # a list that is a small part of a page is just part of its text
STRONG_LIST_QUALITY = 0.7       # unless its items are unmistakable, like product cards
FULL_TITLE_LENGTH = 40          # a title this long scores as well as a title can
MIN_TITLE_LINK_LENGTH = 12
MIN_DESCRIPTION_LENGTH = 30
MAX_SHORT_FIELD = 60
PREFERRED_TABLE_SHARE = 0.7
MATCHED_SHARE = 0.8             # structured items take fields from the visible list only if this many match
HEADERLESS_TABLE_MIN_ROWS = 5
KEY_VALUE_TABLE_MAX_ROWS = 8
DETAIL_PROSE = 500              # an article has this much running text of its own
DETAIL_PROSE_SHARE = 0.5        # and at least half as much as any list on the page
GENERIC_TITLES = {"home", "homepage", "home page", "index", "welcome", "main page"}
DETAIL_TYPES = {"product", "recipe", "event", "book", "movie", "course", "jobposting", "softwareapplication"}
MAX_ATTRIBUTE_PAIRS = 60

ITEM_TAGS = {"li", "div", "article", "section", "tr", "a", "dd", "figure"}

# Page furniture, never content.
SKIP_TAGS = {"head", "script", "style", "noscript", "nav", "header", "footer", "aside",
             "select", "option", "svg", "template", "button", "label"}
HEADINGS = "self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6"
VISIBLE_TEXT = ".//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"
MARKED_MAIN = "//main | //*[@role='main']"
GUESSED_MAIN = ("//*[@id='main' or @id='content' or @id='main-content' or @id='maincontent' or @id='primary'"
                " or @id='mw-content-text']")

BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "menu", "menubar",
                     "dialog", "alertdialog"}
BOILERPLATE_NAMES = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|navigation|menu|mega-?menu|sidebar|side-?bar|footer|masthead|breadcrumbs?"
    r"|pagination|pager|cookies?|consent|gdpr|modal|popup|newsletter|social|sharing|advert|advertisement"
    r"|sponsored|site-?header|site-?footer|global-?(?:header|footer|nav))(?:[\s_-]|$)",
    re.IGNORECASE,
)
MAX_BOILERPLATE_SHARE = 0.4  # a "with-sidebar" wrapper around the whole page is not itself a sidebar
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)

TITLE_CLASSES = re.compile(r"title|headline|heading|(?<![a-z])name", re.IGNORECASE)
NOT_TITLE_CLASSES = re.compile(r"author|user|subtitle|sub-title|byline|site-?name|brand|category", re.IGNORECASE)
TITLE_LABEL = re.compile(r"^(title|name|headline)\s*:\s*", re.IGNORECASE)
DATE_CLASSES = re.compile(r"date|time|publish|posted|timestamp", re.IGNORECASE)
AUTHOR_CLASSES = re.compile(r"author|byline|creator|hnuser", re.IGNORECASE)
DESCRIPTION_CLASSES = re.compile(r"desc|summary|excerpt|teaser|snippet|standfirst|abstract|dek|lede|intro", re.IGNORECASE)
RATING_CLASSES = re.compile(r"rating|stars?(?![a-z])", re.IGNORECASE)
RATING_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
RATING_VALUE = re.compile(r"(\d(?:[.,]\d+)?)\s*(?:out of|of|/)\s*(?:5|five|10)", re.IGNORECASE)

COUNTER_TITLE = re.compile(r"^\W*\d[\d,.]*\s*[\w%]*\W*$", re.UNICODE)  # "54 comments", "2.34.2", "1,204"

# Site-level or supporting schema.org types that describe no content of their own.
NON_CONTENT_TYPES = {
    "webpage", "website", "webcontent", "organization", "corporation", "governmentorganization",
    "newsmediaorganization", "localbusiness", "person", "breadcrumblist", "listitem", "imageobject",
    "videoobject", "searchaction", "sitenavigationelement", "wpheader", "wpfooter", "wpsidebar",
    "collectionpage", "searchresultspage", "itempage", "faqpage", "aboutpage", "contactpage",
    "profilepage", "offer", "aggregateoffer", "aggregaterating", "rating", "review", "postaladdress",
    "place", "brand", "thing", "question", "answer", "howtostep", "itemlist", "blog", "readaction",
    "entrypoint", "contactpoint", "geocoordinates", "openinghoursspecification", "definedterm",
}
# Containers worth opening. Breadcrumbs also use itemListElement, but hold only page furniture.
ENTITY_CONTAINERS = ("@graph", "mainEntity", "blogPost", "itemListElement", "hasPart")
CLOSED_ENTITY_TYPES = {"breadcrumblist", "sitenavigationelement"}

CURRENCY_SYMBOLS = "$€£¥₹₩₽₺₪฿₫₴₦₱"
CURRENCY_WORDS = (r"USD|EUR|GBP|JPY|CNY|INR|CAD|AUD|NZD|CHF|SEK|NOK|DKK|PLN|CZK|HUF|BRL|MXN|KRW|RUB|TRY|ZAR|SGD|HKD"
                  r"|kr|zł|Kč|Ft|lei|руб|円|元|원")
AMOUNT = r"\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
# Tried in this order: in "Model 5 $19.99", "5 $" also looks like an amount and a currency.
PRICE_PATTERNS = (
    re.compile(rf"(?:R\$|C\$|A\$|[{CURRENCY_SYMBOLS}])\s?(?:{AMOUNT})"),
    re.compile(rf"\b(?:USD|EUR|GBP|CHF|CAD|AUD|SEK|NOK|DKK)\s?(?:{AMOUNT})"),
    re.compile(rf"(?<![\w.,])(?:{AMOUNT})\s?(?:[{CURRENCY_SYMBOLS}]|(?:{CURRENCY_WORDS})(?![a-zA-Z]))"),
)


def find_price(text: str) -> Optional[str]:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def visual_length(text: str) -> int:
    """Length that counts wide characters double. Japanese titles are short in characters alone."""
    return sum(2 if ord(character) >= 0x2E80 else 1 for character in text)


def _clean(text: Optional[str]) -> str:
    return " ".join(text.split()) if text else ""


def _leaves(element) -> List[str]:
    """Each piece of visible text inside an element, cleaned."""
    return [text for text in map(_clean, element.xpath(VISIBLE_TEXT)) if text]


def _text(element) -> str:
    return " ".join(_leaves(element))


def _compact(item: Item) -> Item:
    """Drop empty fields."""
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


def _has_letters(text: str) -> bool:
    return any(character.isalpha() for character in text)


def _url(value: Optional[str], page_url: str) -> Optional[str]:
    if not value:
        return None
    try:
        return urljoin(page_url, value.strip())
    except ValueError:
        return None


# --- 1. Structured data ------------------------------------------------------

def _type_of(entity: Dict[str, Any]) -> str:
    value = entity.get("@type")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).rsplit("/", 1)[-1]


def _entities(node: Any) -> Iterator[Dict[str, Any]]:
    """Walk JSON-LD or microdata, opening containers that hold content."""
    if isinstance(node, list):
        for entry in node:
            yield from _entities(entry)
    elif isinstance(node, dict):
        if _type_of(node).lower() in CLOSED_ENTITY_TYPES:
            return
        for key in ENTITY_CONTAINERS:
            if key in node:
                yield from _entities(node[key])
        if isinstance(node.get("item"), dict):  # ListItem wrapper
            yield from _entities(node["item"])
        if "@type" in node:
            yield node


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _plain(value: Any) -> Optional[str]:
    """Reduce schema.org values such as {"name": "x"} or ["x", "y"] to text."""
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("name") or value.get("url") or value.get("@value")
    if value in (None, "") or isinstance(value, (dict, list)):
        return None
    return _clean(str(value))


def _strip_markup(value: Optional[str]) -> Optional[str]:
    if value and "<" in value:
        return _clean(lxml_html.fromstring(f"<div>{value}</div>").text_content())
    return value


def _normalize_entity(entity: Dict[str, Any], page_url: str) -> Item:
    offers = _first(entity.get("offers"))
    offers = offers if isinstance(offers, dict) else {}
    rating = entity.get("aggregateRating")
    rating = rating if isinstance(rating, dict) else {}
    location = _url(_plain(entity.get("url") or entity.get("@id")), page_url)
    image = _first(entity.get("image"))
    availability = _plain(offers.get("availability"))
    ingredients = entity.get("recipeIngredient")

    return _compact({
        "type": _type_of(entity),
        "title": _plain(entity.get("name") or entity.get("headline")),
        "url": location,
        "description": _strip_markup(_plain(entity.get("description"))),
        "text": _plain(entity.get("articleBody")),
        "image": _url(_plain(image.get("url") if isinstance(image, dict) else image), page_url),
        "author": _plain(entity.get("author")),
        "date": _plain(entity.get("datePublished") or entity.get("startDate") or entity.get("datePosted")),
        "price": _plain(offers.get("price") or offers.get("lowPrice")),
        "currency": _plain(offers.get("priceCurrency")),
        "availability": availability.rsplit("/", 1)[-1] if availability else None,
        "rating": _plain(rating.get("ratingValue")),
        "brand": _plain(entity.get("brand")),
        "sku": _plain(entity.get("sku")),
        "ingredients": [_clean(str(entry)) for entry in ingredients] if isinstance(ingredients, list) else None,
    })


def structured_items(metadata: Dict[str, Any], page_url: str) -> List[Item]:
    """Content entities published by the page, skipping site-level ones."""
    items = []
    seen = set()
    for source in ("json-ld", "microdata"):
        for entity in _entities(metadata.get(source, [])):
            if _type_of(entity).lower() in NON_CONTENT_TYPES:
                continue
            item = _normalize_entity(entity, page_url)
            key = (item.get("title"), item.get("url"))
            # Some sites mark a link as the name, which leaves a URL where the title should be.
            if item.get("title") and "://" not in item["title"] and key not in seen:
                seen.add(key)
                items.append({**item, "source": source})
    return items


def _describes_page(item: Item, page_url: str) -> bool:
    """Whether an entity is this page itself, not something the page merely features."""
    location = item.get("url")
    if not location:
        return True
    return urlsplit(location).path.rstrip("/") == urlsplit(page_url).path.rstrip("/")


# --- 2. Repeated layouts -----------------------------------------------------

def is_hidden(element) -> bool:
    if element.get("hidden") is not None or element.get("aria-hidden") == "true":
        return True
    return bool(HIDDEN_STYLE.search(element.get("style") or ""))


def _is_boilerplate(element, page_text_length: int) -> bool:
    if element.tag in ("main", "article"):
        return False  # the page says this is content, whatever its classes suggest
    role = (element.get("role") or "").lower()
    if role in BOILERPLATE_ROLES:
        return True
    names = f"{element.get('class') or ''} {element.get('id') or ''}"
    if not BOILERPLATE_NAMES.search(names):
        return False
    return len(_text(element)) < MAX_BOILERPLATE_SHARE * max(page_text_length, 1)


def _content_length(root) -> int:
    """Characters of text outside menus and footers, which can dwarf a page's real content."""
    total_text = len(_text(root))
    total = 0
    stack = [root]
    while stack:
        element = stack.pop()
        total += len(_clean(element.text))
        for child in element:
            if not isinstance(child.tag, str):
                continue
            total += len(_clean(child.tail))
            if child.tag not in SKIP_TAGS and not is_hidden(child) and not _is_boilerplate(child, total_text):
                stack.append(child)
    return total


def _can_be_item(element) -> bool:
    """Items are containers. Paragraphs in a row are prose, and spans are parts of something."""
    if (element.get("role") or "").lower() == "tabpanel":
        return False
    return element.tag in ITEM_TAGS or "-" in element.tag  # custom elements such as <product-card>


def _signature(element) -> Tuple[str, Tuple[str, ...]]:
    """Blocks of one list share a tag and classes. Numbered classes vary per item."""
    classes = (element.get("class") or "").split()
    return element.tag, tuple(sorted(name for name in classes if not any(c.isdigit() for c in name)))


def _main_area(root):
    """The element holding the page's real content, when the page marks one."""
    if root.xpath("//*[@data-frame-src]"):
        return root  # frames were added at the end of the body, outside any main element

    body_length = _content_length(root)
    best = None
    best_length = 0
    for query, share in ((MARKED_MAIN, MIN_MARKED_MAIN_SHARE), (GUESSED_MAIN, MIN_GUESSED_MAIN_SHARE)):
        for candidate in root.xpath(query):
            length = _content_length(candidate)
            if length >= share * body_length and length > best_length:
                best, best_length = candidate, length
        if best is not None:
            return best
    return root


def _candidate_groups(root) -> List[List[Any]]:
    """Every set of elements that look alike.

    Parents are compared by signature too, so a grid split into rows of three
    still counts as one list. Walking top to bottom keeps items in page order.
    """
    page_text_length = len(_text(root))
    groups = defaultdict(list)
    stack = [root]
    while stack:
        parent = stack.pop()
        if parent is not root and _can_be_item(parent):
            groups[_signature(parent), _signature(parent.getparent())].append(parent)
        children = [child for child in parent
                    if isinstance(child.tag, str) and child.tag not in SKIP_TAGS
                    and not is_hidden(child) and not _is_boilerplate(child, page_text_length)]
        stack.extend(reversed(children))
    return [group for group in groups.values() if len(group) >= MIN_LIST_ITEMS]


def _class_matches(element, pattern: re.Pattern) -> bool:
    return bool(pattern.search(element.get("class") or ""))


def _with_class(block, pattern: re.Pattern) -> Iterator[Any]:
    return (element for element in block.iter() if isinstance(element.tag, str) and _class_matches(element, pattern))


def _link_of(element) -> Optional[Any]:
    """The link an element is in, or holds."""
    if element.tag == "a" and element.get("href"):
        return element
    outer = element.xpath("ancestor::a[@href][1]")
    if outer:
        return outer[0]
    inner = element.xpath(".//a[@href]")
    return inner[0] if inner else None


def _titled(text: str, link, page_url: str) -> Tuple[str, Optional[str]]:
    if link is None:
        return TITLE_LABEL.sub("", text), None

    full_title = _clean(link.get("title"))
    link_text = _clean(link.text_content())
    # Links often hold a shortened title and the full one in their title attribute.
    if full_title and len(full_title) >= len(text.rstrip(".… ")):
        text = full_title
    # A title followed by a site name or a date: the link is the title proper.
    elif link_text and len(link_text) < len(text) and text.startswith(link_text):
        text = link_text
    return TITLE_LABEL.sub("", text), _url(link.get("href"), page_url)


def _unwritten_title(block, page_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Picture-only cards keep their title in alt text or a label."""
    for element in block.xpath("descendant-or-self::*[@aria-label] | .//img[@alt]"):
        title = _clean(element.get("aria-label") or element.get("alt"))
        if _has_letters(title):
            link = _link_of(element)
            return title, _url(link.get("href"), page_url) if link is not None else None
    return None, None


def _title_of(block, page_url: str) -> Tuple[Optional[str], Optional[str]]:
    """Find a block's title, and the URL if the title is a link."""
    for heading in block.xpath(f".//*[{HEADINGS}]"):
        text = _text(heading)
        if _has_letters(text):
            return _titled(text, _link_of(heading), page_url)

    for element in _with_class(block, TITLE_CLASSES):
        if _class_matches(element, NOT_TITLE_CLASSES):
            continue
        text = _text(element)
        if _has_letters(text):
            return _titled(text, _link_of(element), page_url)

    for link in block.xpath(".//a[@href and @title]"):
        if visual_length(_clean(link.get("title"))) >= MIN_TITLE_LINK_LENGTH:
            return _titled(_clean(link.text_content()), link, page_url)

    leaves = [leaf for leaf in _leaves(block) if _has_letters(leaf)]
    if not leaves:
        return _unwritten_title(block, page_url)
    longest_text = max(leaves, key=visual_length)

    links = [(visual_length(_clean(link.text_content())), link) for link in block.xpath("descendant-or-self::a[@href]")]
    if links:
        length, link = max(links, key=lambda pair: pair[0])
        # A link well short of the main text is a tag or "read more", unless it opens the block.
        opens_block = _clean(link.text_content()) == leaves[0]
        if length > 0 and (opens_block or (length >= MIN_TITLE_LINK_LENGTH and length * 2 >= visual_length(longest_text))):
            return _clean(link.text_content()), _url(link.get("href"), page_url)

    return longest_text, None


def _image_of(block, page_url: str) -> Optional[str]:
    for image in block.xpath(".//img | .//source"):
        source = (image.get("data-src") or image.get("data-lazy-src") or image.get("data-original")
                  or image.get("src") or (image.get("srcset") or image.get("data-srcset") or "").split(" ")[0])
        if source and not source.startswith("data:"):
            return _url(source.rstrip(","), page_url)
    return None


def _date_of(block) -> Optional[str]:
    moments = block.xpath(".//time")
    if moments:
        return moments[0].get("datetime") or _text(moments[0]) or None
    for element in _with_class(block, DATE_CLASSES):
        text = _text(element)
        if text and len(text) <= MAX_SHORT_FIELD and any(character.isdigit() for character in text):
            return text
    return None


def _author_of(block, title: Optional[str]) -> Optional[str]:
    for element in _with_class(block, AUTHOR_CLASSES):
        text = _text(element)
        if text and text != title and len(text) <= MAX_SHORT_FIELD:
            return text
    return None


def _description_of(block, title: Optional[str]) -> Optional[str]:
    for element in _with_class(block, DESCRIPTION_CLASSES):
        text = _text(element)
        if len(text) >= MIN_DESCRIPTION_LENGTH and text != title:
            return text
    for paragraph in block.iter("p"):
        text = _text(paragraph)
        if len(text) >= MIN_DESCRIPTION_LENGTH and text != title:
            return text
    return None


def _rating_of(block) -> Optional[str]:
    for element in _with_class(block, RATING_CLASSES):
        for word in (element.get("class") or "").lower().split():
            if word in RATING_WORDS:
                return RATING_WORDS[word]
        label = " ".join(filter(None, (element.get("aria-label"), element.get("title"), _text(element))))
        match = RATING_VALUE.search(label)
        if match:
            return match.group(1)
    return None


def _block_item(block, page_url: str) -> Item:
    """One item from a block. The dt before a dd holds its link in definition lists."""
    title, link = _title_of(block, page_url)
    text = _text(block)
    if link is None and block.tag == "dd":
        previous = block.getprevious()
        if previous is not None and previous.tag == "dt":
            term = _link_of(previous)
            link = _url(term.get("href"), page_url) if term is not None else None

    return _compact({
        "title": title,
        "url": link,
        "image": _image_of(block, page_url),
        "price": find_price(text),
        "date": _date_of(block),
        "author": _author_of(block, title),
        "rating": _rating_of(block),
        "description": _description_of(block, title),
        "text": text if text != title else None,
        "source": "list",
    })


def _block_quality(item: Item, block) -> float:
    """Score 0 to 1 for how much an item looks like content, not a menu entry or a counter."""
    title = item.get("title")
    if not title or COUNTER_TITLE.match(title):
        return 0.05

    score = min(visual_length(title), FULL_TITLE_LENGTH) / FULL_TITLE_LENGTH * 0.4
    location = item.get("url")
    if location:
        path = urlsplit(location).path.strip("/")
        specific = len(path) >= 20 or path.count("/") >= 2 or any(character.isdigit() for character in path)
        score += 0.2 if specific else 0.05
    if block.xpath(f".//*[{HEADINGS}]"):
        score += 0.15
    if item.get("image"):
        score += 0.15
    if item.get("price"):
        score += 0.25
    if item.get("date"):
        score += 0.1
    extra_text = len(item.get("text", "")) - len(title)
    if extra_text >= 30:
        score += 0.2
    elif extra_text >= 8:
        score += 0.1

    # A bullet point has a sentence and nothing else: no link, picture, price or detail.
    has_evidence = location or item.get("image") or item.get("price") or item.get("date")
    if not has_evidence and extra_text < 8 and not block.xpath(".//a[@href]"):
        score *= 0.5
    return min(score, 1.0)


@dataclass
class ScoredGroup:
    blocks: List[Any]
    quality: float
    score: float
    text_length: int


def _score_group(blocks: List[Any], page_url: str, area_text_length: int) -> Optional[ScoredGroup]:
    sample = blocks[:SCORED_BLOCKS_PER_GROUP]
    items = [_block_item(block, page_url) for block in sample]
    titles = [item.get("title") for item in items if item.get("title")]
    # A card inserted twice (an infinite scroll that fired twice) is still one item,
    # but one title on many different links is a row of "Read more" buttons.
    cards = {(item["title"], item.get("url") or position) for position, item in enumerate(items) if item.get("title")}
    if (len(titles) < MIN_TITLE_COVERAGE * len(items)
            or len({title for title, _ in cards}) < MIN_UNIQUE_TITLES * len(cards)):
        return None

    quality = sum(_block_quality(item, block) for item, block in zip(items, sample, strict=True)) / len(sample)
    lengths = [len(_text(block)) for block in blocks]
    text_length = sum(lengths)
    # Page sections are not items. A short list must allow its items a bigger share each.
    # A gallery of pictures has no text at all, which proves nothing either way.
    if area_text_length and max(lengths) >= max(MAX_ITEM_SHARE, 2 / len(blocks)) * area_text_length:
        return None
    score = len(blocks) * quality * quality * (1 + math.log1p(text_length / len(blocks)))
    if _signature(blocks[0])[1]:
        score *= 1.3  # a named class marks a deliberate component, not a layout accident
    return ScoredGroup(blocks, quality, score, text_length)


def _overlaps(group: ScoredGroup, taken: set) -> bool:
    """Whether a group sits inside, or wraps, blocks already chosen."""
    for block in group.blocks:
        if block in taken or any(ancestor in taken for ancestor in block.iterancestors()):
            return True
        if any(element in taken for element in block.iter()):
            return True
    return False


def _chosen_groups(root, area_text_length: int, page_url: str) -> List[ScoredGroup]:
    """The page's main list, plus any other lists convincing enough to keep."""
    groups = sorted(_candidate_groups(root), key=len, reverse=True)[:MAX_SCORED_GROUPS]
    scored = [group for group in (_score_group(blocks, page_url, area_text_length) for blocks in groups) if group]

    def is_directory(group: ScoredGroup) -> bool:
        return (len(group.blocks) >= DIRECTORY_MIN_ITEMS
                and group.text_length >= DIRECTORY_TEXT_SHARE * area_text_length)

    usable = [group for group in scored if group.quality >= MIN_BLOCK_QUALITY or is_directory(group)]
    if not usable:
        return []
    usable.sort(key=lambda group: group.score, reverse=True)

    chosen = [usable[0]]
    taken = set(usable[0].blocks)
    for group in usable[1:]:
        convincing = group.quality >= SECOND_LIST_QUALITY and group.score >= SECOND_LIST_SHARE * usable[0].score
        if convincing and not _overlaps(group, taken):
            chosen.append(group)
            taken.update(group.blocks)
    return chosen


def _cells_of(row) -> List[Any]:
    """A row's cells, repeating any that span columns so they stay under their headers."""
    cells = []
    for cell in row.xpath("./th | ./td"):
        try:
            span = max(int(cell.get("colspan") or 1), 1)
        except ValueError:
            span = 1
        cells.extend([cell] * min(span, 20))
    return cells


def _table_row_item(headers: List[str], cells: List[Any], row, page_url: str) -> Item:
    values = dict(zip(headers, map(_text, cells), strict=True))
    link = row.xpath(".//a[@href]")
    first_text = next((value for value in values.values() if value and _has_letters(value)), None)
    return _compact({
        **values,
        "title": values.get("title") or first_text,
        "url": values.get("url") or (_url(link[0].get("href"), page_url) if link else None),
        "source": "table",
    })


def _table_headers(rows: List[Any]) -> Tuple[Optional[List[str]], List[Any]]:
    """Column names and the remaining data rows, or None if the table has no header row."""
    for position, row in enumerate(rows[:3]):
        cells = _cells_of(row)
        if len(cells) >= 2 and row.xpath("./th") and len(row.xpath("./th")) * 2 >= len(row.xpath("./th | ./td")):
            names = [_text(cell) for cell in cells]
            names = [f"{name}_column" if name.lower() == "source" else name for name in names]
            return unique_headers(names), rows[position + 1:]
    return None, rows


def _table_items(root, page_url: str) -> Tuple[List[Item], int]:
    """Rows of the largest data table, keyed by column name.

    Tables with the same columns are one table split up, such as a list in sections.
    """
    by_columns: Dict[Tuple[str, ...], Tuple[List[Item], int]] = {}

    for table in root.xpath(".//table[not(.//table)]"):
        if is_hidden(table):
            continue
        rows = table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
        if len(rows) <= MIN_LIST_ITEMS - 1:
            continue

        headers, data_rows = _table_headers(rows)
        if headers is None:
            widths = {len(_cells_of(row)) for row in rows}
            if len(rows) < HEADERLESS_TABLE_MIN_ROWS or len(widths) != 1 or widths == {1}:
                continue
            headers = [f"column_{number}" for number in range(1, widths.pop() + 1)]

        if len(headers) == 2 and len(data_rows) < KEY_VALUE_TABLE_MAX_ROWS:
            continue  # a small two-column table is a specification, kept as attributes instead

        items = []
        for row in data_rows:
            cells = _cells_of(row)
            if len(cells) == len(headers):
                items.append(_table_row_item(headers, cells, row, page_url))

        size = sum(len(str(value)) for item in items for value in item.values())
        if len(items) >= MIN_LIST_ITEMS:
            earlier_items, earlier_size = by_columns.get(tuple(headers), ([], 0))
            by_columns[tuple(headers)] = (earlier_items + items, earlier_size + size)

    return max(by_columns.values(), key=lambda found: found[1], default=([], 0))


def _prose_length(root, excluded: List[Any]) -> int:
    """Characters of real paragraphs outside the given blocks."""
    excluded_set = set(excluded)
    total = 0
    for paragraph in root.iter("p"):
        if paragraph in excluded_set or any(ancestor in excluded_set for ancestor in paragraph.iterancestors()):
            continue
        length = len(_text(paragraph))
        if length >= MIN_PROSE_PARAGRAPH:
            total += length
    return total


def _unique(items: List[Item]) -> List[Item]:
    seen = set()
    kept = []
    for item in items:
        key = repr(sorted(item.items(), key=lambda pair: pair[0]))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def list_items(root, page_url: str) -> List[Item]:
    """Items from the page's main lists or table, or nothing if it is really an article."""
    area = _main_area(root)
    area_text_length = _content_length(area)

    groups = _chosen_groups(area, area_text_length, page_url)
    table_rows, table_size = _table_items(area, page_url)
    blocks = [block for group in groups for block in group.blocks]
    block_size = sum(group.text_length for group in groups)

    if not groups and not table_rows:
        return []
    if _prose_length(area, blocks) > max(block_size, table_size):
        return []

    quality = sum(group.quality * len(group.blocks) for group in groups) / max(len(blocks), 1)
    if (not table_rows and quality < STRONG_LIST_QUALITY
            and block_size < MIN_LIST_COVERAGE * area_text_length):
        return []

    # A table's header row is real structure, so it wins whenever it covers similar ground.
    if table_rows and (table_size >= block_size or len(table_rows) >= PREFERRED_TABLE_SHARE * len(blocks)):
        return table_rows

    order = {element: position for position, element in enumerate(area.iter())}
    blocks.sort(key=lambda block: order.get(block, 0))

    # The same story often appears in two sections of a page.
    items = []
    seen = set()
    for block in blocks:
        item = _block_item(block, page_url)
        key = (item.get("title"), item.get("url"))
        if not item.get("url") or key not in seen:
            seen.add(key)
            items.append(item)
    return _unique(items)


# --- 3. Single document ------------------------------------------------------

def _attribute_pairs(root) -> Dict[str, str]:
    """Specifications with labels beside values: tables and definition lists."""
    pairs: Dict[str, str] = {}
    for row in root.xpath(".//table//tr[count(th|td)=2]"):
        cells = row.xpath("./th | ./td")
        key, value = _text(cells[0]), _text(cells[1])
        if key and value and len(key) <= MAX_SHORT_FIELD and _has_letters(key):
            pairs.setdefault(key, value)
    for term in root.xpath(".//dl/dt"):
        definition = term.getnext()
        if definition is not None and definition.tag == "dd":
            key, value = _text(term), _text(definition)
            if key and value and len(key) <= MAX_SHORT_FIELD:
                pairs.setdefault(key, value)
    return dict(list(pairs.items())[:MAX_ATTRIBUTE_PAIRS])


def _page_price(root) -> Optional[str]:
    for element in root.xpath(".//*[contains(translate(@class, 'PRICE', 'price'), 'price')]"):
        price = find_price(_text(element))
        if price:
            return price
    return None


def _open_graph(metadata: Dict[str, Any]) -> Dict[str, Any]:
    entries = metadata.get("opengraph") or [{}]
    return entries[0]


def document_item(html: str, root, metadata: Dict[str, Any], page_url: str) -> Item:
    """The page as one item: its main text with whatever metadata it offers."""
    extracted = trafilatura.bare_extraction(html, url=page_url, with_metadata=True)
    document = extracted.as_dict() if extracted is not None else {}
    graph = _open_graph(metadata)

    page_title = root.xpath("string(.//title)")
    heading = root.xpath(".//h1")
    return _compact({
        "title": graph.get("og:title") or (_text(heading[0]) if heading else None)
        or document.get("title") or _clean(page_title),
        "description": graph.get("og:description") or document.get("description"),
        "text": document.get("text"),
        "author": document.get("author"),
        "date": document.get("date"),
        "image": _url(graph.get("og:image") or document.get("image"), page_url),
        "price": _page_price(root),
        "language": document.get("language") or root.get("lang"),
        "attributes": _attribute_pairs(root),
        "source": "document",
    })


def page_markdown(html: str, page_url: str) -> Optional[str]:
    """The page's main content as Markdown."""
    return trafilatura.extract(html, url=page_url, output_format="markdown",
                               include_links=True, include_tables=True, include_images=False)


# --- Entry point -------------------------------------------------------------

def _attempt(method: Callable[..., Any], errors: List[str], default: Any, *args: Any) -> Any:
    """Run one extraction method. A method that breaks must not cost the others their result."""
    try:
        return method(*args)
    except Exception as e:  # these parse hostile input, and any of several libraries may raise
        errors.append(f"{method.__name__}: {type(e).__name__}: {e}")
        return default


def _read_metadata(html: str, page_url: str) -> Dict[str, Any]:
    return extruct.extract(html, base_url=page_url, errors="ignore", uniform=True,
                           syntaxes=["json-ld", "microdata", "opengraph"])


def _is_detail_page(item: Item, root, page_url: str) -> bool:
    """Whether a single published entity really is this page.

    Blog homepages often call themselves an Article named "Home", so an article must
    have running text that outweighs any list on the page. A product or recipe page is
    what it says it is.
    """
    if not _describes_page(item, page_url):
        return False
    if item.get("type", "").lower() in DETAIL_TYPES:
        return True
    if item.get("title", "").strip().lower() in GENERIC_TITLES:
        return False

    area = _main_area(root)
    groups = _chosen_groups(area, _content_length(area), page_url)
    blocks = [block for group in groups for block in group.blocks]
    prose = _prose_length(area, blocks)
    return prose >= DETAIL_PROSE and prose >= DETAIL_PROSE_SHARE * sum(group.text_length for group in groups)


def compare_methods(html: str, page_url: str, json_payloads: List[Any] = ()) -> Dict[str, List[Item]]:
    """What each method finds on its own, for explaining a result."""
    errors: List[str] = []
    root = Selector(text=html[:MAX_HTML_CHARS], type="html").root
    metadata = _attempt(_read_metadata, errors, {}, html, page_url)
    return {
        "structured": _attempt(structured_items, errors, [], metadata, page_url),
        "list": _attempt(list_items, errors, [], root, page_url),
        "embedded": _attempt(embedded_items, errors, [], html, page_url),
        "network": _attempt(records_to_items, errors, [], json_payloads, page_url, "network"),
        "document": [_attempt(document_item, errors, {}, html, root, metadata, page_url)],
    }


def _best_of(candidates: List[List[Item]]) -> List[Item]:
    """Candidates are ordered by trust. A later one must find far more to take over."""
    best: List[Item] = []
    for items in candidates:
        if len(items) >= MIN_LIST_ITEMS and len(items) > 1.5 * len(best):
            best = items
    return best


def _fill_gaps(trusted: List[Item], visible: List[Item]) -> List[Item]:
    """Add what the visible page shows, such as prices and links, to thin structured data.

    Only when nearly every item has a twin with the same title, so nothing gets mismatched.
    The structured values win wherever both have one.
    """
    twins = {item["title"]: item for item in visible if item.get("title")}
    if not trusted or sum(1 for item in trusted if item.get("title") in twins) < MATCHED_SHARE * len(trusted):
        return trusted
    return [{**item, **{key: value for key, value in twins.get(item.get("title"), {}).items()
                        if key != "source" and item.get(key) in (None, "", [])}}
            for item in trusted]


def _page_items(html: str, page_url: str, include_markdown: bool, errors: List[str]) -> List[Item]:
    root = Selector(text=html[:MAX_HTML_CHARS], type="html").root
    metadata = _attempt(_read_metadata, errors, {}, html, page_url)
    structured = _attempt(structured_items, errors, [], metadata, page_url)

    if len(structured) == 1 and _attempt(_is_detail_page, errors, True, structured[0], root, page_url):
        document = _attempt(document_item, errors, {}, html, root, metadata, page_url)
        items = [{**document, **structured[0]}]
    else:
        listing = _attempt(list_items, errors, [], root, page_url)
        embedded = _attempt(embedded_items, errors, [], html, page_url)
        items = _best_of([structured, listing, embedded])
        if items is structured:
            items = _fill_gaps(structured, listing)
        if not items and structured:
            items = structured
        if not items:
            items = [_attempt(document_item, errors, {}, html, root, metadata, page_url)]

    items = [item for item in items if item]
    if include_markdown and len(items) == 1:
        markdown = _attempt(page_markdown, errors, None, html, page_url)
        items[0] = _compact({**items[0], "markdown": markdown})
    return items


def extract_items(html: str, page_url: str, include_markdown: bool = False, snapshots: List[str] = (),
                  json_payloads: List[Any] = (), errors: Optional[List[str]] = None) -> List[Item]:
    """Extract every item from a page, using the most reliable methods available.

    snapshots are other states of the same page, such as later pagination steps.
    json_payloads are what the page fetched while rendering. Anything a method could
    not handle is added to errors.
    """
    errors = errors if errors is not None else []
    items = _page_items(html, page_url, include_markdown, errors)

    deadline = time.monotonic() + SNAPSHOT_SECONDS
    for snapshot in snapshots:
        if time.monotonic() >= deadline:
            errors.append("Ran out of time reading further page states")
            break
        more = [item for item in _page_items(snapshot, page_url, False, errors) if item.get("source") != "document"]
        if more:
            items = items + more if len(items) > 1 else more
    items = _unique(items)

    # API responses are rarely the whole story, so they only step in for a page that showed
    # nothing, or one with tabs where the page caught less of the data than the API sent.
    if json_payloads:
        network = _attempt(records_to_items, errors, [], json_payloads, page_url, "network")
        fuller_than_tabs = snapshots and len(network) > 1.5 * len(items)
        if len(network) >= MIN_LIST_ITEMS and (len(items) <= 1 or fuller_than_tabs):
            items = network
    return items
