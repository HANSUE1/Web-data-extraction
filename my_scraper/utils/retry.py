"""Retry logic with exponential backoff and dead-letter queue."""
import asyncio
import random
import time
from typing import Any, Callable, Optional, List
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RetryContext:
    """Context for retry attempts."""
    url: str
    attempt: int = 0
    last_error: Optional[Exception] = None
    last_status: Optional[int] = None
    started_at: datetime = None
    
    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.utcnow()


class DeadLetterQueue:
    """Dead-letter queue for failed URLs."""
    
    def __init__(self, dlq_file: str = "./data/dead_letter_queue.jsonl"):
        self.dlq_file = dlq_file
        import os
        os.makedirs(os.path.dirname(dlq_file), exist_ok=True)
    
    def add(self, context: RetryContext, reason: str):
        """Add failed URL to DLQ."""
        import json
        entry = {
            "url": context.url,
            "attempts": context.attempt,
            "last_error": str(context.last_error) if context.last_error else None,
            "last_status": context.last_status,
            "failed_at": datetime.utcnow().isoformat() + "Z",
            "reason": reason
        }
        with open(self.dlq_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def get_all(self):
        """Retrieve all DLQ entries."""
        import json
        entries = []
        try:
            with open(self.dlq_file, 'r') as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
        except FileNotFoundError:
            pass
        return entries


class RetryManager:
    """Retry logic with exponential backoff."""
    
    def __init__(self, config):
        self.max_retries = config.retry.max_retries
        self.base_delay = config.retry.base_delay
        self.max_delay = config.retry.max_delay
        self.exponential_base = config.retry.exponential_base
        self.jitter = config.retry.jitter
        self.retry_on_status = config.retry.retry_on_status
        self.dlq = DeadLetterQueue()
    
    def should_retry(self, context: RetryContext, exception: Exception = None, status: int = None) -> bool:
        """Determine if request should be retried."""
        if context.attempt >= self.max_retries:
            return False
        
        if status is not None and status in self.retry_on_status:
            return True
        
        if exception is not None and self.retry_on_status:
            return True
        
        return False
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter."""
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            # Add jitter: ±25%
            jitter_range = delay * 0.25
            delay = delay + random.uniform(-jitter_range, jitter_range)
            delay = max(0, delay)
        
        return delay
    
    async def retry_with_backoff(self, func: Callable, context: RetryContext, *args, **kwargs):
        """Execute function with retry logic."""
        while context.attempt <= self.max_retries:
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                context.last_error = e
                context.attempt += 1
                
                if not self.should_retry(context, exception=e):
                    self.dlq.add(context, f"Max retries exceeded: {e}")
                    raise
                
                delay = self.get_delay(context.attempt - 1)
                await asyncio.sleep(delay)
        
        self.dlq.add(context, "Max retries exceeded")
        raise Exception(f"Failed after {self.max_retries} retries: {context.url}")
    
    async def retry_http(self, fetch_func: Callable, url: str, **kwargs):
        """Retry HTTP requests specifically."""
        context = RetryContext(url=url)
        
        while context.attempt <= self.max_retries:
            try:
                response = await fetch_func(url, **kwargs)
                context.last_status = getattr(response, 'status_code', None) or getattr(response, 'status', None)
                
                if context.last_status in self.retry_on_status and context.attempt < self.max_retries:
                    context.attempt += 1
                    delay = self.get_delay(context.attempt - 1)
                    await asyncio.sleep(delay)
                    continue
                
                if context.last_status in self.retry_on_status:
                    self.dlq.add(context, f"HTTP {context.last_status}")
                    raise Exception(f"HTTP {context.last_status} for {url}")
                
                return response
            except Exception as e:
                context.last_error = e
                context.attempt += 1
                
                if context.attempt > self.max_retries:
                    self.dlq.add(context, f"Exception: {e}")
                    raise
                
                if self.should_retry(context, exception=e):
                    delay = self.get_delay(context.attempt - 1)
                    await asyncio.sleep(delay)
                    continue
                else:
                    self.dlq.add(context, f"Non-retryable exception: {e}")
                    raise
        
        self.dlq.add(context, "Max retries exceeded")
        raise Exception(f"Failed after {self.max_retries} retries: {url}")