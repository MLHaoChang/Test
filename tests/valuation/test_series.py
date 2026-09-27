"""Tests for the daily value series in EUR (valuation/series.py, plan 5.7, 1.2, 7.2, AC8).

The first tests assert the four hand-computed days of plan 1.2 directly: the totals to the cent on
2024-05-31 and 2024-12-31, and the price dates, rate dates and flags on 2024-12-25 and 2024-12-27.
They value the golden portfolio's lot book with the committed fixture series: the Stooq and ECB
fixtures, read through the replay HTTP layer, and the manual Allianz price file. So a golden file
rewritten by the code cannot drift unnoticed.

The other tests check each rule of 5.7 with small in-memory price and rate stores. A **flag** is a
note on a value: a holding left out, a price or rate that is old, or a check that failed. Nothing
here raises for missing data; a missing price or rate is a flag.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from playground.core.dates import berlin_to_utc
from playground.core.money import eur2
from playground.core.types import TxnType
from playground.http.replay import ReplayHttpClient
from playground.ledger.fifo import CostInput, LedgerTxn, LotBook, build_lots
from playground.marketdata.ecb import EcbClient
from playground.marketdata.lake import FxPoint, PricePoint, PriceSeries
from playground.marketdata.manual_prices import load_price_file
from playground.marketdata.stooq import StooqClient
from playground.valuation.series import (
    FLAG_KINDS,
    DayValue,
    Flag,
    HoldingValue,
    InMemoryFx,
    InMemoryPrices,
    Instrument,
    Trade,
    default_range,
    value_on,
    value_series,
    weekdays,
)

REPO = Path(__file__).resolve().parents[2]
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"
GOLDEN_INPUTS = REPO / "tests" / "fixtures" / "golden" / "inputs"

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"
# Instruments for the rule tests below; their ISINs are only labels here.
STOCK = "DE000A0TGJ55"
OTHER = "DE000BASF111"
GBX_STOCK = "GB0002374006"


def berlin(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A booking time as a document prints it (Europe/Berlin), as the aware UTC time the ledger uses."""
    return berlin_to_utc(datetime(year, month, day, hour, minute))  # noqa: DTZ001


def txn(
    txn_id: int,
    txn_type: TxnType,
    isin: str | None,
    ts_utc: datetime,
    *,
    quantity: str | None = None,
    amount: str | None = None,
    fees: str | None = "0.00",
    tax: str | None = "0.00",
    new_quantity: str | None = None,
    cost_input: CostInput | None = None,
) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=txn_type,
        isin=isin,
        ts_utc=ts_utc,
        quantity=None if quantity is None else Decimal(quantity),
        amount_eur=None if amount is None else Decimal(amount),
        fees_eur=None if fees is None else Decimal(fees),
        tax_eur=None if tax is None else Decimal(tax),
        split_new_quantity=None if new_quantity is None else Decimal(new_quantity),
        cost_input=cost_input,
    )


def buy(txn_id: int, isin: str, ts_utc: datetime, quantity: str, amount: str) -> LedgerTxn:
    return txn(txn_id, TxnType.BUY, isin, ts_utc, quantity=quantity, amount=amount)


def split(txn_id: int, isin: str, ts_utc: datetime, new_total: str) -> LedgerTxn:
    return txn(txn_id, TxnType.SPLIT, isin, ts_utc, new_quantity=new_total)


def instrument(isin: str, symbol: str, *, currency: str = "EUR", source: str = "stooq") -> Instrument:
    return Instrument(
        isin=isin,
        name=f"Instrument {isin}",
        mapping_status="confirmed",
        data_source=source,
        data_symbol=symbol,
        currency=currency,
    )


def unmapped(isin: str) -> Instrument:
    return Instrument(
        isin=isin,
        name=f"Instrument {isin}",
        mapping_status="unmapped",
        data_source=None,
        data_symbol=None,
        currency=None,
    )


def series(
    symbol: str,
    closes: Mapping[date, str],
    *,
    currency: str = "EUR",
    adjustment: str = "split_dividend",
    source: str = "stooq",
) -> PriceSeries:
    points = tuple(
        PricePoint(date=day, open=None, high=None, low=None, close=Decimal(close), volume=None)
        for day, close in sorted(closes.items())
    )
    return PriceSeries(source=source, symbol=symbol, currency=currency, adjustment=adjustment, points=points)


def daily(start: date, end: date, close: str) -> dict[date, str]:
    """The same close on every weekday from `start` to `end`."""
    return {day: close for day in weekdays(start, end)}


def rates(currency: str, values: Mapping[date, str]) -> list[FxPoint]:
    return [FxPoint(date=day, currency=currency, rate=Decimal(value)) for day, value in sorted(values.items())]


def prices(*items: PriceSeries) -> InMemoryPrices:
    return InMemoryPrices(items)


def fx(points: Iterable[FxPoint] = ()) -> InMemoryFx:
    return InMemoryFx(points)


def flags_of(day: DayValue) -> set[tuple[str, str | None]]:
    return {(flag.kind, flag.isin) for flag in day.flags}


def holding(day: DayValue, isin: str) -> HoldingValue:
    return next(item for item in day.holdings if item.isin == isin)


def by_date(days: Iterable[DayValue]) -> dict[date, DayValue]:
    return {day.date: day for day in days}


