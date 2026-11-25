"""Multi-format storage: JSON, JSONL, CSV and SQLite."""
import csv
import hashlib
import importlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from my_scraper.config.settings import RESULTS_DIR
from my_scraper.storage.database import DatabaseError, deliver

Item = Dict[str, Any]

# Spreadsheets run a cell beginning with one of these as a formula, and scraped text is
# written by strangers. A leading quote makes Excel and friends show it as plain text.
MAX_SQLITE_COLUMNS = 2000
SQLITE_INTEGERS = range(-2 ** 63, 2 ** 63)

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _flatten(value: Any) -> Any:
    """Encode nested values as JSON so flat formats keep them readable and reversible."""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _safe_cell(value: Any) -> Any:
    value = _flatten(value)
    if isinstance(value, str) and value.startswith(FORMULA_STARTS) and not _is_number(value):
        return "'" + value
    return value


def _text_safe(value: Any) -> Any:
    """Broken surrogates turn up in scraped JSON, and SQLite refuses them."""
    value = _flatten(value)
    if value is not None and not isinstance(value, (str, int, float)):
        value = str(value)  # dates from spreadsheets, for instance
    if isinstance(value, int) and value not in SQLITE_INTEGERS:
        value = str(value)
    return value.encode("utf-8", "replace").decode("utf-8") if isinstance(value, str) else value


def free_stem(directory: Path, extensions: List[str], label: str = "output") -> str:
    """label_<date>_<time>, numbered if needed so no file of any of the extensions is overwritten."""
    base = f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    stem, counter = base, 1
    while any((directory / f"{stem}.{extension}").exists() for extension in extensions):
        stem, counter = f"{base}_{counter}", counter + 1
    return stem


def run_label(start_urls: List[str]) -> str:
    """The site a crawl started on, as a file name: "books.toscrape.com"."""
    host = urlsplit(start_urls[0]).netloc if start_urls else ""
    return re.sub(r"[^A-Za-z0-9.-]+", "_", host).strip("._") or "output"


def _all_keys(items: List[Item]) -> List[str]:
    return sorted({key for item in items for key in item})


def _column_names(keys: List[str]) -> List[str]:
    """A column name for each key. SQLite ignores case, so "Title" beside "title" becomes "title_2"."""
    names: List[str] = []
    taken = set()
    for key in keys:
        name, counter = _text_safe(key), 2
        while name.lower() in taken:
            name, counter = f"{_text_safe(key)}_{counter}", counter + 1
        taken.add(name.lower())
        names.append(name)
    return names


def _flatten_attributes(items: List[Item]) -> List[Item]:
    """Give each detail ("Year", "Mileage") its own column in a table.

    A detail whose name is already a field stays inside "attributes", so nothing is overwritten.
    """
    flat = []
    for item in items:
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            flat.append(item)
            continue
        row = {key: value for key, value in item.items() if key != "attributes"}
        kept = {}
        for name, value in attributes.items():
            if name in row:
                kept[name] = value
            else:
                row[name] = value
        if kept:
            row["attributes"] = kept
        flat.append(row)
    return flat


class FileExporter:
    """Base class: picks the output path, subclasses write the file."""

    extension = ""
    skip_when_empty = False

    def __init__(self, output_dir: str = RESULTS_DIR):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(self, items: List[Item], filename: str = None, stem: str = None) -> Optional[str]:
        """Write items to filename, or to stem plus this format's extension, or to a new name."""
        if self.skip_when_empty and not items:
            return None
        if filename:
            filepath = self.output_dir / filename
        else:
            filepath = self.output_dir / f"{stem or free_stem(self.output_dir, [self.extension])}.{self.extension}"
        self._write(items, filepath)
        return str(filepath)

    def _write(self, items: List[Item], filepath: Path):
        raise NotImplementedError


class JSONExporter(FileExporter):
    """Export to JSON format."""

    extension = "json"

    def _write(self, items, filepath):
        with open(filepath, "w", encoding="utf-8", errors="replace") as f:
            json.dump(items, f, indent=2, ensure_ascii=False, default=str)


class JSONLExporter(FileExporter):
    """Export to JSONL format."""

    extension = "jsonl"

    def _write(self, items, filepath):
        with open(filepath, "w", encoding="utf-8", errors="replace") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


class CSVExporter(FileExporter):
    """Export to CSV format."""

    extension = "csv"
    skip_when_empty = True

    def _write(self, items, filepath):
        items = _flatten_attributes(items)
        with open(filepath, "w", newline="", encoding="utf-8", errors="replace") as f:
            columns = _all_keys(items)
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writerow({column: _safe_cell(column) for column in columns})  # names come from the page too
            for item in items:
                writer.writerow({key: _safe_cell(value) for key, value in item.items()})


