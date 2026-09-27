"""The daily value of the portfolio in EUR (plan 5.7).

This module is pure: it values a lot book (`ledger.fifo.LotBook`) with stored daily closes and ECB
reference rates, and never reads or writes the registry. `portfolio.py` gives it what it needs and
stores what it returns.

Terms used here:

- A **close** is the last price of a trading day, in the listing currency of the symbol an
  instrument is mapped to.
- **As-of lookup**: taking the latest value dated on or before a given day.
- A **flag** is a note on a value: a holding left out, a price or rate that is old, or a check
  that failed. A value never fails on missing data; it carries a flag instead.

The rules (the UAT guide repeats them):

- **Calendar.** One value per weekday (Monday to Friday) from `start` to `end`. Weekends are
  skipped. Exchange holidays are not, because a holiday on one exchange is a trading day on
  another.
- **Range.** `pg value` without `--from` and `--to`, and the rebuild after an accept, a
  resolution or `pg transfers set-cost`, cover the weekdays from the booking date of the first
  accepted transaction to the last weekday on or before today (`default_range`, with today from
  the clock of plan 3.3). The latest value is the one on the last day of that range.
- **Price basis.** The daily close in the listing currency of the mapped symbol. Each stored
  series declares its adjustment: `raw`, `split` (adjusted for splits) or `split_dividend`
  (adjusted for splits and dividends).
- **Quantity for pricing.** For a `raw` series: the quantity held on that day. For a `split` or
  `split_dividend` series: the quantity held times the ratios of every split the ledger booked
  after that day, so that quantity and price are on the same share basis. That product is the
  same before and after a split, so the value does not depend on the exact day the split was
  booked. A `split_dividend` series carries the info flag `dividend_adjusted_prices`: past values
  are slightly understated. A `raw` series gets `near_split_raw_prices` on the days within 3
  weekdays of a split.
- **As-of alignment.** For each day and instrument: the latest close dated on or before that day.
  For each foreign currency: the latest ECB rate dated on or before that day. Each holding keeps
  the dates it used (`HoldingValue.price_date` and `fx_date`).
- **Staleness.** A close or rate more than `stale_after_days` (default 5) calendar days older
  than the day valued is still used, and is flagged `stale_price` or `stale_fx`.
- **Missing.** No close on or before the day: the holding is left out of that day's value and
  flagged `missing_price`. The same for a missing rate (`missing_fx`). An instrument without a
  confirmed mapping is flagged `unmapped` and left out. A day with any holding left out has
  `complete` false. Nothing raises.
- **Totals.** A holding's value is quantised to 8 decimals. The day's total is the sum of those
  unrounded parts; it is rounded to cents only when shown.
- **Cost basis.** The open cost basis of the lots held that day, from the ledger (a lot counts
  from its booking date). It is unknown while an open lot has no cost (a transfer in whose cost
  you have not entered), which is flagged `cost_basis_missing`. It does not decide `complete`.
- **Price basis check.** For an instrument priced from a `split` or `split_dividend` series, the
  price of its most recent buy or sell in the 365 days before the end of the range (in EUR, as
  Trade Republic prints it) is compared with the close on the day of that trade, converted to EUR
  at that day's ECB rate and multiplied by the ledger's split factor after that day. If the two
  differ by more than a factor of 1.4, the instrument gets `price_basis_mismatch` on every day of
  the range on which it is held, as a warning; `complete` does not change. The usual cause is a
  split after your last import: the source has already adjusted its whole history, but the
  ledger has not booked the split, so the value is off by the split ratio. Importing the split
  document clears the flag. The trade is checked only when a close, and for a foreign listing a
  rate, exists no more than `stale_after_days` before its day.
- **Known limit.** An instrument with no buy or sell in the last 365 days is not checked, so a
  split the ledger has not seen can go unnoticed for it. Import split documents before you trust
  a value.
- **Pence.** A close in pence (`GBX`) is divided by 100 and converted with the GBP rate.
- **Fallback source.** Any instrument can be mapped to `data_source = manual` and priced from a
  file you provide (`pg prices import-file`).
"""

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal, Protocol

from playground.core.money import eur2, q8, to_eur
from playground.core.types import TxnType
from playground.ledger.fifo import LotBook
from playground.ledger.holdings import booking_date
from playground.marketdata.lake import DEFAULT_STALE_AFTER_DAYS, FxPoint, PricePoint, PriceSeries, is_stale

