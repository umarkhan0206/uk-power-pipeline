"""Tests for the source registry."""

from ukpower.config import load_settings
from ukpower.ingestion.landing import SAFE_NAME
from ukpower.ingestion.sources import build_sources


def test_all_nine_sources_are_built() -> None:
    assert len(build_sources(load_settings())) == 9


def test_source_names_are_unique() -> None:
    names = [s.name for s in build_sources(load_settings())]
    assert len(names) == len(set(names))


def test_source_names_are_safe_folder_names() -> None:
    # Names become landing zone folders, so they must pass the landing zone's own check.
    for source in build_sources(load_settings()):
        assert SAFE_NAME.fullmatch(source.name), source.name
