# Define your item pipelines here
# https://docs.scrapy.org/en/latest/topics/item-pipeline.html

import sqlite3
from pathlib import Path

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy import signals


class DataCleaningPipeline:
    """Cleans and normalizes raw scraped data."""

    def __init__(self):
        self._spider = None

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        crawler.signals.connect(pipeline.spider_opened, signal=signals.spider_opened)
        return pipeline

    def spider_opened(self, spider):
        self._spider = spider

    def process_item(self, item):
        adapter = ItemAdapter(item)
        spider = self._spider

        for field in ["price", "price_incl_tax", "price_excl_tax", "tax"]:
            if adapter.get(field):
                raw = str(adapter[field]).replace("$", "").replace(",", "").replace("£", "").strip()
                try:
                    adapter[field] = float(raw)
                except (ValueError, TypeError):
                    spider.logger.warning(f"Could not parse {field}: {adapter[field]}")
                    adapter[field] = None

        if adapter.get("title"):
            adapter["title"] = adapter["title"].strip()

        if adapter.get("description"):
            adapter["description"] = " ".join(adapter["description"].split())

        if adapter.get("url"):
            adapter["url"] = adapter["url"].strip()

        return item


class ValidationPipeline:
    """Validates items before they are saved."""

    def __init__(self):
        self._spider = None

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        crawler.signals.connect(pipeline.spider_opened, signal=signals.spider_opened)
        return pipeline

    def spider_opened(self, spider):
        self._spider = spider

    def process_item(self, item):
        adapter = ItemAdapter(item)
        spider = self._spider

        for field in ["title", "url"]:
            if not adapter.get(field):
                spider.logger.warning(
                    f"Missing required field '{field}' in item from {adapter.get('url', 'unknown')}"
                )
                raise DropItem(f"Missing required field: {field}")

        for field in ["price", "price_incl_tax", "price_excl_tax"]:
            if adapter.get(field) is not None:
                if not (0 <= adapter[field] <= 1_000_000):
                    spider.logger.warning(f"{field} out of reasonable range: {adapter[field]}")
                    adapter[field] = None

        return item


class SQLitePipeline:
    """Saves items to a SQLite database."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.connection = None
        self._spider = None

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls(crawler.settings["BOOKS_DB"])
        crawler.signals.connect(pipeline.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)
        return pipeline

    def spider_opened(self, spider):
        self._spider = spider
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self._create_table()

    def spider_closed(self, spider):
        if self.connection:
            self.connection.close()
            spider.logger.info("SQLite database connection closed")

    def _create_table(self):
        cursor = self.connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price REAL,
                price_incl_tax REAL,
                price_excl_tax REAL,
                tax REAL,
                availability TEXT,
                availability_qty INTEGER,
                rating TEXT,
                rating_num INTEGER,
                category TEXT,
                description TEXT,
                upc TEXT,
                product_type TEXT,
                num_reviews INTEGER,
                url TEXT UNIQUE,
                image_url TEXT
            )
        """)
        self.connection.commit()

    def process_item(self, item):
        adapter = ItemAdapter(item)
        spider = self._spider

        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO books (
                    title, price, price_incl_tax, price_excl_tax, tax,
                    availability, availability_qty, rating, rating_num,
                    category, description, upc, product_type, num_reviews,
                    url, image_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                adapter.get("title"),
                adapter.get("price"),
                adapter.get("price_incl_tax"),
                adapter.get("price_excl_tax"),
                adapter.get("tax"),
                adapter.get("availability"),
                adapter.get("availability_qty"),
                adapter.get("rating"),
                adapter.get("rating_num"),
                adapter.get("category"),
                adapter.get("description"),
                adapter.get("upc"),
                adapter.get("product_type"),
                adapter.get("num_reviews"),
                adapter.get("url"),
                adapter.get("image_url"),
            ))
            self.connection.commit()
        except sqlite3.IntegrityError:
            spider.logger.warning(f"Duplicate URL skipped: {adapter.get('url')}")

        return item
