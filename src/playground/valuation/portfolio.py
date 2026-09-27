"""The value of the stored portfolio (plan 5.7): read it, value it, store it.

This is the service behind `pg value`, the value columns of `pg holdings`, the latest value of
`pg status`, and the value step of accept, a resolution and `pg transfers set-cost`. The CLI and
the API call it with the same arguments, so both show the same figures.

- `load_inputs` reads what the valuation needs from the registry: the lot book of the accepted
  transactions (held-back and staged ones are left out, as for the lots), the instrument master,
  the price of every accepted buy and sell (for the price basis check) and the booking date of
  the first accepted transaction.
- `compute_values` values a range (by default the range of `series.default_range`) and stores
  it: one `portfolio_values` row per weekday and one `holdings_snapshots` row per holding and
  weekday, with the price evidence behind each value. `rebuild_values` does the same for the
  default range and replaces every stored day, after the lot book changed.
- `holdings_report` and `latest_value` value one day and store nothing.

Market data comes in as a `MarketData`: the stores of the data directory for the CLI and the API
(`MarketData.from_data_dir`), in-memory stores in tests. The rules of the valuation are in the
docstring of `series.py`. Nothing here reads the system clock: today comes from the `Clock`.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.errors import PlaygroundError
from playground.core.money import eur2, to_eur
from playground.core.types import TxnType
from playground.ledger.fifo import CostInput, LedgerTxn, LotBook, build_lots
from playground.ledger.holdings import booking_date
from playground.marketdata.lake import DEFAULT_STALE_AFTER_DAYS, FxStore, PriceStore
from playground.storage import repos
from playground.storage.schema import cost_basis_inputs, instruments, transactions
from playground.valuation.series import (
    FLAG_KINDS,
    DayValue,
    FlagKind,
    FxLookup,
    HoldingValue,
    Instrument,
    PriceLookup,
    Trade,
    default_range,
    last_weekday_on_or_before,
    value_on,
    value_series,
)

#: P0 tags every holding as core (spec 1.5: sleeves come later).
SLEEVE = "core"
_TRADE_TYPES = frozenset({TxnType.BUY, TxnType.SELL})


class ValueRangeError(PlaygroundError):
    """The range asked for cannot be valued: it ends before it starts, or after today."""


@dataclass(frozen=True)
class MarketData:
    """The stored closes and ECB rates a valuation reads."""

    prices: PriceLookup
    fx: FxLookup

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> "MarketData":
        """The Parquet lake of `data_dir` (plan 4.2). An empty or missing lake simply has no data."""
        return cls(prices=PriceStore(data_dir), fx=FxStore(data_dir))


# --- Reading the registry ---------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioInputs:
    """What the valuation reads from the registry (see `load_inputs`)."""

    book: LotBook
    instruments: dict[str, Instrument]
    trades: list[Trade]
    first_booking: date | None

    @property
    def names(self) -> dict[str, str]:
        return {isin: instrument.name for isin, instrument in self.instruments.items()}


def load_inputs(conn: Connection, portfolio_id: int) -> PortfolioInputs:
    """The lot book of the accepted transactions, the instruments, the trade prices and the first booking date.

    The stored transaction rows hold the fields in use of their sources (`importer.persist`), so
    this reads them directly, without merging the sources again. Only accepted transactions count:
    held-back ones stay out of lots, holdings and value, and a staged batch counts once accepted.
    The cost you entered for a transfer in is the most recent one (`entered_at`, then `id`).
    """
    rows = conn.execute(
        sa.select(transactions, instruments.c.isin.label("isin"))
        .select_from(transactions.outerjoin(instruments, instruments.c.id == transactions.c.instrument_id))
        .where(transactions.c.portfolio_id == portfolio_id, transactions.c.state == "accepted")
        .order_by(transactions.c.id)
    ).all()
    costs = _cost_inputs(conn, portfolio_id)

    ledger: list[LedgerTxn] = []
    trades: list[Trade] = []
    first: date | None = None
    for row in rows:
        txn_type = TxnType(row.type)
        ts_utc = _parse_ts(row.ts_utc)
        day = booking_date(ts_utc)
        first = day if first is None or day < first else first
        ledger.append(
            LedgerTxn(
                id=int(row.id),
                type=txn_type,
                isin=row.isin,
                ts_utc=ts_utc,
                quantity=row.quantity,
                amount_eur=row.amount_eur,
                fees_eur=row.fees_eur,
                tax_eur=row.tax_eur,
                split_new_quantity=row.split_new_quantity,
                cost_input=costs.get(int(row.id)) if txn_type is TxnType.TRANSFER_IN else None,
            )
        )
        if txn_type in _TRADE_TYPES and row.isin is not None:
            price = _price_eur(row, txn_type)
            if price is not None:
                trades.append(Trade(isin=row.isin, ts_utc=ts_utc, price_eur=price, type=txn_type))
    return PortfolioInputs(book=build_lots(ledger), instruments=_instruments(conn), trades=trades, first_booking=first)


def _parse_ts(value: str) -> datetime:
    """A stored TS (`2024-06-12T09:20:00Z`) as an aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _cost_inputs(conn: Connection, portfolio_id: int) -> dict[int, CostInput]:
    costs: dict[int, CostInput] = {}
    for row in conn.execute(
        sa.select(cost_basis_inputs.c.transaction_id, cost_basis_inputs.c.acquired_on, cost_basis_inputs.c.cost_eur)
        .select_from(cost_basis_inputs.join(transactions, transactions.c.id == cost_basis_inputs.c.transaction_id))
        .where(transactions.c.portfolio_id == portfolio_id)
        .order_by(cost_basis_inputs.c.entered_at, cost_basis_inputs.c.id)
    ):
        costs[int(row.transaction_id)] = CostInput(acquired_on=row.acquired_on, cost_eur=row.cost_eur)
    return costs


