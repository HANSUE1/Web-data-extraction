# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html

from scrapy import signals


class MyScraperSpiderMiddleware:
    """Spider middleware - custom logic for responses going into the spider."""

    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        return None

    def process_spider_output(self, response, result, spider):
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        pass

    async def process_start(self, start):
        async for item_or_request in start:
            yield item_or_request

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


class MyScraperDownloaderMiddleware:
    """Downloader middleware - custom logic for requests and responses."""

    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        # Add a Referer header if not present
        if "Referer" not in request.headers:
            request.headers["Referer"] = spider.start_urls[0] if spider.start_urls else ""

        return None

    def process_response(self, request, response, spider):
        # Log non-200 responses
        if response.status != 200:
            spider.logger.warning(f"Non-200 response: {response.status} from {request.url}")
        return response

    def process_exception(self, request, exception, spider):
        spider.logger.error(f"Request failed: {request.url} - {exception}")
        return None

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)