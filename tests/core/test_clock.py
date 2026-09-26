"""Tests for the injectable clock (core/clock.py, plan 3.3, 5.1, 7.2)."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from playground.config import Settings
from playground.core.clock import FixedClock, SystemClock, clock_from_settings, today_from_env
from playground.core.errors import InvalidClockSettingError


class TestFixedClock:
    def test_today_returns_fixed_date(self) -> None:
        clock = FixedClock(date(2024, 12, 31))
        assert clock.today() == date(2024, 12, 31)

    def test_now_utc_is_noon_berlin_in_winter(self) -> None:
        # 2024-12-31 is winter time (CET, UTC+1): 12:00 Berlin -> 11:00 UTC.
        clock = FixedClock(date(2024, 12, 31))
        assert clock.now_utc() == datetime(2024, 12, 31, 11, 0, 0, tzinfo=timezone.utc)

    def test_now_utc_is_noon_berlin_in_summer(self) -> None:
        # 2024-06-12 is summer time (CEST, UTC+2): 12:00 Berlin -> 10:00 UTC.
        clock = FixedClock(date(2024, 6, 12))
        assert clock.now_utc() == datetime(2024, 6, 12, 10, 0, 0, tzinfo=timezone.utc)

    def test_now_utc_is_aware(self) -> None:
        clock = FixedClock(date(2024, 12, 31))
        assert clock.now_utc().tzinfo is not None


class TestSystemClock:
    def test_now_utc_is_aware_and_close_to_real_time(self) -> None:
        clock = SystemClock()
        now = clock.now_utc()
        assert now.tzinfo is not None
        assert abs((datetime.now(timezone.utc) - now).total_seconds()) < 5

    def test_today_returns_a_date(self) -> None:
        assert isinstance(SystemClock().today(), date)


class TestTodayFromEnv:
    def test_none_is_unset(self) -> None:
        assert today_from_env(None) is None

    def test_empty_string_is_unset(self) -> None:
        assert today_from_env("") is None

    def test_valid_date(self) -> None:
        assert today_from_env("2024-12-31") == date(2024, 12, 31)

    def test_rejects_wrong_order_format(self) -> None:
        with pytest.raises(InvalidClockSettingError):
            today_from_env("31-12-2024")

    def test_rejects_slash_format(self) -> None:
        with pytest.raises(InvalidClockSettingError):
            today_from_env("2024/12/31")

    def test_rejects_non_date_text(self) -> None:
        with pytest.raises(InvalidClockSettingError):
            today_from_env("not-a-date")

    def test_rejects_invalid_calendar_date(self) -> None:
        with pytest.raises(InvalidClockSettingError):
            today_from_env("2024-02-30")


class TestClockFromSettings:
    def test_returns_fixed_clock_when_today_is_set(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path, today=date(2024, 12, 31))
        clock = clock_from_settings(settings)
        assert isinstance(clock, FixedClock)
        assert clock.today() == date(2024, 12, 31)

    def test_returns_system_clock_when_today_is_unset(self, tmp_path: Path) -> None:
        settings = Settings(data_dir=tmp_path)
        clock = clock_from_settings(settings)
        assert isinstance(clock, SystemClock)
