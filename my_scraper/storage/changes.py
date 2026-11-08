"""Change tracking: what is new, what changed and what is gone since the last run of a crawl.

Each crawl keeps a snapshot of its items in data/snapshots, keyed by its start URLs and
selectors, so different crawls never compare against each other. An item only counts as
removed when the page it came from was read again this run and no longer has it. Pages
that were skipped, failed, unchanged (with --incremental) or beyond --max-pages keep
their items from the snapshot, so a partial run never reports half the site as deleted.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

SNAPSHOT_DIR = "./data/snapshots"

# Which method found an item is not a change in the item itself.
IGNORED_FIELDS = {"source"}
META_FIELDS = {"url", "source", "source_url", "type"}

Item = Dict[str, Any]


@dataclass
class ChangeReport:
    since: Optional[str] = None          # when the previous snapshot was saved; None on a first run
    new: List[Item] = field(default_factory=list)
    changed: List[Dict[str, Any]] = field(default_factory=list)  # {"item", "changes": {name: {before, after}}}
    removed: List[Item] = field(default_factory=list)
    unchanged: int = 0
    kept: int = 0                        # items carried over from pages not read again this run
    report_path: Optional[str] = None
    snapshot_path: Optional[str] = None

    @property
    def baseline(self) -> bool:
        return self.since is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "since": self.since,
            "summary": {"new": len(self.new), "changed": len(self.changed), "removed": len(self.removed),
                        "unchanged": self.unchanged, "kept_from_unread_pages": self.kept},
            "new": self.new,
            "changed": self.changed,
            "removed": self.removed,
        }


def snapshot_path(start_urls: Iterable[str], selectors: Dict[str, str], item_selector: Optional[str],
                  directory: str = SNAPSHOT_DIR) -> Path:
    urls = sorted(start_urls)
    key = json.dumps([urls, sorted(selectors.items()), item_selector or ""], ensure_ascii=False)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    host = re.sub(r"[^A-Za-z0-9.-]+", "_", urlparse(urls[0]).netloc if urls else "") or "crawl"
    return Path(directory) / f"{host}_{digest}.json"


def page_of(item: Item) -> Optional[str]:
    """The page an item was read from."""
    return item.get("source_url") or item.get("url")


def _label(item: Item) -> str:
    for name in ("title", "name"):
        if item.get(name):
            return str(item[name])
    for name, value in item.items():  # table rows: the first column is usually what names the row
        if name not in META_FIELDS and isinstance(value, (str, int, float)) and str(value).strip():
            return f"{name}={value}"
    return ""


def _keyed(items: Iterable[Item]) -> Dict[str, Item]:
    """Items by identity: their link plus what names them. Repeats are numbered in page order."""
    keyed: Dict[str, Item] = {}
    seen: Dict[str, int] = {}
    for item in items:
        base = f"{item.get('url')}|{_label(item)}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        keyed[base if count == 0 else f"{base}#{count + 1}"] = item
    return keyed


def _plain(item: Item) -> Item:
    """The item as JSON would store it, so tuples and lists compare equal."""
    return json.loads(json.dumps(item, ensure_ascii=False, default=str))


def field_changes(before: Item, after: Item) -> Dict[str, Dict[str, Any]]:
    changes = {}
    for name in list(before) + [name for name in after if name not in before]:
        if name in IGNORED_FIELDS:
            continue
        old, new = before.get(name), after.get(name)
        if old in ("", []):
            old = None
        if new in ("", []):
            new = None
        if old != new:
            changes[name] = {"before": old, "after": new}
    return changes


def compare(previous: List[Item], current: List[Item], read_pages: Set[str]) -> ChangeReport:
    """Compare two runs. Only pages in read_pages can have lost items."""
    report = ChangeReport()
    old = _keyed(_plain(item) for item in previous)
    now = _keyed(_plain(item) for item in current)

    for key, item in now.items():
        if key not in old:
            report.new.append(item)
            continue
        changes = field_changes(old[key], item)
        if changes:
            report.changed.append({"item": item, "changes": changes})
        else:
            report.unchanged += 1

    for key, item in old.items():
        if key in now:
            continue
        if page_of(item) in read_pages:
            report.removed.append(item)
        else:
            report.kept += 1
    return report


def load_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    return data


def _write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(temporary, path)  # a crash mid-write cannot corrupt the last good snapshot


def _report_file(output_dir: str, now: datetime) -> Path:
    directory = Path(output_dir)
    stem = f"changes_{now.strftime('%Y%m%d_%H%M%S')}"
    path = directory / f"{stem}.json"
    counter = 1
    while path.exists():
        path = directory / f"{stem}_{counter}.json"
        counter += 1
    return path


def track(results: List[Item], path: Path, output_dir: str) -> ChangeReport:
    """Compare results with the last snapshot, write the change report, and save the new snapshot."""
    now = datetime.now().astimezone()
    current = [_plain(item) for item in results]
    read_pages = {page for page in map(page_of, current) if page}
    snapshot = load_snapshot(path)

    if snapshot is None:
        report = ChangeReport()
        kept_items: List[Item] = []
    else:
        report = compare(snapshot["items"], current, read_pages)
        report.since = snapshot.get("saved_at")
        now_keys = set(_keyed(current))
        kept_items = [item for key, item in _keyed(snapshot["items"]).items()
                      if key not in now_keys and page_of(item) not in read_pages]
        report_path = _report_file(output_dir, now)
        _write_json(report_path, report.to_dict())
        report.report_path = str(report_path)

    _write_json(path, {"saved_at": now.isoformat(timespec="seconds"), "items": current + kept_items})
    report.snapshot_path = str(path)
    return report


def describe(report: ChangeReport, limit: int = 5) -> List[str]:
    """A few readable lines for the console summary."""
    if report.baseline:
        return [f"change tracking: first run, baseline saved to {report.snapshot_path}"]
    lines = [f"changes since {report.since}: {len(report.new)} new, {len(report.changed)} changed, "
             f"{len(report.removed)} removed, {report.unchanged} unchanged -> {report.report_path}"]
    shown = 0
    for entry in report.changed:
        if shown >= limit:
            break
        parts = ", ".join(f"{name} {_short(change['before'])} -> {_short(change['after'])}"
                          for name, change in list(entry["changes"].items())[:3])
        lines.append(f"    ~ {_short(_label(entry['item']), 60)}: {parts}")
        shown += 1
    for symbol, items in (("+", report.new), ("-", report.removed)):
        for item in items:
            if shown >= limit:
                break
            lines.append(f"    {symbol} {_short(_label(item), 80)}")
            shown += 1
    return lines


def _short(value: Any, width: int = 30) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"
