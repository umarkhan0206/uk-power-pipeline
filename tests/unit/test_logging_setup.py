"""Tests for ukpower.logging_setup."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from ukpower.logging_setup import (
    _HANDLER_TAG,
    DATE_FORMAT,
    LOG_FILE_NAME,
    UTCFormatter,
    setup_logging,
)


@pytest.fixture(autouse=True)
def remove_our_handlers() -> Iterator[None]:
    """Detach and close handlers added by setup_logging after each test."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()


def test_messages_are_written_to_log_file(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    logging.getLogger("ukpower.test").info("hello file")

    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "hello file" in content
    assert "INFO" in content


def test_log_dir_is_created_if_missing(tmp_path: Path) -> None:
    log_dir = tmp_path / "nested" / "logs"
    setup_logging(log_dir)
    assert log_dir.is_dir()


def test_calling_setup_twice_does_not_duplicate_messages(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    setup_logging(tmp_path)
    logging.getLogger("ukpower.test").info("only once")

    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert content.count("only once") == 1


def test_level_filters_lower_messages(tmp_path: Path) -> None:
    setup_logging(tmp_path, level="WARNING")
    log = logging.getLogger("ukpower.test")
    log.info("hidden")
    log.warning("shown")

    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "hidden" not in content
    assert "shown" in content


def test_unknown_level_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        setup_logging(tmp_path, level="LOUD")


def test_timestamps_are_utc() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    record.created = 0  # the Unix epoch: 1970-01-01 00:00:00 UTC
    assert UTCFormatter().formatTime(record, DATE_FORMAT) == "1970-01-01T00:00:00Z"


def test_urllib3_noise_is_reduced(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    assert logging.getLogger("urllib3").level == logging.WARNING
