"""Holdings over time: what the lot book held at the end of each booking day (plan 5.5).

Holdings and open cost as of a day count every lot from its **booking date**: the
Europe/Berlin calendar day on which it entered the account. For a purchase that is the day of
the trade; for a transfer in it is the day it was booked in, not the day you acquired the shares.
So the Allianz lot of the golden portfolio, acquired in 2020 but booked in on 2024-06-20, is not
part of the holdings or the cost basis of 2024-05-31 (plan 1.2). FIFO order uses the acquisition
date instead (fifo.py).

While `fifo.build_lots` applies transactions in booking order, it records one `PositionPoint`
per ISIN at the end of every booking day on which that ISIN's quantity or open cost changed.
Everything here reads those points with an as-of lookup (the latest point dated on or before a
day). Nothing here reads the system clock or the registry.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from playground.core.dates import BERLIN

_ZERO = Decimal(0)


def booking_date(ts_utc: datetime) -> date:
    """The Europe/Berlin calendar date of an aware booking time (plan 3.3).

    A savings plan booked on 2024-02-01 at 00:00 Berlin time is 2024-01-31 23:00 UTC, and its
    booking date is 2024-02-01. Raises `ValueError` for a datetime without a time zone.
    """
    if ts_utc.tzinfo is None or ts_utc.utcoffset() is None:
        raise ValueError(f"booking_date needs a booking time with a time zone, got {ts_utc!r}.")
    return ts_utc.astimezone(BERLIN).date()


@dataclass(frozen=True)
class PositionPoint:
    """One ISIN's position at the end of one booking day.

    `open_cost_eur` is the cost basis of the lots still open: `None` while one of them has no
    known cost (a transfer in whose cost you have not entered yet), 0 once nothing is held.
    """

    day: date
    quantity: Decimal
    open_cost_eur: Decimal | None


_NOTHING_HELD = PositionPoint(date.min, _ZERO, _ZERO)


class PositionRecorder:
    """Collects the end-of-day points while the lot book is built in booking order."""

    def __init__(self) -> None:
        self._points: dict[str, list[PositionPoint]] = {}

    def record(self, isin: str, day: date, quantity: Decimal, open_cost_eur: Decimal | None) -> None:
        """Set `isin`'s position after the latest transaction of `day`.

        A later call for the same day replaces the earlier one, so a day keeps only its end
        state. A day whose end state equals the day before it leaves no point. Days must not go
        backwards, which booking order guarantees.
        """
        points = self._points.setdefault(isin, [])
        if points and points[-1].day > day:
            raise ValueError(f"Positions must be recorded in booking order: {day} comes after {points[-1].day}.")
        if points and points[-1].day == day:
            points.pop()
        before = points[-1] if points else _NOTHING_HELD
        if before.quantity == quantity and before.open_cost_eur == open_cost_eur:
            return
        points.append(PositionPoint(day, quantity, open_cost_eur))

    def history(self) -> dict[str, list[PositionPoint]]:
        """Every ISIN that ever had a point, sorted by ISIN, with its points in date order."""
        return {isin: list(points) for isin, points in sorted(self._points.items()) if points}


def point_on(points: Sequence[PositionPoint], as_of: date) -> PositionPoint | None:
    """The latest point dated on or before `as_of`, or `None` when there is none yet."""
    index = bisect_right(points, as_of, key=lambda point: point.day)
    return points[index - 1] if index else None


def holdings_on(positions: Mapping[str, Sequence[PositionPoint]], as_of: date) -> dict[str, Decimal]:
    """The quantity of every ISIN held at the end of `as_of`, sorted by ISIN; nothing held is left out."""
    held: dict[str, Decimal] = {}
    for isin in sorted(positions):
        point = point_on(positions[isin], as_of)
        if point is not None and point.quantity != 0:
            held[isin] = point.quantity
    return held


def open_cost_on(positions: Mapping[str, Sequence[PositionPoint]], as_of: date) -> dict[str, Decimal | None]:
    """The open cost basis of every ISIN held at the end of `as_of` (`None`: a lot's cost is unknown)."""
    costs: dict[str, Decimal | None] = {}
    for isin in sorted(positions):
        point = point_on(positions[isin], as_of)
        if point is not None and point.quantity != 0:
            costs[isin] = point.open_cost_eur
    return costs


def timeline_of(positions: Mapping[str, Sequence[PositionPoint]]) -> dict[str, list[tuple[date, Decimal]]]:
    """Per ISIN, `(booking date, quantity at the end of that day)` for every day the quantity changed.

    The first entry is the first day something was held; a position sold out ends with a
    quantity of 0. An ISIN never held overnight has no entry.
    """
    timeline: dict[str, list[tuple[date, Decimal]]] = {}
    for isin in sorted(positions):
        steps: list[tuple[date, Decimal]] = []
        previous = _ZERO
        for point in positions[isin]:
            if point.quantity != previous:
                steps.append((point.day, point.quantity))
                previous = point.quantity
        if steps:
            timeline[isin] = steps
    return timeline
