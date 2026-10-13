"""Command line entry point for ingestion.

Examples:
    python -m ukpower.ingestion.cli incremental --dry-run
    python -m ukpower.ingestion.cli backfill --start 2025-01-01 --end 2025-01-31
    python -m ukpower.ingestion.cli incremental --sources elexon_system_prices
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime

from ukpower.config import PROJECT_ROOT, load_settings
from ukpower.ingestion.http_client import HttpClient
from ukpower.ingestion.landing import LandingZone
from ukpower.ingestion.planner import plan_days
from ukpower.ingestion.runner import RunSummary, run
from ukpower.ingestion.sources import build_sources
from ukpower.ingestion.sources.base import Source
from ukpower.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ukpower-ingest", description=__doc__)
    parser.add_argument("mode", choices=["incremental", "backfill"])
    parser.add_argument("--sources", help="comma separated source names (default: all)")
    parser.add_argument("--start", type=date.fromisoformat, help="backfill only: first day")
    parser.add_argument("--end", type=date.fromisoformat, help="backfill only: last day")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, fetch nothing")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    if args.mode == "incremental" and (args.start or args.end):
        parser.error("--start and --end are only for backfill")
    return args


def select_sources(all_sources: list[Source], names: str | None) -> list[Source]:
    """Pick sources by name, or all of them."""
    if not names:
        return all_sources
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    known = {s.name: s for s in all_sources}
    unknown = sorted(set(wanted) - set(known))
    if unknown:
        raise ValueError(f"unknown sources {unknown}; known: {sorted(known)}")
    return [known[n] for n in wanted]


def main(argv: Sequence[str] | None = None) -> int:
    """Run ingestion. Returns 0 if everything landed, 1 if anything failed."""
    args = parse_args(argv)
    settings = load_settings()
    setup_logging(PROJECT_ROOT / "logs", args.log_level)

    sources = select_sources(build_sources(settings), args.sources)
    landing = LandingZone(settings.data_path / "landing")
    now = datetime.now(UTC)
    start = args.start or settings.backfill_start_date
    refetch_days = settings.elexon.refetch_days if args.mode == "incremental" else 0

    plans: list[tuple[Source, list[date]]] = []
    for source in sources:
        latest = source.latest_available_day(now, settings.tz)
        if args.end:
            latest = min(latest, args.end)
        days = plan_days(
            source, landing.landed_partitions(source.name), start, latest, refetch_days
        )
        plans.append((source, days))
        first, last = (days[0], days[-1]) if days else (None, None)
        logger.info("Plan %s: %d days (%s to %s)", source.name, len(days), first, last)

    if args.dry_run:
        logger.info("Dry run: nothing fetched")
        return 0

    summary = RunSummary()
    with HttpClient(settings.http) as client:
        for source, days in plans:
            part = run([source], days, client, landing)
            summary.outcomes.extend(part.outcomes)
            summary.seconds += part.seconds
    summary.log()
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