FlagKind = Literal[
    "unmapped",
    "missing_price",
    "stale_price",
    "missing_fx",
    "stale_fx",
    "dividend_adjusted_prices",
    "cost_basis_missing",
    "near_split_raw_prices",
    "price_basis_mismatch",
]
FLAG_KINDS: tuple[FlagKind, ...] = (
    "unmapped",
    "missing_price",
    "stale_price",
    "missing_fx",
    "stale_fx",
    "dividend_adjusted_prices",
    "cost_basis_missing",
    "near_split_raw_prices",
    "price_basis_mismatch",
)
"""Every kind of flag, in the order of plan 5.7. Flags are listed in this order, then by ISIN."""

LEFT_OUT_KINDS: frozenset[FlagKind] = frozenset({"unmapped", "missing_price", "missing_fx"})
"""The flags that leave a holding out of the day's value, so the day is not complete."""

ADJUSTED_BASES = frozenset({"split", "split_dividend"})
"""The adjustments whose closes are on today's share basis: they need the split factor."""

MISMATCH_FACTOR = Decimal("1.4")
"""The price basis check flags a trade price and a close that differ by more than this factor."""
PRICE_CHECK_DAYS = 365
"""The price basis check looks at trades up to this many days before the end of the range."""
NEAR_SPLIT_WEEKDAYS = 3
"""A raw series is flagged on the days up to this many weekdays before or after a split."""

_ZERO = Decimal(0)
_COMPUTED_PLACES = 8
_TRADE_LABELS = {TxnType.BUY: "purchase", TxnType.SELL: "sale"}


# --- Inputs and results -----------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    """One note on a value (see the module docstring). `detail` says what it means in plain English."""

    isin: str | None
    kind: FlagKind
    detail: str

    @property
    def label(self) -> str:
        """The short form a day lists its flags in: the kind, then the ISIN."""
        return self.kind if self.isin is None else f"{self.kind} {self.isin}"

    def sort_key(self) -> tuple[int, str]:
        return (FLAG_KINDS.index(self.kind), self.isin or "")


@dataclass(frozen=True)
class Instrument:
    """An instrument and its price mapping, as the valuation sees it (the `instruments` table, plan 4.1)."""

    isin: str
    name: str
    mapping_status: str
    data_source: str | None
    data_symbol: str | None
    currency: str | None

    @property
    def mapped(self) -> bool:
        """True when the mapping is confirmed and names a source and a symbol."""
        return self.mapping_status == "confirmed" and bool(self.data_source) and bool(self.data_symbol)


@dataclass(frozen=True)
class Trade:
    """A buy or sell with its price per share in EUR, as the price basis check needs it."""

    isin: str
    ts_utc: datetime
    price_eur: Decimal
    type: TxnType = TxnType.BUY

    @property
    def day(self) -> date:
        """The booking date: the Europe/Berlin calendar date of the trade."""
        return booking_date(self.ts_utc)


@dataclass(frozen=True)
class HoldingValue:
    """One instrument held on one day, with the evidence behind its value (`holdings_snapshots`, plan 4.1).

    `quantity` is what the ledger holds; `pricing_quantity` is on the share basis of the series (see
    the module docstring). `price`, `price_date` and `price_currency` are the close used; `fx_rate`,
    `fx_currency` and `fx_date` the ECB rate used, for a foreign listing only. `value_eur` is `None`
    when the holding is left out of the day's value, and `cost_basis_eur` when a lot's cost is unknown.
    """

    isin: str
    quantity: Decimal
    pricing_quantity: Decimal
    cost_basis_eur: Decimal | None
    value_eur: Decimal | None
    price: Decimal | None
    price_date: date | None
    price_currency: str | None
    fx_rate: Decimal | None
    fx_currency: str | None
    fx_date: date | None
    source: str | None
    symbol: str | None
    adjustment: str | None
    flags: tuple[Flag, ...]


@dataclass(frozen=True)
class DayValue:
    """The value of the portfolio on one day (`portfolio_values`, plan 4.1).

    `value_eur` is the unrounded sum of the holdings' values; `cost_basis_eur` is `None` while a lot's
    cost is unknown; `complete` is false when a holding is left out. `flags` are the holdings' flags.
    """

    date: date
    value_eur: Decimal
    cost_basis_eur: Decimal | None
    complete: bool
    holdings: tuple[HoldingValue, ...]
    flags: tuple[Flag, ...]


