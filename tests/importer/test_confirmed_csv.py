"""Tests for the confirmed-holdings format (plan 5.3.3, 5.3.6).

Unlike the transaction formats, a confirmed-holdings file is never staged or matched: it is read
once, right before a reconciliation diff, so a bad file is refused outright with a message
naming the line, rather than turned into a review item. These tests check the happy path for
both decimal conventions and the message for each documented way the file can be wrong.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from playground.importer.confirmed_csv import ConfirmedCsvError, ConfirmedHolding, parse_confirmed_csv

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "confirmed"


def test_decimal_comma_is_read_when_declared() -> None:
    data = (FIXTURES_DIR / "holdings_comma.csv").read_bytes()
    holdings = parse_confirmed_csv(data)
    assert holdings == [
        ConfirmedHolding(isin="DE0007164600", quantity=Decimal("3"), as_of=date(2024, 12, 31)),
        ConfirmedHolding(isin="IE00B4L5Y983", quantity=Decimal("5"), as_of=date(2024, 12, 31)),
        ConfirmedHolding(isin="DE0008404005", quantity=Decimal("4.5"), as_of=date(2024, 12, 31)),
    ]


def test_decimal_dot_is_read_when_declared() -> None:
    data = (FIXTURES_DIR / "holdings_dot.csv").read_bytes()
    holdings = parse_confirmed_csv(data)
    assert holdings == [
        ConfirmedHolding(isin="DE0007164600", quantity=Decimal("3"), as_of=date(2024, 12, 31)),
        ConfirmedHolding(isin="IE00B4L5Y983", quantity=Decimal("5"), as_of=date(2024, 12, 31)),
        ConfirmedHolding(isin="DE0008404005", quantity=Decimal("4.5"), as_of=date(2024, 12, 31)),
    ]


def test_the_golden_confirmed_holdings_file_gives_five_rows() -> None:
    # Plan 7.6 step 7/8: "5 of 5 match, NVDA at 20 after the split".
    golden = (
        Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "inputs" / "confirmed_holdings_2024-12-31.csv"
    )
    holdings = parse_confirmed_csv(golden.read_bytes())
    assert len(holdings) == 5
    by_isin = {holding.isin: holding.quantity for holding in holdings}
    assert by_isin == {
        "DE0007164600": Decimal("3"),
        "IE00B4L5Y983": Decimal("5"),
        "US0378331005": Decimal("5"),
        "US67066G1040": Decimal("20"),
        "DE0008404005": Decimal("4"),
    }
    assert {holding.as_of for holding in holdings} == {date(2024, 12, 31)}


def test_a_missing_decimal_directive_is_refused() -> None:
    data = b"isin;quantity;as_of\nDE0007164600;3;2024-12-31\n"
    with pytest.raises(ConfirmedCsvError, match="decimal separator"):
        parse_confirmed_csv(data)


def test_an_unrecognised_decimal_directive_is_refused() -> None:
    data = b"# decimal=x\nisin;quantity;as_of\nDE0007164600;3;2024-12-31\n"
    with pytest.raises(ConfirmedCsvError, match='"," or "."'):
        parse_confirmed_csv(data)


def test_a_wrong_header_is_refused_naming_the_line() -> None:
    data = b"# decimal=,\nisin;amount;as_of\nDE0007164600;3;2024-12-31\n"
    with pytest.raises(ConfirmedCsvError, match="Line 2"):
        parse_confirmed_csv(data)


def test_a_row_with_the_wrong_number_of_fields_is_refused_naming_the_line() -> None:
    data = b"# decimal=,\nisin;quantity;as_of\nDE0007164600;3\n"
    with pytest.raises(ConfirmedCsvError, match="Line 3"):
        parse_confirmed_csv(data)


def test_an_invalid_isin_is_refused_naming_the_line() -> None:
    data = b"# decimal=,\nisin;quantity;as_of\nDE0007164601;3;2024-12-31\n"
    with pytest.raises(ConfirmedCsvError, match="Line 3.*not a valid ISIN"):
        parse_confirmed_csv(data)


def test_a_bad_quantity_is_refused_naming_the_line() -> None:
    data = b"# decimal=,\nisin;quantity;as_of\nDE0007164600;abc;2024-12-31\n"
    with pytest.raises(ConfirmedCsvError, match="Line 3.*quantity"):
        parse_confirmed_csv(data)


def test_a_quantity_in_the_undeclared_decimal_style_is_refused() -> None:
    # decimal=. is declared, so a comma quantity ("3,5") is not English-formatted and is refused.
    data = b"# decimal=.\nisin;quantity;as_of\nDE0007164600;3,5;2024-12-31\n"
    with pytest.raises(ConfirmedCsvError, match="Line 3.*quantity"):
        parse_confirmed_csv(data)


def test_a_bad_date_is_refused_naming_the_line() -> None:
    data = b"# decimal=,\nisin;quantity;as_of\nDE0007164600;3;2024-13-40\n"
    with pytest.raises(ConfirmedCsvError, match="Line 3.*date"):
        parse_confirmed_csv(data)


def test_isin_is_normalised_like_any_user_typed_isin() -> None:
    # normalise_isin (core.isin) strips an "ISIN:" label and internal spaces and upper-cases it,
    # since this file is one you type yourself.
    data = b"# decimal=,\nisin;quantity;as_of\n de0007164600 ;3;2024-12-31\n"
    holdings = parse_confirmed_csv(data)
    assert holdings == [ConfirmedHolding(isin="DE0007164600", quantity=Decimal("3"), as_of=date(2024, 12, 31))]


def test_blank_lines_between_rows_are_ignored() -> None:
    data = b"# decimal=,\nisin;quantity;as_of\n\nDE0007164600;3;2024-12-31\n\nIE00B4L5Y983;5;2024-12-31\n"
    holdings = parse_confirmed_csv(data)
    assert [holding.isin for holding in holdings] == ["DE0007164600", "IE00B4L5Y983"]
