"""Tests for `StooqClient` (plan 5.6, 6.4, 7.2): "No data", an HTML block and a 500."""

from datetime import date
from decimal import Decimal

import pytest

from playground.http.client import HttpRequest, HttpResponse
from playground.http.replay import ReplayHttpClient
from playground.marketdata.stooq import SourceNoData, SourceUnavailable, StooqClient


class _FixedResponseClient:
    def __init__(self, response: HttpResponse) -> None:
        self._response = response
        self.sent: HttpRequest | None = None

    def send(self, request: HttpRequest) -> HttpResponse:
        self.sent = request
        return self._response


def test_daily_bars_parses_the_csv() -> None:
    body = b"Date,Open,High,Low,Close,Volume\n2024-01-02,138.00,139.00,137.50,138.00,100000\n2024-01-03,138.00,138.50,137.00,138.22,90000\n"
    client = StooqClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=body)))

    series = client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")

    assert series.source == "stooq"
    assert series.symbol == "sap.de"
    assert series.currency == "EUR"
    assert series.adjustment == "split_dividend"
    assert len(series.points) == 2
    first = series.points[0]
    assert first.date == date(2024, 1, 2)
    assert first.open == Decimal("138.00")
    assert first.high == Decimal("139.00")
    assert first.low == Decimal("137.50")
    assert first.close == Decimal("138.00")
    assert first.volume == 100000


def test_daily_bars_builds_the_documented_query() -> None:
    body = b"Date,Open,High,Low,Close,Volume\n"
    fake = _FixedResponseClient(HttpResponse(status=200, headers={}, body=body))
    client = StooqClient(fake, base_url="https://stooq.com/q/d/l/")

    client.daily_bars("aapl.us", date(2024, 1, 1), date(2024, 12, 31), currency="USD")

    assert fake.sent is not None
    assert fake.sent.method == "GET"
    assert fake.sent.url == "https://stooq.com/q/d/l/"
    assert fake.sent.params == {"s": "aapl.us", "d1": "20240101", "d2": "20241231", "i": "d"}


def test_no_data_body_raises_source_no_data() -> None:
    client = StooqClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=b"No data")))
    with pytest.raises(SourceNoData):
        client.daily_bars("nope.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")


def test_html_body_raises_source_unavailable() -> None:
    body = b"<html><body>Service Unavailable</body></html>"
    client = StooqClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=body)))
    with pytest.raises(SourceUnavailable):
        client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")


def test_non_200_status_raises_source_unavailable() -> None:
    client = StooqClient(_FixedResponseClient(HttpResponse(status=500, headers={}, body=b"boom")))
    with pytest.raises(SourceUnavailable):
        client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")


def test_an_unexpected_header_raises_source_unavailable() -> None:
    client = StooqClient(
        _FixedResponseClient(HttpResponse(status=200, headers={}, body=b"oops,not,the,right,header\n1,2,3,4,5\n"))
    )
    with pytest.raises(SourceUnavailable):
        client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")


def test_a_row_that_does_not_parse_raises_source_unavailable() -> None:
    body = b"Date,Open,High,Low,Close,Volume\n2024-01-02,not-a-number,139.00,137.50,138.00,100000\n"
    client = StooqClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=body)))
    with pytest.raises(SourceUnavailable):
        client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")


def test_missing_open_high_low_are_none() -> None:
    body = b"Date,Open,High,Low,Close,Volume\n2024-01-02,,,,138.00,\n"
    client = StooqClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=body)))

    series = client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")

    point = series.points[0]
    assert point.open is None
    assert point.high is None
    assert point.low is None
    assert point.volume is None
    assert point.close == Decimal("138.00")


# --- against the committed fixtures (plan 6.4) -------------------------------------------------


def test_against_the_golden_fixture_series(replay_http_client: ReplayHttpClient) -> None:
    client = StooqClient(replay_http_client)

    series = client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 12, 31), currency="EUR")

    by_date = {point.date: point.close for point in series.points}
    assert by_date[date(2024, 1, 15)] == Decimal("140.00")  # T2's document price (1.2)
    assert by_date[date(2024, 5, 31)] == Decimal("170.00")  # the golden valuation anchor (1.2)
    assert by_date[date(2024, 12, 30)] == Decimal("236.00")  # Xetra's last trading day of 2024


def test_the_no_data_html_and_500_fixtures_raise_the_right_error(replay_http_client: ReplayHttpClient) -> None:
    client = StooqClient(replay_http_client)
    with pytest.raises(SourceNoData):
        client.daily_bars("no-data.invalid", date(2024, 1, 1), date(2024, 12, 31), currency="EUR")
    with pytest.raises(SourceUnavailable):
        client.daily_bars("html-block.invalid", date(2024, 1, 1), date(2024, 12, 31), currency="EUR")
    with pytest.raises(SourceUnavailable):
        client.daily_bars("http-500.invalid", date(2024, 1, 1), date(2024, 12, 31), currency="EUR")


class _UnreachableClient:
    """An `HttpClient` that never gets a response: the network cannot be reached."""

    def send(self, request: HttpRequest) -> HttpResponse:
        from playground.http.client import NetworkError

        raise NetworkError("stooq.com", "the connection failed ([Errno 111] Connection refused)")


def test_a_network_failure_is_source_unavailable_with_the_manual_file_hint() -> None:
    client = StooqClient(_UnreachableClient())

    with pytest.raises(SourceUnavailable) as info:
        client.daily_bars("sap.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")

    assert str(info.value) == (
        "Could not reach stooq.com to fetch sap.de: the connection failed ([Errno 111] Connection refused). "
        "Check your connection, or load its closes from a file with pg prices import-file."
    )


def test_an_unrecorded_replay_request_is_source_unavailable(tmp_path) -> None:
    (tmp_path / "index.yaml").write_text("requests: []\n", encoding="utf-8")
    client = StooqClient(ReplayHttpClient(tmp_path))

    with pytest.raises(SourceUnavailable, match="No recorded response"):
        client.daily_bars("alv.de", date(2024, 1, 1), date(2024, 1, 31), currency="EUR")


def test_a_client_without_the_manual_file_hint_says_only_to_try_again() -> None:
    client = StooqClient(_UnreachableClient(), manual_file_hint=False)

    with pytest.raises(SourceUnavailable) as info:
        client.daily_bars("^spx", date(2024, 1, 1), date(2024, 1, 31), currency="USD")

    assert str(info.value) == (
        "Could not reach stooq.com to fetch ^spx: the connection failed ([Errno 111] Connection refused). "
        "Check your connection and try again."
    )
