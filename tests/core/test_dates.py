"""Tests for date parsing and Europe/Berlin to UTC conversion (core/dates.py, plan 3.3, 5.1, 7.2)."""

from datetime import UTC, date, datetime

import pytest

from playground.core.dates import BERLIN, berlin_minute, berlin_to_utc, format_ts_utc, parse_de_date
from playground.core.errors import DateFormatError, NonExistentLocalTimeError


class TestParseDeDate:
    def test_dot_separated(self) -> None:
        assert parse_de_date("13.05.2019") == date(2019, 5, 13)

    def test_iso(self) -> None:
        assert parse_de_date("2019-11-18") == date(2019, 11, 18)

    def test_german_month_abbreviation_with_period(self) -> None:
        assert parse_de_date("01 Apr. 2024") == date(2024, 4, 1)

    def test_german_month_full_name_no_period(self) -> None:
        assert parse_de_date("01 Mai 2024") == date(2024, 5, 1)

    def test_single_digit_day_and_abbreviation(self) -> None:
        assert parse_de_date("3 Dez. 2024") == date(2024, 12, 3)

    def test_strips_surrounding_whitespace(self) -> None:
        assert parse_de_date("  13.05.2019  ") == date(2019, 5, 13)

    def test_rejects_unrecognised_format(self) -> None:
        with pytest.raises(DateFormatError):
            parse_de_date("May 13th, 2019")

    def test_rejects_non_existent_calendar_date(self) -> None:
        with pytest.raises(DateFormatError):
            parse_de_date("31.04.2024")  # April has 30 days

    def test_rejects_unknown_month_name(self) -> None:
        with pytest.raises(DateFormatError):
            parse_de_date("01 Foo 2024")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(DateFormatError):
            parse_de_date("")


class TestBerlinToUtc:
    # Every `datetime(...)` passed INTO berlin_to_utc below is deliberately naive: that is
    # exactly the "local time as printed in the document" contract this function converts.
    # (# noqa: DTZ001 on each: ruff cannot tell a deliberate naive local time from a bug.)

    def test_winter_time_offset(self) -> None:
        # T2 in the golden portfolio: 2024-01-15 10:05 local, CET is UTC+1.
        result = berlin_to_utc(datetime(2024, 1, 15, 10, 5))  # noqa: DTZ001
        assert result == datetime(2024, 1, 15, 9, 5, tzinfo=UTC)

    def test_summer_time_offset(self) -> None:
        # T12 at 11:20 local is 09:20 UTC, spelled out explicitly in plan 3.3.
        result = berlin_to_utc(datetime(2024, 6, 12, 11, 20))  # noqa: DTZ001
        assert result == datetime(2024, 6, 12, 9, 20, tzinfo=UTC)

    def test_result_is_aware_utc(self) -> None:
        result = berlin_to_utc(datetime(2024, 6, 12, 11, 20))  # noqa: DTZ001
        assert result.tzinfo is UTC

    def test_rejects_aware_input(self) -> None:
        with pytest.raises(ValueError, match="naive"):
            berlin_to_utc(datetime(2024, 6, 12, 11, 20, tzinfo=BERLIN))

    def test_spring_forward_gap_is_rejected(self) -> None:
        # EU clocks jump from 02:00 to 03:00 CEST on the last Sunday in March 2024 (31 March);
        # 02:30 that day never happened.
        with pytest.raises(NonExistentLocalTimeError):
            berlin_to_utc(datetime(2024, 3, 31, 2, 30))  # noqa: DTZ001

    def test_just_before_spring_forward_gap(self) -> None:
        result = berlin_to_utc(datetime(2024, 3, 31, 1, 59))  # noqa: DTZ001
        assert result == datetime(2024, 3, 31, 0, 59, tzinfo=UTC)

    def test_just_after_spring_forward_gap(self) -> None:
        result = berlin_to_utc(datetime(2024, 3, 31, 3, 0))  # noqa: DTZ001
        assert result == datetime(2024, 3, 31, 1, 0, tzinfo=UTC)

    def test_autumn_fallback_ambiguous_time_does_not_raise(self) -> None:
        # 2024-10-27: clocks fall back from 03:00 CEST to 02:00 CET, so 02:30 occurs twice.
        # This must not raise (only a non-existent time does).
        berlin_to_utc(datetime(2024, 10, 27, 2, 30))  # noqa: DTZ001

    def test_autumn_fallback_picks_first_occurrence(self) -> None:
        # The first occurrence is summer time (CEST, UTC+2), Python's fold=0 default.
        result = berlin_to_utc(datetime(2024, 10, 27, 2, 30))  # noqa: DTZ001
        assert result == datetime(2024, 10, 27, 0, 30, tzinfo=UTC)


class TestFormatTsUtc:
    def test_formats_with_z_suffix(self) -> None:
        dt = datetime(2024, 6, 12, 9, 20, 0, tzinfo=UTC)
        assert format_ts_utc(dt) == "2024-06-12T09:20:00Z"

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="UTC"):
            format_ts_utc(datetime(2024, 6, 12, 9, 20, 0))  # noqa: DTZ001

    def test_rejects_non_utc_timezone(self) -> None:
        with pytest.raises(ValueError, match="UTC"):
            format_ts_utc(datetime(2024, 6, 12, 9, 20, 0, tzinfo=BERLIN))


@pytest.mark.parametrize(
    ("ts_utc", "shown"),
    [
        ("2024-12-31T11:00:00Z", "2024-12-31 12:00"),  # winter time, UTC+1
        ("2024-07-01T10:00:00Z", "2024-07-01 12:00"),  # summer time, UTC+2
        ("2024-12-31T23:30:00Z", "2025-01-01 00:30"),  # already the next day in Berlin
    ],
)
def test_berlin_minute_shows_a_stored_timestamp_in_berlin_time(ts_utc: str, shown: str) -> None:
    assert berlin_minute(ts_utc) == shown
