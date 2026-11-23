# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

from dataclasses import dataclass


@dataclass
class BookItem:
    """Schema for scraped book data from books.toscrape.com."""
    title: str | None = None
    price: float | None = None
    price_incl_tax: float | None = None
    price_excl_tax: float | None = None
    tax: float | None = None
    availability: str | None = None
    availability_qty: int | None = None
    rating: str | None = None
    rating_num: int | None = None
    category: str | None = None
    description: str | None = None
    upc: str | None = None
    product_type: str | None = None
    num_reviews: int | None = None
    url: str | None = None
    image_url: str | None = None