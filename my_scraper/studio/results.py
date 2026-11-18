"""The results folder as data: which runs exist, and the rows inside each file."""
import csv
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TABLE_FORMATS = {"jsonl", "json", "csv", "sqlite", "db"}
TEXT_FORMATS = {"txt", "md", "log", "html", "xml"}
MAX_TEXT_CHARS = 1_000_000
RUN_NAME = re.compile(r"^(?P<site>.+?)_(?P<stamp>\d{8}_\d{6})(?:_\d+)?$")
# Which file of a run to count its items from, best first.
COUNT_ORDER = ("jsonl", "json", "csv", "sqlite", "db")

csv.field_size_limit(10_000_000)  # scraped text fields can be long


class NotFound(Exception):
    pass


class Library:
    """Reads the results folder. Every path from a request goes through `resolve`."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._rows: Dict[Path, Tuple[Tuple[float, int], List[Dict[str, Any]]]] = {}

    # --- Paths ----------------------------------------------------------------

    def resolve(self, relative: str, kind: str = "file") -> Path:
        """The real path of a results file or folder, refusing anything outside the folder."""
        path = (self.root / (relative or "")).resolve()
        if path == self.root or self.root not in path.parents:
            raise NotFound(relative)
        if (kind == "file" and not path.is_file()) or (kind == "dir" and not path.is_dir()):
            raise NotFound(relative)
        return path

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # --- Runs -----------------------------------------------------------------

    def runs(self) -> List[Dict[str, Any]]:
        """One entry per folder that holds files, newest first."""
        folders: Dict[Path, List[Path]] = {}
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith("."):
                folders.setdefault(path.parent, []).append(path)

        runs = []
        for folder, files in folders.items():
            entries = [self._file_entry(path) for path in sorted(files)]
            modified = max(entry["modified"] for entry in entries)
            match = RUN_NAME.match(folder.name)
            if match:
                site = match["site"]
                created = datetime.strptime(match["stamp"], "%Y%m%d_%H%M%S").timestamp()
            else:
                site, created = folder.name, modified
            runs.append({
                "id": self._relative(folder) if folder != self.root else "",
                "name": folder.name if folder != self.root else "(results)",
                "site": site,
                "created": created,
                "modified": modified,
                "size": sum(entry["size"] for entry in entries),
                "items": _run_items(entries),
                "files": entries,
            })
        runs.sort(key=lambda run: run["created"], reverse=True)
        return runs

    def _file_entry(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        fmt = path.suffix.lstrip(".").lower()
        rows = None
        if fmt in TABLE_FORMATS:
            try:
                rows = len(self.rows(path))
            except Exception:  # a half-written or foreign file still shows in the list
                rows = None
        return {"path": self._relative(path), "name": path.name, "format": fmt,
                "size": stat.st_size, "modified": stat.st_mtime, "rows": rows}

    def stats(self) -> Dict[str, Any]:
        runs = self.runs()
        today = datetime.now().date()
        days: Dict[str, Dict[str, int]] = {}
        for back in range(29, -1, -1):
            day = datetime.fromordinal(today.toordinal() - back).date().isoformat()
            days[day] = {"runs": 0, "items": 0}
        sites: Dict[str, int] = {}
        formats: Dict[str, int] = {}
        for run in runs:
            day = datetime.fromtimestamp(run["created"]).date().isoformat()
            if day in days:
                days[day]["runs"] += 1
                days[day]["items"] += run["items"] or 0
            sites[run["site"]] = sites.get(run["site"], 0) + (run["items"] or 0)
            for entry in run["files"]:
                formats[entry["format"]] = formats.get(entry["format"], 0) + 1
        return {
            "runs": len(runs),
            "items": sum(run["items"] or 0 for run in runs),
            "files": sum(len(run["files"]) for run in runs),
            "size": sum(run["size"] for run in runs),
            "sites": len(sites),
            "top_sites": sorted(sites.items(), key=lambda pair: pair[1], reverse=True)[:6],
            "formats": formats,
            "days": [{"day": day, **counts} for day, counts in days.items()],
            "recent": runs[:6],
        }

    def delete_run(self, relative: str):
        shutil.rmtree(self.resolve(relative, kind="dir"))
        self._rows.clear()

    # --- File contents ----------------------------------------------------------

    def rows(self, path: Path) -> List[Dict[str, Any]]:
        """A table file's records as dicts, cached until the file changes."""
        stat = path.stat()
        key = (stat.st_mtime, stat.st_size)
        cached = self._rows.get(path)
        if cached and cached[0] == key:
            return cached[1]
        rows = _read_rows(path)
        if len(self._rows) > 32:  # shortcut: crude cap, fine for a personal results folder
            self._rows.clear()
        self._rows[path] = (key, rows)
        return rows

    def view(self, relative: str, offset: int = 0, limit: int = 100, query: str = "",
             sort: Optional[str] = None, descending: bool = False) -> Dict[str, Any]:
        path = self.resolve(relative)
        fmt = path.suffix.lstrip(".").lower()
        if fmt not in TABLE_FORMATS:
            text = path.read_text(encoding="utf-8", errors="replace")
            return {"kind": "text", "format": fmt, "text": text[:MAX_TEXT_CHARS],
                    "truncated": len(text) > MAX_TEXT_CHARS}

        rows = self.rows(path)
        columns = _columns(rows)
        if query:
            needle = query.lower()
            rows = [row for row in rows if needle in json.dumps(row, ensure_ascii=False, default=str).lower()]
        if sort in columns:
            present = [row for row in rows if row.get(sort) not in (None, "")]
            missing = [row for row in rows if row.get(sort) in (None, "")]
            rows = sorted(present, key=lambda row: _sort_key(row.get(sort)), reverse=descending) + missing
        offset = max(0, offset)
        limit = max(1, min(limit, 1000))
        return {"kind": "table", "format": fmt, "columns": columns, "total": len(self.rows(path)),
                "matched": len(rows), "offset": offset, "rows": rows[offset:offset + limit]}


def _run_items(entries: List[Dict[str, Any]]) -> Optional[int]:
    for fmt in COUNT_ORDER:
        for entry in entries:
            if entry["format"] == fmt and entry["rows"] is not None:
                return entry["rows"]
    return None


def _columns(rows: List[Dict[str, Any]]) -> List[str]:
    columns: Dict[str, None] = {}
    for row in rows:
        for key in row:
            columns.setdefault(key, None)
    return list(columns)


def _sort_key(value: Any):
    """Numbers before text, numbers by value, so "10" sorts after "9"."""
    text = str(value)
    try:
        return (0, float(text.replace(",", "").lstrip("$£€")), "")
    except ValueError:
        return (1, 0.0, text.lower())


def _as_record(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {"value": value}


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    fmt = path.suffix.lstrip(".").lower()
    if fmt == "jsonl":
        with open(path, encoding="utf-8", errors="replace") as f:
            return [_as_record(json.loads(line)) for line in f if line.strip()]
    if fmt == "json":
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        if isinstance(data, dict):  # {"items": [...]} and similar wrappers
            lists = [value for value in data.values() if isinstance(value, list)]
            data = lists[0] if len(lists) == 1 else [data]
        return [_as_record(value) for value in data] if isinstance(data, list) else [_as_record(data)]
    if fmt == "csv":
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    # SQLite: the largest table, read-only
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [name.replace('"', '""') for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if not tables:
            return []
        sizes = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables}
        table = max(sizes, key=sizes.get)
        cursor = connection.execute(f'SELECT * FROM "{table}"')
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor]
    finally:
        connection.close()
