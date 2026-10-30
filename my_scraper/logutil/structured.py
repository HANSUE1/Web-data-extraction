"""Structured JSON logging with audit trail."""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import uuid


class StructuredLogger:
    """Production-grade structured logger."""
    
    def __init__(self, name: str, log_file: str = None, audit_log: str = None, json_format: bool = True):
        self.name = name
        self.json_format = json_format
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        if json_format:
            console_handler.setFormatter(JsonFormatter())
        else:
            console_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
        self.logger.addHandler(console_handler)
        
        # File handler
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(JsonFormatter())
            self.logger.addHandler(file_handler)
        
        # Audit logger
        self.audit_logger = None
        if audit_log:
            Path(audit_log).parent.mkdir(parents=True, exist_ok=True)
            audit_file_handler = logging.FileHandler(audit_log)
            audit_file_handler.setFormatter(JsonFormatter())
            self.audit_logger = logging.getLogger(f"{name}.audit")
            self.audit_logger.setLevel(logging.INFO)
            self.audit_logger.handlers.clear()
            self.audit_logger.addHandler(audit_file_handler)
    
    def info(self, msg: str, **kwargs):
        self._log(self.logger.info, msg, **kwargs)
    
    def warning(self, msg: str, **kwargs):
        self._log(self.logger.warning, msg, **kwargs)
    
    def error(self, msg: str, **kwargs):
        self._log(self.logger.error, msg, **kwargs)
    
    def debug(self, msg: str, **kwargs):
        self._log(self.logger.debug, msg, **kwargs)
    
    def critical(self, msg: str, **kwargs):
        self._log(self.logger.critical, msg, **kwargs)
    
    def _log(self, log_method, msg: str, **kwargs):
        extra = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "logger": self.name,
            "message": msg,
            **kwargs
        }
        log_method(json.dumps(extra) if self.json_format else msg, extra={"structured": extra})
    
    def audit(self, action: str, url: str = None, data: Dict[str, Any] = None, **kwargs):
        """Log to audit trail for compliance."""
        if not self.audit_logger:
            return
        
        audit_entry = {
            "audit_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action,
            "url": url,
            "data_summary": data,
            **kwargs
        }
        self.audit_logger.info(json.dumps(audit_entry))


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logs."""
    
    def format(self, record):
        if hasattr(record, 'structured'):
            return json.dumps(record.structured)
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        })


# Request logging helper
def log_request(logger: StructuredLogger, url: str, method: str, status: int, 
                duration_ms: float, retry_count: int = 0, **kwargs):
    """Log HTTP request details as per requirements."""
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
