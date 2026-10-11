"""Tests for the structured JSON logger (Pino-compatible)."""

import json
import logging
import tempfile
from pathlib import Path

from sitescrape.logger import (
    ScrapeReportHandler,
    get_logger,
    PinoFormatter,
    error,
    info,
)


def test_pino_formatter_basic():
    """A log line should be valid JSON with level and timestamp."""
    fmt = PinoFormatter(use_color=False)
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py",
        lineno=1, msg="Hello world", args=(), exc_info=None,
    )
    line = fmt.format(record)
    parsed = json.loads(line)
    assert parsed["level"] == 30  # info
    assert parsed["msg"] == "Hello world"
    assert "time" in parsed


def test_pino_formatter_with_ctx():
    """Extra context fields should appear in the 'ctx' key."""
    fmt = PinoFormatter(use_color=False)
    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname="test.py",
        lineno=1, msg="Slow request", args=(), exc_info=None,
    )
    record.ctx = {"url": "http://example.com", "latency_ms": 2500}
    line = fmt.format(record)
    parsed = json.loads(line)
    assert parsed["ctx"]["url"] == "http://example.com"


def test_pino_formatter_with_exception():
    """Exception info should be serialized as structured error fields."""
    fmt = PinoFormatter(use_color=False)
    try:
        raise ValueError("bad value")
    except ValueError as exc:
        import traceback
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="test.py",
            lineno=1, msg="Crash", args=(), exc_info=(type(exc), exc, exc.__traceback__),
        )
        line = fmt.format(record)
        parsed = json.loads(line)
        assert parsed["ctx"]["error_type"] == "ValueError"
        assert "bad value" in parsed["ctx"]["error_message"]


def test_get_logger_json_output():
    """get_logger should return a logger emitting JSON."""
    log_file = Path(tempfile.mkdtemp()) / "test.log"
    logger = get_logger("test_logger", level=logging.DEBUG, logfile=str(log_file))
    assert isinstance(logger.handlers[0].formatter, PinoFormatter)


def test_scrape_report_handler():
    """ScrapeReportHandler should collect records and write a report."""
    handler = ScrapeReportHandler()
    handler.setLevel(logging.DEBUG)

    # Simulate some records
    for i in range(5):
        handler.emit(logging.LogRecord(
            name="test", level=logging.INFO if i < 3 else logging.ERROR,
            pathname="t.py", lineno=1,
            msg=f"msg {i}", args=(), exc_info=None,
        ))

    report_path = Path(tempfile.mkdtemp()) / "report.json"
    handler.write_report(
        report_path,
        stats={"saved": 3, "failed": 1},
        errors=[{"url": "http://bad.com", "error_message": "timeout"}],
    )

    report = json.loads(report_path.read_text())
    assert report["report_version"] == 1
    assert report["stats"]["saved"] == 3
    assert report["log_summary"]["levels"]["INFO"] == 3
    assert report["log_summary"]["levels"]["ERROR"] == 2
    assert len(report["errors"]) == 1
    assert report["errors"][0]["error_message"] == "timeout"