# --- The golden portfolio (plan 1.2) ---------------------------------------------------------

ALV_COST = CostInput(acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))

GOLDEN_TXNS = [
    txn(1, TxnType.DEPOSIT, None, berlin(2024, 1, 2), amount="5000.00"),
    txn(2, TxnType.BUY, SAP, berlin(2024, 1, 15, 10, 5), quantity="10", amount="-1401.00", fees="1.00"),
    txn(3, TxnType.BUY, MSCIW, berlin(2024, 2, 1), quantity="2.5", amount="-200.00"),
    txn(4, TxnType.BUY, MSCIW, berlin(2024, 3, 1), quantity="2.5", amount="-210.00"),
    txn(5, TxnType.BUY, AAPL, berlin(2024, 3, 12, 14, 30), quantity="5", amount="-801.00", fees="1.00"),
    txn(6, TxnType.BUY, NVDA, berlin(2024, 3, 20, 15, 45), quantity="2", amount="-1701.00", fees="1.00"),
    txn(7, TxnType.DEPOSIT, None, berlin(2024, 4, 2), amount="1000.00"),
    txn(8, TxnType.BUY, SAP, berlin(2024, 4, 10, 9, 12), quantity="5", amount="-801.00", fees="1.00"),
    txn(9, TxnType.DIVIDEND, SAP, berlin(2024, 5, 15), quantity="15", amount="24.30", tax="8.70"),
    txn(10, TxnType.DIVIDEND, AAPL, berlin(2024, 5, 16), quantity="5", amount="0.94", tax="0.17"),
    split(11, NVDA, berlin(2024, 6, 10), "20"),
    txn(12, TxnType.SELL, SAP, berlin(2024, 6, 12, 11, 20), quantity="12", amount="1999.41", fees="1.00", tax="99.59"),
    txn(13, TxnType.TRANSFER_IN, ALV, berlin(2024, 6, 20), quantity="4", cost_input=ALV_COST),
    txn(14, TxnType.INTEREST, None, berlin(2024, 7, 1), amount="3.12"),
]

# The document price of every golden buy and sell, in EUR as Trade Republic prints it.
GOLDEN_TRADES = [
    Trade(isin=SAP, ts_utc=berlin(2024, 1, 15, 10, 5), price_eur=Decimal("140.00"), type=TxnType.BUY),
    Trade(isin=MSCIW, ts_utc=berlin(2024, 2, 1), price_eur=Decimal("80.00"), type=TxnType.BUY),
    Trade(isin=MSCIW, ts_utc=berlin(2024, 3, 1), price_eur=Decimal("84.00"), type=TxnType.BUY),
    Trade(isin=AAPL, ts_utc=berlin(2024, 3, 12, 14, 30), price_eur=Decimal("160.00"), type=TxnType.BUY),
    Trade(isin=NVDA, ts_utc=berlin(2024, 3, 20, 15, 45), price_eur=Decimal("850.00"), type=TxnType.BUY),
    Trade(isin=SAP, ts_utc=berlin(2024, 4, 10, 9, 12), price_eur=Decimal("160.00"), type=TxnType.BUY),
    Trade(isin=SAP, ts_utc=berlin(2024, 6, 12, 11, 20), price_eur=Decimal("175.00"), type=TxnType.SELL),
]

# The mapping of tests/fixtures/golden/inputs/instrument_mapping.csv.
GOLDEN_INSTRUMENTS = {
    SAP: Instrument(SAP, "SAP SE", "confirmed", "stooq", "sap.de", "EUR"),
    MSCIW: Instrument(MSCIW, "iShsIII-Core MSCI World U.ETF", "confirmed", "stooq", "eunl.de", "EUR"),
    AAPL: Instrument(AAPL, "Apple Inc.", "confirmed", "stooq", "aapl.us", "USD"),
    NVDA: Instrument(NVDA, "NVIDIA Corp.", "confirmed", "stooq", "nvda.us", "USD"),
    ALV: Instrument(ALV, "Allianz SE", "confirmed", "manual", "ALV.DE", "EUR"),
}


@pytest.fixture(scope="module")
def golden_market() -> tuple[InMemoryPrices, InMemoryFx]:
    """The fixture series of plan 6.4 to 6.6, read the way `pg prices fetch` and `pg fx fetch` read them."""
    http = ReplayHttpClient(HTTP_FIXTURES)
    stooq = StooqClient(http)
    stored = [
        stooq.daily_bars(symbol, date(2024, 1, 1), date(2024, 12, 31), currency=currency)
        for symbol, currency in (("sap.de", "EUR"), ("eunl.de", "EUR"), ("aapl.us", "USD"), ("nvda.us", "USD"))
    ]
    stored += load_price_file((GOLDEN_INPUTS / "manual_prices_allianz.csv").read_bytes())
    return InMemoryPrices(stored), InMemoryFx(EcbClient(http).history().points)


@pytest.fixture(scope="module")
def golden_book() -> LotBook:
    return build_lots(GOLDEN_TXNS)


