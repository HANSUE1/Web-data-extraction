"""Production-grade scraper configuration with all safety defaults."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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
    SQLITE = "sqlite"


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


@dataclass
class BrowserConfig:
    """Browser/fallback configuration."""
    enabled: bool = True
    headless: bool = True
    timeout: int = 30000
    wait_until: str = "domcontentloaded"  # the fetcher then waits for the content itself to settle
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_data_dir: Optional[str] = None
    javascript_enabled: bool = True
    expand_rounds: int = 8  # most scrolls and load-more presses per page. 0 turns this off


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
    console: bool = True


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


_SECTIONS = {
    "robots": RobotsConfig,
    "rate_limit": RateLimitConfig,
    "retry": RetryConfig,
    "concurrency": ConcurrencyConfig,
    "browser": BrowserConfig,
    "storage": StorageConfig,
    "logging": LoggingConfig,
    "compliance": ComplianceConfig,
}


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
    output_dir: str = "./output"

    # What to extract. With neither set, items are found automatically on every page.
    selectors: Dict[str, str] = field(default_factory=dict)
    item_selector: Optional[str] = None  # one item per match, selectors relative to it
    examples: Dict[str, str] = field(default_factory=dict)  # values to learn selectors from
    include_markdown: bool = False

    # Which pages to visit
    use_sitemap: bool = False
    discover: bool = False  # also use feeds and public platform APIs
    follow_pagination: bool = True  # next-page links don't use up max_depth
    include_patterns: List[str] = field(default_factory=list)  # regexes a link must match
    exclude_patterns: List[str] = field(default_factory=list)  # regexes a link must not match

    # Open web: follow links to any site. max_depth then counts hops between sites, and
    # links within a site are bounded by max_pages_per_site instead.
    open_web: bool = False
    max_pages_per_site: Optional[int] = None  # None: no limit per site
    max_frontier: int = 200_000  # pages waiting to be visited; links found beyond this are dropped

    incremental: bool = False  # on repeat runs, skip pages that have not changed
    track_changes: bool = False  # report what is new, changed or gone since the last run

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

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CrawlConfig":
        """Build a config from plain JSON data, converting nested sections and enums.

        Unknown keys raise TypeError so a typo in a config file is not silently ignored.
        """
        values = dict(raw)
        for name, section in _SECTIONS.items():
            if name in values:
                values[name] = section(**values[name])
        if "fetcher" in values:
            values["fetcher"] = FetcherType(values["fetcher"])
        if "export_formats" in values:
            values["export_formats"] = [ExportFormat(fmt) for fmt in values["export_formats"]]
        return cls(**values)
