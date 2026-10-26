"""Load and validate project settings from config/settings.yaml.

All tunable values live in the YAML file. This module turns that file into a
typed, read-only Settings object and fails fast with a clear error if any value
is missing, misspelled or out of range.
"""

from __future__ import annotations

import os
from datetime import date, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
CONFIG_ENV_VAR = "UKPOWER_CONFIG"

# An https URL with no trailing slash, so f"{base_url}/path" never doubles the slash.
URL_PATTERN = r"^https://\S+[^/]$"


class StrictModel(BaseModel):
    """Base model: unknown keys are errors, and settings cannot change after loading."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class HttpSettings(StrictModel):
    """Timeouts, retries and rate limiting shared by every API client."""

    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0, le=10)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)
    requests_per_second: float = Field(gt=0, le=10)

    @model_validator(mode="after")
    def _check_backoff_order(self) -> HttpSettings:
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be at least backoff_base_seconds")
        return self


class SettledPricesSettings(StrictModel):
    """How far back the settled prices look for newer settlement runs."""

    #: About 14 months, so the final run (RF) is caught. 0 turns the long look back off.
    long_refetch_days: int = Field(default=430, ge=0, le=500)
    #: Which day of the week does the long look back. Monday = 0, Sunday = 6.
    long_refetch_weekday: int = Field(default=6, ge=0, le=6)


class ElexonSettings(StrictModel):
    """Elexon Insights API settings."""

    base_url: str = Field(pattern=URL_PATTERN)
    # At least 2, because system prices are reissued about 24.7 hours later.
    refetch_days: int = Field(ge=2, le=14)
    settled_prices: SettledPricesSettings = Field(default_factory=SettledPricesSettings)


class Location(StrictModel):
    """One weather point. Bounds roughly cover Great Britain and its seas, to catch typos."""

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    group: Literal["offshore_wind", "onshore_wind", "solar", "demand"]
    lat: float = Field(ge=49.0, le=61.0)
    lon: float = Field(ge=-9.0, le=3.0)


class OpenMeteoSettings(StrictModel):
    """Open-Meteo Single Runs API settings."""

    base_url: str = Field(pattern=URL_PATTERN)
    model: str = Field(min_length=1)
    run_hour_utc: Literal[0, 6, 12, 18]
    hourly_variables: list[str] = Field(min_length=1)
    locations: list[Location] = Field(min_length=1)
    #: Days of forecast to request from the run start. The target day ends at most
    #: 48 h after the run starts, so 2 is the minimum; 3 leaves a margin.
    forecast_days: int = Field(default=3, ge=2, le=16)
    #: When a run is missing or incomplete, try earlier runs (every 6 h) up to this far back.
    #: 0 turns the fallback off.
    max_fallback_hours: int = Field(default=48, ge=0, le=72)

    @field_validator("locations")
    @classmethod
    def _check_unique_names(cls, locations: list[Location]) -> list[Location]:
        names = [loc.name for loc in locations]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate location names: {duplicates}")
        return locations


class CarbonIntensitySettings(StrictModel):
    """National Grid ESO Carbon Intensity API settings."""

    base_url: str = Field(pattern=URL_PATTERN)


class ForecastSettings(StrictModel):
    """Walk forward testing and baselines for the price forecaster."""

    #: Gold table exported from Databricks. Relative paths start at the project root.
    gold_file: Path = Path("data/gold/price_features.parquet")
    #: First month that is forecast. Everything before it is the first training set.
    test_start: date = date(2025, 10, 1)
    #: Training only uses target days that ended at least this many days before a retrain.
    label_gap_days: int = Field(default=2, ge=0, le=14)
    #: Days before the decision used by the "typical price for this time of day" baseline.
    profile_days: int = Field(default=28, ge=7, le=365)


class Settings(StrictModel):
    """All project settings."""

    timezone: str
    decision_time_local: time
    data_dir: Path
    backfill_start_date: date
    http: HttpSettings
    elexon: ElexonSettings
    open_meteo: OpenMeteoSettings
    carbon_intensity: CarbonIntensitySettings
    forecast: ForecastSettings = Field(default_factory=ForecastSettings)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    @field_validator("backfill_start_date")
    @classmethod
    def _check_backfill_not_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError(f"backfill_start_date {value} is in the future")
        return value

    @property
    def tz(self) -> ZoneInfo:
        """The market timezone as a ZoneInfo object."""
        return ZoneInfo(self.timezone)

    @property
    def data_path(self) -> Path:
        """Absolute data directory. Relative paths are resolved from the project root."""
        return self.data_dir if self.data_dir.is_absolute() else PROJECT_ROOT / self.data_dir

    @property
    def gold_path(self) -> Path:
        """Absolute path of the Gold export. Relative paths are resolved from the project root."""
        path = self.forecast.gold_file
        return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate settings.

    Path priority: the ``path`` argument, then the UKPOWER_CONFIG environment
    variable, then config/settings.yaml in the project root.

    Raises:
        FileNotFoundError: if the settings file does not exist.
        ValueError: if the file is empty or not a YAML mapping.
        pydantic.ValidationError: if any value fails validation.
    """
    config_path = path or Path(os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH))
    if not config_path.is_file():
        raise FileNotFoundError(f"settings file not found: {config_path}")

    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"settings file must contain a YAML mapping: {config_path}")

    return Settings.model_validate(raw)
