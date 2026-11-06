"""Learn selectors from example values, for pages automatic extraction gets wrong.

Say "the title is X and the price is Y", using values you can see on the page, and this
finds where they live, works out the repeating block around them, and writes the selectors.
It needs no knowledge of the site's HTML, and the result is an ordinary, reusable selector.
"""
from typing import Dict, List, Optional, Tuple

from parsel import Selector

from my_scraper.parser.auto_extractor import _candidate_groups, _clean

MIN_ROWS = 2
MIN_FILLED_SHARE = 0.6
MAX_START_ELEMENTS = 12
MAX_GROUPS_PER_ELEMENT = 6
MAX_CHILDREN_FOR_FULL_TEXT = 8  # a value split by inline tags sits in a small element
VALUE_ATTRIBUTES = ("href", "src", "title", "alt", "content", "datetime", "value")
IGNORED_TAGS = ("script", "style", "head", "title")

Learned = Tuple[str, Dict[str, str]]
Found = Tuple[object, Optional[str]]  # (element, attribute holding the value)


def _own_text(element) -> str:
    pieces = [element.text or ""]
    pieces.extend(child.tail or "" for child in element)
    return _clean(" ".join(pieces))


def _find_all_values(root, examples: Dict[str, str]) -> Dict[str, List[Found]]:
    """Where each example appears on the page, exact matches first. One pass over the page."""
    wanted = {name: _clean(value) for name, value in examples.items()}
    exact: Dict[str, List[Found]] = {name: [] for name in examples}
    partial: Dict[str, List[Found]] = {name: [] for name in examples}

    for element in root.iter():
        if not isinstance(element.tag, str) or element.tag in IGNORED_TAGS:
            continue
        text = _own_text(element)
        full_text = _clean(element.text_content()) if len(element) <= MAX_CHILDREN_FOR_FULL_TEXT else text
        attributes = {name: _clean(element.get(name)) for name in VALUE_ATTRIBUTES if element.get(name)}
        for name, value in wanted.items():
            if text == value or full_text == value:
                exact[name].append((element, None))
            elif value in text:
                partial[name].append((element, None))
            for attribute, attribute_value in attributes.items():
                if attribute_value == value:
                    exact[name].append((element, attribute))

    return {name: exact[name] + partial[name] for name in examples}


def _usable_classes(element) -> List[str]:
    return [name for name in (element.get("class") or "").split() if name.replace("-", "").replace("_", "").isalnum()]


def _step(element, classes: List[str]) -> str:
    return element.tag + "".join(f".{name}" for name in classes)


def _row_selector(block, group: List[object]) -> str:
    """CSS for a repeating block, using only the classes all its blocks share."""
    shared = [name for name in _usable_classes(block)
              if all(name in _usable_classes(other) for other in group)]
    parent = block.getparent()
    if parent is None or parent.tag in ("html", "body"):
        return _step(block, shared)
    parent_shared = [name for name in _usable_classes(parent)
                     if all(name in _usable_classes(other.getparent()) for other in group)]
    return f"{_step(parent, parent_shared)} > {_step(block, shared)}"


def _field_paths(block, element) -> List[str]:
    """CSS paths from a block to an element inside it, from most to least specific."""
    chain = []
    node = element
    while node is not None and node is not block:
        chain.append(node)
        node = node.getparent()
    chain.reverse()

    named = " ".join(_step(node, _usable_classes(node)) for node in chain)
    last_only = _step(element, _usable_classes(element)) if element is not block else ""
    plain = " ".join(node.tag for node in chain)
    paths = [named, last_only, plain]
    return list(dict.fromkeys(paths))


def _with_target(path: str, attribute: Optional[str]) -> str:
    # ' ::text' reaches text inside child elements too, for values split by inline tags.
    target = f"::attr({attribute})" if attribute else " ::text"
    return f"{path}{target}" if path else f"*{target}"


def _value_matches(row, selector: str) -> str:
    return " ".join(_clean(value) for value in row.css(selector).getall()).strip()


def _best_field(rows, block, found: Found, example: str) -> Optional[Tuple[str, int]]:
    """The field selector that fills the most rows and still gives back the example."""
    element, attribute = found
    best = None
    for path in _field_paths(block, element):
        selector = _with_target(path, attribute)
        try:
            values = [_value_matches(row, selector) for row in rows]
        except Exception:  # an unusual class name can make invalid CSS
            continue
        filled = sum(1 for value in values if value)
        if any(example in value for value in values) and (best is None or filled > best[1]):
            best = (selector, filled)
    return best


def _try_group(sel: Selector, group: List[object], block, found: Dict[str, List[Found]],
               wanted: Dict[str, str]) -> Optional[Tuple[int, Learned]]:
    """Selectors for one candidate block, with the number of rows they fill completely."""
    item_selector = _row_selector(block, group)
    try:
        rows = sel.css(item_selector)
    except Exception:
        return None
    if len(rows) < MIN_ROWS:
        return None

    fields: Dict[str, str] = {}
    for name, candidates in found.items():
        match = next(((element, attribute) for element, attribute in candidates
                      if element is block or block in element.iterancestors()), None)
        if match is None:
            return None
        field = _best_field(rows, block, match, wanted[name])
        if field is None or field[1] < MIN_FILLED_SHARE * len(rows):
            return None
        fields[name] = field[0]

    complete = sum(1 for row in rows if all(_value_matches(row, selector) for selector in fields.values()))
    return complete, (item_selector, fields)


def learn_selectors(html: str, examples: Dict[str, str]) -> Optional[Learned]:
    """Learn (item selector, field selectors) from examples of one item's values.

    Every plausible block around the first example is tried, and the selectors that
    fill the most rows win. Returns None if no repeating block holds all the examples.
    """
    if not all(_clean(value) for value in examples.values()):
        return None

    sel = Selector(text=html, type="html")
    root = sel.root

    found = _find_all_values(root, examples)
    if not all(found.values()):
        return None

    wanted = {name: _clean(value) for name, value in examples.items()}
    groups = [(set(group), group) for group in sorted(_candidate_groups(root), key=len, reverse=True)]

    best: Optional[Tuple[int, Learned]] = None
    first_field = next(iter(found.values()))
    for element, _ in first_field[:MAX_START_ELEMENTS]:
        around = [element, *element.iterancestors()]
        matching = [(members, group) for members, group in groups if not members.isdisjoint(around)]
        for members, group in matching[:MAX_GROUPS_PER_ELEMENT]:
            block = next(node for node in around if node in members)
            attempt = _try_group(sel, group, block, found, wanted)
            if attempt and (best is None or attempt[0] > best[0]):
                best = attempt

    return best[1] if best else None
