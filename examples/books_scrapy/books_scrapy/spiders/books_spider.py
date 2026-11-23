"""
Books spider for books.toscrape.com.

Two-phase scraping:
1. parse() extracts book listings and follows links to detail pages
2. parse_book() extracts full details from each book page

Demonstrates clean scraping patterns:
- Single Responsibility: each method does one thing
- Guard clauses for robustness
- Pure helper functions for parsing
"""

import re
from urllib.parse import urljoin

import scrapy
from books_scrapy.items import BookItem


def parse_price(price_str: str | None) -> float | None:
    """Pure function: convert '£54.29' to 54.29."""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def parse_rating(class_str: str | None) -> str | None:
    """Extract rating label from star-rating CSS class."""
    if not class_str:
        return None
    match = re.search(r"star-rating\s+(\w+)", class_str)
    return match.group(1) if match else None


def parse_rating_num(class_str: str | None) -> int | None:
    """Convert 'star-rating One' to 1, 'Five' to 5."""
    if not class_str:
        return None
    mapping = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
    match = re.search(r"star-rating\s+(\w+)", class_str)
    return mapping.get(match.group(1)) if match else None


def parse_availability(text: str | None) -> tuple[str | None, int | None]:
    """Parse 'In stock (22 available)' -> ('In stock', 22)."""
    if not text:
        return None, None
    cleaned = text.strip()
    qty_match = re.search(r"(\d+)\s+available", cleaned)
    qty = int(qty_match.group(1)) if qty_match else None
    status = re.sub(r"\s*\(\d+\s+available\)", "", cleaned).strip()
    return status, qty


def parse_int(text: str | None) -> int | None:
    """Pure function: extract integer from string."""
    if not text:
        return None
    match = re.search(r"\d+", text.strip())
    return int(match.group(0)) if match else None


class BooksSpider(scrapy.Spider):
    name = "books"
    allowed_domains = ["books.toscrape.com"]
    start_urls = ["http://books.toscrape.com/catalogue/page-1.html"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
    }

    def parse(self, response):
        """Parse book listing page and follow links to detail pages."""
        books = response.css("article.product_pod")
        if not books:
            self.logger.warning(f"No books found on {response.url}")
            return

        for book in books:
            url = book.css("h3 a::attr(href)").get()
            title = book.css("h3 a::attr(title)").get()

            if not url or not title:
                self.logger.warning(f"Skipping book with missing data on {response.url}")
                continue

            # Follow to detail page - parse_book extracts full data
            detail_url = urljoin(response.url, url)
            raw_rating_class = book.css(".star-rating::attr(class)").get()
            yield response.follow(detail_url, callback=self.parse_book,
                                  meta={"title": title.strip(),
                                        "listing_price": parse_price(book.css(".price_color::text").get()),
                                        "listing_rating_class": raw_rating_class,
                                        "listing_image": book.css("img::attr(src)").get()})

        # Follow pagination
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            next_url = urljoin(response.url, next_page)
            self.logger.info(f"Following pagination to {next_url}")
            yield response.follow(next_url, callback=self.parse)

    def parse_book(self, response):
        """Parse individual book detail page for full product data."""
        # Category from breadcrumb (last link before current page title)
        crumbs = response.css("ul.breadcrumb li a::text").getall()
        category = crumbs[-1].strip() if crumbs else None

        # Extract from product information table
        rows = response.css("table.table tr")
        table_data = {}
        for row in rows:
            key = row.css("th::text").get()
            val = row.css("td::text").get()
            if key:
                table_data[key.strip()] = val.strip() if val else None

        # Extract description (first paragraph after "Product Description" header)
        description = response.css("#product_description + p::text").get()
        if description:
            description = description.strip()
            # Remove trailing " ...more" if present
            description = re.sub(r"\s*\.\.\.more$", "", description)

        # Extract availability with quantity (take first non-empty text node)
        avail_nodes = response.css(".availability::text").getall()
        avail_text = next((t.strip() for t in avail_nodes if t.strip()), None)
        availability, avail_qty = parse_availability(avail_text)

        # Build item combining listing data + detail data
        item = BookItem(
            title=response.meta["title"],
            url=response.url,
            image_url=urljoin(response.url, response.css(".thumbnail img::attr(src)").get()),
            price=parse_price(response.css(".price_color::text").get()),
            price_incl_tax=parse_price(table_data.get("Price (incl. tax)")),
            price_excl_tax=parse_price(table_data.get("Price (excl. tax)")),
            tax=parse_price(table_data.get("Tax")),
            availability=availability,
            availability_qty=avail_qty,
            rating=parse_rating(response.meta.get("listing_rating_class")),
            rating_num=parse_rating_num(response.meta.get("listing_rating_class")),
            category=category,
            description=description.strip() if description else None,
            upc=table_data.get("UPC"),
            product_type=table_data.get("Product Type"),
            num_reviews=parse_int(table_data.get("Number of reviews")),
        )

        yield item
