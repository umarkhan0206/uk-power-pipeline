"""Tests for the ingestion command line tool. No network, no real data folder."""

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from ukpower.config import CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH
from ukpower.ingestion import cli
from ukpower.ingestion.http_client import FetchResult, HttpClient
from ukpower.ingestion.sources.base import (
    FetchedResponse,
    ResponseValidationError,
    Source,
    SourceRequest,
)
from ukpower.logging_setup import _HANDLER_TAG


class PretendSource(Source):
    """Lands one tiny response per day, or fails on chosen days."""

    def __init__(self, name: str, fail_on: set[date] | None = None) -> None:
        self.name = name
        self.fail_on = fail_on or set()
        self.fetched: list[date] = []

    def requests_for_day(self, day: date) -> list[SourceRequest]:
        return [SourceRequest(partition=day.isoformat(), url="https://api.example.test/x")]

    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        return 1

    def fetch_day(self, client: HttpClient, day: date) -> list[FetchedResponse]:
        self.fetched.append(day)
        if day in self.fail_on:
            raise ResponseValidationError("pretend failure")
        request = self.requests_for_day(day)[0]
        result = FetchResult(request.url, 200, datetime.now(UTC), b'{"data": []}')
        return [FetchedResponse(request=request, result=result, record_count=1)]


@pytest.fixture(autouse=True)
def temp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the tool at a copy of the real settings, with data in a temporary folder."""
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    raw["data_dir"] = str(tmp_path / "data")
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def remove_log_handlers() -> Iterator[None]:
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()


def use_sources(monkeypatch: pytest.MonkeyPatch, *sources: Source) -> None:
    monkeypatch.setattr(cli, "build_sources", lambda settings: list(sources))


BACKFILL = ["backfill", "--start", "2025-01-01", "--end", "2025-01-03"]


# Input checks


def test_start_with_incremental_is_refused() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["incremental", "--start", "2025-01-01"])


def test_unknown_mode_is_refused() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["everything"])


def test_unknown_source_name_lists_real_names() -> None:
    with pytest.raises(ValueError, match="unknown sources .*known"):
        cli.select_sources([PretendSource("real_one")], "real_onee")


def test_selected_sources_keep_requested_order() -> None:
    a, b = PretendSource("a"), PretendSource("b")
    assert cli.select_sources([a, b], "b,a") == [b, a]


@pytest.mark.parametrize(
    "argv",
    [
        ["incremental", "--force"],
        ["backfill", "--force"],
        ["backfill", "--start", "2025-01-01", "--force"],
    ],
)
def test_force_needs_backfill_with_start_and_end(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(argv)


# Running


def test_dry_run_fetches_nothing(monkeypatch: pytest.MonkeyPatch, temp_settings: Path) -> None:
    source = PretendSource("pretend")
    use_sources(monkeypatch, source)

    assert cli.main([*BACKFILL, "--dry-run"]) == 0
    assert source.fetched == []
    assert not (temp_settings / "landing").exists()


def test_successful_backfill_returns_0_and_lands_data(
    monkeypatch: pytest.MonkeyPatch, temp_settings: Path
) -> None:
    source = PretendSource("pretend")
    use_sources(monkeypatch, source)

    assert cli.main(BACKFILL) == 0
    assert source.fetched == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    assert len(list((temp_settings / "landing" / "pretend").iterdir())) == 3


def test_any_failure_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    use_sources(monkeypatch, PretendSource("pretend", fail_on={date(2025, 1, 2)}))
    assert cli.main(BACKFILL) == 1


def test_backfill_skips_days_already_landed(monkeypatch: pytest.MonkeyPatch) -> None:
    first = PretendSource("pretend")
    use_sources(monkeypatch, first)
    cli.main(BACKFILL)

    second = PretendSource("pretend")
    use_sources(monkeypatch, second)
    cli.main(BACKFILL)

    assert second.fetched == []


def test_force_backfill_fetches_landed_days_again(monkeypatch: pytest.MonkeyPatch) -> None:
    first = PretendSource("pretend")
    use_sources(monkeypatch, first)
    cli.main(BACKFILL)

    forced = PretendSource("pretend")
    use_sources(monkeypatch, forced)
    assert cli.main([*BACKFILL, "--force"]) == 0

    assert forced.fetched == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
