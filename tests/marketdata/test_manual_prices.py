"""Tests for the manual price file (plan 5.6, 6.5, 7.2): strictness, and the golden ALV fixture."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from playground.marketdata.manual_prices import ManualPriceFileError, load_price_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "manual_prices"
GOLDEN_ALLIANZ = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "inputs" / "manual_prices_allianz.csv"


def test_loads_a_well_formed_file() -> None:
    series_list = load_price_file((FIXTURES / "prices_ok.csv").read_bytes())

    assert len(series_list) == 1
    series = series_list[0]
    assert series.source == "manual"
    assert series.symbol == "TEST.DE"
    assert series.currency == "EUR"
    assert series.adjustment == "raw"
    assert [point.date for point in series.points] == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    assert series.points[1].close == Decimal("101.50")
    assert all(point.open is None and point.volume is None for point in series.points)


def test_points_come_back_sorted_by_date_regardless_of_file_order() -> None:
    data = b"date;data_symbol;close;currency;adjustment\n2024-01-03;TEST.DE;2;EUR;raw\n2024-01-02;TEST.DE;1;EUR;raw\n"
    series = load_price_file(data)[0]
    assert [point.date for point in series.points] == [date(2024, 1, 2), date(2024, 1, 3)]


def test_comment_lines_and_blank_lines_are_ignored() -> None:
    data = (
        b"# a comment\ndate;data_symbol;close;currency;adjustment\n\n2024-01-02;TEST.DE;1;EUR;raw\n# another comment\n"
    )
    series = load_price_file(data)[0]
    assert len(series.points) == 1


def test_wrong_header_is_refused_naming_the_line() -> None:
    with pytest.raises(ManualPriceFileError, match="Line 1"):
        load_price_file((FIXTURES / "prices_bad_header.csv").read_bytes())


def test_duplicate_date_for_one_symbol_is_refused_naming_the_line() -> None:
    with pytest.raises(ManualPriceFileError, match="Line 3"):
        load_price_file((FIXTURES / "prices_duplicate_date.csv").read_bytes())


def test_the_same_date_for_two_different_symbols_is_fine() -> None:
    data = b"date;data_symbol;close;currency;adjustment\n2024-01-02;A.DE;1;EUR;raw\n2024-01-02;B.DE;2;EUR;raw\n"
    series_list = load_price_file(data)
    assert {series.symbol for series in series_list} == {"A.DE", "B.DE"}


def test_a_bad_number_is_refused_naming_the_line() -> None:
    data = b"date;data_symbol;close;currency;adjustment\n2024-01-02;TEST.DE;not-a-number;EUR;raw\n"
    with pytest.raises(ManualPriceFileError, match="Line 2"):
        load_price_file(data)


def test_a_bad_date_is_refused_naming_the_line() -> None:
    data = b"date;data_symbol;close;currency;adjustment\n31.04.2024;TEST.DE;100.00;EUR;raw\n"
    with pytest.raises(ManualPriceFileError, match="Line 2"):
        load_price_file(data)


def test_an_unknown_adjustment_is_refused() -> None:
    data = b"date;data_symbol;close;currency;adjustment\n2024-01-02;TEST.DE;100.00;EUR;funky\n"
    with pytest.raises(ManualPriceFileError, match="adjustment"):
        load_price_file(data)


def test_the_wrong_number_of_fields_is_refused() -> None:
    data = b"date;data_symbol;close;currency;adjustment\n2024-01-02;TEST.DE;100.00;EUR\n"
    with pytest.raises(ManualPriceFileError):
        load_price_file(data)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(ManualPriceFileError, match="Empty file"):
        load_price_file(b"")


def test_multiple_series_group_by_symbol_currency_and_adjustment() -> None:
    data = (
        b"date;data_symbol;close;currency;adjustment\n"
        b"2024-01-02;A.DE;1;EUR;raw\n"
        b"2024-01-03;A.DE;2;EUR;raw\n"
        b"2024-01-02;B.US;3;USD;split\n"
    )
    series_list = load_price_file(data)
    by_symbol = {series.symbol: series for series in series_list}
    assert len(by_symbol["A.DE"].points) == 2
    assert by_symbol["B.US"].currency == "USD"
    assert by_symbol["B.US"].adjustment == "split"


# --- the golden Allianz fixture (plan 6.5, 1.2) -------------------------------------------------


def test_the_golden_allianz_fixture_covers_every_weekday_and_ends_at_290_on_2024_12_20() -> None:
    series = load_price_file(GOLDEN_ALLIANZ.read_bytes())[0]

    assert series.symbol == "ALV.DE"
    assert series.currency == "EUR"
    assert series.adjustment == "raw"
    assert series.points[0].date == date(2024, 6, 20)
    assert series.points[-1].date == date(2024, 12, 20)
    # 1.2: "ALV 4 x 290.00 ... from the manual file, last price 2024-12-20, flagged stale" -- the
    # value every later valuation day's as-of lookup must land on.
    assert series.points[-1].close == Decimal("290.00")
    # "weekdays from 2024-06-20 to 2024-12-20" (6.5): no gap, and no weekend day included.
    expected_days = []
    day = date(2024, 6, 20)
    while day <= date(2024, 12, 20):
        if day.weekday() < 5:
            expected_days.append(day)
        day += timedelta(days=1)
    assert [point.date for point in series.points] == expected_days
