"""Tests for `RecordingHttpClient` (plan 5.4, 7.2): "record mode strips secrets (with a fake inner client)"."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from playground.http.client import HttpRequest, HttpResponse
from playground.http.record import RecordingHttpClient
from playground.http.replay import ReplayHttpClient


@dataclass
class FakeInnerClient:
    """A stand-in for `NetworkHttpClient`: returns canned responses and remembers every request it saw."""

    responses: list[HttpResponse]
    seen: list[HttpRequest] = field(default_factory=list)

    def send(self, request: HttpRequest) -> HttpResponse:
        self.seen.append(request)
        return self.responses[len(self.seen) - 1]


def _index(directory: Path) -> dict:
    return yaml.safe_load((directory / "index.yaml").read_text(encoding="utf-8"))


def test_records_status_body_and_url(tmp_path: Path) -> None:
    inner = FakeInnerClient([HttpResponse(status=200, headers={"Content-Type": "text/csv"}, body=b"Date,Close\n")])
    recorder = RecordingHttpClient(inner, tmp_path)

    response = recorder.send(HttpRequest(method="GET", url="https://stooq.com/q/d/l/", params={"s": "sap.de"}))

    assert response.body == b"Date,Close\n"  # the caller still gets the real response back
    entries = _index(tmp_path)["requests"]
    assert len(entries) == 1
    assert entries[0]["method"] == "GET"
    assert entries[0]["url"] == "https://stooq.com/q/d/l/"
    assert entries[0]["response"]["status"] == 200
    recorded_file = tmp_path / entries[0]["response"]["file"]
    assert recorded_file.read_bytes() == b"Date,Close\n"


def test_strips_secret_query_parameters_from_the_recorded_index(tmp_path: Path) -> None:
    inner = FakeInnerClient([HttpResponse(status=200, headers={}, body=b"ok")])
    recorder = RecordingHttpClient(inner, tmp_path)

    recorder.send(
        HttpRequest(method="GET", url="https://example.com/x", params={"apikey": "super-secret", "s": "sap.de"})
    )

    entries = _index(tmp_path)["requests"]
    assert entries[0]["query"] == {"s": "sap.de"}
    assert "apikey" not in entries[0]["query"]
    assert "super-secret" not in (tmp_path / "index.yaml").read_text(encoding="utf-8")


def test_strips_secret_headers_from_the_recorded_index(tmp_path: Path) -> None:
    inner = FakeInnerClient([HttpResponse(status=200, headers={}, body=b"ok")])
    recorder = RecordingHttpClient(inner, tmp_path)

    recorder.send(
        HttpRequest(
            method="POST", url="https://api.openfigi.com/v3/mapping", headers={"X-OPENFIGI-APIKEY": "super-secret"}
        )
    )

    text = (tmp_path / "index.yaml").read_text(encoding="utf-8")
    assert "super-secret" not in text


def test_the_real_request_still_carries_its_secrets(tmp_path: Path) -> None:
    """Stripping only affects what is written to disk; the actual outgoing request is untouched."""
    inner = FakeInnerClient([HttpResponse(status=200, headers={}, body=b"ok")])
    recorder = RecordingHttpClient(inner, tmp_path)

    recorder.send(HttpRequest(method="GET", url="https://example.com/x", params={"apikey": "super-secret"}))

    assert inner.seen[0].params["apikey"] == "super-secret"


def test_records_a_post_body_so_replay_can_match_it_later(tmp_path: Path) -> None:
    inner = FakeInnerClient(
        [HttpResponse(status=200, headers={"Content-Type": "application/json"}, body=b'{"data": []}')]
    )
    recorder = RecordingHttpClient(inner, tmp_path)
    body = b'[{"idType": "ID_ISIN", "idValue": "DE0007164600"}]'

    recorder.send(HttpRequest(method="POST", url="https://api.openfigi.com/v3/mapping", body=body))

    replay = ReplayHttpClient(tmp_path)
    response = replay.send(HttpRequest(method="POST", url="https://api.openfigi.com/v3/mapping", body=body))
    assert response.body == b'{"data": []}'


def test_appends_across_several_sends_and_keeps_existing_entries(tmp_path: Path) -> None:
    inner = FakeInnerClient(
        [
            HttpResponse(status=200, headers={}, body=b"first"),
            HttpResponse(status=200, headers={}, body=b"second"),
        ]
    )
    recorder = RecordingHttpClient(inner, tmp_path)

    recorder.send(HttpRequest(method="GET", url="https://example.com/a"))
    recorder.send(HttpRequest(method="GET", url="https://example.com/b"))

    entries = _index(tmp_path)["requests"]
    assert [entry["url"] for entry in entries] == ["https://example.com/a", "https://example.com/b"]
    assert (tmp_path / entries[0]["response"]["file"]).read_bytes() == b"first"
    assert (tmp_path / entries[1]["response"]["file"]).read_bytes() == b"second"


def test_reopening_the_same_directory_keeps_earlier_recordings(tmp_path: Path) -> None:
    inner = FakeInnerClient([HttpResponse(status=200, headers={}, body=b"first")])
    RecordingHttpClient(inner, tmp_path).send(HttpRequest(method="GET", url="https://example.com/a"))

    inner2 = FakeInnerClient([HttpResponse(status=200, headers={}, body=b"second")])
    RecordingHttpClient(inner2, tmp_path).send(HttpRequest(method="GET", url="https://example.com/b"))

    entries = _index(tmp_path)["requests"]
    assert [entry["url"] for entry in entries] == ["https://example.com/a", "https://example.com/b"]


def test_picks_a_file_extension_from_the_content_type(tmp_path: Path) -> None:
    inner = FakeInnerClient([HttpResponse(status=200, headers={"Content-Type": "application/zip"}, body=b"PK\x03\x04")])
    recorder = RecordingHttpClient(inner, tmp_path)

    recorder.send(HttpRequest(method="GET", url="https://example.com/a.zip"))

    entries = _index(tmp_path)["requests"]
    assert entries[0]["response"]["file"].endswith(".zip")
