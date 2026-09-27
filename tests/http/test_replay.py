"""Tests for `ReplayHttpClient` (plan 5.4, 7.2): matching and the unrecorded-request error."""

from pathlib import Path

import pytest

from playground.http.client import HttpRequest
from playground.http.replay import FixtureIndexError, ReplayHttpClient, UnrecordedRequestError


def _write_index(tmp_path: Path, text: str) -> Path:
    (tmp_path / "index.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_matches_on_method_url_and_query(tmp_path: Path) -> None:
    (tmp_path / "body.txt").write_bytes(b"hello")
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: GET
            url: https://example.com/data
            query: {a: "1", b: "2"}
            response: {status: 200, file: body.txt}
        """,
    )
    client = ReplayHttpClient(directory)

    response = client.send(HttpRequest(method="get", url="https://example.com/data", params={"b": "2", "a": "1"}))

    assert response.status == 200
    assert response.body == b"hello"


def test_a_different_query_value_does_not_match(tmp_path: Path) -> None:
    (tmp_path / "body.txt").write_bytes(b"hello")
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: GET
            url: https://example.com/data
            query: {a: "1"}
            response: {status: 200, file: body.txt}
        """,
    )
    client = ReplayHttpClient(directory)

    with pytest.raises(UnrecordedRequestError):
        client.send(HttpRequest(method="GET", url="https://example.com/data", params={"a": "2"}))


def test_an_extra_query_parameter_does_not_match(tmp_path: Path) -> None:
    (tmp_path / "body.txt").write_bytes(b"hello")
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: GET
            url: https://example.com/data
            query: {a: "1"}
            response: {status: 200, file: body.txt}
        """,
    )
    client = ReplayHttpClient(directory)

    with pytest.raises(UnrecordedRequestError):
        client.send(HttpRequest(method="GET", url="https://example.com/data", params={"a": "1", "b": "2"}))


def test_a_request_with_no_query_matches_an_entry_with_no_query(tmp_path: Path) -> None:
    (tmp_path / "body.bin").write_bytes(b"\x00\x01")
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: GET
            url: https://example.com/plain
            response: {status: 200, file: body.bin, headers: {Content-Type: application/octet-stream}}
        """,
    )
    client = ReplayHttpClient(directory)

    response = client.send(HttpRequest(method="GET", url="https://example.com/plain"))

    assert response.body == b"\x00\x01"
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_matches_a_post_body_by_hash(tmp_path: Path) -> None:
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: POST
            url: https://example.com/mapping
            body: '{"a": 1}'
            response: {status: 200, body: 'yes'}
          - method: POST
            url: https://example.com/mapping
            body: '{"a": 2}'
            response: {status: 200, body: 'no'}
        """,
    )
    client = ReplayHttpClient(directory)

    matched = client.send(HttpRequest(method="POST", url="https://example.com/mapping", body=b'{"a": 1}'))
    other = client.send(HttpRequest(method="POST", url="https://example.com/mapping", body=b'{"a": 2}'))

    assert matched.body == b"yes"
    assert other.body == b"no"


def test_a_different_body_does_not_match(tmp_path: Path) -> None:
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: POST
            url: https://example.com/mapping
            body: '{"a": 1}'
            response: {status: 200, body: 'yes'}
        """,
    )
    client = ReplayHttpClient(directory)

    with pytest.raises(UnrecordedRequestError):
        client.send(HttpRequest(method="POST", url="https://example.com/mapping", body=b'{"a": 999}'))


def test_an_entry_with_no_body_only_matches_a_request_with_no_body(tmp_path: Path) -> None:
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: POST
            url: https://example.com/mapping
            response: {status: 200, body: 'no-body-response'}
        """,
    )
    client = ReplayHttpClient(directory)

    assert client.send(HttpRequest(method="POST", url="https://example.com/mapping")).body == b"no-body-response"
    with pytest.raises(UnrecordedRequestError):
        client.send(HttpRequest(method="POST", url="https://example.com/mapping", body=b"something"))


def test_missing_index_file_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FixtureIndexError):
        ReplayHttpClient(tmp_path)


def test_missing_response_file_raises_a_clear_error(tmp_path: Path) -> None:
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: GET
            url: https://example.com/data
            response: {status: 200, file: missing.txt}
        """,
    )
    client = ReplayHttpClient(directory)

    with pytest.raises(FixtureIndexError):
        client.send(HttpRequest(method="GET", url="https://example.com/data"))


def test_entry_with_neither_file_nor_body_is_rejected_at_load_time(tmp_path: Path) -> None:
    directory = _write_index(
        tmp_path,
        """
        requests:
          - method: GET
            url: https://example.com/data
            response: {status: 200}
        """,
    )
    with pytest.raises(FixtureIndexError):
        ReplayHttpClient(directory)


def test_the_golden_fixtures_directory_serves_every_recorded_symbol(replay_http_client: ReplayHttpClient) -> None:
    """A light integration check that the committed `tests/fixtures/http` fixtures load at all."""
    response = replay_http_client.send(
        HttpRequest(
            method="GET",
            url="https://stooq.com/q/d/l/",
            params={"s": "sap.de", "d1": "20240101", "d2": "20241231", "i": "d"},
        )
    )
    assert response.status == 200
    assert response.body.startswith(b"Date,Open,High,Low,Close,Volume")
