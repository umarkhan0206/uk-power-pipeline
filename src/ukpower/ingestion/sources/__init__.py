"""All data sources, built from settings in one place."""

from __future__ import annotations

from ukpower.config import Settings
from ukpower.ingestion.sources.base import Source
from ukpower.ingestion.sources.carbon_intensity import CarbonIntensity
from ukpower.ingestion.sources.elexon_daily import (
    ElexonDemandOutturn,
    ElexonGenerationByFuel,
    ElexonWindSolarActual,
    ElexonWindSolarDayAhead,
)
from ukpower.ingestion.sources.elexon_forecasts import ElexonDemandForecast, ElexonWindForecast
from ukpower.ingestion.sources.elexon_settled_prices import ElexonSettledPrices
from ukpower.ingestion.sources.elexon_system_prices import ElexonSystemPrices
from ukpower.ingestion.sources.open_meteo import OpenMeteoForecast


def build_sources(settings: Settings) -> list[Source]:
    """Every source the pipeline ingests, configured from settings."""
    elexon = settings.elexon.base_url
    tz = settings.tz
    decision = settings.decision_time_local
    return [
        ElexonSystemPrices(elexon, tz),
        ElexonSettledPrices(
            elexon,
            tz,
            settings.elexon.settled_prices.long_refetch_days,
            settings.elexon.settled_prices.long_refetch_weekday,
        ),
        ElexonDemandOutturn(elexon, tz),
        ElexonGenerationByFuel(elexon, tz),
        ElexonWindSolarActual(elexon, tz),
        ElexonWindSolarDayAhead(elexon, tz),
        ElexonDemandForecast(elexon, tz, decision),
        ElexonWindForecast(elexon, tz, decision),
        OpenMeteoForecast(settings.open_meteo, tz, decision),
        CarbonIntensity(settings.carbon_intensity.base_url, tz),
    ]
