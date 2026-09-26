"""Date parsing and Europe/Berlin time-zone conversion (plan 3.3, 5.1).

Every transaction time in this app is stored as both `ts_utc` (always
UTC) and `ts_local` (the time exactly as printed in the source document,
with no offset), alongside the zone it came from and how precise it is
(minute or day). This module is where the Europe/Berlin conversion
happens; nothing else in the app should need `zoneinfo` directly.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from playground.core.errors import DateFormatError, NonExistentLocalTimeError

BERLIN = ZoneInfo("Europe/Berlin")

_DE_DOT_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DE_MONTH_NAME_DATE = re.compile(r"^(\d{1,2})\s+([A-Za-zÄÖÜäöüß]+)\.?\s+(\d{4})$")

_GERMAN_MONTHS: dict[str, int] = {
    "jan": 1,
    "januar": 1,
    "feb": 2,
    "februar": 2,
    "mär": 3,
    "maerz": 3,
    "märz": 3,
    "mrz": 3,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "jun": 6,
    "juni": 6,
    "jul": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "okt": 10,
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dez": 12,
    "dezember": 12,
}


def parse_de_date(text: str) -> date:
    """Parse a date in one of the forms Trade Republic documents use (6.2).

    Recognised forms: "13.05.2019" (day.month.year, dots); "2019-11-18"
    (ISO 8601); and a day, a German month name or abbreviation (with or
    without a trailing "."), and a year, separated by spaces -- "01 Apr.
    2024", "01 Mai 2024", "3 Dez. 2024".

    Raises `DateFormatError` on anything else, including a syntactically
    plausible but non-existent calendar date such as "31.04.2024" (April
    has 30 days).
    """
    cleaned = text.strip()

    match = _DE_DOT_DATE.match(cleaned)
    if match:
        day_text, month_text, year_text = match.groups()
        return _build_date(int(year_text), int(month_text), int(day_text), text)

    match = _ISO_DATE.match(cleaned)
    if match:
        year_text, month_text, day_text = match.groups()
        return _build_date(int(year_text), int(month_text), int(day_text), text)

    match = _DE_MONTH_NAME_DATE.match(cleaned)
    if match:
        day_text, month_name, year_text = match.groups()
        month = _GERMAN_MONTHS.get(month_name.lower())
        if month is None:
            raise DateFormatError(f"Not a recognised date: {text!r} (unknown month {month_name!r}).")
        return _build_date(int(year_text), month, int(day_text), text)

    raise DateFormatError(f"Not a recognised date: {text!r}.")


def _build_date(year: int, month: int, day: int, original: str) -> date:
    """Build a `date` from its parts, turning an invalid calendar date into a `DateFormatError`."""
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise DateFormatError(f"Not a real calendar date: {original!r}.") from exc


def berlin_to_utc(local: datetime) -> datetime:
    """Convert a naive Europe/Berlin datetime to an aware UTC datetime (3.3).

    `local` must be naive (no `tzinfo`); this function attaches the
    Europe/Berlin zone itself, so a datetime that already carries one is
    a caller error and raises `ValueError`. Summer time (CEST, UTC+2) and
    winter time (CET, UTC+1) are both handled through `zoneinfo`, which
    knows the EU's spring-forward and fall-back dates.

    A local time that does not exist -- the hour skipped every year when
    clocks spring forward from 02:00 to 03:00 CEST on the last Sunday in
    March -- raises `NonExistentLocalTimeError`: no document ever really
    booked a transaction then.

    A local time that occurs twice -- the hour repeated every year when
    clocks fall back from 03:00 CEST to 02:00 CET on the last Sunday in
    October -- is resolved to its FIRST occurrence (summer time, UTC+2),
    which is Python's default (`fold=0`). This is a deliberate choice,
    not a limitation: nothing in a source document distinguishes the two
    occurrences, and FIFO order (5.5) breaks any remaining tie by
    transaction id, so picking one consistently is enough.
    """
    if local.tzinfo is not None:
        raise ValueError(f"berlin_to_utc expects a naive datetime, got one with tzinfo already set: {local!r}.")

    aware = local.replace(tzinfo=BERLIN)
    utc = aware.astimezone(UTC)

    # zoneinfo silently shifts a non-existent local time onto a real instant instead of
    # raising, so the gap is detected by converting back: a genuine local time always
    # round-trips to itself; one from the spring-forward gap does not.
    roundtrip = utc.astimezone(BERLIN).replace(tzinfo=None)
    if roundtrip != local:
        raise NonExistentLocalTimeError(
            f"{local.isoformat()} does not exist in Europe/Berlin (spring daylight-saving gap)."
        )
    return utc


def format_ts_utc(dt: datetime) -> str:
    """Format an aware UTC datetime as the ISO-8601-with-"Z" string every stored TS uses (3.3).

    Example: `datetime(2024, 6, 12, 9, 20, tzinfo=UTC)` ->
    `"2024-06-12T09:20:00Z"`. Raises `ValueError` if `dt` is naive or not
    exactly UTC.
    """
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError(f"format_ts_utc requires an aware UTC datetime, got {dt!r}.")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SourceTime:
    """A transaction's time, in both forms the registry stores (3.3)."""

    ts_utc: datetime
    ts_local: datetime
    source_tz: str
    precision: Literal["minute", "day"]