@pytest.fixture(scope="module")
def golden_year(golden_book: LotBook, golden_market: tuple[InMemoryPrices, InMemoryFx]) -> dict[date, DayValue]:
    """The golden value series over the range `pg value` gives with PG_TODAY=2024-12-31."""
    price_store, fx_store = golden_market
    days = value_series(
        golden_book,
        GOLDEN_INSTRUMENTS,
        price_store,
        fx_store,
        date(2024, 1, 2),
        date(2024, 12, 31),
        trades=GOLDEN_TRADES,
    )
    return by_date(days)


# --- The four hand-computed days (plan 1.2) ---------------------------------------------------


def test_2024_05_31_is_5916_67_to_the_cent(golden_year: dict[date, DayValue]) -> None:
    # SAP 15 x 170.00; MSCIW 5 x 90.00; AAPL 5 x 190.00 / 1.08; NVDA 2 shares held, the series is
    # split-adjusted, so 2 x 10 x 110.00 / 1.08.
    day = golden_year[date(2024, 5, 31)]

    assert {item.isin for item in day.holdings} == {SAP, MSCIW, AAPL, NVDA}
    assert holding(day, SAP).value_eur == Decimal("2550.00")
    assert holding(day, MSCIW).value_eur == Decimal("450.00")
    assert holding(day, AAPL).value_eur == Decimal("879.62962963")
    assert holding(day, NVDA).value_eur == Decimal("2037.03703704")
    assert (holding(day, NVDA).quantity, holding(day, NVDA).pricing_quantity) == (Decimal("2"), Decimal("20"))
    assert (holding(day, AAPL).fx_rate, holding(day, AAPL).fx_date) == (Decimal("1.0800"), date(2024, 5, 31))
    assert holding(day, SAP).price_date == date(2024, 5, 31)
    assert eur2(day.value_eur) == Decimal("5916.67")
    # The ALV lot is left out: it was acquired in 2020 but booked in on 2024-06-20.
    assert day.cost_basis_eur == Decimal("5114.00")
    assert day.complete is True


def test_2024_12_31_is_6146_85_from_unrounded_parts(golden_year: dict[date, DayValue]) -> None:
    # Xetra is closed, so SAP and MSCIW use the 2024-12-30 close; the US is open; ECB 1.0400.
    day = golden_year[date(2024, 12, 31)]

    assert (holding(day, SAP).value_eur, holding(day, SAP).price_date) == (Decimal("708.00"), date(2024, 12, 30))
    assert (holding(day, MSCIW).value_eur, holding(day, MSCIW).price_date) == (Decimal("500.00"), date(2024, 12, 30))
    assert holding(day, AAPL).value_eur == Decimal("1201.92307692")
    assert holding(day, NVDA).value_eur == Decimal("2576.92307692")
    assert (holding(day, NVDA).quantity, holding(day, NVDA).pricing_quantity) == (Decimal("20"), Decimal("20"))
    assert (holding(day, AAPL).fx_rate, holding(day, AAPL).fx_date) == (Decimal("1.0400"), date(2024, 12, 31))
    # ALV from the manual file: the last price is from 2024-12-20, so it is flagged stale.
    assert (holding(day, ALV).value_eur, holding(day, ALV).price_date) == (Decimal("1160.00"), date(2024, 12, 20))
    assert ("stale_price", ALV) in flags_of(day)

    assert day.value_eur == Decimal("6146.84615384")
    assert eur2(day.value_eur) == Decimal("6146.85")
    assert day.cost_basis_eur == Decimal("4192.60")
    assert day.complete is True


def test_2024_12_25_uses_the_last_closes_and_the_last_rate_without_a_flag(golden_year: dict[date, DayValue]) -> None:
    day = golden_year[date(2024, 12, 25)]

    assert holding(day, SAP).price_date == date(2024, 12, 23)
    assert holding(day, MSCIW).price_date == date(2024, 12, 23)
    assert (holding(day, AAPL).price_date, holding(day, AAPL).fx_date) == (date(2024, 12, 24), date(2024, 12, 24))
    assert holding(day, NVDA).fx_date == date(2024, 12, 24)
    # The ALV price is 5 days old: not stale yet.
    assert holding(day, ALV).price_date == date(2024, 12, 20)
    assert {kind for kind, _ in flags_of(day)} == {"dividend_adjusted_prices"}
    assert day.complete is True


def test_2024_12_27_flags_the_allianz_price_as_stale(golden_year: dict[date, DayValue]) -> None:
    day = golden_year[date(2024, 12, 27)]

    # The ALV price is 7 days old: stale, and still used.
    assert holding(day, ALV).price_date == date(2024, 12, 20)
    assert holding(day, ALV).value_eur == Decimal("1160.00")
    assert holding(day, SAP).price_date == date(2024, 12, 27)
    assert holding(day, AAPL).fx_date == date(2024, 12, 27)
    assert {flag for flag in flags_of(day) if flag[0] != "dividend_adjusted_prices"} == {("stale_price", ALV)}
    assert day.complete is True


def test_the_golden_year_has_261_weekdays_every_one_complete(golden_year: dict[date, DayValue]) -> None:
    days = sorted(golden_year)
    assert (days[0], days[-1], len(days)) == (date(2024, 1, 2), date(2024, 12, 31), 261)
    assert all(day.complete for day in golden_year.values())
    # Nothing is held before the first purchase on 2024-01-15.
    assert golden_year[date(2024, 1, 12)].holdings == ()
    assert golden_year[date(2024, 1, 12)].value_eur == 0


