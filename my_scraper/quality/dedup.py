"""Find records already stored, in this run or an earlier one.

Short records are compared exactly, link included: two products may share a title and price. Long
texts are compared with SimHash, so an article republished with a new byline is still recognised.

A run's records are only remembered for later runs once they are published. A run that crashes or
is quarantined leaves no trace, so its records are not mistaken for duplicates when it runs again.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from my_scraper.storage.etag_cache import connect

BITS = 64
BANDS = 4            # with at most 3 differing bits, one of 4 bands must match exactly
MAX_DISTANCE = 3
MIN_WORDS = 50
SHINGLE = 3
NOT_CONTENT = {"source", "source_url", "_meta"}


def simhash(text: str) -> int:
    words = re.findall(r"\w+", text.lower())
    totals = [0] * BITS
    for i in range(max(len(words) - SHINGLE + 1, 1)):
        shingle = " ".join(words[i:i + SHINGLE]).encode()
        digest = int.from_bytes(hashlib.blake2b(shingle, digest_size=BITS // 8).digest(), "big")
        for bit in range(BITS):
            totals[bit] += 1 if digest >> bit & 1 else -1
    return sum(1 << bit for bit, total in enumerate(totals) if total > 0)


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _bands(value: int) -> List[str]:
    width = BITS // BANDS
    return [f"{i}:{value >> (i * width) & ((1 << width) - 1):x}" for i in range(BANDS)]


class Deduplicator:
    def __init__(self, db_path: str, across_runs: bool):
        self.db_path, self.across_runs = db_path, across_runs
        self.repeats_earlier_run = False  # whether the last duplicate found is from an earlier run
        self._new: Dict[str, Tuple[Optional[int], str]] = {}
        self._new_bands: Dict[str, List[str]] = {}
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with connect(db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, simhash TEXT, url TEXT, at REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS bands (band TEXT, key TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS bands_band ON bands (band)")

    def duplicate_of(self, item: Dict[str, Any]) -> Optional[str]:
        """The URL of the record this one repeats, or None. A new record is noted."""
        content = {name: value for name, value in item.items() if name not in NOT_CONTENT}
        key = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
        text = str(item.get("text") or item.get("markdown") or "")
        fingerprint = simhash(text) if len(text.split()) >= MIN_WORDS else None

        self.repeats_earlier_run = False
        if key in self._new:
            return self._new[key][1]
        found = self._near(fingerprint) if fingerprint is not None else None
        if found is None and self.across_runs:
            found = self._stored(key, fingerprint)
            self.repeats_earlier_run = found is not None
        if found is not None:
            return found

        self._new[key] = (fingerprint, str(item.get("url")))
        for band in _bands(fingerprint) if fingerprint is not None else ():
            self._new_bands.setdefault(band, []).append(key)
        return None

    def _near(self, fingerprint: int) -> Optional[str]:
        for band in _bands(fingerprint):
            for key in self._new_bands.get(band, ()):
                other, url = self._new[key]
                if distance(other, fingerprint) <= MAX_DISTANCE:
                    return url
        return None

    def _stored(self, key: str, fingerprint: Optional[int]) -> Optional[str]:
        with connect(self.db_path) as db:
            row = db.execute("SELECT url FROM seen WHERE key = ?", (key,)).fetchone()
            if row:
                return row[0]
            for band in _bands(fingerprint) if fingerprint is not None else ():
                for other, url in db.execute("SELECT s.simhash, s.url FROM bands b JOIN seen s ON s.key = b.key "
                                             "WHERE b.band = ?", (band,)):
                    if distance(int(other, 16), fingerprint) <= MAX_DISTANCE:
                        return url
        return None

    def commit(self):
        """Remember this run's records for later runs. Call once they are published."""
        with connect(self.db_path) as db:
            for key, (fingerprint, url) in self._new.items():
                stored = db.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)", (
                    key, None if fingerprint is None else f"{fingerprint:016x}", url, time.time())).rowcount
                if stored and fingerprint is not None:
                    db.executemany("INSERT INTO bands VALUES (?,?)", [(band, key) for band in _bands(fingerprint)])
