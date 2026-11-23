# Scrapy settings for my_scraper project
# https://docs.scrapy.org/en/latest/topics/settings.html

BOT_NAME = "my_scraper"

SPIDER_MODULES = ["my_scraper.spiders"]
NEWSPIDER_MODULE = "my_scraper.spiders"

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
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Item pipelines
ITEM_PIPELINES = {
    "my_scraper.pipelines.DataCleaningPipeline": 300,
    "my_scraper.pipelines.ValidationPipeline": 400,
    "my_scraper.pipelines.SQLitePipeline": 500,
}

FEED_EXPORT_ENCODING = "utf-8"

# Retry settings
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]