def test_the_golden_portfolio_has_no_price_basis_mismatch(golden_year: dict[date, DayValue]) -> None:
    # The fixture series pass through every golden trade price (plan 6.4), by construction.
    assert not any(flag.kind == "price_basis_mismatch" for day in golden_year.values() for flag in day.flags)


def test_the_stooq_series_carry_the_dividend_flag_and_the_manual_file_does_not(
    golden_year: dict[date, DayValue],
) -> None:
    day = golden_year[date(2024, 12, 31)]
    assert {isin for kind, isin in flags_of(day) if kind == "dividend_adjusted_prices"} == {SAP, MSCIW, AAPL, NVDA}
    assert holding(day, ALV).adjustment == "raw"
    assert holding(day, SAP).adjustment == "split_dividend"


def test_the_only_stale_flags_of_the_golden_year_are_the_allianz_days_after_its_last_price(
    golden_year: dict[date, DayValue],
) -> None:
    stale = sorted(
        (day.date, flag.isin) for day in golden_year.values() for flag in day.flags if flag.kind.startswith("stale")
    )
    assert stale == [
        (date(2024, 12, 26), ALV),
        (date(2024, 12, 27), ALV),
        (date(2024, 12, 30), ALV),
        (date(2024, 12, 31), ALV),
    ]


# --- Calendar and range ---------------------------------------------------------------------


def test_weekdays_skips_saturdays_and_sundays() -> None:
    assert weekdays(date(2024, 1, 12), date(2024, 1, 16)) == [date(2024, 1, 12), date(2024, 1, 15), date(2024, 1, 16)]
    assert len(weekdays(date(2024, 1, 1), date(2024, 12, 31))) == 262
    assert weekdays(date(2024, 1, 13), date(2024, 1, 14)) == []
    assert weekdays(date(2024, 1, 16), date(2024, 1, 15)) == []


def test_the_series_has_one_value_per_weekday_and_none_for_the_weekend() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00")])
    closes = prices(series("stock.de", daily(date(2024, 1, 1), date(2024, 1, 31), "100.00")))

    days = value_series(
        book, {STOCK: instrument(STOCK, "stock.de")}, closes, fx(), date(2024, 1, 12), date(2024, 1, 16)
    )

    assert [day.date for day in days] == [date(2024, 1, 12), date(2024, 1, 15), date(2024, 1, 16)]


def test_an_exchange_holiday_is_a_day_of_the_series_valued_with_the_close_before_it() -> None:
    # Xetra is closed on Good Friday (2024-03-29) and Easter Monday (2024-04-01): a holiday on one
    # exchange is a trading day on another, so the series keeps the day.
    book = build_lots([buy(1, STOCK, berlin(2024, 3, 1), "2", "-200.00")])
    closes = prices(series("stock.de", {date(2024, 3, 28): "101.00", date(2024, 4, 2): "103.00"}))

    days = by_date(
        value_series(book, {STOCK: instrument(STOCK, "stock.de")}, closes, fx(), date(2024, 3, 28), date(2024, 4, 2))
    )

    assert sorted(days) == [date(2024, 3, 28), date(2024, 3, 29), date(2024, 4, 1), date(2024, 4, 2)]
    easter_monday = days[date(2024, 4, 1)]
    assert holding(easter_monday, STOCK).price_date == date(2024, 3, 28)
    assert easter_monday.value_eur == Decimal("202.00")
    assert not any(kind == "stale_price" for kind, _ in flags_of(easter_monday))  # 4 days old


@pytest.mark.parametrize(
    ("first_booking", "today", "expected"),
    [
        # The golden portfolio: the first accepted transaction is the deposit of 2024-01-02.
        (date(2024, 1, 2), date(2024, 12, 31), (date(2024, 1, 2), date(2024, 12, 31))),
        # Today is a Saturday: the range ends on the Friday before.
        (date(2024, 1, 2), date(2024, 12, 28), (date(2024, 1, 2), date(2024, 12, 27))),
        # The first booking is on a Sunday: the range starts on the Monday after.
        (date(2024, 1, 14), date(2024, 12, 31), (date(2024, 1, 15), date(2024, 12, 31))),
        # Nothing accepted yet, or nothing before today: no range.
        (None, date(2024, 12, 31), None),
        (date(2025, 1, 6), date(2024, 12, 31), None),
        (date(2024, 12, 28), date(2024, 12, 29), None),
    ],
)
def test_the_default_range_runs_from_the_first_booking_to_the_last_weekday_on_or_before_today(
    first_booking: date | None, today: date, expected: tuple[date, date] | None
) -> None:
    assert default_range(first_booking, today) == expected


def test_a_single_day_can_be_valued_on_a_weekend_with_the_friday_close() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "3", "-300.00")])
    closes = prices(series("stock.de", {date(2024, 1, 12): "110.00"}))

    saturday = value_on(book, {STOCK: instrument(STOCK, "stock.de")}, closes, fx(), date(2024, 1, 13))

    assert saturday.date == date(2024, 1, 13)
    assert holding(saturday, STOCK).price_date == date(2024, 1, 12)
    assert saturday.value_eur == Decimal("330.00")


# --- Staleness --------------------------------------------------------------------------------