def _price_eur(row: Any, txn_type: TxnType) -> Decimal | None:
    """A trade's price per share in EUR: the printed price, or, when none is stored, the booking per share.

    A price in another currency is converted with the rate the document printed. Without a price,
    a purchase's price is its booking amount less fees per share, and a sale's is its booking
    amount plus fees and taxes per share. `None` when neither can be known.
    """
    if row.price is not None:
        if row.currency == "EUR":
            return Decimal(row.price)
        if row.fx_rate is not None and row.fx_rate > 0:
            return to_eur(row.price, row.currency, row.fx_rate)
        return None
    if row.quantity is None or row.quantity <= 0 or row.amount_eur is None:
        return None
    fees = row.fees_eur if row.fees_eur is not None else Decimal(0)
    if txn_type is TxnType.BUY:
        gross = -row.amount_eur - fees
    elif row.tax_eur is None:
        return None
    else:
        gross = row.amount_eur + fees + row.tax_eur
    return Decimal(gross / row.quantity) if gross > 0 else None


def _instruments(conn: Connection) -> dict[str, Instrument]:
    return {
        row.isin: Instrument(
            isin=row.isin,
            name=row.name,
            mapping_status=row.mapping_status,
            data_source=row.data_source,
            data_symbol=row.data_symbol,
            currency=row.currency,
        )
        for row in conn.execute(sa.select(instruments))
    }


# --- Valuing and storing a range --------------------------------------------------------------


