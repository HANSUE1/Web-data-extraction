"""Retry logic with exponential backoff and dead-letter queue."""
import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from my_scraper.utils.timeutil import utc_now, utc_now_iso


class HttpStatusError(Exception):
    """A URL kept returning a retryable HTTP status after all retries."""

    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url


@dataclass
class RetryContext:
    """Context for retry attempts."""
    url: str
    attempt: int = 0  # attempts that have failed so far
    last_error: Optional[Exception] = None
    last_status: Optional[int] = None
    started_at: datetime = field(default_factory=utc_now)


class DeadLetterQueue:
    """Dead-letter queue for failed URLs."""

    def __init__(self, dlq_file: str = "./data/dead_letter_queue.jsonl"):
        self.dlq_file = dlq_file
        Path(dlq_file).parent.mkdir(parents=True, exist_ok=True)

    def add(self, context: RetryContext, reason: str):
        """Add failed URL to DLQ."""
        entry = {
            "url": context.url,
            "attempts": context.attempt,
            "last_error": str(context.last_error) if context.last_error else None,
            "last_status": context.last_status,
            "failed_at": utc_now_iso(),
            "reason": reason,
        }
        with open(self.dlq_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def get_all(self) -> List[Dict[str, Any]]:
        """Retrieve all DLQ entries."""
        try:
            with open(self.dlq_file, "r", encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []


class RetryManager:
    """Retry logic with exponential backoff."""

    def __init__(self, config):
        self.max_retries = config.retry.max_retries
        self.base_delay = config.retry.base_delay
        self.max_delay = config.retry.max_delay
        self.exponential_base = config.retry.exponential_base
        self.jitter = config.retry.jitter
        self.retry_on_status = config.retry.retry_on_status
        self.retry_on_exceptions = config.retry.retry_on_exceptions
        self.dlq = DeadLetterQueue()

    def should_retry(self, context: RetryContext, exception: Exception = None, status: int = None) -> bool:
        """Determine if a failed attempt should be retried."""
        if context.attempt > self.max_retries:
            return False
        if status is not None:
            return status in self.retry_on_status
        return exception is not None and self.retry_on_exceptions

    def get_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter."""
        delay = min(self.base_delay * (self.exponential_base ** attempt), self.max_delay)
        if self.jitter:
            jitter_range = delay * 0.25  # ±25%
            delay = max(0, delay + random.uniform(-jitter_range, jitter_range))
        return delay

    async def retry_http(self, fetch_func: Callable, url: str,
                         on_status: Optional[Callable[[int], None]] = None, **kwargs) -> Dict[str, Any]:
        """Fetch url, retrying up to max_retries times after the first attempt.

        on_status, when given, is called with every HTTP status received. Statuses
        outside retry_on_status (a 404, say) are returned for the caller to handle.
        Raises HttpStatusError when a retryable status outlasts the retries, or the
        last exception when fetching keeps failing.
        """
        context = RetryContext(url=url)

        while True:
            try:
                response = await fetch_func(url, **kwargs)
            except Exception as e:
                context.last_error = e
                context.attempt += 1
                if not self.should_retry(context, exception=e):
                    reason = "Max retries exceeded" if self.retry_on_exceptions else "Non-retryable exception"
                    self.dlq.add(context, f"{reason}: {e}")
                    raise
            else:
                status = response.get("status_code")
                context.last_status = status
                if on_status is not None:
                    on_status(status)
                if status not in self.retry_on_status:
                    return response
                context.attempt += 1
                if not self.should_retry(context, status=status):
                    self.dlq.add(context, f"HTTP {status}")
                    raise HttpStatusError(status, url)

            await asyncio.sleep(self.get_delay(context.attempt - 1))