def test_a_price_five_days_old_is_not_stale_and_six_days_old_is_stale_but_still_used() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00")])
    closes = prices(series("stock.de", {date(2024, 1, 10): "100.00"}))
    mapping = {STOCK: instrument(STOCK, "stock.de")}

    days = by_date(value_series(book, mapping, closes, fx(), date(2024, 1, 15), date(2024, 1, 16)))

    five, six = days[date(2024, 1, 15)], days[date(2024, 1, 16)]
    assert ("stale_price", STOCK) not in flags_of(five)
    assert ("stale_price", STOCK) in flags_of(six)
    assert six.value_eur == Decimal("100.00")
    assert six.complete is True


def test_a_rate_five_days_old_is_not_stale_and_six_days_old_is_stale_but_still_used() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-90.00")])
    closes = prices(series("stock.us", daily(date(2024, 1, 1), date(2024, 1, 31), "108.00"), currency="USD"))
    usd = fx(rates("USD", {date(2024, 1, 10): "1.0800"}))
    mapping = {STOCK: instrument(STOCK, "stock.us", currency="USD")}

    days = by_date(value_series(book, mapping, closes, usd, date(2024, 1, 15), date(2024, 1, 16)))

    five, six = days[date(2024, 1, 15)], days[date(2024, 1, 16)]
    assert holding(five, STOCK).fx_date == date(2024, 1, 10)
    assert ("stale_fx", STOCK) not in flags_of(five)
    assert ("stale_fx", STOCK) in flags_of(six)
    assert six.value_eur == Decimal("100.00")
    assert six.complete is True


# --- Missing data and unmapped instruments -----------------------------------------------------


def test_a_day_before_the_first_close_leaves_the_holding_out_and_is_not_complete() -> None:
    book = build_lots(
        [buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00"), buy(2, OTHER, berlin(2024, 1, 8), "2", "-40.00")]
    )
    closes = prices(
        series("stock.de", daily(date(2024, 1, 10), date(2024, 1, 31), "100.00")),
        series("other.de", daily(date(2024, 1, 1), date(2024, 1, 31), "20.00")),
    )
    mapping = {STOCK: instrument(STOCK, "stock.de"), OTHER: instrument(OTHER, "other.de")}

    days = by_date(value_series(book, mapping, closes, fx(), date(2024, 1, 8), date(2024, 1, 10)))

    before = days[date(2024, 1, 9)]
    assert ("missing_price", STOCK) in flags_of(before)
    assert holding(before, STOCK).value_eur is None
    assert holding(before, STOCK).price is None
    assert before.value_eur == Decimal("40.00")  # the other holding only
    assert before.complete is False
    after = days[date(2024, 1, 10)]
    assert after.value_eur == Decimal("140.00")
    assert after.complete is True


def test_an_instrument_with_no_stored_series_is_flagged_missing_price_every_day() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00")])

    days = value_series(
        book, {STOCK: instrument(STOCK, "stock.de")}, prices(), fx(), date(2024, 1, 8), date(2024, 1, 12)
    )

    assert len(days) == 5
    assert all(("missing_price", STOCK) in flags_of(day) and not day.complete for day in days)
    assert all(day.value_eur == 0 for day in days)


def test_a_foreign_listing_without_a_rate_is_left_out_with_missing_fx() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-90.00")])
    closes = prices(series("stock.us", daily(date(2024, 1, 1), date(2024, 1, 31), "108.00"), currency="USD"))
    usd = fx(rates("USD", {date(2024, 1, 11): "1.0800"}))
    mapping = {STOCK: instrument(STOCK, "stock.us", currency="USD")}

    days = by_date(value_series(book, mapping, closes, usd, date(2024, 1, 10), date(2024, 1, 11)))

    missing = days[date(2024, 1, 10)]
    assert ("missing_fx", STOCK) in flags_of(missing)
    assert holding(missing, STOCK).value_eur is None
    assert holding(missing, STOCK).price_date == date(2024, 1, 10)  # the close was there
    assert (missing.value_eur, missing.complete) == (Decimal(0), False)
    assert days[date(2024, 1, 11)].value_eur == Decimal("100.00")


def test_an_unmapped_instrument_is_left_out_and_nothing_raises() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "4", "-400.00")])

    days = value_series(book, {STOCK: unmapped(STOCK)}, prices(), fx(), date(2024, 1, 8), date(2024, 1, 9))

    for day in days:
        assert flags_of(day) == {("unmapped", STOCK)}
        assert (day.value_eur, day.complete) == (Decimal(0), False)
        assert day.cost_basis_eur == Decimal("400.00")
        assert holding(day, STOCK).quantity == Decimal("4")
        assert holding(day, STOCK).value_eur is None


def test_an_isin_the_instrument_master_does_not_know_counts_as_unmapped() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00")])

    day = value_on(book, {}, prices(), fx(), date(2024, 1, 8))

    assert flags_of(day) == {("unmapped", STOCK)}
    assert day.complete is False


