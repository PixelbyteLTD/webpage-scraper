"""Graceful shutdown handling for the scraper.

Sets up signal handlers so the scraper saves its current state
before exiting on SIGTERM/SIGINT, preventing corruption of
the manifest and partial output files.
"""

from __future__ import annotations

import atexit
import json
import signal
import threading
from pathlib import Path
from typing import Any, Callable

from sitescrape.logger import get_logger

logger = get_logger("sitescrape.shutdown")

# ------------------------------------------------------------------
# Global shutdown flag
# ------------------------------------------------------------------

_shutdown_requested = threading.Event()


def is_shutdown_requested() -> bool:
    """Return True if a shutdown signal has been received."""
    return _shutdown_requested.is_set()


# ------------------------------------------------------------------
# Shutdown handler registration
# ------------------------------------------------------------------

_shutdown_handlers: list[Callable[[], None]] = []
_lock = threading.Lock()


def register_handler(fn: Callable[[], None]) -> None:
    """Register a cleanup function to be called at shutdown."""
    with _lock:
        _shutdown_handlers.append(fn)


def run_shutdown_handlers() -> None:
    """Execute all registered shutdown handlers."""
    with _lock:
        handlers = list(_shutdown_handlers)

    for fn in handlers:
        try:
            fn()
        except Exception:
            logger.error(
                "Error during shutdown handler %s", fn,
                exc_info=True,
                extra={"ctx": "shutdown_handler"},
            )


# ------------------------------------------------------------------
# Signal handling
# ------------------------------------------------------------------

def install_signal_handlers() -> None:
    """Install SIGINT / SIGTERM handlers that set the shutdown flag
    and run cleanup handlers before exiting.
    """

    def _handler(signum: int, _frame: Any) -> None:
        _shutdown_requested.set()
        logger.info(
            "Shutdown signal %d received — flushing state …",
            signum,
            extra={"ctx": "shutdown", "signal": signum},
        )
        run_shutdown_handlers()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    # Also register via atexit as a fallback
    atexit.register(run_shutdown_handlers)


# ------------------------------------------------------------------
# Progress saver (for manifest / temporary files)
# ------------------------------------------------------------------

class ProgressSaver:
    """Atomic save of the crawl manifest so partial state is never lost.

    Writes to a .tmp file first, then renames — on SIGINT/SIGTERM the
    tmp file is cleaned up or kept as the final manifest depending on
    whether the save completed.
    """

    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self._tmp_path = manifest_path.with_suffix(".tmp")
        self._data: dict[str, Any] | None = None

    def save(self, data: dict[str, Any]) -> None:
        """Atomically save *data* as JSON to manifest_path."""
        self._data = data
        try:
            self._tmp_path.write_text(
                data, encoding="utf-8",
            )
            self._tmp_path.replace(self.manifest_path)
        except Exception:
            # If we can't save during shutdown, keep the tmp file
            # so the user can inspect it later
            logger.error(
                "Failed to save manifest — tmp file left at %s",
                self._tmp_path,
                extra={"ctx": "progress_saver"},
            )

    def save_partial(self, data: str) -> None:
        """Write partial state without atomic rename (called during shutdown)."""
        try:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(data, encoding="utf-8")
        except Exception:
            logger.error(
                "Failed partial save to %s", self.manifest_path,
                extra={"ctx": "progress_saver"},
            )
