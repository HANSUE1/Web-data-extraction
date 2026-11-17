"""ETag, Last-Modified and content hashes from earlier runs, for --incremental crawls."""
import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection that commits on success and is always closed."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class ETagCache:
    """What each URL looked like last time, so an unchanged page can be skipped."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS etag_cache (
                    url TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    content_hash TEXT,
                    fetched_at REAL,
                    content_length INTEGER
                )
            """)

    def get_headers(self, url: str) -> Dict[str, str]:
        """Conditional request headers, so the server can answer 304 Not Modified."""
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT etag, last_modified FROM etag_cache WHERE url = ?", (url,)).fetchone()
        if not row:
            return {}
        headers = {}
        if row[0]:
            headers["If-None-Match"] = row[0]
        if row[1]:
            headers["If-Modified-Since"] = row[1]
        return headers

    def update(self, url: str, response_headers: Dict[str, str], content: Optional[str] = None):
        """Remember a response's validators and a hash of its body. Header names are lowercase."""
        content_hash = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest() if content else None
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO etag_cache "
                "(url, etag, last_modified, content_hash, fetched_at, content_length) VALUES (?, ?, ?, ?, ?, ?)",
                (url, response_headers.get("etag"), response_headers.get("last-modified"), content_hash,
                 time.time(), response_headers.get("content-length")),
            )

    def get_content_hash(self, url: str) -> Optional[str]:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT content_hash FROM etag_cache WHERE url = ?", (url,)).fetchone()
        return row[0] if row else None
