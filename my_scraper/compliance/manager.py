"""Compliance and governance module."""
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import urlparse

from my_scraper.utils.timeutil import utc_now_iso

# Whole path segments, so /author/x is public while /auth/x and /login.php are not.
LOGIN_SEGMENTS = {
    "login", "logon", "signin", "sign-in", "auth", "oauth", "authenticate", "authorize",
    "account", "accounts", "my-account", "myaccount", "dashboard",
}
PAYWALL_SEGMENTS = {"paywall", "subscribe", "premium"}

PII_FIELD_KEYWORDS = ("email", "phone", "ssn", "social_security", "address", "zip", "postal", "dob", "birth")
PII_VALUE_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                        # email
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                           # US SSN
    re.compile(r"\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}"),  # international phone
    re.compile(r"(?<!\d)\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),  # US phone
)


class ComplianceManager:
    """Legal and ethical compliance enforcement."""

    def __init__(self, config):
        self.config = config
        self.audit_log_file = config.logging.audit_log
        Path(self.audit_log_file).parent.mkdir(parents=True, exist_ok=True)
        self.blocklist_file = Path(self.audit_log_file).with_name("takedown_blocklist.txt")
        self.blocked_domains = self._load_blocklist()

        # Track scraped URLs and data
        self.scraped_urls = []
        self.personal_data_found = []

    def _load_blocklist(self) -> Set[str]:
        if not self.blocklist_file.exists():
            return set()
        lines = self.blocklist_file.read_text(encoding="utf-8").splitlines()
        return {line.strip().lower() for line in lines if line.strip()}

    def _is_blocked_domain(self, host: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in self.blocked_domains)

    def validate_url(self, url: str) -> bool:
        """Validate URL is allowed. Raises ValueError if it is not."""
        parsed = urlparse(url)

        # Takedowns apply whatever the public_only setting says.
        if self._is_blocked_domain((parsed.hostname or "").lower()):
            raise ValueError(f"Domain is on the takedown blocklist: {url}")

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

    def _append_audit(self, event: Dict[str, Any]):
        with open(self.audit_log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def log_scrape_event(self, url: str, status: str, items_count: int = 0,
                         personal_data: List[str] = None, error: str = None):
        """Log scrape event to audit trail."""
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

        self.scraped_urls.append(url)
        if personal_data:
            self.personal_data_found.extend(personal_data)

    def generate_compliance_report(self) -> Dict[str, Any]:
        """Generate compliance report."""
        return {
            "report_generated": utc_now_iso(),
            "total_urls_scraped": len(self.scraped_urls),
            "unique_domains": len({urlparse(url).netloc for url in self.scraped_urls}),
            "personal_data_fields_found": sorted(set(self.personal_data_found)),
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
