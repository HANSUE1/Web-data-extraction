"""Pydantic models for data validation and serialization."""
from pydantic import BaseModel, Field, HttpUrl, validator, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from typing import Union
from enum import Enum
import re


class ExportFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"
    PARQUET = "parquet"
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MONGODB = "mongodb"
    ELASTICSEARCH = "elasticsearch"


class FetcherType(str, Enum):
    STATIC = "static"
    BROWSER = "browser"
    AUTO = "auto"


class DeviceType(str, Enum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    TABLET = "tablet"


class CrawlStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Jurisdiction(str, Enum):
    US = "US"
    EU = "EU"
    CA = "CA"
    UK = "UK"
    AU = "AU"


class RobotsConfig(BaseModel):
    respect: bool = True
    cache_ttl: int = 3600
    user_agent: str = "ProductionScraperBot/1.0 (+https://example.com/bot; compliance@example.com)"


class RateLimitConfig(BaseModel):
    requests_per_second: float = 1.0
    burst_size: int = 5
    use_sliding_window: bool = True
    redis_url: Optional[str] = None
    per_domain: Dict[str, float] = {}


class RetryConfig(BaseModel):
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on_status: List[int] = [408, 429, 500, 502, 503, 504]
    retry_on_exceptions: bool = True


class ConcurrencyConfig(BaseModel):
    max_workers: int = 10
    max_per_domain: int = 2
    use_semaphore: bool = True


class BrowserConfig(BaseModel):
    enabled: bool = True
    headless: bool = True
    timeout: int = 30000
    wait_until: str = "networkidle"
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_data_dir: Optional[str] = None
    javascript_enabled: bool = True
    block_images: bool = False
    block_css: bool = False


class StorageConfig(BaseModel):
    save_raw_html: bool = True
    blob_storage_path: str = "./data/raw_html"
    db_url: Optional[str] = None
    use_s3: bool = False
    s3_bucket: Optional[str] = None
    s3_prefix: str = "raw_html/"


class LoggingConfig(BaseModel):
    level: LogLevel = LogLevel.INFO
    json_format: bool = True
    log_file: str = "./logs/scraper.log"
    audit_log: str = "./logs/audit.log"
    metrics_port: int = 9090
    include_request_details: bool = True


class ComplianceConfig(BaseModel):
    public_only: bool = True
    data_minimization: bool = True
    respect_paywalls: bool = True
    respect_logins: bool = True
    require_legal_review: bool = False
    jurisdiction: Jurisdiction = Jurisdiction.US
    audit_all_requests: bool = True
    takedown_email: Optional[str] = None
    allowed_content_types: List[str] = ["text/html", "application/json", "application/pdf"]


class ProxyConfig(BaseModel):
    enabled: bool = False
    proxy_file: Optional[str] = None
    proxy_list: List[str] = []
    rotate_per_request: bool = True
    health_check_interval: int = 300
    failure_threshold: int = 5


class CrawlConfig(BaseModel):
    name: str = "production_scraper"
    start_urls: List[HttpUrl] = []
    allowed_domains: List[str] = []
    max_depth: int = 3
    max_pages: int = 10000
    fetcher: FetcherType = FetcherType.AUTO
    export_formats: List[ExportFormat] = [ExportFormat.JSONL]
    
    robots: RobotsConfig = RobotsConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    retry: RetryConfig = RetryConfig()
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    browser: BrowserConfig = BrowserConfig()
    storage: StorageConfig = StorageConfig()
    logging: LoggingConfig = LoggingConfig()
    compliance: ComplianceConfig = ComplianceConfig()
    proxy: ProxyConfig = ProxyConfig()
    
    required_fields: List[str] = []
    personal_data_fields: List[str] = []
    selectors: Dict[str, str] = {}
    pagination_selector: Optional[str] = None
    follow_links: bool = True
    respect_canonical: bool = True
    incremental: bool = False
    etag_cache: bool = True
    
    @field_validator('start_urls', mode='before')
    @classmethod
    def validate_start_urls(cls, v):
        if isinstance(v, str):
            return [HttpUrl(v)]
        return [HttpUrl(u) for u in v] if v else []


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
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    scraped_by: str = "production_scraper"
    crawl_id: Optional[str] = None
    
    @field_validator('price', mode='before')
    @classmethod
    def clean_price(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            cleaned = re.sub(r'[^\d.]', '', v)
            try:
                return float(cleaned)
            except:
                return None
        return None
    
    @field_validator('rating_num', mode='before')
    @classmethod
    def validate_rating(cls, v):
        if v is not None and (v < 0 or v > 5):
            return None
        return v
    
    model_config = {"extra": "allow"}


class CrawlJob(BaseModel):
    """Crawl job specification."""
    id: str
    config: CrawlConfig
    status: CrawlStatus = CrawlStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
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