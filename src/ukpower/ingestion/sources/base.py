"""The interface every data source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class ResponseValidationError(Exception):
    """A response arrived but its content does not look right."""


@dataclass(frozen=True)
class SourceRequest:
    """One API call: what it is about (partition), where it goes, and with which parameters."""

    partition: str
    url: str
    params: dict[str, str | int | float] = field(default_factory=dict)


class Source(ABC):
    """A dataset we ingest. Subclasses describe requests and check responses."""

    #: Folder name in the landing zone, for example "elexon_system_prices".
    name: str

    @abstractmethod
    def requests_for_day(self, day: date) -> list[SourceRequest]:
        """The API calls needed to cover one UK settlement day."""

    @abstractmethod
    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        """Check a parsed response. Return the number of records.

        Raises:
            ResponseValidationError: if the content is structurally wrong.
        """


def require_data_list(payload: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Return payload["data"] if it is a list of objects, otherwise raise."""
    if not isinstance(payload, dict) or "data" not in payload:
        raise ResponseValidationError("response has no 'data' key")
    data = payload["data"]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ResponseValidationError("'data' is not a list of records")
    return data
