"""Immutable record of every fetch, and of what the parser made of it.

A snapshot holds all the parser needs (HTML, rendered states, captured JSON, file bytes), so any
page can be parsed again months later without a request to the site. Rows are never updated, which
SQLite enforces with a trigger, and blobs are named by their hash, so a changed one is noticed.
"""
import base64
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from my_scraper.storage.blobs import open_blobs

# Session cookies are credentials, and nothing here needs them.
PRIVATE_HEADERS = {"set-cookie", "cookie", "authorization", "proxy-authorization"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, final_url TEXT, fetched_at REAL NOT NULL, status INTEGER,
    headers TEXT, fetcher TEXT, content_hash TEXT NOT NULL, blob TEXT NOT NULL, screenshot TEXT,
    run_id TEXT, job_id TEXT);
CREATE INDEX IF NOT EXISTS snapshots_url ON snapshots (url, fetched_at);
CREATE INDEX IF NOT EXISTS snapshots_run ON snapshots (run_id);
CREATE TABLE IF NOT EXISTS extractions (
    id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, schema_version TEXT,
    spec TEXT NOT NULL, items_blob TEXT NOT NULL, item_count INTEGER, errors TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS extractions_snapshot ON extractions (snapshot_id, created_at);
CREATE TRIGGER IF NOT EXISTS snapshots_immutable BEFORE UPDATE ON snapshots
    BEGIN SELECT RAISE(ABORT, 'snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS extractions_immutable BEFORE UPDATE ON extractions
    BEGIN SELECT RAISE(ABORT, 'extractions are immutable'); END;
"""


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", "replace")


class SnapshotStore:
    def __init__(self, state_dir: str, blob_location: Optional[str] = None):
        root = Path(state_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = str(root / "snapshots.db")
        self.blobs = open_blobs(blob_location or str(root / "blobs"))
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")  # safe with WAL
        try:
            with db:
                yield db
        finally:
            db.close()

    # --- Writing ----------------------------------------------------------------

    def save(self, url: str, result: Dict[str, Any], run_id: str = "", job_id: str = "") -> str:
        """Keep one fetch. Returns its snapshot id."""
        headers = {name: value for name, value in (result.get("headers") or {}).items()
                   if name.lower() not in PRIVATE_HEADERS}
        content = result.get("content") or ""
        bundle: Dict[str, Any] = {"content": content}
        if result.get("body") and "html" not in headers.get("content-type", "html").lower():
            bundle["body"] = base64.b64encode(result["body"]).decode("ascii")
        for name in ("snapshots", "json_payloads"):
            if result.get(name):
                bundle[name] = result[name]
        screenshot = self.blobs.put(result["screenshot"]) if result.get("screenshot") else None
        content_hash = hashlib.sha256(content.encode("utf-8", "replace") or result.get("body") or b"").hexdigest()
        snapshot_id = f"snap_{uuid.uuid4().hex}"
        row = (snapshot_id, url, result.get("final_url") or url, time.time(), result.get("status_code"),
               json.dumps(headers), result.get("fetcher_type"), content_hash, self.blobs.put(_dump(bundle)),
               screenshot, run_id, job_id)
        with self._db() as db:
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
        return snapshot_id

    def save_extraction(self, snapshot_id: str, items: List[Dict[str, Any]], spec: Dict[str, Any],
                        parser_version: str, schema_version: str = "", errors: Iterable[str] = ()):
        row = (f"ext_{uuid.uuid4().hex}", snapshot_id, parser_version, schema_version,
               json.dumps(spec, sort_keys=True, ensure_ascii=False), self.blobs.put(_dump(items)), len(items),
               json.dumps(list(errors)), time.time())
        with self._db() as db:
            db.execute("INSERT INTO extractions VALUES (?,?,?,?,?,?,?,?,?)", row)

    # --- Reading ----------------------------------------------------------------

    def find(self, url: Optional[str] = None, host: Optional[str] = None, run_id: Optional[str] = None,
             snapshot_id: Optional[str] = None, since: Optional[float] = None, latest: bool = False,
             limit: int = -1) -> List[Dict[str, Any]]:
        """Matching snapshots, newest first. latest keeps only the newest of each URL."""
        where, values = ["1=1"], []
        for clause, value in (("url = ?", url), ("run_id = ?", run_id), ("id = ?", snapshot_id),
                              ("fetched_at >= ?", since)):
            if value is not None:
                where.append(clause)
                values.append(value)
        if host:
            where.append("(url LIKE ? OR url LIKE ?)")
            values += [f"%://{host}/%", f"%://%.{host}/%"]
        if latest:
            where.append("fetched_at = (SELECT max(s.fetched_at) FROM snapshots s WHERE s.url = snapshots.url)")
        query = f"SELECT * FROM snapshots WHERE {' AND '.join(where)} ORDER BY fetched_at DESC LIMIT ?"
        with self._db() as db:
            return [dict(row) for row in db.execute(query, (*values, limit))]

    def fetch_result(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """The snapshot as the fetcher returned it, ready for the parser."""
        raw = self.blobs.get(snapshot["blob"])
        if raw is None:
            raise FileNotFoundError(f"blob {snapshot['blob']} of {snapshot['id']} is gone")
        bundle = json.loads(raw)
        if "body" in bundle:
            bundle["body"] = base64.b64decode(bundle["body"])
        return {"url": snapshot["url"], "final_url": snapshot["final_url"], "status_code": snapshot["status"],
                "headers": json.loads(snapshot["headers"] or "{}"), "fetcher_type": snapshot["fetcher"],
                "snapshot_id": snapshot["id"], **bundle}

    def extraction(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """What was extracted when the snapshot was crawled."""
        with self._db() as db:
            row = db.execute("SELECT * FROM extractions WHERE snapshot_id = ? ORDER BY created_at LIMIT 1",
                             (snapshot_id,)).fetchone()
        if row is None:
            return None
        items = self.blobs.get(row["items_blob"])
        return {**dict(row), "spec": json.loads(row["spec"]), "items": json.loads(items) if items else []}

    def history(self, url: str) -> List[Dict[str, Any]]:
        """When a URL was read and what it hashed to, oldest first."""
        with self._db() as db:
            return [dict(row) for row in db.execute(
                "SELECT fetched_at, content_hash FROM snapshots WHERE url = ? AND status < 400 ORDER BY fetched_at",
                (url,))]

    def count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT count(*) FROM snapshots").fetchone()[0]

    # --- Retention ----------------------------------------------------------------

    def prune(self, older_than_days: float) -> int:
        """Delete snapshots past the retention period, and blobs nothing refers to any more."""
        cutoff = time.time() - older_than_days * 86400
        with self._db() as db:
            old = db.execute("SELECT id, blob, screenshot FROM snapshots WHERE fetched_at < ?", (cutoff,)).fetchall()
            blobs = {value for row in old for value in (row["blob"], row["screenshot"]) if value}
            for row in old:
                blobs.update(r[0] for r in db.execute(
                    "SELECT items_blob FROM extractions WHERE snapshot_id = ?", (row["id"],)))
                db.execute("DELETE FROM extractions WHERE snapshot_id = ?", (row["id"],))
                db.execute("DELETE FROM snapshots WHERE id = ?", (row["id"],))
            unused = [blob for blob in blobs if not db.execute(
                "SELECT 1 FROM snapshots WHERE blob = ? OR screenshot = ? "
                "UNION SELECT 1 FROM extractions WHERE items_blob = ?", (blob, blob, blob)).fetchone()]
        for blob in unused:
            self.blobs.delete(blob)
        return len(old)
