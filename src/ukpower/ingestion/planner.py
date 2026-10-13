"""Decide which days to fetch for a source. Pure: no file or network access."""

from __future__ import annotations

from datetime import date, timedelta

from ukpower.ingestion.sources.base import Source


def plan_days(
    source: Source,
    landed: set[str],
    start: date,
    latest: date,
    refetch_days: int,
) -> list[date]:
    """Days to fetch, oldest first.

    Args:
        source: The source being planned.
        landed: Partitions already in the landing zone, as ISO dates.
        start: First day ever wanted (the backfill start).
        latest: Latest day whose data can exist now.
        refetch_days: How many of the most recent days to fetch again, if the
            source's data can be republished. 0 disables refetching.
    """
    if latest < start:
        return []

    days = set()
    current = start
    while current <= latest:
        if current.isoformat() not in landed:
            days.add(current)
        current += timedelta(days=1)

    if source.refetch_recent:
        for back in range(refetch_days):
            day = latest - timedelta(days=back)
            if day >= start:
                days.add(day)

    return sorted(days)
