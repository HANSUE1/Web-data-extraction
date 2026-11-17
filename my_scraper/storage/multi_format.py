"""Multi-format storage: JSON, JSONL, CSV and SQLite."""
import csv
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from my_scraper.config.settings import RESULTS_DIR

Item = Dict[str, Any]

# Spreadsheets run a cell beginning with one of these as a formula, and scraped text is
# written by strangers. A leading quote makes Excel and friends show it as plain text.
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
            writer = csv.DictWriter(f, fieldnames=_all_keys(items))
            writer.writeheader()
            for item in items:
                writer.writerow({key: _safe_cell(value) for key, value in item.items()})


class SQLiteExporter(FileExporter):
    """Export to a SQLite database with one text column per field."""

    extension = "db"
    skip_when_empty = True
    table_name = "items"

    def _write(self, items, filepath):
        items = _flatten_attributes(items)
        columns = _all_keys(items)
        names = ['"{}"'.format(column.replace('"', '""')) for column in columns]
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
        self.exporters = {name: cls(config.output_dir) for name, cls in self.EXPORTERS.items()}
        self.errors: Dict[str, str] = {}

    def export(self, items: List[Item], formats: List = None) -> Dict[str, Optional[str]]:
        """Export to all configured formats. One failing must not cost the others their file."""
        if formats is None:
            formats = self.config.export_formats

        # Every file of one run shares a name, so a run's CSV and JSONL are easy to pair up.
        extensions = [self.exporters[fmt.value].extension for fmt in formats]
        stem = free_stem(Path(self.config.output_dir), extensions, run_label(self.config.start_urls))

        results: Dict[str, Optional[str]] = {}
        for fmt in formats:
            try:
                results[fmt.value] = self.exporters[fmt.value].export(items, stem=stem)
            except Exception as e:
                results[fmt.value] = None
                self.errors[fmt.value] = f"{type(e).__name__}: {e}"
        return results
