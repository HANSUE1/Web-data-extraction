"""The production gate: what must hold before a scraper is released. CI runs it, and it exits 1 when a check fails."""
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from my_scraper.config.settings import CrawlConfig
from my_scraper.parser.universal_parser import UniversalParser
from my_scraper.storage.snapshots import SnapshotStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "my_scraper" / "tests" / "fixtures"
MAX_PARSE_SECONDS = 5.0
MIN_URLS = 2
CONTRACT = PROJECT_ROOT / "docs" / "OPERATIONS.md"
CONTRACT_SECTIONS = ("Scope", "Freshness", "Correctness", "Failure policy", "Delivery", "Access review", "Ownership")

SECRETS = re.compile("|".join((
    r"AKIA[0-9A-Z]{16}",                                     # AWS
    r"sk-ant-[A-Za-z0-9_-]{20,}", r"\bsk-[A-Za-z0-9]{32,}",  # model providers
    r"gh[pousr]_[A-Za-z0-9]{36}", r"github_pat_\w{60,}", r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"hooks\.slack\.com/services/T\w+/B\w+/\w+",
    r"\b(?i:password|passwd|api_key|secret_key|auth_token)\b[\"']?\s*[:=]\s*[\"'](?!env:|file:)[^\"'\s{}$<>]{12,}[\"']",
)))
# Text that says it is not a secret: placeholders in documentation and tests.
PLACEHOLDER = re.compile(r"example|placeholder|my-password|changeme|dummy|fake", re.IGNORECASE)
TEXT_LIMIT = 2_000_000


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def find_secrets(root: Path = PROJECT_ROOT) -> List[str]:
    """Lines of tracked files that look like a credential, as path:line. The value is not repeated."""
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                            capture_output=True, check=True)
    found = []
    for name in listed.stdout.decode("utf-8", "replace").split("\0"):
        path = root / name
        if not name or not path.is_file() or path.stat().st_size > TEXT_LIMIT:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if SECRETS.search(line) and not PLACEHOLDER.search(line):
                found.append(f"{name}:{number}")
    return found


def _run(name: str, command: List[str]) -> Check:
    done = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    last = (done.stdout.strip() or done.stderr.strip()).splitlines()[-1:]
    return Check(name, done.returncode == 0, last[0] if last else "")


def _performance() -> Check:
    slowest, worst = "", 0.0
    parser = UniversalParser()
    for path in sorted(FIXTURES.rglob("*.html")):
        started = time.perf_counter()
        parser.parse_page({"url": "https://fixture.test/", "content": path.read_text(encoding="utf-8"),
                           "headers": {"content-type": "text/html"}})
        seconds = time.perf_counter() - started
        if seconds > worst:
            slowest, worst = path.name, seconds
    return Check("performance", 0 < worst <= MAX_PARSE_SECONDS, f"slowest fixture: {slowest} in {worst:.2f}s")


def _configuration(config: CrawlConfig) -> List[Check]:
    quality = config.quality
    tried = {row["url"] for row in SnapshotStore(config.storage.state_dir, config.storage.snapshot_location).find(limit=200)}
    return [
        Check("schema", quality.schema is not None, "quality.schema is set" if quality.schema else "no quality.schema"),
        Check("acceptance criteria", quality.min_items > 0 and quality.min_valid_ratio > 0,
              f"min_items {quality.min_items}, min_valid_ratio {quality.min_valid_ratio:g}"),
        Check("recovery path", config.storage.snapshots and config.retry.max_retries > 0,
              "snapshots and retries are on" if config.storage.snapshots else "snapshots are off"),
        Check("several URLs tried", len(tried) >= MIN_URLS, f"{len(tried)} distinct URLs on record"),
    ]


def gate(config: Optional[CrawlConfig] = None, quick: bool = False) -> List[Check]:
    """Every check. With quick, the test suites are left to CI's own steps."""
    secrets = find_secrets()
    contract = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
    missing = [section for section in CONTRACT_SECTIONS if f"## {section}" not in contract]
    checks = [
        Check("no secrets", not secrets, ", ".join(secrets[:5]) or "none found"),
        Check("operational contract", not missing, f"missing: {', '.join(missing)}" if missing else "complete"),
        _performance(),
    ]
    if not quick:
        checks.append(_run("acceptance fixtures and replay", [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "my_scraper/tests/test_fixtures.py"]))
        checks.append(_run("recovery tests", [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "my_scraper/tests/test_jobqueue.py"]))
    if config is not None:
        checks += _configuration(config)
    return checks
