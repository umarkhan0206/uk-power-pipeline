"""The interface every data source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ukpower.ingestion.http_client import FetchResult, HttpClient


class ResponseValidationError(Exception):
    """A response arrived but its content does not look right."""


@dataclass(frozen=True)
class SourceRequest:
    """One API call: what it is about (partition), where it goes, and with which parameters."""

    partition: str
    url: str
    params: dict[str, str | int | float] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchedResponse:
    """A validated response together with the request that produced it."""

    request: SourceRequest
    result: FetchResult
    record_count: int


class Source(ABC):
    """A dataset we ingest. Subclasses describe requests and check responses."""

    #: Folder name in the landing zone, for example "elexon_system_prices".
    name: str

    @abstractmethod
    def requests_for_day(self, day: date) -> list[SourceRequest]:
        """The API calls needed to cover one UK settlement day (or the first one, if adaptive)."""

    @abstractmethod
    def validate(self, request: SourceRequest, payload: Any) -> int:  # noqa: ANN401
        """Check a parsed response. Return the number of records.

        Raises:
            ResponseValidationError: if the content is structurally wrong.
        """

    def fetch_day(self, client: HttpClient, day: date) -> list[FetchedResponse]:
        """Fetch and validate everything for one day.

        The default makes each request from requests_for_day once. Sources whose
        later requests depend on earlier responses override this.

        Raises:
            ResponseValidationError: if any response fails validation.
        """
        fetched = []
        for request in self.requests_for_day(day):
            result = client.get(request.url, request.params)
            count = self.validate(request, result.json())
            fetched.append(FetchedResponse(request=request, result=result, record_count=count))
        return fetched


def require_data_list(payload: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Return payload["data"] if it is a list of objects, otherwise raise."""
    if not isinstance(payload, dict) or "data" not in payload:
        raise ResponseValidationError("response has no 'data' key")
    data = payload["data"]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ResponseValidationError("'data' is not a list of records")
    return data
