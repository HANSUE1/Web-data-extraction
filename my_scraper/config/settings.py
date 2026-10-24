"""Production-grade scraper configuration with all safety defaults."""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import os


class FetcherType(Enum):
    """Fetcher type selection."""
    STATIC = "static"
    BROWSER = "browser"
    AUTO = "auto"


class ExportFormat(Enum):
    """Supported export formats."""
    JSON = "json"
    CSV = "csv"
    JSONL = "jsonl"
    PARQUET = "parquet"


@dataclass
class RobotsConfig:
    """Robots.txt configuration."""
    respect: bool = True
    cache_ttl: int = 3600  # seconds
    user_agent: str = "ProductionScraperBot/1.0 (+https://example.com/bot; compliance@example.com)"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_second: float = 1.0
    burst_size: int = 5
    use_sliding_window: bool = True
    redis_url: Optional[str] = None


@dataclass
class RetryConfig:
    """Retry configuration with exponential backoff."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on_status: List[int] = field(default_factory=lambda: [408, 429, 500, 502, 503, 504])
    retry_on_exceptions: bool = True


@dataclass
class ConcurrencyConfig:
    """Concurrency configuration."""
    max_workers: int = 10
    max_per_domain: int = 2
    use_semaphore: bool = True


@dataclass
class BrowserConfig:
    """Browser/fallback configuration."""
    enabled: bool = True
    headless: bool = True
    timeout: int = 30000
    wait_until: str = "networkidle"
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_data_dir: Optional[str] = None


@dataclass
class StorageConfig:
    """Storage configuration."""
    save_raw_html: bool = True
    blob_storage_path: str = "./data/raw_html"
    db_url: Optional[str] = None  # postgresql://, sqlite:/// etc
    use_s3: bool = False
    s3_bucket: Optional[str] = None


@dataclass
class LoggingConfig:
    """Structured logging configuration."""
    level: str = "INFO"
    json_format: bool = True
    log_file: str = "./logs/scraper.log"
    audit_log: str = "./logs/audit.log"
    include_request_details: bool = True


@dataclass
class ComplianceConfig:
    """Legal and ethical compliance configuration."""
    public_only: bool = True
    data_minimization: bool = True
    respect_paywalls: bool = True  # Never bypass
    respect_logins: bool = True    # Never bypass
    require_legal_review: bool = False
    jurisdiction: str = "US"  # US, EU (GDPR), CA (CCPA/CPRA)
    audit_all_requests: bool = True
    takedown_email: Optional[str] = None


@dataclass
class CrawlConfig:
    """Main crawler configuration."""
    name: str = "production_scraper"
    start_urls: List[str] = field(default_factory=list)
    allowed_domains: List[str] = field(default_factory=list)
    max_depth: int = 3
    max_pages: int = 10000
    fetcher: FetcherType = FetcherType.AUTO
    export_formats: List[ExportFormat] = field(default_factory=lambda: [ExportFormat.JSONL])
    
    # Sub-configs
    robots: RobotsConfig = field(default_factory=RobotsConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    
    # Required schema
    required_fields: List[str] = field(default_factory=list)
    personal_data_fields: List[str] = field(default_factory=list)
    
    class Config:
        env_prefix = "SCRAPER_"


# Default configuration
DEFAULT_CONFIG = CrawlConfig()