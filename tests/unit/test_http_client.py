"""Tests for the shared HTTP client. All HTTP calls are faked with `responses`."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
import requests
import responses

from ukpower.config import HttpSettings
from ukpower.ingestion.http_client import (
    USER_AGENT,
    HttpClient,
    HttpError,
    RateLimiter,
    RetryableHttpError,
    parse_retry_after,
)

URL = "https://api.example.test/data"


@pytest.fixture
def settings() -> HttpSettings:
    return HttpSettings(
        timeout_seconds=5,
        max_retries=3,
        backoff_base_seconds=0.01,
        backoff_max_seconds=60,
        requests_per_second=10,
    )


@pytest.fixture
def sleeps() -> list[float]:
    """Records every wait instead of actually sleeping."""
    return []


@pytest.fixture
def client(settings: HttpSettings, sleeps: list[float]) -> Iterator[HttpClient]:
    with HttpClient(settings, sleep=sleeps.append) as http_client:
        yield http_client


# Successful requests


@responses.activate
def test_success_returns_raw_body_and_metadata(client: HttpClient) -> None:
    body = b'{"data": [1, 2, 3]}'
    responses.add(responses.GET, URL, body=body, status=200)

    result = client.get(URL, {"format": "json"})

    assert result.status_code == 200
    assert result.content == body
    assert result.json() == {"data": [1, 2, 3]}
    assert result.url == f"{URL}?format=json"
    assert result.fetched_at.tzinfo == UTC


@responses.activate
def test_user_agent_is_sent(client: HttpClient) -> None:
    responses.add(responses.GET, URL, json={})
    client.get(URL)
    assert responses.calls[0].request.headers["User-Agent"] == USER_AGENT


# Temporary failures are retried


@responses.activate
def test_server_error_is_retried_then_succeeds(client: HttpClient) -> None:
    responses.add(responses.GET, URL, status=500)
    responses.add(responses.GET, URL, json={"ok": True})

    assert client.get(URL).json() == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_connection_error_is_retried(client: HttpClient) -> None:
    responses.add(responses.GET, URL, body=requests.ConnectionError("network down"))
    responses.add(responses.GET, URL, json={"ok": True})

    assert client.get(URL).json() == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_persistent_server_error_gives_up_after_max_retries(
    client: HttpClient, settings: HttpSettings
) -> None:
    responses.add(responses.GET, URL, status=503)

    with pytest.raises(RetryableHttpError) as exc_info:
        client.get(URL)

    assert exc_info.value.status_code == 503
    assert len(responses.calls) == settings.max_retries + 1


@responses.activate
def test_retry_after_header_is_respected(client: HttpClient, sleeps: list[float]) -> None:
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "7"})
    responses.add(responses.GET, URL, json={})

    client.get(URL)

    assert max(sleeps) >= 7


@responses.activate
def test_retry_after_is_capped_at_backoff_max(settings: HttpSettings, sleeps: list[float]) -> None:
    capped = settings.model_copy(update={"backoff_max_seconds": 2})
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "3600"})
    responses.add(responses.GET, URL, json={})

    with HttpClient(capped, sleep=sleeps.append) as capped_client:
        capped_client.get(URL)

    assert max(sleeps) <= 2


# Permanent failures are not retried


@responses.activate
def test_client_error_is_not_retried(client: HttpClient) -> None:
    responses.add(responses.GET, URL, status=400, json={"errors": "ProcessType missing"})

    with pytest.raises(HttpError) as exc_info:
        client.get(URL)

    assert not isinstance(exc_info.value, RetryableHttpError)
    assert "ProcessType missing" in str(exc_info.value)
    assert len(responses.calls) == 1


# Rate limiter


class FakeClock:
    """A clock that only moves when told to, so timing tests are exact."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_spaces_back_to_back_calls() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock.time, sleep=clock.sleep)

    for _ in range(3):
        limiter.wait()

    assert clock.sleeps == pytest.approx([0.5, 0.5])


def test_rate_limiter_does_not_wait_when_calls_are_already_slow() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock.time, sleep=clock.sleep)

    limiter.wait()
    clock.now += 10
    limiter.wait()

    assert clock.sleeps == []


def test_rate_limiter_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


# Retry-After parsing


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("5", 5.0), ("-3", 0.0), ("soon", None)],
)
def test_parse_retry_after_seconds(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date() -> None:
    future = datetime.now(UTC) + timedelta(seconds=120)
    result = parse_retry_after(format_datetime(future, usegmt=True))
    assert result is not None
    assert 100 <= result <= 120