class PriceLookup(Protocol):
    """Where stored closes come from: `marketdata.lake.PriceStore`, or `InMemoryPrices`."""

    def series(self, source: str, symbol: str) -> PriceSeries | None:
        """The stored series for `(source, symbol)`, or `None` when nothing is stored."""
        ...


class FxLookup(Protocol):
    """Where stored ECB rates come from: `marketdata.lake.FxStore`, or `InMemoryFx`."""

    def rates(self, currency: str) -> Sequence[FxPoint]:
        """Every stored rate for `currency` (units of it per 1 EUR), oldest first."""
        ...


class InMemoryPrices:
    """Closes held in memory, for tests and for callers that have the series at hand."""

    def __init__(self, series: Iterable[PriceSeries] = ()) -> None:
        self._series = {(item.source, item.symbol): item for item in series}

    def series(self, source: str, symbol: str) -> PriceSeries | None:
        return self._series.get((source, symbol))


class InMemoryFx:
    """ECB rates held in memory, for tests and for callers that have the rates at hand."""

    def __init__(self, points: Iterable[FxPoint] = ()) -> None:
        self._points: dict[str, list[FxPoint]] = {}
        for point in points:
            self._points.setdefault(point.currency, []).append(point)

    def rates(self, currency: str) -> list[FxPoint]:
        return sorted(self._points.get(currency, []), key=lambda point: point.date)


# --- Calendar and range -----------------------------------------------------------------------


def weekdays(start: date, end: date) -> list[date]:
    """Every Monday to Friday from `start` to `end`, both included; empty when `start` is after `end`."""
    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def last_weekday_on_or_before(day: date) -> date:
    """`day` itself on a weekday, else the Friday before it."""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def default_range(first_booking: date | None, today: date) -> tuple[date, date] | None:
    """The weekdays from the first booking date to the last weekday on or before `today` (5.7).

    `None` when nothing has been booked, or when no weekday lies between the two.
    """
    if first_booking is None:
        return None
    start = first_booking
    while start.weekday() >= 5:
        start += timedelta(days=1)
    end = last_weekday_on_or_before(today)
    return (start, end) if start <= end else None


# --- Valuing ----------------------------------------------------------------------------------