def test_a_transfer_in_without_cost_leaves_the_cost_basis_unknown_but_values_it() -> None:
    book = build_lots(
        [
            buy(1, OTHER, berlin(2024, 1, 8), "1", "-50.00"),
            txn(2, TxnType.TRANSFER_IN, STOCK, berlin(2024, 1, 9), quantity="4"),
        ]
    )
    closes = prices(
        series("stock.de", daily(date(2024, 1, 1), date(2024, 1, 31), "25.00")),
        series("other.de", daily(date(2024, 1, 1), date(2024, 1, 31), "60.00")),
    )
    mapping = {STOCK: instrument(STOCK, "stock.de"), OTHER: instrument(OTHER, "other.de")}

    days = by_date(value_series(book, mapping, closes, fx(), date(2024, 1, 8), date(2024, 1, 9)))

    assert days[date(2024, 1, 8)].cost_basis_eur == Decimal("50.00")
    day = days[date(2024, 1, 9)]
    assert ("cost_basis_missing", STOCK) in flags_of(day)
    assert holding(day, STOCK).cost_basis_eur is None
    assert day.cost_basis_eur is None
    assert day.value_eur == Decimal("160.00")
    assert day.complete is True  # the cost basis does not decide completeness


def test_a_day_with_nothing_held_is_zero_complete_and_has_no_flags() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 15), "1", "-100.00")])

    day = value_on(book, {STOCK: unmapped(STOCK)}, prices(), fx(), date(2024, 1, 12))

    assert (day.value_eur, day.cost_basis_eur, day.complete) == (Decimal(0), Decimal(0), True)
    assert day.holdings == ()
    assert day.flags == ()


# --- Totals and currencies --------------------------------------------------------------------


def test_the_day_total_is_the_sum_of_unrounded_parts() -> None:
    # Three holdings of 1.005 EUR each: 3.015 in total, 3.02 when displayed. Rounding each part
    # first would give 3 x 1.01 = 3.03.
    isins = [STOCK, OTHER, GBX_STOCK]
    book = build_lots([buy(index, isin, berlin(2024, 1, 8), "1", "-1.00") for index, isin in enumerate(isins, 1)])
    closes = prices(*(series(f"s{index}.de", {date(2024, 1, 8): "1.005"}) for index in range(3)))
    mapping = {isin: instrument(isin, f"s{index}.de") for index, isin in enumerate(isins)}

    day = value_on(book, mapping, closes, fx(), date(2024, 1, 8))

    assert [item.value_eur for item in day.holdings] == [Decimal("1.005")] * 3
    assert day.value_eur == Decimal("3.015")
    assert eur2(day.value_eur) == Decimal("3.02")


def test_a_holding_value_is_quantised_to_eight_decimals() -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 5, 2), "5", "-800.00")])
    closes = prices(series("stock.us", {date(2024, 5, 31): "190.00"}, currency="USD"))
    usd = fx(rates("USD", {date(2024, 5, 31): "1.0800"}))

    day = value_on(book, {STOCK: instrument(STOCK, "stock.us", currency="USD")}, closes, usd, date(2024, 5, 31))

    assert day.value_eur == Decimal("879.62962963")
    assert holding(day, STOCK).price_currency == "USD"


def test_pence_are_converted_to_pounds_before_the_gbp_rate_is_applied() -> None:
    book = build_lots([buy(1, GBX_STOCK, berlin(2024, 1, 8), "10", "-100.00")])
    closes = prices(series("stock.uk", {date(2024, 1, 8): "850.00"}, currency="GBX"))
    gbp = fx(rates("GBP", {date(2024, 1, 8): "0.8500"}))

    day = value_on(book, {GBX_STOCK: instrument(GBX_STOCK, "stock.uk", currency="GBX")}, closes, gbp, date(2024, 1, 8))

    # 10 x 850 pence = 85.00 GBP = 100.00 EUR at 0.85 GBP per EUR.
    assert day.value_eur == Decimal("100.00")
    assert (holding(day, GBX_STOCK).fx_rate, holding(day, GBX_STOCK).fx_date) == (Decimal("0.8500"), date(2024, 1, 8))


# --- Splits and the price basis --------------------------------------------------------------

SPLIT_DAY = date(2024, 6, 10)  # a Monday
SPLIT_RANGE = (date(2024, 6, 3), date(2024, 6, 21))
SPLIT_TXNS = [
    buy(1, STOCK, berlin(2024, 3, 20, 15, 45), "2", "-1700.00"),
    split(2, STOCK, berlin(2024, 6, 10), "20"),
]


def split_adjusted_closes(adjustment: str = "split_dividend") -> PriceSeries:
    """Closes on the post-split share basis on every weekday, as a split-adjusted source shows them.

    They rise by 0.50 a weekday and pass through 85.00 on 2024-03-20, the day of the purchase at
    850.00 a share before the 10-for-1 split: 850.00 = 85.00 x 10.
    """
    days = weekdays(date(2024, 3, 1), date(2024, 6, 28))
    start = days.index(date(2024, 3, 20))
    closes = {day: str(Decimal("85.00") + (index - start) * Decimal("0.50")) for index, day in enumerate(days)}
    return series("stock.de", closes, adjustment=adjustment)


def adjusted_close_on(day: date) -> Decimal:
    return next(point.close for point in split_adjusted_closes().points if point.date == day)


def raw_closes(ex_date: date = SPLIT_DAY) -> PriceSeries:
    """The same closes as the exchange printed them: ten times higher before the ex-date."""
    adjusted = split_adjusted_closes()
    closes = {point.date: str(point.close * (10 if point.date < ex_date else 1)) for point in adjusted.points}
    return series("stock.de", closes, adjustment="raw")


