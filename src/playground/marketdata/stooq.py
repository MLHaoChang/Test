"""The Stooq daily-bars price source (plan 5.6, 6.4).

`StooqClient` never builds its symbols from an ISIN: it only ever requests the `data_symbol` you
mapped an instrument to, or a benchmark's configured symbol (`configs/benchmarks.yaml`). Its
response is CSV with dot decimals; the body `"No data"` means Stooq has nothing for that symbol
(reported per instrument by the caller, not fatal); an HTML body or a non-200 status means the
source itself is unavailable, and the message points at the manual price file fallback (6.5).
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from playground.core.errors import PlaygroundError
from playground.http.client import HttpClient, HttpRequest, NetworkError, NoResponseError
from playground.marketdata.lake import PricePoint, PriceSeries

DEFAULT_BASE_URL = "https://stooq.com/q/d/l/"
_DATE_FORMAT = "%Y%m%d"
_NO_DATA_BODY = "No data"


class SourceNoData(PlaygroundError):
    """Stooq answered but has no data at all for this symbol."""


class SourceUnavailable(PlaygroundError):
    """Stooq could not be reached, or its response could not be read as daily bars (a non-200 status,
    or an HTML body). The message says what to do instead."""


@dataclass(frozen=True)
class StooqClient:
    """`daily_bars` never guesses a symbol: it fetches exactly the one it is given (plan 5.6).

    `manual_file_hint` says whether a `SourceUnavailable` message points to the manual price file
    (6.5). It does for an instrument's prices; a benchmark is read from its configured series, so
    `benchmarks.fetch_benchmark` turns the hint off.
    """

    http: HttpClient
    base_url: str = DEFAULT_BASE_URL
    manual_file_hint: bool = True

    def daily_bars(
        self, symbol: str, start: date, end: date, *, currency: str, adjustment: str = "split_dividend"
    ) -> PriceSeries:
        """Fetch `symbol`'s daily closes from `start` to `end` (inclusive).

        `currency` and `adjustment` are not part of Stooq's own CSV response (which is bare
        `Date,Open,High,Low,Close,Volume`); the caller supplies them from what it already knows
        (the instrument's mapped currency, or a benchmark's configured one, and 5.7's
        `split_dividend` assumption for Stooq, confirmed or corrected at UAT).

        Raises `SourceNoData` when Stooq's body is exactly `"No data"`, and `SourceUnavailable`
        for anything else that is not a 200 with a CSV body, and when no response came at all.
        """
        request = HttpRequest(
            method="GET",
            url=self.base_url,
            params={"s": symbol, "d1": start.strftime(_DATE_FORMAT), "d2": end.strftime(_DATE_FORMAT), "i": "d"},
        )
        try:
            response = self.http.send(request)
        except NetworkError as exc:
            if self.manual_file_hint:
                advice = "Check your connection, or load its closes from a file with pg prices import-file."
            else:
                advice = "Check your connection and try again."
            raise SourceUnavailable(f"Could not reach {exc.host} to fetch {symbol}: {exc.detail}. {advice}") from exc
        except NoResponseError as exc:
            raise SourceUnavailable(str(exc)) from exc
        if response.status != 200:
            raise SourceUnavailable(f"Stooq returned status {response.status} for {symbol}. {self._instead()}")
        text = response.body.decode("utf-8", errors="replace").strip()
        if text == _NO_DATA_BODY or text.startswith(_NO_DATA_BODY):
            raise SourceNoData(f"Stooq has no data for {symbol}.")
        if text.startswith("<"):
            raise SourceUnavailable(
                f"Stooq's response for {symbol} was not the expected CSV (it looks like an HTML page). "
                f"{self._instead()}"
            )
        points = _parse_csv(text, symbol=symbol)
        return PriceSeries(source="stooq", symbol=symbol, currency=currency, adjustment=adjustment, points=points)

    def _instead(self) -> str:
        """What to do when Stooq answers with something other than daily bars."""
        if self.manual_file_hint:
            return "Try the manual price file instead (pg prices import-file)."
        return "Try again later."


def _parse_csv(text: str, *, symbol: str) -> tuple[PricePoint, ...]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or [name.strip() for name in reader.fieldnames] != [
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]:
        raise SourceUnavailable(
            f"Stooq's response for {symbol} does not have the expected header: {reader.fieldnames!r}."
        )
    points = []
    for row in reader:
        try:
            points.append(
                PricePoint(
                    date=date.fromisoformat(row["Date"]),
                    open=_decimal_or_none(row["Open"]),
                    high=_decimal_or_none(row["High"]),
                    low=_decimal_or_none(row["Low"]),
                    close=Decimal(row["Close"]),
                    volume=int(row["Volume"]) if row["Volume"] else None,
                )
            )
        except (InvalidOperation, ValueError) as exc:
            raise SourceUnavailable(
                f"Stooq's response for {symbol} has a row that could not be read: {row!r}."
            ) from exc
    return tuple(points)


def _decimal_or_none(text: str) -> Decimal | None:
    return Decimal(text) if text else None
