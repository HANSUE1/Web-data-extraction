"""Recurring crawls: when each is due, and putting it on the queue exactly once.

A schedules file is a JSON list:

    [{"name": "books-nightly", "cron": "30 2 * * *", "timezone": "Europe/Berlin", "priority": 5,
      "config_file": "config/books.json"},
     {"name": "prices", "every": "6h", "window": "22:00-06:00", "config": {"start_urls": ["https://example.com/"]}},
     {"name": "news", "every": "auto", "config": {...}}]

cron is minute hour day month weekday (0 or 7 is Sunday). every takes s, m, h or d, or "auto": as often
as the first start URL has been changing. window keeps "every" to off-peak hours. Any number of
schedulers may run: each due time is claimed once in the queue.
"""
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from my_scraper.config.settings import STATE_DIR
from my_scraper.search.recrawl import recrawl_interval
from my_scraper.storage.snapshots import SnapshotStore

CATCH_UP_MINUTES = 60    # a scheduler that was down this long still starts what it missed
FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
EVERY = re.compile(r"(\d+)\s*([smhd])")
SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
WINDOW = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")


class ScheduleError(ValueError):
    """A schedule that cannot be used."""


def _cron_field(text: str, low: int, high: int) -> Set[int]:
    values: Set[int] = set()
    for part in text.split(","):
        span, _, step = part.partition("/")
        try:
            if span == "*":
                first, last = low, high
            elif "-" in span:
                first, last = map(int, span.split("-"))
            else:
                first = int(span)
                last = high if step else first
            every = int(step) if step else 1
        except ValueError:
            raise ScheduleError(f"unreadable cron field {text!r}") from None
        if not (low <= first <= last <= high and every > 0):
            raise ScheduleError(f"cron field {text!r} is outside {low}-{high}")
        values.update(range(first, last + 1, every))
    return values


def cron_matches(expression: str, moment: datetime) -> bool:
    parts = expression.split()
    if len(parts) != 5:
        raise ScheduleError(f"cron needs five fields: {expression!r}")
    minute, hour, day, month, weekday = (_cron_field(part, *limits) for part, limits in zip(parts, FIELDS, strict=True))
    if not (moment.minute in minute and moment.hour in hour and moment.month in month):
        return False
    today = moment.isoweekday() % 7  # cron counts from Sunday, as 0 or 7
    on_day, on_weekday = moment.day in day, today in weekday or (today == 0 and 7 in weekday)
    # Standard cron: with both a day and a weekday given, either will do.
    return (on_day or on_weekday) if parts[2] != "*" and parts[4] != "*" else (on_day and on_weekday)


def in_window(window: Optional[str], moment: datetime) -> bool:
    if not window:
        return True
    match = WINDOW.fullmatch(window.strip())
    if not match:
        raise ScheduleError(f"window must look like 22:00-06:00, got {window!r}")
    start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
    start, end, now = start_hour * 60 + start_minute, end_hour * 60 + end_minute, moment.hour * 60 + moment.minute
    return start <= now < end if start <= end else now >= start or now < end  # past midnight


def _zone(schedule: Dict[str, Any]):
    name = schedule.get("timezone")
    if not name:
        return None  # this computer's
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown timezone {name!r} (on Windows: pip install tzdata)") from None


def _interval(schedule: Dict[str, Any], state_dir: str) -> float:
    every = str(schedule["every"])
    if every == "auto":
        config = schedule.get("config") or {}
        if not config.get("start_urls"):
            raise ScheduleError("every: auto needs config.start_urls")
        return recrawl_interval(SnapshotStore(state_dir), config["start_urls"][0])
    match = EVERY.fullmatch(every.strip())
    if not match or int(match.group(1)) < 1:
        raise ScheduleError(f"every must look like 30m, 6h or 1d, got {every!r}")
    return int(match.group(1)) * SECONDS[match.group(2)]


def due_slot(schedule: Dict[str, Any], now: float, state_dir: str = STATE_DIR) -> Optional[str]:
    """Names the moment a schedule is due for, or None. The same moment always gets the same name."""
    zone = _zone(schedule)
    if "cron" in schedule:
        minute = datetime.fromtimestamp(now, zone).replace(second=0, microsecond=0)
        for back in range(CATCH_UP_MINUTES):
            moment = minute - timedelta(minutes=back)
            if cron_matches(schedule["cron"], moment):
                return moment.isoformat()
        return None
    if not in_window(schedule.get("window"), datetime.fromtimestamp(now, zone)):
        return None
    return str(int(now // _interval(schedule, state_dir)))


def load_schedules(path: str, missing_ok: bool = False) -> List[Dict[str, Any]]:
    """Read and check a schedules file, so a mistake shows when it is loaded, not at 2 a.m.

    With missing_ok, a file that does not exist yet means no schedules: a fresh deploy starts,
    and the scheduler picks the file up once it is written."""
    if missing_ok and not Path(path).exists():
        return []
    try:
        schedules = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ScheduleError(f"schedules {path}: {e}") from e
    if not isinstance(schedules, list):
        raise ScheduleError(f"schedules {path} must be a list")
    names = set()
    for schedule in schedules:
        name = schedule.get("name")
        if not name or name in names:
            raise ScheduleError(f"every schedule needs its own name, got {name!r}")
        names.add(name)
        if ("cron" in schedule) == ("every" in schedule):
            raise ScheduleError(f"{name}: give either cron or every")
        if ("config" in schedule) == ("config_file" in schedule):
            raise ScheduleError(f"{name}: give either config or config_file")
        if schedule.get("every") != "auto":
            due_slot(schedule, time.time())
        in_window(schedule.get("window"), datetime.now())
    return schedules


def tick(queue, schedules: List[Dict[str, Any]], now: Optional[float] = None, state_dir: str = STATE_DIR) -> List[str]:
    """Queue everything that is due. Returns the names queued."""
    started = []
    for schedule in schedules:
        if schedule.get("enabled") is False:
            continue
        slot = due_slot(schedule, now or time.time(), state_dir)
        if slot is None or not queue.seen_add(f"schedule:{schedule['name']}:{slot}"):
            continue
        payload = {key: schedule[key] for key in ("config", "config_file", "fan_out") if key in schedule}
        # A run still going when the next one is due is left to finish.
        queue.enqueue({**payload, "schedule": schedule["name"]}, priority=int(schedule.get("priority", 0)),
                      max_attempts=int(schedule.get("max_attempts", 3)), dedupe_key=f"schedule:{schedule['name']}")
        started.append(schedule["name"])
    return started
