"""Compliance and governance module."""
import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse

from my_scraper.utils.timeutil import utc_now_iso

# Whole path segments, so /author/x is public while /auth/x and /login.php are not.
LOGIN_SEGMENTS = {
    "login", "logon", "signin", "sign-in", "auth", "oauth", "authenticate", "authorize",
    "account", "accounts", "my-account", "myaccount", "dashboard",
}
PAYWALL_SEGMENTS = {"paywall", "subscribe", "premium"}

# Outcomes where a page really was read. Refusals, errors and robots.txt blocks are not.
READ_STATUSES = {"success", "no_data_extracted", "unchanged"}

PII_FIELD_KEYWORDS = ("email", "phone", "ssn", "social_security", "address", "zip", "postal", "dob", "birth")
PII_VALUE_PATTERNS = (
    re.compile(r"[\w.+-]@[\w-]+\.[\w.-]"),  # email; no + before the @, or long unbroken text takes minutes
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                           # US SSN
    re.compile(r"\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}"),  # international phone
    re.compile(r"(?<!\d)\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),  # US phone
)
# The same kinds of value, matched whole, for taking them out.
REDACT_PATTERNS = (re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+"), *PII_VALUE_PATTERNS[1:])
REDACTED = "[redacted]"
LINK_FIELDS = {"url", "source_url", "image", "source"}  # numbers in an address are not phone numbers

EU_COUNTRIES = {"at", "be", "bg", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gr", "hr", "hu", "ie", "it", "lt",
                "lu", "lv", "mt", "nl", "pl", "pt", "ro", "se", "si", "sk", "eu"}


def country_of(host: str) -> Optional[str]:
    """The country a domain ends in. A generic ending such as .com says nothing about where a site is."""
    ending = host.rsplit(".", 1)[-1].lower()
    if len(ending) != 2 or not ending.isalpha():
        return None
    return "gb" if ending == "uk" else ending


def _countries(codes: List[str]) -> Set[str]:
    chosen = {"gb" if code.lower() == "uk" else code.lower() for code in codes}
    return chosen | EU_COUNTRIES if "eu" in chosen else chosen


class ComplianceManager:
    """Legal and ethical compliance enforcement."""

    def __init__(self, config):
        self.config = config
        self.audit_log_file = config.logging.audit_log
        Path(self.audit_log_file).parent.mkdir(parents=True, exist_ok=True)
        # Kept with the crawl state, not the logs, so clearing old logs never lifts a takedown.
        self.blocklist_file = Path(config.compliance.blocklist_file
                                   or Path(config.storage.state_dir) / "takedown_blocklist.txt")
        self.blocklist_file.parent.mkdir(parents=True, exist_ok=True)
        self.blocked_domains = self._load_blocklist()
        self.allowed_countries = _countries(config.compliance.allowed_countries)
        self.denied_countries = _countries(config.compliance.denied_countries)
        # Called with (url, status, error) for every outcome, whether or not auditing is on.
        self.on_event: Optional[Callable[[str, str, Optional[str]], None]] = None

        # Pages really read, and personal data seen, counted whether or not auditing is on.
        self.scraped_urls: Set[str] = set()
        self.personal_data_found: Set[str] = set()
        self.status_counts: Counter = Counter()
        self.first_errors: Dict[str, str] = {}

    def _load_blocklist(self) -> Set[str]:
        if not self.blocklist_file.exists():
            return set()
        lines = self.blocklist_file.read_text(encoding="utf-8-sig").splitlines()  # Notepad adds a BOM
        return {line.strip().lower() for line in lines if line.strip()}

    def _is_blocked_domain(self, host: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in self.blocked_domains)

    def validate_url(self, url: str) -> bool:
        """Validate URL is allowed. Raises ValueError if it is not."""
        parsed = urlparse(url)

        # Takedowns and country rules apply whatever the public_only setting says.
        host = (parsed.hostname or "").lower()
        if self._is_blocked_domain(host):
            raise ValueError(f"Domain is on the takedown blocklist: {url}")
        country = country_of(host)
        if country in self.denied_countries:
            raise ValueError(f"Country .{country} is denied by compliance.denied_countries: {url}")
        if self.allowed_countries and country not in self.allowed_countries:
            raise ValueError(f"Not in compliance.allowed_countries: {url}")

        if not self.config.compliance.public_only:
            return True

        segments = {segment.split(".")[0] for segment in parsed.path.lower().split("/")}
        if segments & LOGIN_SEGMENTS:
            raise ValueError(f"Attempting to scrape login/auth URL: {url}. Public data only.")
        if segments & PAYWALL_SEGMENTS:
            raise ValueError(f"Attempting to scrape paywall URL: {url}. Public data only.")

        return True

    def check_data_minimization(self, data: Dict[str, Any], schema: List[str]) -> Dict[str, Any]:
        """Enforce data minimization - only collect required fields."""
        if not self.config.compliance.data_minimization:
            return data
        return {name: data[name] for name in schema if name in data}

    def scan_for_personal_data(self, data: Dict[str, Any]) -> List[str]:
        """Return the fields that look like personal data (PII), judged field by field."""
        found = []
        for name, value in data.items():
            if any(keyword in name.lower() for keyword in PII_FIELD_KEYWORDS):
                found.append(name)
                continue
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            if any(pattern.search(text) for pattern in PII_VALUE_PATTERNS):
                found.append(name)
        return found

    @staticmethod
    def redact_text(text: str) -> str:
        for pattern in REDACT_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def redact(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """A copy with personal data taken out: whole fields named for it, and values that look like it."""
        def clean(value: Any) -> Any:
            if isinstance(value, str):
                return self.redact_text(value)
            if isinstance(value, dict):
                return self.redact(value)
            return [clean(entry) for entry in value] if isinstance(value, list) else value

        redacted = {}
        for name, value in data.items():
            if name in LINK_FIELDS:
                redacted[name] = REDACT_PATTERNS[0].sub(REDACTED, value) if isinstance(value, str) else value
            elif value and any(keyword in name.lower() for keyword in PII_FIELD_KEYWORDS):
                redacted[name] = REDACTED
            else:
                redacted[name] = clean(value)
        return redacted

    def _append_audit(self, event: Dict[str, Any]):
        with open(self.audit_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def log_scrape_event(self, url: str, status: str, items_count: int = 0,
                         personal_data: List[str] = None, error: str = None):
        """Log scrape event to audit trail."""
        self.status_counts[status] += 1
        if error:
            self.first_errors.setdefault(status, error)
        if status in READ_STATUSES:
            self.scraped_urls.add(url)
        if personal_data:
            self.personal_data_found.update(personal_data)
        if self.on_event:
            self.on_event(url, status, error)

        if not self.config.compliance.audit_all_requests:
            return

        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "timestamp": utc_now_iso(),
            "url": url,
            "domain": urlparse(url).netloc,
            "status": status,
            "items_extracted": items_count,
            "personal_data_fields": personal_data or [],
            "user_agent": self.config.robots.user_agent,
            "jurisdiction": self.config.compliance.jurisdiction,
        }
        if error:
            event["error"] = error
        self._append_audit(event)

    def generate_compliance_report(self) -> Dict[str, Any]:
        """Generate compliance report."""
        return {
            "report_generated": utc_now_iso(),
            "total_urls_scraped": len(self.scraped_urls),
            "unique_domains": len({urlparse(url).netloc for url in self.scraped_urls}),
            "personal_data_fields_found": sorted(self.personal_data_found),
            "jurisdiction": self.config.compliance.jurisdiction,
            "public_only_enforced": self.config.compliance.public_only,
            "data_minimization_enforced": self.config.compliance.data_minimization,
            "audit_trail_location": self.audit_log_file,
        }

    def handle_takedown_request(self, domain: str, reason: str):
        """Block a domain from all future crawls and record why."""
        domain = domain.strip().lower()
        if domain not in self.blocked_domains:
            self.blocked_domains.add(domain)
            with open(self.blocklist_file, "a", encoding="utf-8") as f:
                f.write(domain + "\n")

        self._append_audit({
            "event_type": "takedown_request",
            "timestamp": utc_now_iso(),
            "domain": domain,
            "reason": reason,
            "action_taken": "Added to blocklist",
        })
