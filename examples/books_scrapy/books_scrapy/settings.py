# Scrapy settings for the books_scrapy example
# https://docs.scrapy.org/en/latest/topics/settings.html

from pathlib import Path

# Everything written goes under the main project's outputs/ folder.
OUTPUTS = Path(__file__).resolve().parents[3] / "outputs"
BOOKS_DB = str(OUTPUTS / "results" / "books_scrapy" / "books.db")

BOT_NAME = "books_scrapy"

SPIDER_MODULES = ["books_scrapy.spiders"]
NEWSPIDER_MODULE = "books_scrapy.spiders"

ADDONS = {}

# Identify yourself
USER_AGENT = "MyScraperBot/1.0 (+https://example.com/bot; bot@example.com)"

# books.toscrape.com is scraper-friendly, obey their robots.txt
ROBOTSTXT_OBEY = True

# Polite crawling
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 0.5

# AutoThrottle for adaptive politeness
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 30
AUTOTHROTTLE_DEBUG = False

# HTTP cache for development
HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_DIR = str(OUTPUTS / "state" / "scrapy_httpcache")
HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Item pipelines
ITEM_PIPELINES = {
    "books_scrapy.pipelines.DataCleaningPipeline": 300,
    "books_scrapy.pipelines.ValidationPipeline": 400,
    "books_scrapy.pipelines.SQLitePipeline": 500,
}

FEED_EXPORT_ENCODING = "utf-8"

# Retry settings
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]