class SQLiteExporter(FileExporter):
    """Export to a SQLite database with one text column per field."""

    extension = "db"
    skip_when_empty = True
    table_name = "items"

    def _write(self, items, filepath):
        flat = _flatten_attributes(items)
        if len(_all_keys(flat)) <= MAX_SQLITE_COLUMNS:  # beyond that, details stay together in one column
            items = flat
        columns = _all_keys(items)
        names = ['"{}"'.format(name.replace('"', '""')) for name in _column_names(columns)]
        definitions = ", ".join(f"{name} TEXT" for name in names)
        placeholders = ", ".join("?" for _ in columns)

        with closing(sqlite3.connect(filepath)) as conn, conn:
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{self.table_name}" ({definitions})')
            conn.executemany(
                f'INSERT INTO "{self.table_name}" ({", ".join(names)}) VALUES ({placeholders})',
                [[_text_safe(item.get(column)) for column in columns] for item in items],
            )


class StorageManager:
    """Unified storage manager."""

    EXPORTERS = {
        "json": JSONExporter,
        "jsonl": JSONLExporter,
        "csv": CSVExporter,
        "sqlite": SQLiteExporter,
    }

    def __init__(self, config):
        self.config = config
        self.errors: Dict[str, str] = {}
        self.last_run_dir: Optional[str] = None  # the folder this run's files went to
        # Set by the crawler once it has judged the run.
        self.run_id = ""
        self.crawl_key = ""
        self.quarantined = False  # below its quality thresholds: kept apart, and not delivered
        self.complete = True      # an interrupted run must not replace a complete dataset

    def export(self, items: List[Item], formats: List = None) -> Dict[str, Optional[str]]:
        """Export to all configured formats. One failing must not cost the others their file.

        Each run gets its own folder, <output_dir>/<site>_<time>/, so one scrape is one folder.
        """
        if formats is None:
            formats = self.config.export_formats
        results: Dict[str, Optional[str]] = {}
        if formats:
            results.update(self._export_files(items, formats))
        if self.config.storage.database_url:
            try:
                results["database"] = self._deliver(items)
            except Exception as e:  # drivers raise their own types, and the files are still good
                results["database"] = None
                self.errors["database"] = f"{type(e).__name__}: {e}"
        for sink in self.config.storage.sinks if items and not self.quarantined else ():
            try:
                module, _, function = sink.partition(":")
                getattr(importlib.import_module(module), function)(items, self.config)
                results[sink] = sink
            except Exception as e:
                self.errors[sink] = f"{type(e).__name__}: {e}"
        return results

    def _export_files(self, items: List[Item], formats: List) -> Dict[str, Optional[str]]:
        try:
            run_dir = self._new_run_dir()
        except OSError as e:
            self.errors.update({fmt.value: f"{type(e).__name__}: {e}" for fmt in formats})
            return {fmt.value: None for fmt in formats}
        self.last_run_dir = str(run_dir)
        stem = run_dir.name  # files share the folder's name, so a run's CSV and JSONL pair up

        results: Dict[str, Optional[str]] = {}
        for fmt in formats:
            try:
                exporter = self.EXPORTERS[fmt.value](str(run_dir))
                results[fmt.value] = exporter.export(items, filename=f"{stem}.{exporter.extension}")
            except Exception as e:
                results[fmt.value] = None
                self.errors[fmt.value] = f"{type(e).__name__}: {e}"
        return results

    def _deliver(self, items: List[Item]) -> Optional[str]:
        storage = self.config.storage
        if self.quarantined or not items:
            return None
        if storage.delivery == "snapshot" and not self.complete:
            raise DatabaseError("an interrupted run does not replace the delivered snapshot")
        count = deliver(items, storage.database_url, storage.table, storage.delivery, storage.key_fields,
                        self.crawl_key, self.run_id)
        return f"{count} rows in {storage.table}"

    def write_rejected(self, rejected: List[Item]) -> Optional[str]:
        """Keep the records validation turned away, with the reasons, beside the run's exports."""
        if not rejected or not self.last_run_dir:
            return None
        path = Path(self.last_run_dir) / "rejected.jsonl"
        path.write_text("".join(json.dumps(entry, ensure_ascii=False, default=str) + "\n" for entry in rejected),
                        encoding="utf-8", errors="replace")
        return str(path)

    def write_manifest(self, contents: Dict[str, Any]) -> Optional[str]:
        """Describe the run's dataset: what it is, how good, and the hash of every file."""
        if not self.last_run_dir:
            return None
        run_dir = Path(self.last_run_dir)
        files = {path.name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in sorted(run_dir.iterdir()) if path.is_file()}
        path = Path(self.config.storage.state_dir) / "manifests" / f"{run_dir.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**contents, "folder": str(run_dir), "files": files}, indent=1, default=str,
                                   ensure_ascii=False), encoding="utf-8", errors="replace")
        return str(path)

    def _new_run_dir(self) -> Path:
        """A fresh folder named <site>_<time>, numbered if two runs land in the same second."""
        root = Path(self.config.output_dir) / ("quarantine" if self.quarantined else "")
        root.mkdir(parents=True, exist_ok=True)
        base = f"{run_label(self.config.start_urls)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        name, counter = base, 1
        while True:
            try:
                (root / name).mkdir()  # making it is the check: another process may be naming one too
                return root / name
            except FileExistsError:
                name, counter = f"{base}_{counter}", counter + 1
