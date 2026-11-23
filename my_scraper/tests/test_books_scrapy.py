"""The helper functions of the Scrapy example in examples/books_scrapy."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("scrapy")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples" / "books_scrapy"))

from books_scrapy.spiders.books_spider import (  # noqa: E402
    parse_availability,
    parse_int,
    parse_price,
    parse_rating,
    parse_rating_num,
)


def test_parse_price():
    assert parse_price("1234.56") == 1234.56
    assert parse_price("51.77") == 51.77
    assert parse_price("0.00") == 0.0
    assert parse_price(None) is None
    assert parse_price("") is None
    assert parse_price("abc") is None
    assert parse_price("$1,234.56") == 1234.56


def test_parse_rating():
    assert parse_rating("star-rating Three") == "Three"
    assert parse_rating("star-rating Five") == "Five"
    assert parse_rating("star-rating One") == "One"
    assert parse_rating(None) is None
    assert parse_rating("") is None


def test_parse_rating_num():
    assert parse_rating_num("star-rating Five") == 5
    assert parse_rating_num("star-rating One") == 1
    assert parse_rating_num("star-rating Three") == 3
    assert parse_rating_num("star-rating Two") == 2
    assert parse_rating_num("star-rating Four") == 4
    assert parse_rating_num(None) is None


def test_parse_availability():
    assert parse_availability("In stock (22 available)") == ("In stock", 22)
    assert parse_availability("Out of stock") == ("Out of stock", None)
    assert parse_availability(None) == (None, None)
    assert parse_availability("") == (None, None)


def test_parse_int():
    assert parse_int("Number of reviews: 0") == 0
    assert parse_int("123 reviews") == 123
    assert parse_int("42") == 42
    assert parse_int(None) is None
    assert parse_int("abc") is None
