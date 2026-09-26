"""The injectable clock (plan 3.3): the only place allowed to read the system clock.

Every part of the app that needs "now" or "today" takes a `Clock`
instead of calling `datetime.now()` or `date.today()` itself
(`tests/test_repo_hygiene.py` checks that those two calls appear nowhere
else). Tests, the end-to-end scenario and the Playwright server all fix
the day with `PG_TODAY`, so a run on any real day gives the same
timestamps and the same 30-day import reminder. Without `PG_TODAY` the
app falls back to `SystemClock`, which reads the real system clock, here
and nowhere else in the codebase.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Protocol

from playground.config import Settings
from playground.core.dates import BERLIN, berlin_to_utc
from playground.core.errors import InvalidClockSettingError

_ISO_DATE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Clock(Protocol):
    """Something that can answer "what time is it" and "what day is it"."""

    def now_utc(self) -> datetime:
        """Return the current instant, aware, in UTC."""
        ...

    def today(self) -> date:
        """Return the current calendar date, in the Europe/Berlin time zone."""
        ...


@dataclass(frozen=True)
class SystemClock:
    """Reads the real system clock. The only class in the app allowed to."""

    def now_utc(self) -> datetime:
        """Return the current instant, aware, in UTC."""
        return datetime.now(UTC)

    def today(self) -> date:
        """Return today's date in the Europe/Berlin calendar (3.3), not the server's own time zone."""
        return datetime.now(BERLIN).date()


@dataclass(frozen=True)
class FixedClock:
    """A clock stopped on one calendar day, for tests, the e2e scenario and the Playwright server.

    `now_utc()` reports 12:00 Berlin time on that day (3.3), converted to
    UTC (11:00 in winter, 10:00 in summer), so anything that stamps "when
    this happened" with the current time gets a stable value regardless
    of what day the app is actually run on.
    """

    fixed_date: date

    def now_utc(self) -> datetime:
        """Return 12:00 Berlin time on `fixed_date`, converted to UTC."""
        noon_local = datetime.combine(self.fixed_date, time(12, 0, 0))
        return berlin_to_utc(noon_local)

    def today(self) -> date:
        """Return `fixed_date`."""
        return self.fixed_date


def today_from_env(value: str | None) -> date | None:
    """Parse the `PG_TODAY` environment variable, or return `None` if it is unset or empty.

    The format is strictly `YYYY-MM-DD` (3.3). Anything else raises
    `InvalidClockSettingError` with a plain-English message; the value is
    never guessed at or silently ignored.
    """
    if value is None or value == "":
        return None
    if not _ISO_DATE_SHAPE.match(value):
        raise InvalidClockSettingError(f"PG_TODAY must be a date in YYYY-MM-DD format. Got: {value!r}.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidClockSettingError(f"PG_TODAY must be a date in YYYY-MM-DD format. Got: {value!r}.") from exc


def clock_from_settings(settings: Settings) -> Clock:
    """Return the `Clock` the app should use for `settings` (3.3).

    `settings.today` is set (via `today_from_env`) whenever `PG_TODAY` is
    set; this then returns a `FixedClock` on that day. Otherwise it
    returns a `SystemClock`.
    """
    if settings.today is not None:
        return FixedClock(settings.today)
    return SystemClock()
