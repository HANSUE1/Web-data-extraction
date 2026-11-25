"""Every run on record: outcomes per minute, quality, and how it ended. Alerts, drift and the dashboard read it."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

OK = {"success", "no_data_extracted", "unchanged", "duplicate_content"}
# Pages a rule or the site's own wishes kept us from are not failures, and not successes either.
DECLINED = {"robots_disallowed", "robots_noindex", "blocked", "site_abandoned"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, job_id TEXT, crawl_key TEXT, site TEXT, started REAL, finished REAL, state TEXT,
    pages INTEGER, items INTEGER, success_ratio REAL, statuses TEXT, quality TEXT, parser_version TEXT, notes TEXT);
CREATE INDEX IF NOT EXISTS runs_key ON runs (crawl_key, started);
CREATE TABLE IF NOT EXISTS minutes (minute INTEGER PRIMARY KEY, ok INTEGER, failed INTEGER);
CREATE TABLE IF NOT EXISTS errors (at REAL, run_id TEXT, url TEXT, status TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS alerts (at REAL, rule TEXT, message TEXT);
"""
KEPT_ERRORS = 1000


def success_ratio(statuses: Dict[str, int]) -> Optional[float]:
    ok = sum(count for status, count in statuses.items() if status in OK)
    failed = sum(count for status, count in statuses.items() if status not in OK | DECLINED)
    return round(ok / (ok + failed), 4) if ok + failed else None


class History:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "history.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, and a page outcome is not worth a disk flush
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, run_id: str, job_id: str, crawl_key: str, site: str, parser_version: str):
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO runs (run_id, job_id, crawl_key, site, started, state, parser_version) "
                       "VALUES (?,?,?,?,?,'running',?)", (run_id, job_id, crawl_key, site, time.time(), parser_version))

    def finish(self, run_id: str, state: str, pages: int, items: int, statuses: Dict[str, int],
               quality: Dict[str, Any], notes: List[str]):
        with self._db() as db:
            db.execute("UPDATE runs SET finished=?, state=?, pages=?, items=?, success_ratio=?, statuses=?, quality=?, "
                       "notes=? WHERE run_id=?",
                       (time.time(), state, pages, items, success_ratio(statuses), json.dumps(statuses),
                        json.dumps(quality), json.dumps(notes), run_id))

    def outcome(self, run_id: str, url: str, status: str, error: Optional[str]):
        if status in DECLINED:
            return
        ok = status in OK
        with self._db() as db:
            db.execute("INSERT INTO minutes VALUES (?,?,?) ON CONFLICT (minute) DO UPDATE SET "
                       "ok = ok + excluded.ok, failed = failed + excluded.failed",
                       (int(time.time() // 60), int(ok), int(not ok)))
            if not ok:
                db.execute("INSERT INTO errors VALUES (?,?,?,?,?)", (time.time(), run_id, url, status, (error or "")[:500]))
                db.execute("DELETE FROM errors WHERE rowid <= (SELECT max(rowid) FROM errors) - ?", (KEPT_ERRORS,))

    def window(self, minutes: int) -> Dict[str, int]:
        first = int(time.time() // 60) - minutes + 1
        with self._db() as db:
            ok, failed = db.execute("SELECT coalesce(sum(ok),0), coalesce(sum(failed),0) FROM minutes WHERE minute >= ?",
                                    (first,)).fetchone()
        return {"ok": ok, "failed": failed}

    def runs(self, limit: int = 50, crawl_key: Optional[str] = None, state: Optional[str] = None) -> List[Dict[str, Any]]:
        where, values = ["1=1"], []
        for column, value in (("crawl_key", crawl_key), ("state", state)):
            if value is not None:
                where.append(f"{column} = ?")
                values.append(value)
        query = f"SELECT * FROM runs WHERE {' AND '.join(where)} ORDER BY started DESC LIMIT ?"
        with self._db() as db:
            rows = db.execute(query, (*values, limit)).fetchall()
        return [{**dict(row), **{name: json.loads(row[name] or "null") for name in ("statuses", "quality", "notes")}}
                for row in rows]

    def errors(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM errors ORDER BY at DESC LIMIT ?", (limit,))]

    def last_alert(self, rule: str) -> float:
        with self._db() as db:
            return db.execute("SELECT coalesce(max(at), 0) FROM alerts WHERE rule = ?", (rule,)).fetchone()[0]

    def add_alert(self, rule: str, message: str):
        with self._db() as db:
            db.execute("INSERT INTO alerts VALUES (?,?,?)", (time.time(), rule, message))

    def alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM alerts ORDER BY at DESC LIMIT ?", (limit,))]
