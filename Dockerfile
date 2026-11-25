FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /app

COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt \
    && playwright install --with-deps chromium \
    && useradd --uid 10001 --create-home scraper \
    && mkdir -p /app/outputs && chown scraper /app/outputs

COPY my_scraper my_scraper
COPY config config

USER scraper
VOLUME /app/outputs
EXPOSE 9100

# Every long-running command serves /health on 9100 without a key. Set SCRAPER_API_KEY for the rest.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9100/health', timeout=4)"]

ENTRYPOINT ["python", "-m"]
CMD ["my_scraper.ops", "worker", "--host", "0.0.0.0", "--port", "9100"]
