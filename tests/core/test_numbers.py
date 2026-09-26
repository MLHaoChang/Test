"""Tests for German and English decimal number parsing (core/numbers.py, plan 5.1, 7.2)."""

from decimal import Decimal

import pytest

from playground.core.errors import NumberFormatError
from playground.core.numbers import parse_de_decimal, parse_en_decimal


class TestParseDeDecimal:
    def test_thousands_and_decimal(self) -> None:
        assert parse_de_decimal("1.825,60") == Decimal("1825.60")

    def test_negative_leading_zero(self) -> None:
        assert parse_de_decimal("-0,890") == Decimal("-0.890")

    def test_thousands_without_decimal_part(self) -> None:
        assert parse_de_decimal("1.000") == Decimal("1000")

    def test_keeps_six_decimal_places_exactly(self) -> None:
        # Trade Republic shows up to 6 decimal places for fractional shares (3.3);
        # parsed values are kept exactly as written, so trailing zeros must survive.
        value = parse_de_decimal("0,453400")
        assert value == Decimal("0.453400")
        assert str(value) == "0.453400"

    def test_plain_integer_no_separators(self) -> None:
        assert parse_de_decimal("15") == Decimal("15")

    def test_large_number_multiple_thousands_groups(self) -> None:
        assert parse_de_decimal("12.345.678,90") == Decimal("12345678.90")

    def test_positive_sign_is_accepted(self) -> None:
        assert parse_de_decimal("+140,00") == Decimal("140.00")

    def test_strips_surrounding_whitespace(self) -> None:
        assert parse_de_decimal("  140,00  ") == Decimal("140.00")

    def test_rejects_english_formatted_number(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_de_decimal("1,234.5")

    def test_rejects_double_dot(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_de_decimal("1..000")

    def test_rejects_bad_thousands_grouping(self) -> None:
        # A dot-separated group must be exactly 3 digits; "2345" is not.
        with pytest.raises(NumberFormatError):
            parse_de_decimal("1.2345,60")

    def test_rejects_two_decimal_commas(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_de_decimal("1,23,45")

    def test_rejects_non_numeric_text(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_de_decimal("abc")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_de_decimal("")

    def test_rejects_lone_sign(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_de_decimal("-")


class TestParseEnDecimal:
    def test_thousands_and_decimal(self) -> None:
        assert parse_en_decimal("1,825.60") == Decimal("1825.60")

    def test_plain_integer(self) -> None:
        assert parse_en_decimal("15") == Decimal("15")

    def test_keeps_trailing_zeros(self) -> None:
        assert str(parse_en_decimal("0.400")) == "0.400"

    def test_rejects_german_formatted_number(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_en_decimal("1.825,60")

    def test_rejects_non_numeric_text(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_en_decimal("xyz")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(NumberFormatError):
            parse_en_decimal("")
