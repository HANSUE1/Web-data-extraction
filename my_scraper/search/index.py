"""Search what was crawled, and rank pages by how they link to each other.

Built from stored snapshots, so it never touches a site. Text is found two ways and the rankings are
fused: BM25 over words (SQLite FTS5), and cosine over hashed word and letter groups, which forgives
spelling and word forms. Link analysis (PageRank, HITS) breaks ties towards pages others point to.
"""
import hashlib
import math
import re
import sqlite3
from array import array
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from parsel import Selector

from my_scraper.core.links import base_url_of, page_links
from my_scraper.core.page_checks import VISIBLE_TEXT, is_html
from my_scraper.storage.snapshots import SnapshotStore
from my_scraper.utils.urls import join_url, normalize_url

# shortcut: vectors are hashed words and letter groups, not learned embeddings, so synonyms are not
# matched. Upgrade by replacing vector_of with an embedding model when meaning matters more than setup.
DIMENSIONS = 512
CANDIDATES = 100
FUSION = 60            # reciprocal rank fusion's usual constant
LINK_WEIGHT = 0.15
DAMPING = 0.85
ROUNDS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, snapshot_id TEXT, vector BLOB,
    pagerank REAL DEFAULT 0, hub REAL DEFAULT 0, authority REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS links (source INTEGER, target TEXT, PRIMARY KEY (source, target)) WITHOUT ROWID;
CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(title, body);
"""


def _features(text: str) -> Iterator[str]:
    for word in re.findall(r"\w+", text.lower()):
        yield word
        padded = f"<{word}>"
        for i in range(len(padded) - 2):
            yield padded[i:i + 3]


def vector_of(text: str) -> array:
    vector = array("f", [0.0]) * DIMENSIONS
    for feature, count in Counter(_features(text)).items():
        digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        vector[digest % DIMENSIONS] += (1 if digest >> 63 else -1) * (1 + math.log(count))
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return array("f", (value / length for value in vector))


def pagerank(graph: Dict[str, Set[str]]) -> Dict[str, float]:
    """How likely a visitor following links at random is to be on each page."""
    if not graph:
        return {}
    nodes = list(graph)
    rank = dict.fromkeys(nodes, 1 / len(nodes))
    for _ in range(ROUNDS):
        dangling = sum(rank[node] for node in nodes if not graph[node])
        fresh = dict.fromkeys(nodes, (1 - DAMPING + DAMPING * dangling) / len(nodes))
        for node, targets in graph.items():
            for target in targets:
                fresh[target] += DAMPING * rank[node] / len(targets)
        rank = fresh
    return rank


def hits(graph: Dict[str, Set[str]]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """(hubs, authorities): pages that point to good pages, and pages good pages point to."""
    hub = dict.fromkeys(graph, 1.0)
    authority = dict.fromkeys(graph, 1.0)

    def scaled(scores: Dict[str, float]) -> Dict[str, float]:
        length = math.sqrt(sum(score * score for score in scores.values())) or 1.0
        return {node: score / length for node, score in scores.items()}

    for _ in range(ROUNDS):
        pointed = dict.fromkeys(graph, 0.0)
        for node, targets in graph.items():
            for target in targets:
                pointed[target] += hub[node]
        authority = scaled(pointed)
        hub = scaled({node: sum(authority[target] for target in targets) for node, targets in graph.items()})
    return hub, authority


class SearchIndex:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def build(self, store: SnapshotStore) -> int:
        """Add pages whose newest snapshot is not indexed yet, then rank everything again."""
        added = 0
        with self._db() as db:
            known = {row["url"]: row["snapshot_id"] for row in db.execute("SELECT url, snapshot_id FROM pages")}
            for snapshot in store.find(latest=True):
                if snapshot["status"] >= 400 or known.get(snapshot["url"]) == snapshot["id"]:
                    continue
                result = store.fetch_result(snapshot)
                if is_html(result) and result["content"].strip():
                    self._add(db, snapshot, result)
                    added += 1
            if added:
                self._rank(db)
        return added

    @staticmethod
    def _add(db: sqlite3.Connection, snapshot: Dict, result: Dict):
        sel = Selector(text=result["content"], type="html")
        title = " ".join(sel.xpath("string(//title)").get(default="").split())
        body = " ".join(" ".join(sel.xpath(VISIBLE_TEXT).getall()).split())
        url = snapshot["url"]
        row = db.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()
        if row:
            for table, column in (("pages", "id"), ("words", "rowid"), ("links", "source")):
                db.execute(f"DELETE FROM {table} WHERE {column} = ?", (row["id"],))
        page_id = db.execute("INSERT INTO pages (url, title, snapshot_id, vector) VALUES (?,?,?,?)",
                             (url, title, snapshot["id"], vector_of(f"{title} {title} {body}").tobytes())).lastrowid
        db.execute("INSERT INTO words (rowid, title, body) VALUES (?,?,?)", (page_id, title, body))
        base = base_url_of(sel, result["final_url"])
        for href in page_links(sel):
            try:
                target = normalize_url(join_url(base, href.strip()))
            except ValueError:
                continue
            if target != url:
                db.execute("INSERT OR IGNORE INTO links VALUES (?,?)", (page_id, target))

    @staticmethod
    def graph(db: sqlite3.Connection) -> Dict[str, Set[str]]:
        """Links between indexed pages."""
        pages = {row["id"]: row["url"] for row in db.execute("SELECT id, url FROM pages")}
        graph: Dict[str, Set[str]] = {url: set() for url in pages.values()}
        for source, target in db.execute("SELECT source, target FROM links"):
            if target in graph:
                graph[pages[source]].add(target)
        return graph

    def _rank(self, db: sqlite3.Connection):
        graph = self.graph(db)
        ranks = pagerank(graph)
        hubs, authorities = hits(graph)
        db.executemany("UPDATE pages SET pagerank=?, hub=?, authority=? WHERE url=?",
                       [(ranks[url], hubs[url], authorities[url], url) for url in graph])

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        words = re.findall(r"\w+", query.lower())
        if not words:
            return []
        wanted = vector_of(query)
        with self._db() as db:
            # Each word quoted, so the query is words and never FTS syntax.
            match = " OR ".join(f'"{word}"' for word in words)
            by_words = [row[0] for row in db.execute(
                "SELECT rowid FROM words WHERE words MATCH ? ORDER BY bm25(words, 3.0, 1.0) LIMIT ?", (match, CANDIDATES))]
            # shortcut: compares with every page, fine to about 100,000. Beyond that use a vector database.
            by_vector = [page for _, page in sorted(
                ((sum(a * b for a, b in zip(wanted, array("f", row["vector"]), strict=True)), row["id"])
                 for row in db.execute("SELECT id, vector FROM pages")), reverse=True)[:CANDIDATES]]
            scores = self._fuse((by_words, by_vector))
            rows = {row["id"]: row for row in self._pages(db, scores)}
            top = max((row["pagerank"] for row in rows.values()), default=0) or 1.0
            ranked = sorted(scores, key=lambda page: -scores[page] * (1 + LINK_WEIGHT * rows[page]["pagerank"] / top))
            return [{"url": rows[page]["url"], "title": rows[page]["title"], "score": round(scores[page], 5),
                     "pagerank": round(rows[page]["pagerank"], 6), "snapshot_id": rows[page]["snapshot_id"],
                     "excerpt": self._excerpt(db, page, match if page in by_words else None)}
                    for page in ranked[:limit]]

    @staticmethod
    def _fuse(rankings: Iterable[List[int]]) -> Dict[int, float]:
        scores: Dict[int, float] = {}
        for ranking in rankings:
            for place, page in enumerate(ranking):
                scores[page] = scores.get(page, 0.0) + 1 / (FUSION + place + 1)
        return scores

    @staticmethod
    def _pages(db: sqlite3.Connection, ids: Iterable[int]) -> List[sqlite3.Row]:
        ids = list(ids)
        marks = ",".join("?" * len(ids))
        return db.execute(f"SELECT * FROM pages WHERE id IN ({marks})", ids).fetchall()

    @staticmethod
    def _excerpt(db: sqlite3.Connection, page: int, match: Optional[str]) -> str:
        if match is None:
            return (db.execute("SELECT substr(body, 1, 160) FROM words WHERE rowid = ?", (page,)).fetchone() or [""])[0]
        return db.execute("SELECT snippet(words, 1, '', '', ' … ', 24) FROM words WHERE words MATCH ? AND rowid = ?",
                          (match, page)).fetchone()[0]

    def top(self, by: str = "pagerank", limit: int = 20) -> List[Dict]:
        if by not in ("pagerank", "hub", "authority"):
            raise ValueError(f"rank by pagerank, hub or authority, not {by!r}")
        with self._db() as db:
            return [dict(row) for row in db.execute(
                f"SELECT url, title, pagerank, hub, authority FROM pages ORDER BY {by} DESC LIMIT ?", (limit,))]