def test_a_split_adjusted_series_gives_the_same_value_as_a_raw_series_with_the_split_on_its_day() -> None:
    book = build_lots(SPLIT_TXNS)
    mapping = {STOCK: instrument(STOCK, "stock.de")}

    adjusted = by_date(value_series(book, mapping, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE))
    raw = by_date(value_series(book, mapping, prices(raw_closes()), fx(), *SPLIT_RANGE))

    assert sorted(adjusted) == sorted(raw)
    for day in adjusted:
        assert adjusted[day].value_eur == raw[day].value_eur, day
    before = date(2024, 6, 7)
    assert (holding(adjusted[before], STOCK).quantity, holding(adjusted[before], STOCK).pricing_quantity) == (
        Decimal("2"),
        Decimal("20"),
    )
    assert holding(raw[before], STOCK).pricing_quantity == Decimal("2")
    assert holding(adjusted[date(2024, 6, 11)], STOCK).pricing_quantity == Decimal("20")


def test_with_a_split_adjusted_series_the_value_does_not_depend_on_the_day_the_split_is_booked() -> None:
    mapping = {STOCK: instrument(STOCK, "stock.de")}
    on_time = build_lots(SPLIT_TXNS)
    late = build_lots([SPLIT_TXNS[0], split(2, STOCK, berlin(2024, 6, 14), "20")])

    first = value_series(on_time, mapping, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE)
    second = value_series(late, mapping, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE)

    assert [day.value_eur for day in first] == [day.value_eur for day in second]


def test_days_within_three_weekdays_of_a_split_are_flagged_for_a_raw_series() -> None:
    book = build_lots(SPLIT_TXNS)

    days = by_date(value_series(book, {STOCK: instrument(STOCK, "stock.de")}, prices(raw_closes()), fx(), *SPLIT_RANGE))

    flagged = sorted(day for day, value in days.items() if ("near_split_raw_prices", STOCK) in flags_of(value))
    # Three weekdays before the Monday split (Wednesday to Friday), the day itself, three after.
    assert flagged == weekdays(date(2024, 6, 5), date(2024, 6, 13))
    assert all(value.complete for value in days.values())


def test_a_split_adjusted_series_gets_no_near_split_flag() -> None:
    book = build_lots(SPLIT_TXNS)

    days = value_series(
        book, {STOCK: instrument(STOCK, "stock.de")}, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE
    )

    assert not any(flag.kind == "near_split_raw_prices" for day in days for flag in day.flags)


@pytest.mark.parametrize(("adjustment", "flagged"), [("split_dividend", True), ("split", False), ("raw", False)])
def test_only_a_split_and_dividend_adjusted_series_carries_the_dividend_flag(adjustment: str, flagged: bool) -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00")])
    closes = prices(series("stock.de", {date(2024, 1, 8): "100.00"}, adjustment=adjustment))

    day = value_on(book, {STOCK: instrument(STOCK, "stock.de")}, closes, fx(), date(2024, 1, 8))

    assert (("dividend_adjusted_prices", STOCK) in flags_of(day)) is flagged
    assert day.complete is True  # an info flag


def buy_trade(ts_utc: datetime, price: str, isin: str = STOCK) -> Trade:
    return Trade(isin=isin, ts_utc=ts_utc, price_eur=Decimal(price), type=TxnType.BUY)


def test_price_basis_mismatch_when_the_ledger_lacks_a_split() -> None:
    # The source has already adjusted the whole history for a 10-for-1 split, but the ledger has
    # not booked it: the close of the trade day (85.00 on the post-split basis) is a tenth of the
    # price the trade was booked at (850.00).
    book = build_lots([SPLIT_TXNS[0]])
    trades = [buy_trade(berlin(2024, 3, 20, 15, 45), "850.00")]

    days = value_series(
        book, {STOCK: instrument(STOCK, "stock.de")}, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE, trades=trades
    )

    assert all(("price_basis_mismatch", STOCK) in flags_of(day) for day in days)
    assert all(day.complete for day in days)  # a warning: complete does not change
    detail = next(flag.detail for flag in days[0].flags if flag.kind == "price_basis_mismatch")
    assert "2024-03-20" in detail
    assert "850.00" in detail


def test_no_price_basis_mismatch_once_the_split_is_booked() -> None:
    book = build_lots(SPLIT_TXNS)
    trades = [buy_trade(berlin(2024, 3, 20, 15, 45), "850.00")]

    days = value_series(
        book, {STOCK: instrument(STOCK, "stock.de")}, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE, trades=trades
    )

    assert not any(flag.kind == "price_basis_mismatch" for day in days for flag in day.flags)


def test_the_price_basis_check_uses_the_most_recent_trade() -> None:
    book = build_lots([SPLIT_TXNS[0]])
    mapping = {STOCK: instrument(STOCK, "stock.de")}
    close = adjusted_close_on(date(2024, 5, 2))
    old_mismatched = buy_trade(berlin(2024, 3, 20, 15, 45), "850.00")
    new_matching = buy_trade(berlin(2024, 5, 2, 10, 0), str(close))

    clean = value_series(
        book, mapping, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE, trades=[new_matching, old_mismatched]
    )
    assert not any(flag.kind == "price_basis_mismatch" for day in clean for flag in day.flags)

    # A later trade on the same day is the most recent one.
    newer_mismatched = buy_trade(berlin(2024, 5, 2, 15, 0), str(close * 10))
    flagged = value_series(
        book, mapping, prices(split_adjusted_closes()), fx(), *SPLIT_RANGE, trades=[newer_mismatched, new_matching]
    )
    assert all(("price_basis_mismatch", STOCK) in flags_of(day) for day in flagged)


