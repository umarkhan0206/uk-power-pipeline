"""Tests for ukpower.config: the real settings load, and bad settings are rejected."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from ukpower.config import (
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    load_settings,
)


@pytest.fixture
def raw_settings() -> dict[str, Any]:
    """A fresh copy of the real settings file as a plain dictionary."""
    with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as f:
        return copy.deepcopy(yaml.safe_load(f))


def write_settings(raw: dict[str, Any], folder: Path) -> Path:
    """Write a settings dictionary to a temporary YAML file and return its path."""
    path = folder / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


# Good settings


def test_real_settings_file_loads() -> None:
    settings = load_settings()
    assert settings.decision_time_local.hour == 10
    assert settings.timezone == "Europe/London"
    assert len(settings.open_meteo.locations) == 12
    assert settings.elexon.refetch_days >= 2


def test_relative_data_dir_resolves_from_project_root() -> None:
    settings = load_settings()
    assert settings.data_path == PROJECT_ROOT / "data"


def test_env_var_selects_settings_file(
    raw_settings: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_settings["decision_time_local"] = "19:00"
    path = write_settings(raw_settings, tmp_path)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert load_settings().decision_time_local.hour == 19


# Bad settings must be rejected


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "does_not_exist.yaml")


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_settings(path)


def test_misspelled_key_is_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    raw_settings["elexon"]["refetch_dayz"] = raw_settings["elexon"].pop("refetch_days")
    with pytest.raises(ValidationError):
        load_settings(write_settings(raw_settings, tmp_path))


def test_impossible_decision_time_is_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    raw_settings["decision_time_local"] = "25:00"
    with pytest.raises(ValidationError):
        load_settings(write_settings(raw_settings, tmp_path))


def test_unknown_timezone_is_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    raw_settings["timezone"] = "Europe/Leeds"
    with pytest.raises(ValidationError, match="unknown timezone"):
        load_settings(write_settings(raw_settings, tmp_path))


def test_refetch_days_below_two_is_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    raw_settings["elexon"]["refetch_days"] = 1
    with pytest.raises(ValidationError):
        load_settings(write_settings(raw_settings, tmp_path))


def test_url_with_trailing_slash_is_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    raw_settings["elexon"]["base_url"] += "/"
    with pytest.raises(ValidationError):
        load_settings(write_settings(raw_settings, tmp_path))


def test_swapped_coordinates_are_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    loc = raw_settings["open_meteo"]["locations"][0]
    loc["lat"], loc["lon"] = loc["lon"], loc["lat"]
    with pytest.raises(ValidationError):
        load_settings(write_settings(raw_settings, tmp_path))


def test_duplicate_location_names_are_rejected(
    raw_settings: dict[str, Any], tmp_path: Path
) -> None:
    locations = raw_settings["open_meteo"]["locations"]
    locations.append(dict(locations[0]))
    with pytest.raises(ValidationError, match="duplicate location names"):
        load_settings(write_settings(raw_settings, tmp_path))


def test_backfill_start_in_future_is_rejected(raw_settings: dict[str, Any], tmp_path: Path) -> None:
    raw_settings["backfill_start_date"] = "2999-01-01"
    with pytest.raises(ValidationError, match="in the future"):
        load_settings(write_settings(raw_settings, tmp_path))


def test_settings_cannot_be_changed_after_loading() -> None:
    settings = load_settings()
    with pytest.raises(ValidationError):
        settings.elexon.refetch_days = 99  # type: ignore[misc]
