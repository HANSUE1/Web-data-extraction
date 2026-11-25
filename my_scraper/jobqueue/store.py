"""The durable job queue: SQLite on one machine, Redis across several (see redis_store.py).

A job is leased to a worker, which keeps renewing the lease. A worker that dies stops renewing,
and the job goes to the next one. A job that uses up its attempts becomes a dead letter for a
person to look at. Nothing is lost by a crash at any moment.
"""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from my_scraper.config.settings import STATE_DIR
from my_scraper.storage.etag_cache import connect

STATES = ("queued", "running", "done", "dead", "cancelled")
DEFAULT_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, payload TEXT NOT NULL, priority INTEGER NOT NULL, state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL, run_after REAL NOT NULL,
    created REAL NOT NULL, finished REAL, worker TEXT, lease_until REAL, error TEXT, result TEXT, dedupe_key TEXT);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (state, priority DESC, created);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe ON jobs (dedupe_key) WHERE state IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS slots (domain TEXT PRIMARY KEY, next REAL NOT NULL);
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, expires REAL NOT NULL);
"""


@dataclass
class Job:
    id: str
    payload: Dict[str, Any]
    priority: int = 0
    state: str = "queued"
    attempts: int = 0
    max_attempts: int = DEFAULT_ATTEMPTS
    run_after: float = 0.0
    created: float = 0.0
    finished: Optional[float] = None
    worker: Optional[str] = None
    lease_until: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    dedupe_key: Optional[str] = None


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


class SQLiteQueue:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with connect(str(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")  # one writer at a time, so claims cannot race
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        values = dict(row)
        values["payload"] = json.loads(values["payload"])
        values["result"] = json.loads(values["result"]) if values["result"] else None
        return Job(**values)

    # --- Producers --------------------------------------------------------------

    def enqueue(self, payload: Dict[str, Any], priority: int = 0, delay: float = 0,
                max_attempts: int = DEFAULT_ATTEMPTS, dedupe_key: Optional[str] = None) -> str:
        """Add a job. One that is waiting or running under the same dedupe_key is returned instead."""
        now = time.time()
        with self._db(write=True) as db:
            if dedupe_key:
                row = db.execute("SELECT id FROM jobs WHERE dedupe_key = ? AND state IN ('queued', 'running')",
                                 (dedupe_key,)).fetchone()
                if row:
                    return row["id"]
            job_id = new_job_id()
            db.execute("INSERT INTO jobs (id, payload, priority, state, max_attempts, run_after, created, dedupe_key) "
                       "VALUES (?,?,?,'queued',?,?,?,?)",
                       (job_id, json.dumps(payload), priority, max_attempts, now + delay, now, dedupe_key))
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a waiting job, or a running one: its worker notices when it next renews its lease."""
        with self._db(write=True) as db:
            return db.execute("UPDATE jobs SET state='cancelled', finished=? WHERE id=? AND state IN ('queued','running')",
                              (time.time(), job_id)).rowcount > 0

    def retry(self, job_id: str) -> bool:
        """Give a dead or cancelled job a fresh set of attempts."""
        with self._db(write=True) as db:
            return db.execute("UPDATE jobs SET state='queued', attempts=0, run_after=?, finished=NULL, error=NULL "
                              "WHERE id=? AND state IN ('dead','cancelled')", (time.time(), job_id)).rowcount > 0

    # --- Workers ----------------------------------------------------------------

    def claim(self, worker: str, lease: float) -> Optional[Job]:
        """Take the most urgent job that is due."""
        now = time.time()
        with self._db(write=True) as db:
            db.execute("UPDATE jobs SET state = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END, "
                       "finished = CASE WHEN attempts >= max_attempts THEN ? END, error = 'worker stopped responding' "
                       "WHERE state = 'running' AND lease_until < ?", (now, now))
            row = db.execute("SELECT id FROM jobs WHERE state = 'queued' AND run_after <= ? "
                             "ORDER BY priority DESC, created LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running', worker=?, lease_until=?, attempts=attempts+1 WHERE id=?",
                       (worker, now + lease, row["id"]))
            return self._job(db.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone())

    def _if_mine(self, job: Job, assignments: str, values: tuple) -> bool:
        """Update a job only while this worker still holds it."""
        with self._db(write=True) as db:
            return db.execute(f"UPDATE jobs SET {assignments} WHERE id=? AND state='running' AND worker=?",
                              (*values, job.id, job.worker)).rowcount > 0

    def heartbeat(self, job: Job, lease: float) -> bool:
        """Renew a lease. False means the job was cancelled or given to another worker."""
        return self._if_mine(job, "lease_until=?", (time.time() + lease,))

    def complete(self, job: Job, result: Dict[str, Any]) -> bool:
        return self._if_mine(job, "state='done', finished=?, result=?, error=NULL",
                             (time.time(), json.dumps(result, default=str)))

    def fail(self, job: Job, error: str, retry_in: Optional[float], result: Optional[Dict[str, Any]] = None) -> str:
        """Put a job back for later, or make it a dead letter. retry_in None means never try again."""
        state = "dead" if retry_in is None or job.attempts >= job.max_attempts else "queued"
        now = time.time()
        self._if_mine(job, "state=?, finished=?, run_after=?, error=?, result=?",
                      (state, now if state == "dead" else None, now + (retry_in or 0), error[:2000],
                       json.dumps(result, default=str) if result else None))
        return state

    def release(self, job: Job) -> bool:
        """Hand a job back untouched, as when a worker is told to stop. It costs no attempt."""
        return self._if_mine(job, "state='queued', attempts=attempts-1, run_after=?", (time.time(),))

    # --- Shared between workers ---------------------------------------------------

    def reserve(self, domain: str, interval: float) -> float:
        """Reserve a site's next request slot. Returns the seconds to wait for it."""
        now = time.time()
        with self._db(write=True) as db:
            row = db.execute("SELECT next FROM slots WHERE domain = ?", (domain,)).fetchone()
            slot = max(now, row["next"] if row else 0.0)
            db.execute("INSERT OR REPLACE INTO slots VALUES (?,?)", (domain, slot + interval))
        return slot - now

    def seen_add(self, key: str, ttl: float = 86400 * 7) -> bool:
        """Remember a key. False when some worker already had it."""
        now = time.time()
        with self._db(write=True) as db:
            db.execute("DELETE FROM seen WHERE expires < ?", (now,))
            return db.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (key, now + ttl)).rowcount > 0

    # --- Reading ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list(self, state: Optional[str] = None, limit: int = 50) -> List[Job]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE ? IS NULL OR state = ? ORDER BY created DESC LIMIT ?",
                              (state, state, limit)).fetchall()
        return [self._job(row) for row in rows]

    def counts(self) -> Dict[str, int]:
        with self._db() as db:
            found = {row[0]: row[1] for row in db.execute("SELECT state, count(*) FROM jobs GROUP BY state")}
        return {state: found.get(state, 0) for state in STATES}

    def workers(self) -> List[str]:
        with self._db() as db:
            return [row[0] for row in db.execute(
                "SELECT DISTINCT worker FROM jobs WHERE state='running' AND lease_until > ?", (time.time(),))]

    def prune(self, older_than_days: float) -> int:
        """Forget finished jobs. Dead letters stay until someone has looked at them."""
        with self._db(write=True) as db:
            return db.execute("DELETE FROM jobs WHERE state IN ('done','cancelled') AND finished < ?",
                              (time.time() - older_than_days * 86400,)).rowcount


def open_queue(url: Optional[str] = None):
    """The queue at url, else $SCRAPER_QUEUE_URL, else a file in the state folder."""
    url = url or os.environ.get("SCRAPER_QUEUE_URL") or f"{STATE_DIR}/queue.db"
    if url.startswith(("redis://", "rediss://")):
        from my_scraper.jobqueue.redis_store import RedisQueue

        return RedisQueue(url)
    return SQLiteQueue(url.removeprefix("sqlite:///"))