def test_the_price_basis_check_ignores_trades_older_than_365_days_before_the_end() -> None:
    book = build_lots([buy(1, STOCK, berlin(2023, 5, 2), "2", "-1700.00")])
    closes = prices(series("stock.de", daily(date(2023, 5, 1), date(2024, 6, 28), "100.00")))
    trades = [buy_trade(berlin(2023, 5, 2), "850.00")]
    mapping = {STOCK: instrument(STOCK, "stock.de")}

    checked = value_on(book, mapping, closes, fx(), date(2024, 5, 1), trades=trades)
    too_old = value_on(book, mapping, closes, fx(), date(2024, 5, 3), trades=trades)

    assert ("price_basis_mismatch", STOCK) in flags_of(checked)  # 365 days
    assert ("price_basis_mismatch", STOCK) not in flags_of(too_old)  # 367 days


def test_the_price_basis_check_skips_a_raw_series() -> None:
    book = build_lots([SPLIT_TXNS[0]])
    closes = series(
        "stock.de", {point.date: str(point.close) for point in split_adjusted_closes().points}, adjustment="raw"
    )
    trades = [buy_trade(berlin(2024, 3, 20, 15, 45), "850.00")]

    days = value_series(book, {STOCK: instrument(STOCK, "stock.de")}, prices(closes), fx(), *SPLIT_RANGE, trades=trades)

    assert not any(flag.kind == "price_basis_mismatch" for day in days for flag in day.flags)


@pytest.mark.parametrize(("price", "flagged"), [("140.00", False), ("140.01", True), ("71.43", False), ("71.42", True)])
def test_a_factor_of_1_4_is_the_limit_of_the_price_basis_check(price: str, flagged: bool) -> None:
    book = build_lots([buy(1, STOCK, berlin(2024, 5, 2), "1", "-100.00")])
    closes = prices(series("stock.de", daily(date(2024, 5, 1), date(2024, 5, 31), "100.00")))

    day = value_on(
        book,
        {STOCK: instrument(STOCK, "stock.de")},
        closes,
        fx(),
        date(2024, 5, 31),
        trades=[buy_trade(berlin(2024, 5, 2), price)],
    )

    assert (("price_basis_mismatch", STOCK) in flags_of(day)) is flagged


def test_the_price_basis_check_converts_a_foreign_close_at_the_rate_of_the_trade_day() -> None:
    # 93.50 USD at 1.1000 USD per EUR is 85.00 EUR; times the ledger's split factor of 10 after
    # the trade day gives 850.00, the trade's own price.
    txns = [buy(1, STOCK, berlin(2024, 3, 20, 15, 45), "2", "-1700.00"), split(2, STOCK, berlin(2024, 6, 10), "20")]
    closes = prices(series("stock.us", daily(date(2024, 3, 1), date(2024, 6, 28), "93.50"), currency="USD"))
    usd = fx(rates("USD", {day: "1.1000" for day in weekdays(date(2024, 3, 1), date(2024, 6, 28))}))
    mapping = {STOCK: instrument(STOCK, "stock.us", currency="USD")}
    trades = [buy_trade(berlin(2024, 3, 20, 15, 45), "850.00")]

    booked = value_on(build_lots(txns), mapping, closes, usd, date(2024, 6, 28), trades=trades)
    missing_split = value_on(build_lots(txns[:1]), mapping, closes, usd, date(2024, 6, 28), trades=trades)

    assert ("price_basis_mismatch", STOCK) not in flags_of(booked)
    assert ("price_basis_mismatch", STOCK) in flags_of(missing_split)


# --- Flags ------------------------------------------------------------------------------------


def test_the_flag_kinds_are_the_nine_of_plan_5_7() -> None:
    assert FLAG_KINDS == (
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


def test_a_day_lists_the_flags_of_its_holdings_with_a_plain_detail() -> None:
    book = build_lots(
        [buy(1, STOCK, berlin(2024, 1, 8), "1", "-100.00"), buy(2, OTHER, berlin(2024, 1, 8), "1", "-1.00")]
    )
    closes = prices(series("stock.de", {date(2024, 1, 2): "100.00"}))
    mapping = {STOCK: instrument(STOCK, "stock.de"), OTHER: unmapped(OTHER)}

    day = value_on(book, mapping, closes, fx(), date(2024, 1, 9))

    assert list(day.flags) == sorted(
        [flag for item in day.holdings for flag in item.flags],
        key=lambda flag: (FLAG_KINDS.index(flag.kind), flag.isin or ""),
    )
    assert flags_of(day) == {("unmapped", OTHER), ("stale_price", STOCK), ("dividend_adjusted_prices", STOCK)}
    for flag in day.flags:
        assert isinstance(flag, Flag)
        assert flag.detail.endswith(".")
        assert "—" not in flag.detail  # plain English, no em-dash
        assert flag.label == f"{flag.kind} {flag.isin}"