@dataclass(frozen=True)
class ValueReport:
    """A valued range: its days and the names of the instruments its flags mention."""

    start: date | None
    end: date | None
    days: list[DayValue]
    names: Mapping[str, str]
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS

    @property
    def latest(self) -> DayValue | None:
        """The value on the last day of the range."""
        return self.days[-1] if self.days else None

    @property
    def complete_days(self) -> int:
        return sum(1 for day in self.days if day.complete)

    def summary(self) -> dict[str, Any]:
        """The range, its number of days and its latest value, as accept and set-cost report them."""
        latest = self.latest
        return {
            "from": _iso(self.start),
            "to": _iso(self.end),
            "days": len(self.days),
            "complete_days": self.complete_days,
            "latest": day_record(latest) if latest is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """The whole range as `pg value --json` prints it (plan 5.7, 5.9)."""
        latest = self.latest
        return {
            "currency": "EUR",
            "from": _iso(self.start),
            "to": _iso(self.end),
            "stale_after_days": self.stale_after_days,
            "days": len(self.days),
            "complete_days": self.complete_days,
            "latest": day_record(latest) if latest is not None else None,
            "flags": flag_summaries(self.days, self.names),
            "series": [day_record(day) for day in self.days],
        }

    def csv_text(self) -> str:
        """One row per day, semicolon-separated, dot decimals, EUR rounded to cents (`pg value --csv`)."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
        writer.writerow(["date", "value_eur", "cost_basis_eur", "complete", "flags"])
        for day in self.days:
            record = day_record(day)
            writer.writerow(
                [
                    record["date"],
                    record["value_eur"],
                    record["cost_basis_eur"] if record["cost_basis_eur"] is not None else "",
                    "true" if day.complete else "false",
                    ", ".join(record["flags"]),
                ]
            )
        return buffer.getvalue()


def compute_values(
    conn: Connection,
    portfolio_id: int,
    *,
    market: MarketData,
    clock: Clock,
    start: date | None = None,
    end: date | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> ValueReport:
    """Value the weekdays from `start` to `end` and store them, replacing the stored days of that range.

    `start` defaults to the booking date of the first accepted transaction and `end` to the last
    weekday on or before today (plan 5.7). With nothing accepted and no `start`, there is nothing to
    value: the report is empty and nothing is stored. Raises `ValueRangeError` for a range that
    starts after it ends, or ends after today.
    """
    inputs = load_inputs(conn, portfolio_id)
    today = clock.today()
    if end is not None and end > today:
        raise ValueRangeError(
            f"The value series ends today ({today.isoformat()}) at the latest, not on {end.isoformat()}."
        )
    span = default_range(inputs.first_booking, today)
    first = start if start is not None else (span[0] if span is not None else None)
    last = end if end is not None else last_weekday_on_or_before(today)
    if first is None:
        return ValueReport(start=None, end=None, days=[], names=inputs.names, stale_after_days=stale_after_days)
    if first > last:
        raise ValueRangeError(
            f"The first day ({first.isoformat()}) is after the last day ({last.isoformat()}) of the range to value."
        )
    days = value_series(
        inputs.book,
        inputs.instruments,
        market.prices,
        market.fx,
        first,
        last,
        trades=inputs.trades,
        stale_after_days=stale_after_days,
    )
    store_values(conn, portfolio_id, days, start=first, end=last)
    return ValueReport(start=first, end=last, days=days, names=inputs.names, stale_after_days=stale_after_days)


def rebuild_values(conn: Connection, portfolio_id: int, *, market: MarketData, clock: Clock) -> ValueReport:
    """Value the default range (plan 5.7) and replace every stored day with it.

    The value step after the lot book changed: accept, a resolution and `pg transfers set-cost`.
    With nothing accepted, every stored day is removed and the report is empty.
    """
    inputs = load_inputs(conn, portfolio_id)
    span = default_range(inputs.first_booking, clock.today())
    if span is None:
        repos.replace_value_series(conn, portfolio_id, values=[], snapshots=[])
        return ValueReport(start=None, end=None, days=[], names=inputs.names)
    days = value_series(inputs.book, inputs.instruments, market.prices, market.fx, *span, trades=inputs.trades)
    store_values(conn, portfolio_id, days)
    return ValueReport(start=span[0], end=span[1], days=days, names=inputs.names)


def store_values(
    conn: Connection,
    portfolio_id: int,
    days: Sequence[DayValue],
    *,
    start: date | None = None,
    end: date | None = None,
) -> None:
    """Store `days` as `portfolio_values` and `holdings_snapshots` rows (plan 4.1).

    Replaces the stored days from `start` to `end`, or every stored day when neither is given.
    Values and costs are stored unrounded; flags as a list of `{"kind", "isin", "detail"}`.
    """
    ids = repos.instrument_ids(conn)
    values: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for day in days:
        values.append(
            {
                "portfolio_id": portfolio_id,
                "date": day.date,
                "value_eur": day.value_eur,
                "cost_basis_eur": day.cost_basis_eur,
                "complete": 1 if day.complete else 0,
                "flags_json": [_flag_json(flag.kind, flag.isin, flag.detail) for flag in day.flags],
            }
        )
        for holding in day.holdings:
            if holding.isin not in ids:
                ids[holding.isin] = repos.upsert_instrument_from_document(conn, isin=holding.isin, name=None)
            snapshots.append(_snapshot_row(portfolio_id, day.date, ids[holding.isin], holding))
    repos.replace_value_series(conn, portfolio_id, values=values, snapshots=snapshots, start=start, end=end)


def _snapshot_row(portfolio_id: int, day: date, instrument_id: int, holding: HoldingValue) -> dict[str, Any]:
    return {
        "portfolio_id": portfolio_id,
        "as_of": day,
        "instrument_id": instrument_id,
        "quantity": holding.quantity,
        "pricing_quantity": holding.pricing_quantity,
        "price": holding.price,
        "price_date": holding.price_date,
        "price_currency": holding.price_currency,
        "fx_rate": holding.fx_rate,
        "fx_date": holding.fx_date,
        "value_eur": holding.value_eur,
        "cost_basis_eur": holding.cost_basis_eur,
        "sleeve": SLEEVE,
        "flags_json": [_flag_json(flag.kind, flag.isin, flag.detail) for flag in holding.flags],
    }


def _flag_json(kind: str, isin: str | None, detail: str) -> dict[str, Any]:
    return {"kind": kind, "isin": isin, "detail": detail}


# --- One day ----------------------------------------------------------------------------------


def latest_value(conn: Connection, portfolio_id: int, *, market: MarketData, clock: Clock) -> DayValue | None:
    """The value on the last day of the default range (plan 5.7), worked out now from the stored prices.

    `None` when nothing has been accepted yet. Nothing is stored.
    """
    inputs = load_inputs(conn, portfolio_id)
    span = default_range(inputs.first_booking, clock.today())
    if span is None:
        return None
    return value_on(inputs.book, inputs.instruments, market.prices, market.fx, span[1], trades=inputs.trades)


def holdings_report(conn: Connection, portfolio_id: int, *, day: date, market: MarketData) -> dict[str, Any]:
    """Every holding on `day` with its quantity, cost, value, price evidence, mapping and flags (`pg holdings`).

    Quantities and costs count lots from their booking date, as the ledger does (plan 5.5). The
    value uses the latest close and rate on or before `day`, which may be any calendar day. Nothing
    is stored.
    """
    inputs = load_inputs(conn, portfolio_id)
    value = value_on(inputs.book, inputs.instruments, market.prices, market.fx, day, trades=inputs.trades)
    return {
        "as_of": day.isoformat(),
        "currency": "EUR",
        "value_eur": money(value.value_eur),
        "cost_basis_eur": money(value.cost_basis_eur),
        "complete": value.complete,
        "holdings": [holding_record(item, inputs.instruments.get(item.isin)) for item in value.holdings],
    }


# --- Records for the CLI and the API ------------------------------------------------------------


def money(value: Decimal | None) -> str | None:
    """An EUR figure as shown: rounded to cents (plan 3.3), as a string."""
    return None if value is None else format(eur2(value), "f")


def day_record(day: DayValue) -> dict[str, Any]:
    """One day of the series: its value and cost basis in EUR, whether it is complete, and its flags.

    Each flag is written short, as the kind and the ISIN (`stale_price DE0008404005`); the flag
    summary of `flag_summaries` says what each one means.
    """
    return {
        "date": day.date.isoformat(),
        "value_eur": money(day.value_eur),
        "cost_basis_eur": money(day.cost_basis_eur),
        "complete": day.complete,
        "flags": [flag.label for flag in day.flags],
    }


def flag_summaries(days: Sequence[DayValue], names: Mapping[str, str]) -> list[dict[str, Any]]:
    """Every flag of `days` once, with its meaning, its first and last day and the number of days it is on.

    Sorted by kind (in the order of plan 5.7), then ISIN, then first day.
    """
    groups: dict[tuple[FlagKind, str | None, str], list[date]] = {}
    for day in days:
        for flag in day.flags:
            groups.setdefault((flag.kind, flag.isin, flag.detail), []).append(day.date)
    ordered = sorted(groups.items(), key=lambda item: (FLAG_KINDS.index(item[0][0]), item[0][1] or "", item[1][0]))
    return [
        {
            "kind": kind,
            "isin": isin,
            "name": names.get(isin, isin) if isin is not None else None,
            "detail": detail,
            "first": dates[0].isoformat(),
            "last": dates[-1].isoformat(),
            "days": len(dates),
        }
        for (kind, isin, detail), dates in ordered
    ]


def holding_record(item: HoldingValue, instrument: Instrument | None) -> dict[str, Any]:
    """One holding as `pg holdings --json` shows it: the ledger's figures, then the value and its evidence."""
    return {
        "isin": item.isin,
        "name": instrument.name if instrument is not None else item.isin,
        "quantity": quantity_text(item.quantity),
        "cost_eur": format(item.cost_basis_eur, "f") if item.cost_basis_eur is not None else None,
        "value_eur": money(item.value_eur),
        "pricing_quantity": quantity_text(item.pricing_quantity),
        "price": None
        if item.price is None
        else {
            "close": trimmed(item.price, 2),
            "date": _iso(item.price_date),
            "currency": item.price_currency,
            "source": item.source,
            "symbol": item.symbol,
            "adjustment": item.adjustment,
        },
        "fx": None
        if item.fx_rate is None
        else {"currency": item.fx_currency, "rate": trimmed(item.fx_rate, 4), "date": _iso(item.fx_date)},
        "mapping": {
            "status": instrument.mapping_status if instrument is not None else "unmapped",
            "source": instrument.data_source if instrument is not None else None,
            "symbol": instrument.data_symbol if instrument is not None else None,
            "currency": instrument.currency if instrument is not None else None,
        },
        "flags": [{"kind": flag.kind, "detail": flag.detail} for flag in item.flags],
    }


def quantity_text(quantity: Decimal) -> str:
    """A quantity without padding zeros or an exponent: 20.000 gives "20", 0.4798 stays "0.4798"."""
    return "0" if quantity == 0 else format(quantity.normalize(), "f")


def trimmed(value: Decimal, places: int) -> str:
    """`value` with at least `places` decimals and no padding zeros beyond: 236.00000000 gives "236.00"."""
    short = value.quantize(Decimal(1).scaleb(-places))
    return format(short if short == value else value.normalize(), "f")


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day is not None else None
