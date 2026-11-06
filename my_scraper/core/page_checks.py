"""Recognise responses that look like success but are not the page that was asked for.

A block page, login wall or "not found" page often arrives with HTTP 200. Stored as
data, it would quietly pollute the results, so these pages are named and reported.
Recognising a block is all this does: nothing here tries to get past one.
"""
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from parsel import Selector

from my_scraper.compliance.manager import LOGIN_SEGMENTS

BLOCKED = "blocked_by_site"
LOGIN = "login_required"
NOT_FOUND = "soft_404"
CONSENT = "consent_wall"

# Interstitials are short, so only short pages are judged. An article about CAPTCHAs is safe.
MAX_INTERSTITIAL_TEXT = 1500

VISIBLE_TEXT = "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"

BLOCK_PHRASES = re.compile(
    r"just a moment|attention required|access (to this page has been )?denied|access blocked"
    r"|are you (a )?(robot|human)|you('re| are) a (robot|human)|verify(ing)? (that )?you('re| are)( a)? human"
    r"|(robot|bot) check|checking (your|if the site connection)|captcha|unusual traffic"
    r"|automated (access|queries|requests|traffic)|pardon our interruption|request unsuccessful"
    r"|enable javascript and cookies|disable any ad ?blocker|ddos protection|security check"
    r"|too many requests|you have been blocked|has been blocked|sorry, you('ve| have) been",
    re.IGNORECASE,
)
CONSENT_PHRASES = re.compile(
    r"before you continue|cookie (settings|preferences|consent)|accept all cookies|consent to (the )?use"
    r"|we (value|care about) your privacy|manage (your )?(privacy|cookies)"
    r"|(confirm|verify) your age|age verification|are you (over )?(18|21)",
    re.IGNORECASE,
)
NOT_FOUND_PHRASES = re.compile(
    r"\b404\b|not found|does(n't| not) exist|no longer (available|exists)|(could not|cannot|can't) be found"
    r"|page introuvable|nicht gefunden|no encontrad|non trovata|não encontrada|niet gevonden"
    r"|страница не найдена|見つかりません|页面不存在|找不到|찾을 수 없",
    re.IGNORECASE,
)


def _is_html(fetch_result: Dict[str, Any]) -> bool:
    content_type = (fetch_result.get("headers") or {}).get("content-type", "").lower()
    return not content_type or "html" in content_type


def classify_page(fetch_result: Dict[str, Any]) -> Optional[str]:
    """Name what a successful response really is, or None if it looks like a normal page."""
    final_url = fetch_result.get("final_url") or fetch_result.get("url", "")
    segments = {segment.split(".")[0] for segment in urlparse(final_url).path.lower().split("/")}
    requested = {segment.split(".")[0] for segment in urlparse(fetch_result.get("url", "")).path.lower().split("/")}
    if (segments - requested) & LOGIN_SEGMENTS:
        return LOGIN  # sent somewhere we did not ask to go

    content = fetch_result.get("content") or ""
    if not content.strip() or not _is_html(fetch_result):
        return None

    sel = Selector(text=content, type="html")
    text = " ".join("".join(sel.xpath(VISIBLE_TEXT).getall()).split())
    if len(text) > MAX_INTERSTITIAL_TEXT:
        return None

    title = " ".join(sel.xpath("string(//title)").get(default="").split())
    heading = " ".join(sel.xpath("string(//h1)").get(default="").split())
    summary = f"{title} {heading} {text}"

    if BLOCK_PHRASES.search(summary):
        return BLOCKED
    if sel.xpath("//input[@type='password']"):
        return LOGIN
    if NOT_FOUND_PHRASES.search(f"{title} {heading}"):
        return NOT_FOUND
    if CONSENT_PHRASES.search(summary):
        return CONSENT
    return None
