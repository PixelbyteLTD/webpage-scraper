"""Structured JSON logging (Pino-compatible).

Provides structured logging with JSON output suitable for production use.
Log lines are written as newline-delimited JSON (NDJSON) to allow
easy parsing by log aggregation tools.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ------------------------------------------------------------------
# Pino-style JSON formatter
# ------------------------------------------------------------------


class PinoFormatter(logging.Formatter):
    """Formatter that emits NDJSON (newline-delimited JSON) like Pino."""

    # Pino's default timestamp format uses ISO 8601 with epoch ms
    # but we keep ISO strings for readability
    _LEVEL_MAP = {
        logging.DEBUG: 20,    # "debug"
        logging.INFO: 30,     # "info"
        logging.WARNING: 40,  # "warn"
        logging.ERROR: 50,    # "error"
        logging.CRITICAL: 60, # "fatal"
    }

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self._use_color = use_color and self._term_supports_color()
        self._levels = {"DEBUG": "debug", "INFO": "info", "WARNING": "warn",
                        "ERROR": "error", "CRITICAL": "fatal"}
        # ANSI colour codes keyed by level name
        self._colors: dict[str, str] = {
            "DEBUG": "\033[36m",    # cyan
            "INFO": "\033[32m",     # green
            "WARNING": "\033[33m",  # yellow
            "ERROR": "\033[31m",    # red
            "CRITICAL": "\033[1;31m",  # bold red
        }

    @staticmethod
    def _term_supports_color() -> bool:
        """Return True if the terminal appears to support ANSI colours."""
        if not hasattr(sys.stdout, "isatty"):
            return False
        return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
             f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
        levelname = record.levelname
        level = self._LEVEL_MAP.get(record.levelno, 30)

        msg = record.getMessage()
        extra: dict[str, Any] = {}

        # Attach structured context if present — if ctx is a dict, spread it
        ctx_val: Any = None
        if hasattr(record, "ctx"):
            ctx_val = getattr(record, "ctx")
            if isinstance(ctx_val, dict):
                extra.update(ctx_val)
            else:
                extra["ctx"] = ctx_val

        # Also accept top-level ctx keys for backwards compatibility
        for key in ("url", "file", "page", "error_type", "retry"):
            if hasattr(record, key) and key not in extra:
                extra[key] = getattr(record, key)

        # Attach exception info as a structured error object
        if record.exc_info and record.exc_info[0] is not None:
            extra["error_type"] = record.exc_info[0].__name__
            extra["stack_trace"] = traceback.format_exception(*record.exc_info)
            extra["error_message"] = str(record.exc_info[1])

        # Include any extra fields added via logger.*(..., extra={...})
        for k in ("msg", "context", "page_num", "total_pages"):
            if hasattr(record, k) and k not in extra:
                extra[k] = getattr(record, k)

        obj: dict[str, Any] = {
            "v": 1,
            "level": level,
            "time": ts,
            "msg": msg,
        }
        if extra:
            obj["ctx"] = extra

        if self._use_color:
            color = self._colors.get(levelname, "")
            reset = "\033[0m"
            json_str = json.dumps(obj, ensure_ascii=False, default=str)
            return f"{color}{json_str}{reset}"

        return json.dumps(obj, ensure_ascii=False, default=str)


# ------------------------------------------------------------------
# Logger configuration helpers
# ------------------------------------------------------------------


def get_logger(
    name: str = "sitescrape",
    level: int = logging.INFO,
    json: bool = True,
    logfile: str | None = None,
) -> logging.Logger:
    """Return a configured logger.

    Parameters
    ----------
    name:
        Logger name (usually the module name).
    level:
        Logging level (e.g. logging.DEBUG, logging.INFO).
    json:
        If True, emit JSON (Pino-style) to stdout.
    logfile:
        If provided, also write structured logs to *logfile*.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    fmt = PinoFormatter(use_color=True)

    # Stdout handler (JSON)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Optional file handler
    if logfile:
        log_path = Path(logfile)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(PinoFormatter(use_color=False))
        logger.addHandler(fh)

    return logger


# ------------------------------------------------------------------
# Convenience helpers
# ------------------------------------------------------------------

# Module-level default logger (lazy)
_default_logger: logging.Logger | None = None


def _default() -> logging.Logger:
    global _default_logger
    if _default_logger is None:
        _default_logger = get_logger()
    return _default_logger


def info(msg: str, /, **kwargs: Any) -> None:
    _default().info(msg, extra=kwargs)


def warn(msg: str, /, **kwargs: Any) -> None:
    _default().warning(msg, extra=kwargs)


def error(msg: str, /, **kwargs: Any) -> None:
    _default().error(msg, exc_info=True, extra=kwargs)


def debug(msg: str, /, **kwargs: Any) -> None:
    _default().debug(msg, extra=kwargs)


def fatal(msg: str, /, **kwargs: Any) -> None:
    _default().critical(msg, exc_info=True, extra=kwargs)


# ------------------------------------------------------------------
# Log-sink for the scrape report
# ------------------------------------------------------------------

class ScrapeReportHandler(logging.Handler):
    """Captures log records so a summary report can be written at shutdown."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def write_report(
        self,
        output_path: Path,
        stats: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        """Write a structured JSON scrape report."""
        report: dict[str, Any] = {
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "stats": stats,
            "log_summary": {
                "total_records": len(self.records),
                "levels": {
                    level: sum(
                        1 for r in self.records
                        if r.levelno == getattr(logging, level, 0)
                    )
                    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
                },
            },
            "errors": errors or [],
        }
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
