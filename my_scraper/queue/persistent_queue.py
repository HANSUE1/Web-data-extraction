"""Persistent queue with Redis and disk backends."""
import asyncio
import json
import os
import sqlite3
import hashlib
import time
from typing import Optional, List, Dict, Any, Deque
from collections import deque
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
from urllib.parse import urlparse


@dataclass
class QueueItem:
    """Item in the crawl queue."""
    url: str
    depth: int = 0
    priority: int = 0
    retries: int = 0
    added_at: float = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.added_at is None:
            self.added_at = time.time()
        if self.metadata is None:
            self.metadata = {}


class DiskQueue:
    """Disk-based persistent queue using SQLite."""
    
    def __init__(self, db_path: str = "./data/queue.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    depth INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 0,
                    retries INTEGER DEFAULT 0,
                    added_at REAL NOT NULL,
                    metadata TEXT,
                    status TEXT DEFAULT 'pending',
                    UNIQUE(url)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_priority ON queue(status, priority DESC, added_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON queue(url)")
            conn.commit()
    
    def push(self, item: QueueItem) -> bool:
        """Add item to queue. Returns False if duplicate."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO queue (url, depth, priority, retries, added_at, metadata, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                    (item.url, item.depth, item.priority, item.retries, item.added_at, 
                     json.dumps(item.metadata))
                )
                conn.commit()
                return conn.total_changes > 0
        except Exception:
            return False
    
    def pop(self) -> Optional[QueueItem]:
        """Pop highest priority item."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """SELECT id, url, depth, priority, retries, added_at, metadata
                   FROM queue WHERE status = 'pending'
                   ORDER BY priority DESC, added_at ASC
                   LIMIT 1"""
            )
            row = cursor.fetchone()
            if row:
                item_id, url, depth, priority, retries, added_at, metadata = row
                conn.execute("UPDATE queue SET status = 'processing' WHERE id = ?", (item_id,))
                conn.commit()
                return QueueItem(
                    url=url, depth=depth, priority=priority, retries=retries,
                    added_at=added_at, metadata=json.loads(metadata) if metadata else {}
                )
            return None
    
    def mark_done(self, url: str):
        """Mark item as completed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE queue SET status = 'done' WHERE url = ?", (url,))
            conn.commit()
    
    def mark_failed(self, url: str, retries: int, max_retries: int):
        """Mark item as failed, requeue if retries remain."""
        with sqlite3.connect(self.db_path) as conn:
            if retries < max_retries:
                conn.execute(
                    "UPDATE queue SET status = 'pending', retries = ? WHERE url = ?",
                    (retries, url)
                )
            else:
                conn.execute("UPDATE queue SET status = 'failed' WHERE url = ?", (url,))
            conn.commit()
    
    def size(self, status: str = None) -> int:
        """Get queue size."""
        with sqlite3.connect(self.db_path) as conn:
            if status:
                return conn.execute("SELECT COUNT(*) FROM queue WHERE status = ?", (status,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
    
    def clear(self, status: str = None):
        """Clear queue."""
        with sqlite3.connect(self.db_path) as conn:
            if status:
                conn.execute("DELETE FROM queue WHERE status = ?", (status,))
            else:
                conn.execute("DELETE FROM queue")
            conn.commit()


class RedisQueue:
    """Redis-backed distributed queue."""
    
    def __init__(self, redis_url: str = "redis://localhost:6379", namespace: str = "crawler"):
        self.redis_url = redis_url
        self.namespace = namespace
        self._redis = None
    
    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as redis
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
        return self._redis
    
    def _key(self, *parts) -> str:
        return f"{self.namespace}:{':'.join(parts)}"
    
    async def push(self, item: QueueItem) -> bool:
        """Push item to queue using sorted set for priority."""
        redis = await self._get_redis()
        score = -item.priority * 1000000 + item.added_at  # Negative priority for max-heap
        member = json.dumps({
            "url": item.url,
            "depth": item.depth,
            "retries": item.retries,
            "metadata": item.metadata
        })
        return await redis.zadd(self._key("queue"), {member: score})
    
    async def pop(self) -> Optional[QueueItem]:
        """Pop highest priority item."""
        redis = await self._get_redis()
        # Use Lua script for atomic pop
        lua = """
        local items = redis.call('ZRANGE', KEYS[1], 0, 0)
        if #items > 0 then
            redis.call('ZREM', KEYS[1], items[1])
            return items[1]
        end
        return nil
        """
        result = await redis.eval(lua, 1, self._key("queue"))
        if result:
            data = json.loads(result)
            return QueueItem(**data)
        return None
    
    async def mark_done(self, url: str):
        await self._get_redis().sadd(self._key("done"), url)
    
    async def mark_failed(self, url: str, retries: int, max_retries: int):
        if retries < max_retries:
            await self.push(QueueItem(url=url, depth=0, retries=retries))
        else:
            await self._get_redis().sadd(self._key("failed"), url)
    
    async def size(self) -> int:
        return await self._get_redis().zcard(self._key("queue"))


class HybridQueue:
    """Hybrid queue with disk persistence and Redis distribution."""
    
    def __init__(self, config):
        self.config = config
        self.disk_queue = DiskQueue(config.get("queue_db", "./data/queue.db"))
        self.redis_queue = None
        if config.get("redis_url"):
            self.redis_queue = RedisQueue(config["redis_url"])
        self._local_buffer: Deque = deque()
        self._buffer_size = 100
    
    def push(self, item: QueueItem) -> bool:
        """Push to local buffer, flush when full."""
        self._local_buffer.append(item)
        if len(self._local_buffer) >= self._buffer_size:
            self._flush()
        return True
    
    def _flush(self):
        while self._local_buffer:
            item = self._local_buffer.popleft()
            self.disk_queue.push(item)
    
    def pop(self) -> Optional[QueueItem]:
        # Try local buffer first
        if self._local_buffer:
            return self._local_buffer.popleft()
        return self.disk_queue.pop()
    
    def mark_done(self, url: str):
        self.disk_queue.mark_done(url)
    
    def mark_failed(self, url: str, retries: int, max_retries: int):
        self.disk_queue.mark_failed(url, retries, max_retries)
    
    def size(self) -> int:
        return self.disk_queue.size("pending") + len(self._local_buffer)
    
    async def close(self):
        self._flush()
        if self.redis_queue and self.redis_queue._redis:
            await self.redis_queue._redis.close()


class ETagCache:
    """ETag/Last-Modified cache for incremental crawling."""
    
    def __init__(self, db_path: str = "./data/etag_cache.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()
    
    def get_headers(self, url: str) -> Dict[str, str]:
        """Get conditional headers for URL."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT etag, last_modified FROM etag_cache WHERE url = ?", (url,)
            ).fetchone()
            if row:
                headers = {}
                if row[0]:
                    headers["If-None-Match"] = row[0]
                if row[1]:
                    headers["If-Modified-Since"] = row[1]
                return headers
        return {}
    
    def update(self, url: str, response_headers: Dict, content: str = None):
        """Update cache with response headers."""
        etag = response_headers.get("ETag") or response_headers.get("etag")
        last_modified = response_headers.get("Last-Modified") or response_headers.get("last-modified")
        content_length = response_headers.get("Content-Length") or response_headers.get("content-length")
        
        content_hash = None
        if content:
            content_hash = hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO etag_cache 
                (url, etag, last_modified, content_hash, fetched_at, content_length)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (url, etag, last_modified, content_hash, time.time(), content_length))
            conn.commit()
    
    def is_unchanged(self, url: str, response_status: int) -> bool:
        """Check if 304 Not Modified."""
        return response_status == 304
    
    def get_content_hash(self, url: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT content_hash FROM etag_cache WHERE url = ?", (url,)).fetchone()
            return row[0] if row else None


class ContentDeduplicator:
    """Deduplicate content using content hashes."""
    
    def __init__(self, db_path: str = "./data/dedup.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_hashes (
                    hash TEXT PRIMARY KEY,
                    first_url TEXT,
                    first_seen REAL,
                    count INTEGER DEFAULT 1
                )
            """)
            conn.commit()
    
    def is_duplicate(self, content: str) -> bool:
        """Check if content is duplicate."""
        content_hash = hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()
        
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT count FROM content_hashes WHERE hash = ?", (content_hash,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE content_hashes SET count = count + 1 WHERE hash = ?",
                    (content_hash,)
                )
                conn.commit()
                return True
            
            conn.execute(
                "INSERT INTO content_hashes (hash, first_url, first_seen, count) VALUES (?, ?, ?, 1)",
                (content_hash, "", time.time())
            )
            conn.commit()
            return False
    
    def get_stats(self) -> Dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM content_hashes").fetchone()[0]
            duplicates = conn.execute("SELECT SUM(count - 1) FROM content_hashes").fetchone()[0] or 0
            return {"unique": total, "duplicates": duplicates}


class QueueManager:
    """Unified queue manager."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        if config.get("redis_url"):
            self.queue = RedisQueue(config["redis_url"])
        else:
            self.queue = DiskQueue(config.get("queue_db", "./data/queue.db"))
        self.etag_cache = ETagCache(config.get("etag_db", "./data/etag_cache.db"))
        self.dedup = ContentDeduplicator(config.get("dedup_db", "./data/dedup.db"))
    
    def push(self, item) -> bool:
        return self.queue.push(item)
    
    def pop(self):
        return self.queue.pop()
    
    def mark_done(self, url: str):
        self.queue.mark_done(url)
    
    def mark_failed(self, url: str, retries: int, max_retries: int):
        self.queue.mark_failed(url, retries, self.config.get("max_retries", 3))
    
    def size(self) -> int:
        return self.queue.size()
    
    async def close(self):
        if hasattr(self.queue, 'close'):
            await self.queue.close()