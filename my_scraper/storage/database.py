"""Deliver records to a database table: SQLite for one machine, PostgreSQL (needs psycopg) for more.

One table serves every crawl: the record as JSON, its key, a hash of its content, and when it was
first and last seen. Delivery is a snapshot (the table's rows of this crawl are replaced), an append
(every run's rows are kept) or an upsert (one row per record, updated in place).
"""
import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List

from my_scraper.utils.timeutil import utc_now_iso

TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
VOLATILE = {"_meta", "source"}


class DatabaseError(Exception):
    """The database export could not be set up."""


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
                          .encode("utf-8", "replace")).hexdigest()


def _connect(url: str):
    """(connection, parameter mark)"""
    if url.startswith("sqlite:///"):
        return sqlite3.connect(url[len("sqlite:///"):], timeout=30), "?"
    if url.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
        except ImportError as e:
            raise DatabaseError("PostgreSQL needs psycopg: pip install 'psycopg[binary]'") from e
        return psycopg.connect(url), "%s"
    raise DatabaseError("storage.database_url must begin with sqlite:/// or postgresql://")


def deliver(items: List[Dict[str, Any]], url: str, table: str, mode: str, key_fields: List[str], crawl: str,
            run_id: str) -> int:
    """Write records in one transaction, so readers see the whole delivery or none of it. Returns rows written."""
    if not TABLE_NAME.fullmatch(table):
        raise DatabaseError(f"not a table name: {table!r}")
    now = utc_now_iso()
    rows: Dict[str, tuple] = {}
    for number, item in enumerate(items):
        identity = [crawl, *(str(item.get(name)) for name in key_fields)]
        if not any(item.get(name) for name in key_fields):
            identity.append(str(number))  # nothing names it, so it cannot replace another
        if mode == "append":
            identity.append(run_id)
        key = _sha(identity)
        content = {name: value for name, value in item.items() if name not in VOLATILE}
        if key in rows and rows[key][3] != _sha(content):
            key = _sha([*identity, _sha(content)])  # rows of one table share a URL and are separate records
        data = json.dumps(item, ensure_ascii=False, default=str)
        rows[key] = (key, crawl, run_id, _sha(content), data.encode("utf-8", "replace").decode("utf-8"), now, now)

    connection, mark = _connect(url)
    try:
        cursor = connection.cursor()
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} (record_key TEXT PRIMARY KEY, crawl TEXT, run_id TEXT, "
                       "content_hash TEXT, data TEXT, first_seen TEXT, last_seen TEXT)")
        if mode == "snapshot":
            cursor.execute(f"DELETE FROM {table} WHERE crawl = {mark}", (crawl,))
        cursor.executemany(
            f"INSERT INTO {table} VALUES ({', '.join([mark] * 7)}) ON CONFLICT (record_key) DO UPDATE SET "
            "run_id = excluded.run_id, content_hash = excluded.content_hash, data = excluded.data, "
            "last_seen = excluded.last_seen", list(rows.values()))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)
