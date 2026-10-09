"""Tests for the append only landing zone."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ukpower.ingestion.http_client import FetchResult
from ukpower.ingestion.landing import LandingZone

SOURCE = "elexon_system_prices"
PARTITION = "2026-10-06"


def make_result(content: bytes, second: int = 0) -> FetchResult:
    """A fake response fetched at a fixed time, so file names are predictable."""
    return FetchResult(
        url="https://api.example.test/prices?format=json",
        status_code=200,
        fetched_at=datetime(2026, 10, 9, 20, 0, second, 123456, tzinfo=UTC),
        content=content,
    )


@pytest.fixture
def zone(tmp_path: Path) -> LandingZone:
    return LandingZone(tmp_path / "landing")


def test_content_is_stored_byte_for_byte(zone: LandingZone) -> None:
    content = b'{"data": [{"price": 415.21}]}\n'
    landed = zone.write(SOURCE, PARTITION, make_result(content), {"format": "json"})

    assert landed.data_path.read_bytes() == content
    assert landed.sha256 == hashlib.sha256(content).hexdigest()


def test_metadata_describes_the_fetch(zone: LandingZone) -> None:
    content = b'{"data": []}'
    landed = zone.write(SOURCE, PARTITION, make_result(content), {"format": "json"})
    meta = json.loads(landed.meta_path.read_text(encoding="utf-8"))

    assert meta["source"] == SOURCE
    assert meta["partition"] == PARTITION
    assert meta["params"] == {"format": "json"}
    assert meta["status_code"] == 200
    assert meta["fetched_at"] == "2026-10-09T20:00:00.123456+00:00"
    assert meta["sha256"] == landed.sha256
    assert meta["size_bytes"] == len(content)


def test_file_name_starts_with_utc_fetch_time_and_fingerprint(zone: LandingZone) -> None:
    landed = zone.write(SOURCE, PARTITION, make_result(b"abc"))
    assert landed.data_path.name == f"20261009T200000123456Z_{landed.sha256[:8]}.json"


def test_repeated_fetches_are_all_kept_oldest_first(zone: LandingZone) -> None:
    first = zone.write(SOURCE, PARTITION, make_result(b"version 1", second=1))
    second = zone.write(SOURCE, PARTITION, make_result(b"version 2", second=2))

    assert zone.list_data_files(SOURCE, PARTITION) == [first.data_path, second.data_path]


def test_overwrite_is_refused_and_original_survives(zone: LandingZone) -> None:
    result = make_result(b"original")
    landed = zone.write(SOURCE, PARTITION, result)

    with pytest.raises(FileExistsError):
        zone.write(SOURCE, PARTITION, result)

    assert landed.data_path.read_bytes() == b"original"


def test_no_temporary_files_are_left_behind(zone: LandingZone) -> None:
    zone.write(SOURCE, PARTITION, make_result(b"abc"))
    assert list(zone.root.rglob("*.tmp")) == []


@pytest.mark.parametrize("bad_name", ["../evil", "a/b", "a\\b", "..", "", "a..b"])
def test_unsafe_source_names_are_rejected(zone: LandingZone, bad_name: str) -> None:
    with pytest.raises(ValueError, match="unsafe source"):
        zone.write(bad_name, PARTITION, make_result(b"abc"))


@pytest.mark.parametrize("bad_name", ["../2026-10-06", "2026/10/06", ""])
def test_unsafe_partition_names_are_rejected(zone: LandingZone, bad_name: str) -> None:
    with pytest.raises(ValueError, match="unsafe partition"):
        zone.write(SOURCE, bad_name, make_result(b"abc"))


def test_colons_in_partition_are_removed_from_folder_name(zone: LandingZone) -> None:
    landed = zone.write("open_meteo_ecmwf", "2026-10-04T00:00", make_result(b"abc"))
    meta = json.loads(landed.meta_path.read_text(encoding="utf-8"))

    assert landed.data_path.parent.name == "2026-10-04T0000"
    assert meta["partition"] == "2026-10-04T00:00"


def test_listing_excludes_metadata_files(zone: LandingZone) -> None:
    zone.write(SOURCE, PARTITION, make_result(b"abc"))
    files = zone.list_data_files(SOURCE)

    assert len(files) == 1
    assert not files[0].name.endswith(".meta.json")


def test_listing_unknown_source_returns_empty(zone: LandingZone) -> None:
    assert zone.list_data_files("never_fetched") == []
