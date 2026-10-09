"""HTTP client shared by every API source: timeouts, retries and rate limiting.

Responses are returned as raw bytes exactly as received, with the UTC fetch
time, so the landing layer can store them unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ukpower.config import HttpSettings

logger = logging.getLogger(__name__)

USER_AGENT = "uk-power-pipeline/0.1 (portfolio project; github.com/umarkhan0206/uk-power-pipeline)"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ERROR_BODY_CHARS = 500

Params = Mapping[str, str | int | float]


class HttpError(Exception):
    """A request failed with an HTTP error status. Not retried unless a subclass says so."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status_code} for {url}: {body[:MAX_ERROR_BODY_CHARS]}")


class RetryableHttpError(HttpError):
    """A temporary HTTP failure (429 or 5xx) that is worth retrying."""

    def __init__(self, status_code: int, url: str, body: str, retry_after: float | None) -> None:
        super().__init__(status_code, url, body)
        self.retry_after = retry_after


@dataclass(frozen=True)
class FetchResult:
    """One successful response, kept exactly as received."""

    url: str  # full URL including the query string, as sent
    status_code: int
    fetched_at: datetime  # UTC
    content: bytes  # raw body, unmodified

    def json(self) -> Any:  # noqa: ANN401 (JSON can be any shape)
        """Parse the body as JSON."""
        return json.loads(self.content)


class RateLimiter:
    """Spaces calls at least 1 / requests_per_second seconds apart."""

    def __init__(
        self,
        requests_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._min_interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None

    def wait(self) -> None:
        """Block until the next call is allowed, then reserve the following slot."""
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._min_interval


def parse_retry_after(value: str | None) -> float | None:
    """Read a Retry-After header: either seconds or an HTTP date. None if absent or invalid."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class HttpClient:
    """GET requests with timeouts, rate limiting and retries on temporary failures."""

    def __init__(
        self,
        settings: HttpSettings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._limiter = RateLimiter(settings.requests_per_second, sleep=sleep)
        backoff = wait_exponential_jitter(
            initial=settings.backoff_base_seconds, max=settings.backoff_max_seconds
        )

        def wait(state: RetryCallState) -> float:
            # Exponential backoff, but never shorter than the server's Retry-After (capped).
            delay = backoff(state)
            exc = state.outcome.exception() if state.outcome else None
            if isinstance(exc, RetryableHttpError) and exc.retry_after is not None:
                delay = max(delay, min(exc.retry_after, settings.backoff_max_seconds))
            return delay

        self._retrying = Retrying(
            stop=stop_after_attempt(settings.max_retries + 1),
            wait=wait,
            retry=retry_if_exception_type(
                (requests.ConnectionError, requests.Timeout, RetryableHttpError)
            ),
            before_sleep=self._log_retry,
            sleep=sleep,
            reraise=True,
        )

    def get(self, url: str, params: Params | None = None) -> FetchResult:
        """GET a URL, retrying temporary failures.

        Raises:
            HttpError: on a non-retryable status such as 400 or 404.
            RetryableHttpError: if a temporary failure persists after all retries.
            requests.ConnectionError, requests.Timeout: if the network fails after all retries.
        """
        return self._retrying(self._get_once, url, params)

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get_once(self, url: str, params: Params | None) -> FetchResult:
        self._limiter.wait()
        response = self._session.get(url, params=params, timeout=self._settings.timeout_seconds)
        fetched_at = datetime.now(UTC)

        if response.status_code in RETRYABLE_STATUS_CODES:
            raise RetryableHttpError(
                response.status_code,
                response.url,
                response.text,
                parse_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code >= 400:
            raise HttpError(response.status_code, response.url, response.text)

        logger.debug(
            "GET %s -> %d (%d bytes)", response.url, response.status_code, len(response.content)
        )
        return FetchResult(
            url=response.url,
            status_code=response.status_code,
            fetched_at=fetched_at,
            content=response.content,
        )

    @staticmethod
    def _log_retry(state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        delay = state.next_action.sleep if state.next_action else 0.0
        logger.warning(
            "Attempt %d failed (%s). Retrying in %.1f s", state.attempt_number, exc, delay
        )
