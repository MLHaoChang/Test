"""Tests for the ECB reference-rate client and CSV parser (plan 5.6, 6.6, 7.2): "ECB zip and N/A"."""

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from playground.http.client import HttpRequest, HttpResponse
from playground.http.replay import ReplayHttpClient
from playground.marketdata.ecb import EcbClient, EcbSourceError, load_fx_file, parse_fx_csv

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "http" / "ecb"


class _FixedResponseClient:
    def __init__(self, response: HttpResponse) -> None:
        self._response = response

    def send(self, request: HttpRequest) -> HttpResponse:
        return self._response


def _zip_of(csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("eurofxref-hist.csv", csv_text)
    return buffer.getvalue()


def test_parse_fx_csv_reads_every_currency_column() -> None:
    text = "Date, USD, GBP, \n2024-01-02, 1.0900, 0.8600, \n2024-01-03, 1.0901, 0.8601, \n"
    table = parse_fx_csv(text.encode("utf-8"))

    by_key = {(point.date, point.currency): point.rate for point in table.points}
    assert by_key[(date(2024, 1, 2), "USD")] == Decimal("1.0900")
    assert by_key[(date(2024, 1, 2), "GBP")] == Decimal("0.8600")
    assert by_key[(date(2024, 1, 3), "USD")] == Decimal("1.0901")


def test_parse_fx_csv_skips_n_a_and_empty_cells() -> None:
    text = "Date, USD, GBP, \n2024-01-02, N/A, 0.8600, \n2024-01-03, 1.0901, , \n"
    table = parse_fx_csv(text.encode("utf-8"))

    currencies_on = {day: set() for day in (date(2024, 1, 2), date(2024, 1, 3))}
    for point in table.points:
        currencies_on[point.date].add(point.currency)
    assert currencies_on[date(2024, 1, 2)] == {"GBP"}
    assert currencies_on[date(2024, 1, 3)] == {"USD"}


def test_parse_fx_csv_requires_a_date_column_first() -> None:
    with pytest.raises(EcbSourceError):
        parse_fx_csv(b"Currency,USD\n2024-01-02,1.09\n")


def test_parse_fx_csv_rejects_an_unparsable_date() -> None:
    with pytest.raises(EcbSourceError, match="Line 2"):
        parse_fx_csv(b"Date,USD\nnot-a-date,1.09\n")


def test_parse_fx_csv_rejects_an_unparsable_rate() -> None:
    with pytest.raises(EcbSourceError, match="Line 2"):
        parse_fx_csv(b"Date,USD\n2024-01-02,not-a-number\n")


def test_parse_fx_csv_rejects_an_empty_file() -> None:
    with pytest.raises(EcbSourceError):
        parse_fx_csv(b"")


def test_client_unzips_and_parses() -> None:
    body = _zip_of("Date,USD\n2024-01-02,1.09\n")
    client = EcbClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=body)))

    table = client.history()

    assert table.points == (parse_fx_csv(b"Date,USD\n2024-01-02,1.09\n").points[0],)


def test_client_raises_on_a_non_200_status() -> None:
    client = EcbClient(_FixedResponseClient(HttpResponse(status=500, headers={}, body=b"boom")))
    with pytest.raises(EcbSourceError):
        client.history()


def test_client_raises_when_the_body_is_not_a_zip() -> None:
    client = EcbClient(
        _FixedResponseClient(HttpResponse(status=200, headers={}, body=(FIXTURES / "not_a_zip.bin").read_bytes()))
    )
    with pytest.raises(EcbSourceError):
        client.history()


def test_client_raises_on_an_empty_zip() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w"):
        pass
    client = EcbClient(_FixedResponseClient(HttpResponse(status=200, headers={}, body=buffer.getvalue())))
    with pytest.raises(EcbSourceError):
        client.history()


def test_load_fx_file_reads_the_na_fixture_directly() -> None:
    table = load_fx_file((FIXTURES / "eurofxref-hist-na.csv").read_bytes())

    by_key = {(point.date, point.currency) for point in table.points}
    # The middle day's USD cell is N/A in this fixture (6.6): it must not appear as a rate.
    dates = sorted({point.date for point in table.points})
    middle_day = dates[1]
    assert (middle_day, "USD") not in by_key
    assert (middle_day, "GBP") in by_key


# --- against the committed fixtures (plan 6.6) --------------------------------------------------


def test_client_against_the_golden_fixture(replay_http_client: ReplayHttpClient) -> None:
    client = EcbClient(replay_http_client)

    table = client.history()

    by_key = {(point.date, point.currency): point.rate for point in table.points}
    # plan 1.2, 6.6: the three anchor rates the golden portfolio's value depends on.
    assert by_key[(date(2024, 1, 2), "USD")] == Decimal("1.0900")
    assert by_key[(date(2024, 5, 31), "USD")] == Decimal("1.0800")
    assert by_key[(date(2024, 12, 31), "USD")] == Decimal("1.0400")
    # 1.2's staleness example: no USD rate at all on 2024-12-25 (a TARGET holiday), but one on
    # the 24th, which is what "the ECB rate from 2024-12-24" (1.2) then falls back to.
    assert (date(2024, 12, 25), "USD") not in by_key
    assert (date(2024, 12, 24), "USD") in by_key


class _UnreachableClient:
    """An `HttpClient` that never gets a response: the network cannot be reached."""

    def send(self, request: HttpRequest) -> HttpResponse:
        from playground.http.client import NetworkError

        raise NetworkError("www.ecb.europa.eu", "no answer within 20 seconds, 4 times")


def test_a_network_failure_is_a_plain_error_with_the_import_file_hint() -> None:
    with pytest.raises(EcbSourceError) as info:
        EcbClient(_UnreachableClient()).history()

    assert str(info.value) == (
        "Could not reach www.ecb.europa.eu to fetch the ECB rates: no answer within 20 seconds, 4 times. "
        "Check your connection, or load the rates CSV from a file with pg fx import-file."
    )
