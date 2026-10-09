"""Logging setup shared by every part of the pipeline.

Call setup_logging() once at program start. Every module then gets its logger
with logging.getLogger(__name__) and never configures handlers itself.
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOG_FILE_NAME = "ukpower.log"
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 5  # keep the 5 most recent files

# Marks handlers we added, so calling setup_logging() twice does not duplicate output.
_HANDLER_TAG = "_ukpower_handler"


class UTCFormatter(logging.Formatter):
    """Formatter that writes timestamps in UTC, matching how data is stored."""

    converter = time.gmtime


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Send logs to the console and to a rotating file in log_dir.

    Safe to call more than once: earlier handlers added by this function are
    replaced, not duplicated.

    Args:
        log_dir: Folder for the log file. Created if missing.
        level: Minimum level to record, for example "INFO" or "DEBUG".

    Raises:
        ValueError: if level is not a known logging level name.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unknown log level: {level!r}")

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = UTCFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()

    for handler in (console, file_handler):
        setattr(handler, _HANDLER_TAG, True)
        root.addHandler(handler)
    root.setLevel(numeric_level)

    # The HTTP library logs every connection at INFO, which drowns out our own messages.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
