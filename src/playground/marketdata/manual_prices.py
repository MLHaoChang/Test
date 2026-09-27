"""The manual price file: a documented fallback for any instrument (plan 5.6, 6.5).

Format: `date;data_symbol;close;currency;adjustment`, semicolon-separated, dot decimals, ISO
dates, `#` comment lines, one header line first. Strict on purpose (5.6, 6.5): an unknown header,
a bad number, an unknown `adjustment`, or the same symbol given a price twice for one date is
refused with a message naming the line, rather than silently keeping one of the two.
"""

import csv
from collections import defaultdict
from datetime import date

from playground.core.errors import NumberFormatError, PlaygroundError
from playground.core.numbers import parse_en_decimal
from playground.marketdata.lake import PricePoint, PriceSeries

HEADER = ("date", "data_symbol", "close", "currency", "adjustment")
VALID_ADJUSTMENTS = frozenset({"raw", "split", "split_dividend"})


class ManualPriceFileError(PlaygroundError):
    """The manual price file is not in the documented format (plan 6.5)."""


def load_price_file(data: bytes) -> list[PriceSeries]:
    """Parse a manual price file into one `PriceSeries` per `(data_symbol, currency, adjustment)`.

    Raises `ManualPriceFileError`, naming the 1-based line, for a missing or wrong header, the
    wrong number of fields, an unparsable date or number, an unknown `adjustment`, or a second
    price for a symbol on a date it already has one.
    """
    text = data.decode("utf-8-sig")
    header_seen = False
    seen_dates: dict[str, set[date]] = defaultdict(set)
    rows: dict[tuple[str, str, str], list[PricePoint]] = {}

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = next(csv.reader([line], delimiter=";"))
        if not header_seen:
            if tuple(field.strip().lower() for field in fields) != HEADER:
                raise ManualPriceFileError(f"Line {line_no}: expected the header {';'.join(HEADER)}, got {line!r}.")
            header_seen = True
            continue
        if len(fields) != len(HEADER):
            raise ManualPriceFileError(f"Line {line_no}: expected {len(HEADER)} fields, got {len(fields)}: {line!r}.")

        date_text, symbol, close_text, currency, adjustment = (field.strip() for field in fields)
        try:
            day = date.fromisoformat(date_text)
        except ValueError as exc:
            raise ManualPriceFileError(f"Line {line_no}: not a date (expected YYYY-MM-DD): {date_text!r}.") from exc
        try:
            close = parse_en_decimal(close_text)
        except NumberFormatError as exc:
            raise ManualPriceFileError(f"Line {line_no}: not a number: {close_text!r}.") from exc
        if adjustment not in VALID_ADJUSTMENTS:
            raise ManualPriceFileError(
                f"Line {line_no}: unknown adjustment {adjustment!r}. Use one of: {', '.join(sorted(VALID_ADJUSTMENTS))}."
            )
        if day in seen_dates[symbol]:
            raise ManualPriceFileError(f"Line {line_no}: {symbol} already has a price for {day.isoformat()}.")
        seen_dates[symbol].add(day)

        key = (symbol, currency.upper(), adjustment)
        rows.setdefault(key, []).append(PricePoint(date=day, open=None, high=None, low=None, close=close, volume=None))

    if not header_seen:
        raise ManualPriceFileError(f"Empty file: expected a header line {';'.join(HEADER)}.")

    return [
        PriceSeries(
            source="manual",
            symbol=symbol,
            currency=currency,
            adjustment=adjustment,
            points=tuple(sorted(points, key=lambda point: point.date)),
        )
        for (symbol, currency, adjustment), points in rows.items()
    ]
