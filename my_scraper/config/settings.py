"""Production-grade scraper configuration with all safety defaults.

Everything the scraper writes goes under one folder, outputs/:

    outputs/results/   exported items (JSONL, CSV, JSON, SQLite) and change reports
    outputs/logs/      the run log and the audit trail
    outputs/state/     crawl checkpoints, raw HTML, caches, snapshots, the queue, the takedown blocklist
"""
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from my_scraper.config.secret_refs import resolve

OUTPUTS_DIR = "./outputs"
RESULTS_DIR = f"{OUTPUTS_DIR}/results"
LOGS_DIR = f"{OUTPUTS_DIR}/logs"
STATE_DIR = f"{OUTPUTS_DIR}/state"

PLACEHOLDER_USER_AGENT = "ProductionScraperBot/1.0 (+https://example.com/bot; compliance@example.com)"


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
    user_agent: str = field(default_factory=lambda: os.environ.get("SCRAPER_USER_AGENT") or PLACEHOLDER_USER_AGENT)
    # Sites whose robots.txt is not obeyed, by host name ("example.com" also covers its subdomains).
    # Only for sites you own or have permission to scrape. Pacing and refusals still apply.
    ignore_for: List[str] = field(default_factory=list)
    respect_meta: bool = True  # pages marked noindex (robots meta tag or X-Robots-Tag) are read but not stored


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_second: float = 1.0


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
    # Circuit breaker: a site is left alone after this many refusals in a row, or when this share of its
    # last breaker_window pages failed.
    breaker_failures: int = 5
    breaker_error_rate: float = 0.6
    breaker_window: int = 20


@dataclass
class BrowserConfig:
    """Browser/fallback configuration."""
    enabled: bool = True
    headless: bool = True
    timeout: int = 30000
    wait_until: str = "domcontentloaded"  # the fetcher then waits for the content itself to settle
    viewport_width: int = 1920
    viewport_height: int = 1080
    expand_rounds: int = 8  # most scrolls and load-more presses per page. 0 turns this off
    # One steady profile for the whole crawl. The user agent is robots.user_agent.
    locale: str = "en-US"
    timezone: Optional[str] = None  # such as "Europe/Berlin"; None: this computer's
    screenshots: bool = False  # keep a screenshot with each browser snapshot
    # Steps to take on every page, such as [{"click": "role=button[name='Load more']", "until": ".end"}].
    # See fetcher/actions.py. Needs the browser.
    actions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ProxyConfig:
    """HTTP proxies to go out through, for networks that need them. See fetcher/proxies.py."""
    urls: List[str] = field(default_factory=list)  # "http://user:pass@host:port", or "env:NAME"
    max_failures: int = 3       # connection failures in a row before a proxy rests
    rest_seconds: float = 300
    check_url: Optional[str] = None  # requested through every proxy when a crawl starts


@dataclass
class StorageConfig:
    """Storage configuration."""
    save_raw_html: bool = True
    state_dir: str = STATE_DIR  # checkpoints, caches, snapshots and the takedown blocklist
    blob_storage_path: Optional[str] = None  # raw HTML; default: state_dir/raw_html
    snapshots: bool = True  # keep every fetch, unchangeable, so pages can be parsed again later
    snapshot_location: Optional[str] = None  # a folder or s3://bucket/prefix; default: state_dir/blobs
    retention_days: float = 0  # `ops prune` deletes snapshots older than this. 0 keeps them
    # Also deliver records to a database: sqlite:///path or postgresql://..., usually "env:SCRAPER_DATABASE_URL"
    database_url: Optional[str] = None
    table: str = "scraped_records"
    delivery: str = "upsert"  # snapshot (replace all), append (keep every run), upsert (one row per record)
    key_fields: List[str] = field(default_factory=lambda: ["url", "title"])  # what makes a record the same record
    sinks: List[str] = field(default_factory=list)  # your own exporters, "package.module:function"


@dataclass
class LoggingConfig:
    """Structured logging configuration."""
    level: str = "INFO"
    json_format: bool = True
    log_file: str = f"{LOGS_DIR}/scraper.log"
    audit_log: str = f"{LOGS_DIR}/audit.log"
    console: bool = True


@dataclass
class ComplianceConfig:
    """Legal and ethical compliance configuration."""
    public_only: bool = True
    data_minimization: bool = True
    jurisdiction: str = "US"  # US, EU (GDPR), CA (CCPA/CPRA)
    audit_all_requests: bool = True
    blocklist_file: Optional[str] = None  # domains never to crawl; default: state_dir/takedown_blocklist.txt
    redact_pii: bool = False  # replace emails, phone numbers and SSNs with [redacted] before records are stored
    # Country codes a site's domain may or may not end in: ["de", "fr"]. "eu" stands for every EU member.
    allowed_countries: List[str] = field(default_factory=list)
    denied_countries: List[str] = field(default_factory=list)
    site_register: Optional[str] = None  # reviewed sites and their terms, see compliance/risk.py
    require_go: bool = False  # refuse to start unless the register gives every start site a "go"


