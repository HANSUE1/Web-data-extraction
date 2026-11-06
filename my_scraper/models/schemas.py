"""Pydantic models for validating scraped items and describing crawl jobs.

Crawl configuration itself lives in my_scraper.config.settings.
"""
import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from my_scraper.config.settings import CrawlConfig
from my_scraper.utils.timeutil import utc_now


class CrawlStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class ScrapedItem(BaseModel):
    """Validated scraped item."""
    url: HttpUrl
    title: Optional[str] = None
    content: Optional[str] = None
    price: Optional[float] = None
    currency: str = "USD"
    availability: Optional[str] = None
    availability_qty: Optional[int] = None
    rating: Optional[str] = None
    rating_num: Optional[int] = None
    category: Optional[str] = None
    description: Optional[str] = None
    upc: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    images: List[HttpUrl] = []
    metadata: Dict[str, Any] = {}
    scraped_at: datetime = Field(default_factory=utc_now)
    scraped_by: str = "production_scraper"
    crawl_id: Optional[str] = None

    @field_validator('price', mode='before')
    @classmethod
    def clean_price(cls, v):
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(re.sub(r'[^\d.]', '', v))
            except ValueError:
                return None
        return None

    @field_validator('rating_num', mode='before')
    @classmethod
    def validate_rating(cls, v):
        try:
            rating = int(v)
        except (TypeError, ValueError):
            return None
        return rating if 0 <= rating <= 5 else None

    model_config = {"extra": "allow"}


class CrawlJob(BaseModel):
    """Crawl job specification."""
    id: str
    config: CrawlConfig
    status: CrawlStatus = CrawlStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_urls: int = 0
    processed_urls: int = 0
    failed_urls: int = 0
    extracted_items: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}


class CrawlResult(BaseModel):
    """Result of a crawl operation."""
    job_id: str
    items: List[ScrapedItem] = []
    stats: Dict[str, int] = {}
    errors: List[Dict[str, Any]] = []
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