def value_series(
    book: LotBook,
    instruments: Mapping[str, Instrument],
    prices: PriceLookup,
    fx: FxLookup,
    start: date,
    end: date,
    *,
    trades: Sequence[Trade] = (),
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> list[DayValue]:
    """The value of `book` on every weekday from `start` to `end` (plan 5.7; the rules are in the module docstring).

    `instruments` maps an ISIN to its price mapping; an ISIN it does not know counts as unmapped.
    `trades` are the buys and sells the price basis check looks at. The holdings and the quantity
    timeline come from `book` itself (`LotBook.holdings`, `open_cost` and `splits`).
    """
    valuer = _Valuer(book, instruments, prices, fx, end=end, trades=trades, stale_after_days=stale_after_days)
    return [valuer.value_on(day) for day in weekdays(start, end)]


def value_on(
    book: LotBook,
    instruments: Mapping[str, Instrument],
    prices: PriceLookup,
    fx: FxLookup,
    day: date,
    *,
    trades: Sequence[Trade] = (),
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> DayValue:
    """The value of `book` on one day, any calendar day, as a range of that one day (plan 5.7)."""
    valuer = _Valuer(book, instruments, prices, fx, end=day, trades=trades, stale_after_days=stale_after_days)
    return valuer.value_on(day)


@dataclass(frozen=True)
class _Closes:
    """One stored series, ready for as-of lookups."""

    series: PriceSeries
    dates: tuple[date, ...]
    points: tuple[PricePoint, ...]

    @classmethod
    def of(cls, series: PriceSeries) -> "_Closes":
        points = tuple(sorted(series.points, key=lambda point: point.date))
        return cls(series=series, dates=tuple(point.date for point in points), points=points)

    def on_or_before(self, day: date) -> PricePoint | None:
        index = bisect_right(self.dates, day)
        return self.points[index - 1] if index else None


@dataclass(frozen=True)
class _Rates:
    """One currency's stored rates, ready for as-of lookups."""

    dates: tuple[date, ...]
    points: tuple[FxPoint, ...]

    @classmethod
    def of(cls, points: Iterable[FxPoint]) -> "_Rates":
        ordered = tuple(sorted(points, key=lambda point: point.date))
        return cls(dates=tuple(point.date for point in ordered), points=ordered)

    def on_or_before(self, day: date) -> FxPoint | None:
        index = bisect_right(self.dates, day)
        return self.points[index - 1] if index else None


class _Valuer:
    """Values one lot book over one range; reads every series and every currency's rates once."""

    def __init__(
        self,
        book: LotBook,
        instruments: Mapping[str, Instrument],
        prices: PriceLookup,
        fx: FxLookup,
        *,
        end: date,
        trades: Sequence[Trade],
        stale_after_days: int,
    ) -> None:
        self._book = book
        self._instruments = instruments
        self._prices = prices
        self._fx = fx
        self._end = end
        self._stale_after = stale_after_days
        self._closes: dict[tuple[str, str], _Closes | None] = {}
        self._rates: dict[str, _Rates] = {}
        self._checks: dict[str, Flag | None] = {}
        self._splits: dict[str, list[tuple[date, Fraction]]] = {}
        for applied in book.splits:
            self._splits.setdefault(applied.isin, []).append((booking_date(applied.ts_utc), applied.ratio))
        self._trades: dict[str, list[Trade]] = {}
        for trade in trades:
            self._trades.setdefault(trade.isin, []).append(trade)

    def value_on(self, day: date) -> DayValue:
        quantities = self._book.holdings(day)
        costs = self._book.open_cost(day)
        holdings = tuple(self._holding(isin, quantity, costs.get(isin), day) for isin, quantity in quantities.items())
        value = sum((item.value_eur for item in holdings if item.value_eur is not None), start=_ZERO)
        known_costs = [item.cost_basis_eur for item in holdings if item.cost_basis_eur is not None]
        cost = sum(known_costs, start=_ZERO) if len(known_costs) == len(holdings) else None
        return DayValue(
            date=day,
            value_eur=value,
            cost_basis_eur=cost,
            complete=all(item.value_eur is not None for item in holdings),
            holdings=holdings,
            flags=tuple(sorted((flag for item in holdings for flag in item.flags), key=Flag.sort_key)),
        )

    # --- One holding ---

    def _holding(self, isin: str, quantity: Decimal, cost: Decimal | None, day: date) -> HoldingValue:
        flags: list[Flag] = []
        if cost is None:
            flags.append(Flag(isin, "cost_basis_missing", _COST_MISSING))
        evidence = _Evidence(isin=isin, quantity=quantity, cost=cost, flags=flags)

        instrument = self._instruments.get(isin)
        if instrument is None or not instrument.mapped or not instrument.data_source or not instrument.data_symbol:
            flags.append(Flag(isin, "unmapped", _UNMAPPED))
            return evidence.left_out()
        source, symbol = instrument.data_source, instrument.data_symbol
        evidence.source, evidence.symbol = source, symbol

        closes = self._closes_of(source, symbol)
        point = closes.on_or_before(day) if closes is not None else None
        if closes is None or point is None:
            flags.append(Flag(isin, "missing_price", _missing_price(source, symbol)))
            if closes is not None:
                evidence.adjustment = closes.series.adjustment
                evidence.pricing_quantity = self._pricing_quantity(isin, quantity, day, closes.series.adjustment)
            return evidence.left_out()

        series = closes.series
        evidence.adjustment = series.adjustment
        evidence.pricing_quantity = self._pricing_quantity(isin, quantity, day, series.adjustment)
        evidence.price, evidence.price_date, evidence.price_currency = point.close, point.date, series.currency
        if is_stale(point.date, day, stale_after_days=self._stale_after):
            flags.append(Flag(isin, "stale_price", _stale_price(source, self._stale_after)))
        if series.adjustment == "split_dividend":
            flags.append(Flag(isin, "dividend_adjusted_prices", _dividend_adjusted(source, symbol)))
        if series.adjustment == "raw":
            near = self._near_split(isin, day)
            if near is not None:
                flags.append(Flag(isin, "near_split_raw_prices", _near_split_detail(source, symbol, near)))
        if series.adjustment in ADJUSTED_BASES:
            mismatch = self._price_basis_check(isin, closes)
            if mismatch is not None:
                flags.append(mismatch)

        rate: Decimal | None = None
        if series.currency != "EUR":
            rate_currency = _rate_currency(series.currency)
            rate_point = self._rate_on(rate_currency, day)
            if rate_point is None:
                flags.append(Flag(isin, "missing_fx", _missing_fx(rate_currency)))
                return evidence.left_out()
            if is_stale(rate_point.date, day, stale_after_days=self._stale_after):
                flags.append(Flag(isin, "stale_fx", _stale_fx(rate_currency, self._stale_after)))
            rate = rate_point.rate
            evidence.fx_rate, evidence.fx_currency, evidence.fx_date = rate, rate_currency, rate_point.date

        evidence.value = q8(to_eur(evidence.pricing_quantity * point.close, series.currency, rate))
        return evidence.valued()

    def _closes_of(self, source: str, symbol: str) -> _Closes | None:
        key = (source, symbol)
        if key not in self._closes:
            found = self._prices.series(source, symbol)
            self._closes[key] = _Closes.of(found) if found is not None and found.points else None
        return self._closes[key]

    def _rate_on(self, currency: str, day: date) -> FxPoint | None:
        rates = self._rates.get(currency)
        if rates is None:
            rates = _Rates.of(self._fx.rates(currency))
            self._rates[currency] = rates
        return rates.on_or_before(day)

    # --- Splits ---

    def _split_factor_after(self, isin: str, day: date) -> Fraction:
        """The product of the ratios of every split of `isin` the ledger booked after `day`."""
        factor = Fraction(1)
        for split_day, ratio in self._splits.get(isin, ()):
            if split_day > day:
                factor *= ratio
        return factor

    def _pricing_quantity(self, isin: str, quantity: Decimal, day: date, adjustment: str) -> Decimal:
        if adjustment not in ADJUSTED_BASES:
            return quantity
        return _scaled(quantity, self._split_factor_after(isin, day))

    def _near_split(self, isin: str, day: date) -> date | None:
        """The booking date of a split of `isin` within 3 weekdays of `day`, the nearest one; else `None`."""
        near = [
            split_day
            for split_day, _ in self._splits.get(isin, ())
            if _weekday_distance(day, split_day) <= NEAR_SPLIT_WEEKDAYS
        ]
        return min(near, key=lambda split_day: (_weekday_distance(day, split_day), split_day), default=None)

    # --- The price basis check ---

    def _price_basis_check(self, isin: str, closes: _Closes) -> Flag | None:
        if isin not in self._checks:
            self._checks[isin] = self._check_price_basis(isin, closes)
        return self._checks[isin]

    def _check_price_basis(self, isin: str, closes: _Closes) -> Flag | None:
        window_start = self._end - timedelta(days=PRICE_CHECK_DAYS)
        recent = [trade for trade in self._trades.get(isin, ()) if window_start <= trade.day <= self._end]
        if not recent:
            return None
        trade = max(recent, key=lambda item: item.ts_utc)
        day = trade.day
        point = closes.on_or_before(day)
        if point is None or is_stale(point.date, day, stale_after_days=self._stale_after):
            return None
        currency = closes.series.currency
        rate: Decimal | None = None
        if currency != "EUR":
            rate_point = self._rate_on(_rate_currency(currency), day)
            if rate_point is None or is_stale(rate_point.date, day, stale_after_days=self._stale_after):
                return None
            rate = rate_point.rate
        factor = self._split_factor_after(isin, day)
        on_ledger_basis = to_eur(point.close, currency, rate) * factor.numerator / factor.denominator
        if on_ledger_basis <= 0 or trade.price_eur <= 0:
            return None
        high, low = max(on_ledger_basis, trade.price_eur), min(on_ledger_basis, trade.price_eur)
        if high <= low * MISMATCH_FACTOR:
            return None
        return Flag(isin, "price_basis_mismatch", _mismatch_detail(trade, on_ledger_basis))


@dataclass
class _Evidence:
    """What is known about one holding while it is being valued."""

    isin: str
    quantity: Decimal
    cost: Decimal | None
    flags: list[Flag]
    pricing_quantity: Decimal | None = None
    value: Decimal | None = None
    price: Decimal | None = None
    price_date: date | None = None
    price_currency: str | None = None
    fx_rate: Decimal | None = None
    fx_currency: str | None = None
    fx_date: date | None = None
    source: str | None = None
    symbol: str | None = None
    adjustment: str | None = None

    def left_out(self) -> HoldingValue:
        self.value = None
        return self.valued()

    def valued(self) -> HoldingValue:
        return HoldingValue(
            isin=self.isin,
            quantity=self.quantity,
            pricing_quantity=self.pricing_quantity if self.pricing_quantity is not None else self.quantity,
            cost_basis_eur=self.cost,
            value_eur=self.value,
            price=self.price,
            price_date=self.price_date,
            price_currency=self.price_currency,
            fx_rate=self.fx_rate,
            fx_currency=self.fx_currency,
            fx_date=self.fx_date,
            source=self.source,
            symbol=self.symbol,
            adjustment=self.adjustment,
            flags=tuple(sorted(self.flags, key=Flag.sort_key)),
        )


# --- Helpers ----------------------------------------------------------------------------------


def _rate_currency(currency: str) -> str:
    """The ECB currency a close in `currency` is converted with: pence (GBX) use the pound's rate."""
    return "GBP" if currency == "GBX" else currency


def _weekday_distance(first: date, second: date) -> int:
    """The number of weekdays from the earlier of two days up to, but not including, the later one."""
    low, high = min(first, second), max(first, second)
    full_weeks, rest = divmod((high - low).days, 7)
    return full_weeks * 5 + sum(1 for offset in range(rest) if (low + timedelta(days=offset)).weekday() < 5)


def _scaled(quantity: Decimal, factor: Fraction) -> Decimal:
    """`quantity` times `factor`: exact when that fits 8 decimals, else quantised (plan 3.3)."""
    if factor == 1:
        return quantity
    scaled = quantity * factor.numerator / factor.denominator
    exponent = scaled.as_tuple().exponent
    places = max(0, -exponent) if isinstance(exponent, int) else 0
    return scaled if places <= _COMPUTED_PLACES else q8(scaled)


def _refresh_hint(source: str) -> str:
    if source == "manual":
        return "Load newer closes with pg prices import-file."
    return "Fetch newer closes with pg prices fetch."


_UNMAPPED = (
    "No price source is mapped for this instrument, so it is left out of the value. Map it with pg instruments map."
)
_COST_MISSING = "An open lot has no cost basis yet, so the cost basis is unknown. Enter it with pg transfers set-cost."


def _missing_price(source: str, symbol: str) -> str:
    fetch = (
        "Load a price file with pg prices import-file." if source == "manual" else "Fetch prices with pg prices fetch."
    )
    return (
        f"No close of {symbol} ({source}) is stored on or before the day valued, so it is left out of the value. "
        f"{fetch}"
    )


def _stale_price(source: str, stale_after_days: int) -> str:
    return (
        f"The close used is more than {stale_after_days} days older than the day valued. It is still used. "
        f"{_refresh_hint(source)}"
    )


def _missing_fx(currency: str) -> str:
    return (
        f"No ECB rate for {currency} is stored on or before the day valued, so it is left out of the value. "
        "Fetch rates with pg fx fetch."
    )


def _stale_fx(currency: str, stale_after_days: int) -> str:
    return (
        f"The ECB rate for {currency} used is more than {stale_after_days} days older than the day valued. "
        "It is still used. Fetch newer rates with pg fx fetch."
    )


def _dividend_adjusted(source: str, symbol: str) -> str:
    return (
        f"The closes of {symbol} ({source}) are adjusted for splits and dividends, "
        "so values before a dividend are slightly understated."
    )


def _near_split_detail(source: str, symbol: str, split_day: date) -> str:
    return (
        f"The closes of {symbol} ({source}) are not adjusted for splits, and a split was booked on "
        f"{split_day.isoformat()}, within {NEAR_SPLIT_WEEKDAYS} weekdays. Until the exchange and your ledger "
        "show the split on the same day, the value can be off by the split ratio."
    )


def _mismatch_detail(trade: Trade, on_ledger_basis: Decimal) -> str:
    label = _TRADE_LABELS.get(trade.type, "trade")
    return (
        f"Your {label} on {trade.day.isoformat()} was at {eur2(trade.price_eur)} EUR a share, but the close that day "
        f"is {eur2(on_ledger_basis)} EUR on the share basis of your ledger. They differ by more than a factor of "
        f"{MISMATCH_FACTOR}, so a split may be missing from your imports. Import the split document, then check "
        "the value again."
    )