@dataclass
class QualityConfig:
    """What a record must be before it is published, and what a dataset must be."""
    schema: Optional[Dict[str, Any]] = None  # JSON Schema; records that break it go to rejected.jsonl
    schema_version: str = "1"
    normalize: bool = False  # parsed prices and dates in fields beside the originals, and real nulls
    currency: Optional[str] = None  # also give prices in this currency, using rates
    rates: Dict[str, float] = field(default_factory=dict)  # {"EUR": 1.08}: units of currency per unit
    dedupe: str = "off"  # off, run (within a run), cross (also against earlier runs)
    provenance: bool = False  # add _meta to every record: snapshot, parser, selector and byte range per field
    # Below these, the dataset goes to <output_dir>/quarantine and not to the database.
    min_items: int = 0
    min_valid_ratio: float = 0.0
    max_null_ratio: float = 1.0


@dataclass
class AlertConfig:
    """When to tell the owner. The webhook takes JSON with a "text" field."""
    enabled: bool = True
    webhook: Optional[str] = None  # usually "env:SCRAPER_ALERT_WEBHOOK"
    change_webhook: Optional[str] = None  # where track_changes sends what changed
    min_success_ratio: float = 0.9
    window_minutes: int = 15
    max_queue_depth: int = 1000


@dataclass
class LLMConfig:
    """A language model as a last resort, when nothing deterministic found a record. Sends page text to Anthropic."""
    enabled: bool = False
    model: str = "claude-opus-5-5"  # pinned: only ever changed here
    effort: str = "medium"
    max_calls: int = 50       # per run
    max_chars: int = 400_000  # longer pages are skipped, never cut


_SECTIONS = {
    "robots": RobotsConfig,
    "rate_limit": RateLimitConfig,
    "retry": RetryConfig,
    "concurrency": ConcurrencyConfig,
    "browser": BrowserConfig,
    "proxy": ProxyConfig,
    "quality": QualityConfig,
    "alerts": AlertConfig,
    "llm": LLMConfig,
    "storage": StorageConfig,
    "logging": LoggingConfig,
    "compliance": ComplianceConfig,
}

# Settings that never did anything. Old config files may still set them, so they are dropped
# quietly instead of failing as unknown keys. Logins and paywalls are never bypassed regardless.
_RETIRED = {
    None: {"personal_data_fields"},
    "rate_limit": {"redis_url"},
    "browser": {"user_data_dir", "javascript_enabled"},
    "storage": {"db_url", "use_s3", "s3_bucket"},
    "logging": {"include_request_details"},
    "compliance": {"respect_paywalls", "respect_logins", "require_legal_review", "takedown_email"},
}


def _without_retired(values: Dict[str, Any], section: Optional[str]) -> Dict[str, Any]:
    retired = _RETIRED.get(section, set())
    return {key: value for key, value in values.items() if key not in retired}


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
    output_dir: str = RESULTS_DIR

    # What to extract. With neither set, items are found automatically on every page.
    selectors: Dict[str, str] = field(default_factory=dict)
    item_selector: Optional[str] = None  # one item per match, selectors relative to it
    examples: Dict[str, str] = field(default_factory=dict)  # values to learn selectors from
    fields: List[str] = field(default_factory=list)  # export only these item fields; empty keeps all
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
    job_id: str = ""  # set by a queue worker: names the checkpoint and appears in every log line

    # Log in before crawling, for sites you may access. {"url", "username", "password"}. Off by default.
    # Needs the browser. The session cookies are reused for the crawl and never written to logs or exports.
    login: Optional[Dict[str, str]] = None

    # Sub-configs
    robots: RobotsConfig = field(default_factory=RobotsConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    # Required schema
    required_fields: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CrawlConfig":
        """Build a config from plain JSON data, converting nested sections and enums.

        Unknown keys raise TypeError so a typo in a config file is not silently ignored.
        """
        values = _without_retired(dict(raw), None)
        for name, section in _SECTIONS.items():
            if name in values:
                values[name] = section(**_without_retired(values[name], name))
        if "fetcher" in values:
            values["fetcher"] = FetcherType(values["fetcher"])
        if "export_formats" in values:
            values["export_formats"] = [ExportFormat(fmt) for fmt in values["export_formats"]]
        config = cls(**values)
        config.check()
        config.resolve_secrets()
        return config

    def check(self):
        """Refuse settings that name no known choice, before a crawl runs half way on them."""
        for name, value, choices in (("storage.delivery", self.storage.delivery, ("snapshot", "append", "upsert")),
                                     ("quality.dedupe", self.quality.dedupe, ("off", "run", "cross"))):
            if value not in choices:
                raise ValueError(f"{name} must be one of {', '.join(choices)}, got {value!r}")
        if self.llm.enabled and not self.quality.schema:
            raise ValueError("llm.enabled needs quality.schema, which is what the model's answers must fit")

    def resolve_secrets(self):
        """Replace env: and file: references with the secrets they name."""
        self.storage.database_url = resolve(self.storage.database_url)
        self.alerts.webhook = resolve(self.alerts.webhook)
        self.alerts.change_webhook = resolve(self.alerts.change_webhook)
        self.proxy.urls = [url.strip() for entry in self.proxy.urls for url in resolve(entry).split(",") if url.strip()]
        if self.login:
            self.login = {name: resolve(value) for name, value in self.login.items()}
