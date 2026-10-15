"""Structured (JSON-lines) logging. Detailed diagnostics - including tracebacks - go to a rotating log file;
the GUI only ever shows short human-readable messages."""
import json
import logging
import logging.handlers
import os
import time

from .platform_info import log_dir

ROOT = "engineering_console"
LOG_FILE = "engineering-console.log"
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
             "level": record.levelname, "logger": record.name, "event": record.getMessage()}
        d.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            d["traceback"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


def setup(path=None, level=logging.INFO):
    """Idempotent. Never raises: if the log file cannot be created, logging simply stays silent."""
    global _configured
    log = logging.getLogger(ROOT)
    if _configured:
        return log
    log.setLevel(level)
    log.propagate = False
    try:
        path = path or os.path.join(log_dir(), LOG_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
    except OSError:
        log.addHandler(logging.NullHandler())
    _configured = True
    return log


def event(logger, name, level=logging.INFO, exc_info=False, **fields):
    """log_event(log, "speedtest.failed", reason="timeout", endpoint="...")  ->  one JSON line."""
    logger.log(level, name, extra={"fields": fields}, exc_info=exc_info)
