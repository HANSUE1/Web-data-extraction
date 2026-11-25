"""Bring values to one form: nulls, prices, dates. Originals stay; parsed values go in fields beside them."""
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

NULL_WORDS = {"", "-", "--", "—", "n/a", "na", "null", "none", "nil", "undefined", "unknown"}
# Longest first, so "A$" is not read as "$".
CURRENCIES = {"HK$": "HKD", "NZ$": "NZD", "US$": "USD", "CHF": "CHF", "A$": "AUD", "C$": "CAD", "S$": "SGD",
              "R$": "BRL", "zł": "PLN", "Kč": "CZK", "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR",
              "₩": "KRW", "₱": "PHP", "₽": "RUB", "₺": "TRY", "₫": "VND", "฿": "THB"}
CURRENCY_CODE = re.compile(r"\b(USD|EUR|GBP|JPY|CNY|INR|AUD|CAD|CHF|SEK|NOK|DKK|PLN|CZK|BRL|MXN|KRW|PHP|SGD|HKD"
                           r"|NZD|TRY|THB|VND|ZAR|RUB)\b", re.IGNORECASE)
GROUP_SPACES = r"\s  '"
NUMBER = re.compile(rf"\d[\d.,{GROUP_SPACES}]*")
PRICE_FIELDS = {"price", "cost", "amount", "sale_price", "low_price", "high_price"}
DATE_FIELDS = {"date", "published", "updated", "created", "modified", "date_published", "date_modified"}
# Only formats with one reading: 03/04/2026 is March or April depending on who wrote it, so it is left alone.
DATE_FORMATS = ("%Y/%m/%d", "%Y.%m.%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                "%d %B, %Y", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z")


def parse_number(text: str) -> Optional[float]:
    match = NUMBER.search(text)
    if not match:
        return None
    digits = re.sub(rf"[{GROUP_SPACES}]", "", match.group()).rstrip(".,")
    last = max(digits.rfind("."), digits.rfind(","))
    if last >= 0:
        separator, tail = digits[last], digits[last + 1:]
        other = "," if separator == "." else "."
        # One kind of separator before exactly three digits groups thousands: 1,234 and 1.234.
        grouping = digits.count(separator) > 1 or (len(tail) == 3 and other not in digits and digits[:last] != "0")
        whole = re.sub(r"[.,]", "", digits[:last])
        digits = whole + tail if grouping else f"{whole}.{tail}"
    try:
        return float(digits)
    except ValueError:
        return None


def parse_price(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """(amount, ISO currency), either of which may be None."""
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    text = str(value).strip()
    code = CURRENCY_CODE.search(text)
    currency = code.group().upper() if code else next((iso for sign, iso in CURRENCIES.items() if sign in text), None)
    return parse_number(text), currency


def parse_date(value: Any) -> Optional[str]:
    """ISO 8601, or None when the text is not certainly one date."""
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return text[:10] if len(text) == 10 else parsed.isoformat()
        except ValueError:
            return None
    for form in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, form)
        except ValueError:
            continue
        return parsed.isoformat() if "%H" in form else parsed.date().isoformat()
    return None


def normalize_item(item: Dict[str, Any], target_currency: Optional[str] = None,
                   rates: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """rates are units of target_currency per one unit of each currency, as the operator supplies them."""
    clean: Dict[str, Any] = {}
    for name, value in item.items():
        if isinstance(value, str):
            value = value.strip()
            if value.lower() in NULL_WORDS:
                value = None
        clean[name] = value

    for name in [key for key in clean if key.lower() in PRICE_FIELDS and clean[key] is not None]:
        amount, currency = parse_price(clean[name])
        currency = currency or (str(clean["currency"]).upper() if clean.get("currency") else None)
        if amount is None:
            continue
        clean.setdefault(f"{name}_amount", amount)
        clean.setdefault(f"{name}_currency", currency)
        rate = 1.0 if currency == target_currency else (rates or {}).get(currency or "")
        if target_currency and rate:
            clean.setdefault(f"{name}_{target_currency.lower()}", round(amount * rate, 2))
    for name in [key for key in clean if key.lower() in DATE_FIELDS and clean[key] is not None]:
        iso = parse_date(clean[name])
        if iso:
            clean.setdefault(f"{name}_iso", iso)
    return clean
