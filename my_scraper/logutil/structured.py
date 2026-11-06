"""Structured JSON logging."""
import json
import logging
import sys
from pathlib import Path

from my_scraper.utils.timeutil import utc_now_iso


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logs."""

    def format(self, record):
        entry = getattr(record, "structured", None) or {
            "timestamp": utc_now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(entry, default=str)


class StructuredLogger:
    """Logger that attaches key/value fields to every message."""

    def __init__(self, name: str, log_file: str = None, json_format: bool = True, level: str = "INFO",
                 console: bool = True):
        self.name = name
        self.json_format = json_format
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()

        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(
                JsonFormatter() if json_format
                else logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            self.logger.addHandler(console_handler)

        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(JsonFormatter())
            self.logger.addHandler(file_handler)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, kwargs)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, kwargs)

    def _log(self, level: int, msg: str, fields: dict):
        structured = {
            "timestamp": utc_now_iso(),
            "level": logging.getLevelName(level),
            "logger": self.name,
            "message": msg,
            **fields,
        }
        text = msg
        if fields:
            text += " " + " ".join(f"{key}={value}" for key, value in fields.items())
        self.logger.log(level, text, extra={"structured": structured})


def log_request(logger: StructuredLogger, url: str, method: str, status: int,
                duration_ms: float, retry_count: int = 0, **kwargs):
    """Log HTTP request details."""
    logger.info("http_request",
                url=url,
                method=method,
                status=status,
                duration_ms=round(duration_ms, 2),
                retry_count=retry_count,
                **kwargs)


def log_parsing(logger: StructuredLogger, url: str, items_extracted: int,
                errors: int = 0, **kwargs):
    """Log parsing results."""
    logger.info("parsing_complete",
                url=url,
                items_extracted=items_extracted,
                errors=errors,
                **kwargs)
