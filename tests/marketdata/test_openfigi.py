"""Tests for `OpenFigiClient` (plan 5.6, 6.7, 7.2): "OpenFIGI mapping and 429"."""

import json

import pytest

from playground.http.client import HttpRequest, HttpResponse
from playground.http.replay import ReplayHttpClient
from playground.marketdata.openfigi import API_KEY_HEADER, OpenFigiClient, OpenFigiError


class _RecordingClient:
    def __init__(self, response: HttpResponse) -> None:
        self._response = response
        self.sent: HttpRequest | None = None

    def send(self, request: HttpRequest) -> HttpResponse:
        self.sent = request
        return self._response


def _listing_response(**fields: str) -> HttpResponse:
    body = json.dumps([{"data": [fields]}]).encode("utf-8")
    return HttpResponse(status=200, headers={"Content-Type": "application/json"}, body=body)


def test_suggest_returns_the_listings() -> None:
    client = OpenFigiClient(
        _RecordingClient(_listing_response(ticker="SAP", exchCode="GY", name="SAP SE", securityType="Common Stock"))
    )

    suggestions = client.suggest("DE0007164600")

    assert len(suggestions) == 1
    assert suggestions[0].ticker == "SAP"
    assert suggestions[0].exchange_code == "GY"
    assert suggestions[0].name == "SAP SE"
    assert suggestions[0].security_type == "Common Stock"
    assert suggestions[0].stooq_symbol == "sap.de"


def test_suggest_sends_the_documented_request_body() -> None:
    fake = _RecordingClient(HttpResponse(status=200, headers={}, body=b'[{"data": []}]'))
    OpenFigiClient(fake).suggest("DE0007164600")

    assert fake.sent is not None
    assert fake.sent.method == "POST"
    assert fake.sent.url == "https://api.openfigi.com/v3/mapping"
    assert json.loads(fake.sent.body) == [{"idType": "ID_ISIN", "idValue": "DE0007164600"}]


def test_suggest_sends_the_api_key_header_only_when_given() -> None:
    fake = _RecordingClient(HttpResponse(status=200, headers={}, body=b'[{"data": []}]'))
    OpenFigiClient(fake, api_key="secret-key").suggest("DE0007164600")
    assert fake.sent.headers[API_KEY_HEADER] == "secret-key"

    fake_no_key = _RecordingClient(HttpResponse(status=200, headers={}, body=b'[{"data": []}]'))
    OpenFigiClient(fake_no_key).suggest("DE0007164600")
    assert API_KEY_HEADER not in fake_no_key.sent.headers


def test_an_empty_result_gives_an_empty_list() -> None:
    body = json.dumps([{"data": []}]).encode("utf-8")
    client = OpenFigiClient(_RecordingClient(HttpResponse(status=200, headers={}, body=body)))
    assert client.suggest("GB9999999998") == []


def test_a_warning_result_gives_an_empty_list_not_an_error() -> None:
    body = json.dumps([{"warning": "No identifier found."}]).encode("utf-8")
    client = OpenFigiClient(_RecordingClient(HttpResponse(status=200, headers={}, body=body)))
    assert client.suggest("GB9999999998") == []


def test_429_raises_openfigi_error() -> None:
    client = OpenFigiClient(_RecordingClient(HttpResponse(status=429, headers={}, body=b"too many requests")))
    with pytest.raises(OpenFigiError):
        client.suggest("US9999999991")


def test_another_non_200_status_raises_openfigi_error() -> None:
    client = OpenFigiClient(_RecordingClient(HttpResponse(status=500, headers={}, body=b"boom")))
    with pytest.raises(OpenFigiError):
        client.suggest("DE0007164600")


def test_unreadable_json_raises_openfigi_error() -> None:
    client = OpenFigiClient(_RecordingClient(HttpResponse(status=200, headers={}, body=b"not json")))
    with pytest.raises(OpenFigiError):
        client.suggest("DE0007164600")


def test_stooq_symbol_is_none_for_an_unmapped_exchange_code() -> None:
    client = OpenFigiClient(
        _RecordingClient(_listing_response(ticker="XYZ", exchCode="FR", name="Something", securityType="Common Stock"))
    )
    assert client.suggest("DE0007164600")[0].stooq_symbol is None


# --- against the committed fixtures (plan 6.7) --------------------------------------------------


def test_against_the_golden_fixtures(replay_http_client: ReplayHttpClient) -> None:
    client = OpenFigiClient(replay_http_client)

    suggestions = client.suggest("DE0007164600")
    assert suggestions[0].ticker == "SAP"
    assert suggestions[0].stooq_symbol == "sap.de"

    assert client.suggest("GB9999999998") == []

    with pytest.raises(OpenFigiError):
        client.suggest("US9999999991")
