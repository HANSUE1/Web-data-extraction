"""Unit tests for pure helper functions in books_spider.py"""
import re


def parse_price(price_str):
    """Convert '£54.29' to 54.29."""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def parse_rating(class_str):
    """Extract rating label from star-rating CSS class."""
    if not class_str:
        return None
    match = re.search(r"star-rating\s+(\w+)", class_str)
    return match.group(1) if match else None


def parse_rating_num(class_str):
    """Convert 'star-rating One' to 1, 'Five' to 5."""
    if not class_str:
        return None
    mapping = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
    match = re.search(r"star-rating\s+(\w+)", class_str)
    return mapping.get(match.group(1)) if match else None


def parse_availability(text):
    """Parse 'In stock (22 available)' -> ('In stock', 22)."""
    if not text:
        return None, None
    cleaned = text.strip()
    qty_match = re.search(r"(\d+)\s+available", cleaned)
    qty = int(qty_match.group(1)) if qty_match else None
    status = re.sub(r"\s*\(\d+\s+available\)", "", cleaned).strip()
    return status, qty


def parse_int(text):
    """Extract integer from string."""
    if not text:
        return None
    match = re.search(r"\d+", text.strip())
    return int(match.group(0)) if match else None


# --- Tests ---

def test_parse_price():
    assert parse_price("1234.56") == 1234.56
    assert parse_price("51.77") == 51.77
    assert parse_price("0.00") == 0.0
    assert parse_price(None) is None
    assert parse_price("") is None
    assert parse_price("abc") is None
    assert parse_price("$1,234.56") == 1234.56
    print("parse_price: PASS")


def test_parse_rating():
    assert parse_rating("star-rating Three") == "Three"
    assert parse_rating("star-rating Five") == "Five"
    assert parse_rating("star-rating One") == "One"
    assert parse_rating(None) is None
    assert parse_rating("") is None
    print("parse_rating: PASS")


def test_parse_rating_num():
    assert parse_rating_num("star-rating Five") == 5
    assert parse_rating_num("star-rating One") == 1
    assert parse_rating_num("star-rating Three") == 3
    assert parse_rating_num("star-rating Two") == 2
    assert parse_rating_num("star-rating Four") == 4
    assert parse_rating_num(None) is None
    print("parse_rating_num: PASS")


def test_parse_availability():
    assert parse_availability("In stock (22 available)") == ("In stock", 22)
    assert parse_availability("Out of stock") == ("Out of stock", None)
    assert parse_availability(None) == (None, None)
    assert parse_availability("") == (None, None)
    print("parse_availability: PASS")


def test_parse_int():
    assert parse_int("Number of reviews: 0") == 0
    assert parse_int("123 reviews") == 123
    assert parse_int("42") == 42
    assert parse_int(None) is None
    assert parse_int("abc") is None
    print("parse_int: PASS")


if __name__ == "__main__":
    test_parse_price()
    test_parse_rating()
    test_parse_rating_num()
    test_parse_availability()
    test_parse_int()
    print("\nAll 5 helper functions pass their tests!")