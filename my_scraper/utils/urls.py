"""URL clean-up and safety checks, so one page is crawled once and traps are avoided."""
import re
from collections import Counter
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from w3lib.url import canonicalize_url

# Query parameters that say nothing about the page itself.
THROWAWAY_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_ga",
    "jsessionid", "phpsessid", "sid", "sessionid", "session_id", "aspsessionid", "cfid", "cftoken",
}
PATH_SESSION = re.compile(r";(?:jsessionid|sid|phpsessid)=[^/?#]*", re.IGNORECASE)
DEFAULT_PORTS = {"http": 80, "https": 443}

# Files that are never pages worth parsing.
ASSET_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".css", ".js", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".webm", ".avi", ".mov", ".wav",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".iso", ".apk",
)

# Links that do something rather than show something. A crawler must never press these.
ACTION_SEGMENTS = {
    "cart", "basket", "checkout", "logout", "logoff", "log-out", "signout", "sign-out", "signup", "sign-up",
    "register", "wishlist", "add-to-cart", "addtocart", "unsubscribe",
}
ACTION_PARAMS = {"add-to-cart", "add_to_cart", "addtocart", "replytocom", "logout", "action", "do"}
PAGINATION_PARAMS = {"page", "p", "pg", "offset", "start", "skip", "paged"}

# A "next month" link on a calendar leads on forever, so dates far ahead are not real pages.
CALENDAR_PATH = re.compile(r"/((?:19|20)\d{2})[/-](?:0?[1-9]|1[0-2])(?:[/-]|$)")
CALENDAR_PARAM = re.compile(r"(?:^|&)(?:year|date|month|y|start_date)=((?:19|20)\d{2})", re.IGNORECASE)
CALENDAR_YEARS_AHEAD = 2

MAX_URL_LENGTH = 2000
MAX_PATH_DEPTH = 16
MAX_SEGMENT_REPEATS = 3  # /a/b/a/b/a/b is a link loop, not a real page


def normalize_url(url: str) -> str:
    """Reduce URL variants to one form: no tracking or session id, sorted query, lowercase host.

    Fragments go too, except #/ and #! routes, which are separate pages in single-page apps.
    Raises ValueError for a URL that cannot be parsed.
    """
    fragment = urlsplit(url).fragment
    route = fragment if fragment.startswith(("/", "!")) else ""

    parts = urlsplit(PATH_SESSION.sub("", url))
    kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in THROWAWAY_PARAMS]
    parts = urlsplit(canonicalize_url(parts._replace(query=urlencode(kept)).geturl()))

    netloc = parts.netloc
    if parts.port == DEFAULT_PORTS.get(parts.scheme):
        netloc = netloc.rsplit(":", 1)[0]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, route))


def has_route_fragment(url: str) -> bool:
    """A #/ or #! address only exists inside the browser."""
    return urlsplit(url).fragment.startswith(("/", "!"))


def site_key(netloc: str) -> str:
    """Host used to compare domains. example.com and www.example.com are the same site."""
    host = netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_key(domain: str) -> str:
    """site_key for a bare domain, in the form URLs take, so münchen.de matches its punycode."""
    try:
        return site_key(urlsplit(normalize_url(f"http://{domain}/")).netloc)
    except ValueError:
        return site_key(domain)


def is_asset(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(ASSET_EXTENSIONS)


def is_action(url: str) -> bool:
    """Whether following this link would change something, such as filling a basket."""
    parts = urlsplit(url)
    segments = {segment.split(".")[0] for segment in parts.path.lower().split("/")}
    if segments & ACTION_SEGMENTS:
        return True
    return any(key.lower() in ACTION_PARAMS for key, _ in parse_qsl(parts.query, keep_blank_values=True))


def is_trap(url: str) -> bool:
    """Whether a URL looks like an endless generated space rather than a real page."""
    if len(url) > MAX_URL_LENGTH:
        return True
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) > MAX_PATH_DEPTH:
        return True
    if segments and max(Counter(segments).values()) >= MAX_SEGMENT_REPEATS:
        return True
    latest_year = date.today().year + CALENDAR_YEARS_AHEAD
    years = CALENDAR_PATH.findall(parts.path) + CALENDAR_PARAM.findall(parts.query)
    return any(int(year) > latest_year for year in years)


def facet_key(url: str) -> tuple:
    """(path, filter query) of a URL. Pagination is left out, as it is finite and wanted."""
    parts = urlsplit(url)
    filters = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                     if key.lower() not in PAGINATION_PARAMS)
    return parts.netloc + parts.path, tuple(filters)
