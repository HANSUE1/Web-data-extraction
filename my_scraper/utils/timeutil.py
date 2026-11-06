"""Time helpers."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with a trailing Z."""
    return utc_now().isoformat().replace("+00:00", "Z")
