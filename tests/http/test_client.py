"""Tests for `NetworkHttpClient` and `make_http_client` (plan 5.4).

`NetworkHttpClient` is exercised through `httpx.MockTransport`, which never opens a real socket
(it answers entirely in-process), so these tests run fine under `pytest-socket`'s disabled
sockets -- the same guarantee `tests/http/test_no_network.py` checks for the rest of the suite.
`sleep` is injected too, so the retry and per-host-throttle tests run instantly.
"""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from playground.config import Settings
from playground.http.client import HttpConfigurationError, HttpRequest, NetworkHttpClient, make_http_client
from playground.http.record import RecordingHttpClient
from playground.http.replay import ReplayHttpClient


def _client(handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float] | None = None) -> NetworkHttpClient:
    return NetworkHttpClient(
        transport=httpx.MockTransport(handler), sleep=(sleeps.append if sleeps is not None else None)
    )


def test_sends_a_user_agent_with_the_package_version() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, content=b"ok")

    response = _client(handler).send(HttpRequest(method="GET", url="https://example.com/x"))

    assert response.status == 200
    assert response.body == b"ok"
    assert seen["user_agent"] is not None
    assert seen["user_agent"].startswith("playground/")


def test_sends_query_parameters_and_extra_headers() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["custom"] = request.headers.get("x-custom")
        return httpx.Response(200, content=b"ok")

    _client(handler).send(
        HttpRequest(method="GET", url="https://example.com/x", params={"a": "1"}, headers={"X-Custom": "yes"})
    )

    assert seen["params"] == {"a": "1"}
    assert seen["custom"] == "yes"


def test_retries_a_5xx_status_and_then_succeeds() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, content=b"try again")
        return httpx.Response(200, content=b"ok")

    sleeps: list[float] = []
    response = _client(handler, sleeps).send(HttpRequest(method="GET", url="https://example.com/x"))

    assert response.status == 200
    assert calls["count"] == 3
    assert len(sleeps) == 2  # one backoff sleep before each retry, none after the final success


def test_gives_up_after_the_maximum_number_of_retries() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500, content=b"still broken")

    response = _client(handler, []).send(HttpRequest(method="GET", url="https://example.com/x"))

    assert response.status == 500
    assert calls["count"] == 4  # the first attempt plus MAX_RETRIES (3)


def test_does_not_retry_a_4xx_status() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(404, content=b"not found")

    response = _client(handler, []).send(HttpRequest(method="GET", url="https://example.com/x"))

    assert response.status == 404
    assert calls["count"] == 1


def test_throttles_requests_to_the_same_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    sleeps: list[float] = []
    client = _client(handler, sleeps)
    client.send(HttpRequest(method="GET", url="https://example.com/a"))
    client.send(HttpRequest(method="GET", url="https://example.com/b"))

    # Same host both times, so the second request waits out the minimum spacing.
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_does_not_throttle_requests_to_different_hosts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    sleeps: list[float] = []
    client = _client(handler, sleeps)
    client.send(HttpRequest(method="GET", url="https://one.example.com/a"))
    client.send(HttpRequest(method="GET", url="https://two.example.com/b"))

    assert sleeps == []


# --- make_http_client -------------------------------------------------------------------------


def test_make_http_client_defaults_to_network(tmp_path: Path) -> None:
    client = make_http_client(Settings(data_dir=tmp_path))
    assert isinstance(client, NetworkHttpClient)


def test_make_http_client_replay_needs_a_directory(tmp_path: Path) -> None:
    with pytest.raises(HttpConfigurationError):
        make_http_client(Settings(data_dir=tmp_path, http_mode="replay"))


def test_make_http_client_replay_builds_a_replay_client(tmp_path: Path) -> None:
    replay_dir = Path(__file__).resolve().parents[1] / "fixtures" / "http"
    client = make_http_client(Settings(data_dir=tmp_path, http_mode="replay", http_replay_dir=replay_dir))
    assert isinstance(client, ReplayHttpClient)


def test_make_http_client_record_needs_a_directory(tmp_path: Path) -> None:
    with pytest.raises(HttpConfigurationError):
        make_http_client(Settings(data_dir=tmp_path, http_mode="record"))


def test_make_http_client_record_builds_a_recording_client(tmp_path: Path) -> None:
    client = make_http_client(Settings(data_dir=tmp_path, http_mode="record", http_record_dir=tmp_path / "rec"))
    assert isinstance(client, RecordingHttpClient)
