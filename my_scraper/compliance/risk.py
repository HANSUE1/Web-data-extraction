"""Go or no-go for a site, decided by written rules from a register of reviewed sites.

The register is a JSON file, one entry per site, that a person fills in after reading the site's terms:

    {"example.com": {
        "tos_url": "https://example.com/terms", "tos_verdict": "allows",      allows | silent | prohibits
        "tos_reviewed": "2026-11-01", "reviewed_by": "legal@acme.test",
        "official_api": "https://example.com/api", "api_rejected_because": "no price history",
        "data_class": "public",                                               public | personal | sensitive
        "legal_basis": "legitimate interest, LIA-12", "owner": "data-team",
        "login_permitted": false}}

This records decisions people made. It is not legal advice and cannot replace it.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from parsel import Selector

from my_scraper.utils.urls import safe_join, site_key

GO, REVIEW, NO_GO = "go", "review", "no-go"
REVIEW_VALID_DAYS = 365
TERMS_LINK = "terms", "tos", "conditions", "legal", "acceptable-use"
API_LINK = "developer", "/api", "api.", "openapi", "swagger"

KNOWN_APIS = {
    "github.com": "https://docs.github.com/rest",
    "wikipedia.org": "https://www.mediawiki.org/wiki/API:Main_page",
    "reddit.com": "https://www.reddit.com/dev/api",
    "youtube.com": "https://developers.google.com/youtube/v3",
    "x.com": "https://developer.x.com",
    "twitter.com": "https://developer.x.com",
    "stackoverflow.com": "https://api.stackexchange.com/docs",
    "news.ycombinator.com": "https://github.com/HackerNews/API",
    "openlibrary.org": "https://openlibrary.org/developers/api",
    "arxiv.org": "https://info.arxiv.org/help/api",
    "amazon.com": "https://webservices.amazon.com/paapi5/documentation",
    "ebay.com": "https://developer.ebay.com",
    "spotify.com": "https://developer.spotify.com/documentation/web-api",
}


class RegisterError(ValueError):
    """The site register cannot be used as it is."""


def _covering(host: str, table: Dict[str, Any]) -> Optional[Any]:
    """The entry for a host or the nearest parent domain."""
    labels = host.split(".")
    return next((table[name] for name in (".".join(labels[i:]) for i in range(len(labels))) if name in table), None)


def load_register(path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise RegisterError(f"site register {path}: {e}") from e
    if not isinstance(data, dict) or not all(isinstance(entry, dict) for entry in data.values()):
        raise RegisterError(f"site register {path} must map each site to an object")
    return {site_key(name): entry for name, entry in data.items()}


def known_api(url: str) -> Optional[str]:
    return _covering(site_key(urlsplit(url).hostname or ""), KNOWN_APIS)


@dataclass
class Facts:
    """What is known about a site when the decision is made. None means not checked."""
    host: str
    entry: Optional[Dict[str, Any]] = None
    robots_allowed: Optional[bool] = None
    robots_reason: str = ""
    blocked: str = ""                 # takedown list or country rule that names the site
    authenticated: bool = False
    api_candidates: List[str] = field(default_factory=list)
    personal_data: List[str] = field(default_factory=list)
    page_status: str = ""             # blocked_by_site, login_required... when a page was tried


@dataclass
class Decision:
    host: str
    verdict: str
    reasons: List[str]
    facts: Dict[str, Any] = field(default_factory=dict)


def decide(facts: Facts, today: Optional[date] = None) -> Decision:
    """Apply the rules. Any no-go rule wins, then any rule that needs a person."""
    stop: List[str] = []
    ask: List[str] = []
    entry = facts.entry or {}

    if facts.blocked:
        stop.append(facts.blocked)
    if facts.robots_allowed is False:
        stop.append(f"robots.txt: {facts.robots_reason or 'does not allow the start page'}")
    if entry.get("tos_verdict") == "prohibits":
        stop.append("the terms of service prohibit automated access")
    if entry.get("data_class") == "sensitive" and not entry.get("legal_basis"):
        stop.append("sensitive data with no legal basis on record")
    if facts.page_status == "blocked_by_site":
        stop.append("the site answers automated visitors with a block page, which is not worked around")

    if not facts.entry:
        ask.append("the site is not in the register: nobody has recorded a review of its terms")
    else:
        if entry.get("tos_verdict") not in ("allows", "silent", "prohibits"):
            ask.append("the register has no verdict on the terms of service")
        try:
            age = ((today or date.today()) - date.fromisoformat(str(entry.get("tos_reviewed")))).days
            if age > REVIEW_VALID_DAYS:
                ask.append(f"the terms were reviewed {age} days ago, over {REVIEW_VALID_DAYS}")
        except ValueError:
            ask.append("the register does not say when the terms were reviewed")
    if facts.authenticated and not entry.get("login_permitted"):
        ask.append("the crawl logs in, so it reads more than the public pages")
    api = entry.get("official_api") or (facts.api_candidates[0] if facts.api_candidates else None)
    if api and not entry.get("api_rejected_because"):
        ask.append(f"an API may cover this: {api}. Use it, or record why not")
    if (facts.personal_data or entry.get("data_class") in ("personal", "sensitive")) and not entry.get("legal_basis"):
        ask.append("personal data " + (f"({', '.join(facts.personal_data[:5])}) " if facts.personal_data else "")
                   + "with no legal basis on record")
    if facts.page_status in ("login_required", "consent_wall"):
        ask.append(f"the start page answered {facts.page_status}")

    verdict = NO_GO if stop else REVIEW if ask else GO
    return Decision(facts.host, verdict, stop + ask or ["every rule passed"], asdict(facts))


def page_clues(html: str, url: str) -> Dict[str, List[str]]:
    """Links on a page that look like its terms, or like an API."""
    terms, apis = [], []
    for link in Selector(text=html, type="html").xpath("//a[@href]"):
        target = safe_join(url, link.attrib["href"])
        if not target:
            continue
        hint = f"{target} {' '.join(link.xpath('.//text()').getall())}".lower()
        if any(word in hint for word in TERMS_LINK) and target not in terms:
            terms.append(target)
        if any(word in hint for word in API_LINK) and target not in apis:
            apis.append(target)
    return {"terms": terms[:5], "apis": apis[:5]}


def register_decision(url: str, register: Dict[str, Dict[str, Any]], blocked: str = "",
                      authenticated: bool = False) -> Decision:
    """The decision that needs no request to the site, as made when a crawl starts."""
    host = site_key(urlsplit(url).hostname or "")
    entry = _covering(host, register)
    api = known_api(url)
    return decide(Facts(host, entry, blocked=blocked, authenticated=authenticated,
                        api_candidates=[api] if api else []))


async def assess(config, url: str) -> Decision:
    """Look at a site through the normal pipeline (robots.txt, pacing, audit) and decide."""
    from my_scraper.core.crawler import ProductionCrawler
    from my_scraper.core.page_checks import classify_page
    from my_scraper.fetcher.discovery import feed_links, platform_endpoints

    register = load_register(config.compliance.site_register)
    crawler = ProductionCrawler(config)
    facts = Facts(site_key(urlsplit(url).hostname or ""), authenticated=bool(config.login))
    facts.entry = _covering(facts.host, register)
    api = known_api(url)
    facts.api_candidates = [api] if api else []
    clues: Dict[str, List[str]] = {"terms": [], "apis": []}
    try:
        try:
            crawler.compliance.validate_url(url)
        except ValueError as e:
            facts.blocked = str(e)
            return decide(facts)
        facts.robots_allowed = await crawler.politeness.can_fetch(url)
        facts.robots_reason = crawler.politeness.robots_checker.failure_for(url) or ""
        if facts.robots_allowed:
            result = await crawler.fetch_page(url)
            if result is not None:
                facts.page_status = classify_page(result) or ""
                clues = page_clues(result["content"], result["final_url"])
                facts.api_candidates += (clues["apis"] + platform_endpoints(result["content"], result["final_url"])
                                         + feed_links(result["content"], result["final_url"]))
                page = await crawler._parse(result)
                facts.personal_data = sorted({name for item in page.items
                                              for name in crawler.compliance.scan_for_personal_data(item)})
        decision = decide(facts)
        decision.facts["terms_links"] = clues["terms"]
        return decision
    finally:
        await crawler.close()
