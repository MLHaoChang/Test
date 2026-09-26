"""Tests for holdings, open cost and the quantity timeline by booking date (plan 5.5, 1.2, 7.2).

Holdings and open cost as of a day count every lot from its booking date: the day it entered the
account, which for a transfer in is the day it was booked in, not the day you acquired it. The
booking date is the Europe/Berlin calendar date, so a savings plan booked at 00:00 Berlin time
counts on its own day even though that moment is still the day before in UTC.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from playground.core.dates import berlin_to_utc
from playground.core.types import TxnType
from playground.ledger.fifo import CostInput, LedgerTxn, build_lots, quantity_timeline
from playground.ledger.holdings import booking_date

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"


def D(text: str) -> Decimal:
    """A `Decimal` from its exact text."""
    return Decimal(text)


def berlin(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A Europe/Berlin local time as a document prints it, converted to aware UTC."""
    return berlin_to_utc(datetime(year, month, day, hour, minute))  # noqa: DTZ001


def trade(
    txn_id: int,
    txn_type: TxnType,
    isin: str,
    ts: datetime,
    quantity: str,
    amount: str | None = None,
    *,
    cost_input: CostInput | None = None,
    new_quantity: str | None = None,
) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=txn_type,
        isin=isin,
        ts_utc=ts,
        quantity=None if txn_type is TxnType.SPLIT else D(quantity),
        amount_eur=None if amount is None else D(amount),
        fees_eur=D("0.00"),
        tax_eur=D("0.00"),
        split_new_quantity=None if new_quantity is None else D(new_quantity),
        cost_input=cost_input,
    )


def without_alv_cost(txns: list[LedgerTxn]) -> list[LedgerTxn]:
    """The golden transactions as they are before you enter the cost of the transfer in T13."""
    return [
        LedgerTxn(
            id=txn.id,
            type=txn.type,
            isin=txn.isin,
            ts_utc=txn.ts_utc,
            quantity=txn.quantity,
            amount_eur=txn.amount_eur,
            fees_eur=txn.fees_eur,
            tax_eur=txn.tax_eur,
            split_new_quantity=txn.split_new_quantity,
            cost_input=None,
        )
        for txn in txns
    ]


class TestBookingDate:
    def test_a_day_precision_entry_books_on_its_berlin_date(self) -> None:
        # T3: 2024-02-01 at 00:00 Berlin time is still 2024-01-31 in UTC.
        assert booking_date(datetime(2024, 1, 31, 23, 0, tzinfo=UTC)) == date(2024, 2, 1)

    def test_summer_time_is_two_hours_ahead_of_utc(self) -> None:
        # T13: booked in on 2024-06-20 at 00:00 Berlin time, 22:00 UTC the day before.
        assert booking_date(datetime(2024, 6, 19, 22, 0, tzinfo=UTC)) == date(2024, 6, 20)

    def test_a_trade_during_the_day_books_on_that_day(self) -> None:
        assert booking_date(datetime(2024, 6, 12, 9, 20, tzinfo=UTC)) == date(2024, 6, 12)

    def test_a_booking_time_without_a_time_zone_is_refused(self) -> None:
        with pytest.raises(ValueError, match="time zone"):
            booking_date(datetime(2024, 6, 12, 9, 20))  # noqa: DTZ001 (the mistake under test)


class TestHoldings:
    def test_golden_holdings_on_2024_05_31(self, golden_txns: list[LedgerTxn]) -> None:
        holdings = build_lots(golden_txns).holdings(date(2024, 5, 31))

        assert holdings == {SAP: D("15"), MSCIW: D("5"), AAPL: D("5"), NVDA: D("2")}
        assert list(holdings) == sorted(holdings)

    def test_golden_holdings_on_2024_12_31(self, golden_txns: list[LedgerTxn]) -> None:
        holdings = build_lots(golden_txns).holdings(date(2024, 12, 31))

        assert holdings == {ALV: D("4"), SAP: D("3"), MSCIW: D("5"), AAPL: D("5"), NVDA: D("20")}
        assert list(holdings) == sorted(holdings)

    def test_nothing_is_held_before_the_first_purchase(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        assert book.holdings(date(2024, 1, 14)) == {}
        assert book.holdings(date(2024, 1, 15)) == {SAP: D("10")}

    def test_a_lot_counts_from_its_booking_day(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        assert book.holdings(date(2024, 1, 31)) == {SAP: D("10")}
        assert book.holdings(date(2024, 2, 1)) == {SAP: D("10"), MSCIW: D("2.5")}

    def test_a_transfer_in_counts_from_its_booking_date_not_its_acquisition_date(
        self, golden_txns: list[LedgerTxn]
    ) -> None:
        # T13 was acquired on 2020-03-02 but booked in on 2024-06-20.
        book = build_lots(golden_txns)

        assert book.holdings(date(2020, 3, 2)) == {}
        assert ALV not in book.holdings(date(2024, 6, 19))
        assert book.holdings(date(2024, 6, 20))[ALV] == D("4")

    def test_between_a_sell_and_a_later_transfer_in_the_transferred_shares_are_left_out(self) -> None:
        book = build_lots(
            [
                trade(1, TxnType.BUY, ALV, berlin(2024, 1, 10, 10, 0), "10", "-2500.00"),
                trade(2, TxnType.SELL, ALV, berlin(2024, 2, 1, 10, 0), "4", "1100.00"),
                trade(
                    3,
                    TxnType.TRANSFER_IN,
                    ALV,
                    berlin(2024, 3, 1),
                    "5",
                    cost_input=CostInput(acquired_on=date(2020, 5, 5), cost_eur=D("800.00")),
                ),
            ]
        )

        assert book.holdings(date(2024, 2, 15)) == {ALV: D("6")}
        assert book.open_cost(date(2024, 2, 15)) == {ALV: D("1500.00")}
        assert book.holdings(date(2024, 3, 1)) == {ALV: D("11")}
        assert book.open_cost(date(2024, 3, 1)) == {ALV: D("2300.00")}

    def test_a_position_sold_out_is_no_longer_listed(self) -> None:
        book = build_lots(
            [
                trade(1, TxnType.BUY, SAP, berlin(2024, 1, 10, 10, 0), "5", "-700.00"),
                trade(2, TxnType.SELL, SAP, berlin(2024, 2, 1, 10, 0), "5", "750.00"),
            ]
        )

        assert book.holdings(date(2024, 1, 31)) == {SAP: D("5")}
        assert book.holdings(date(2024, 2, 1)) == {}
        assert book.open_cost(date(2024, 2, 1)) == {}

    def test_a_split_changes_holdings_from_its_booking_day(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        assert book.holdings(date(2024, 6, 9))[NVDA] == D("2")
        assert book.holdings(date(2024, 6, 10))[NVDA] == D("20")

    def test_holdings_are_as_of_the_end_of_the_day(self) -> None:
        book = build_lots(
            [
                trade(1, TxnType.BUY, SAP, berlin(2024, 3, 4, 9, 0), "10", "-1400.00"),
                trade(2, TxnType.SELL, SAP, berlin(2024, 3, 4, 12, 0), "4", "600.00"),
                trade(3, TxnType.BUY, SAP, berlin(2024, 3, 4, 16, 0), "1", "-150.00"),
            ]
        )

        assert book.holdings(date(2024, 3, 3)) == {}
        assert book.holdings(date(2024, 3, 4)) == {SAP: D("7")}
        assert book.holdings(date(2024, 3, 5)) == {SAP: D("7")}


class TestOpenCost:
    def test_golden_open_cost_on_2024_05_31_leaves_the_transfer_in_out(self, golden_txns: list[LedgerTxn]) -> None:
        # Plan 1.2: cost basis 5,114.00 on 2024-05-31; the Allianz lot was acquired in 2020 but
        # booked in only on 2024-06-20.
        cost = build_lots(golden_txns).open_cost(date(2024, 5, 31))

        assert cost == {SAP: D("2202.00"), MSCIW: D("410.00"), AAPL: D("801.00"), NVDA: D("1701.00")}
        assert sum(value for value in cost.values() if value is not None) == D("5114.00")

    def test_golden_open_cost_on_2024_12_31_is_4192_60(self, golden_txns: list[LedgerTxn]) -> None:
        cost = build_lots(golden_txns).open_cost(date(2024, 12, 31))

        assert cost == {
            ALV: D("800.00"),
            SAP: D("480.60"),
            MSCIW: D("410.00"),
            AAPL: D("801.00"),
            NVDA: D("1701.00"),
        }
        assert sum(value for value in cost.values() if value is not None) == D("4192.60")

    def test_open_cost_is_unknown_while_an_open_lot_has_no_cost(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(without_alv_cost(golden_txns))

        cost = book.open_cost(date(2024, 12, 31))

        assert cost[ALV] is None
        assert cost[SAP] == D("480.60")
        assert book.open_cost(date(2024, 6, 19))[SAP] == D("480.60")
        assert ALV not in book.open_cost(date(2024, 6, 19))

    def test_a_lot_without_cost_stops_counting_once_it_is_used_up(self) -> None:
        book = build_lots(
            [
                trade(1, TxnType.TRANSFER_IN, ALV, berlin(2024, 1, 10), "5"),
                trade(2, TxnType.BUY, ALV, berlin(2024, 2, 1, 10, 0), "10", "-2500.00"),
                trade(3, TxnType.SELL, ALV, berlin(2024, 3, 1, 10, 0), "5", "1300.00"),
            ]
        )

        assert book.open_cost(date(2024, 2, 29)) == {ALV: None}
        assert book.open_cost(date(2024, 3, 1)) == {ALV: D("2500.00")}

    def test_open_cost_lists_the_same_instruments_as_holdings(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        for day in (date(2024, 1, 1), date(2024, 3, 15), date(2024, 6, 12), date(2024, 6, 20), date(2025, 1, 1)):
            assert list(book.open_cost(day)) == list(book.holdings(day))


class TestQuantityTimeline:
    def test_golden_timeline(self, golden_txns: list[LedgerTxn]) -> None:
        timeline = quantity_timeline(golden_txns)

        assert timeline == {
            ALV: [(date(2024, 6, 20), D("4"))],
            SAP: [(date(2024, 1, 15), D("10")), (date(2024, 4, 10), D("15")), (date(2024, 6, 12), D("3"))],
            MSCIW: [(date(2024, 2, 1), D("2.5")), (date(2024, 3, 1), D("5"))],
            AAPL: [(date(2024, 3, 12), D("5"))],
            # By booking date with the split applied: 2 shares, then 20 from the split's day on.
            NVDA: [(date(2024, 3, 20), D("2")), (date(2024, 6, 10), D("20"))],
        }
        assert list(timeline) == sorted(timeline)

    def test_the_function_and_the_lot_book_agree(self, golden_txns: list[LedgerTxn]) -> None:
        assert quantity_timeline(golden_txns) == build_lots(golden_txns).quantity_timeline()

    def test_several_transactions_on_one_day_give_one_point_at_the_end_of_the_day(self) -> None:
        timeline = quantity_timeline(
            [
                trade(1, TxnType.BUY, SAP, berlin(2024, 3, 4, 9, 0), "10", "-1400.00"),
                trade(2, TxnType.SELL, SAP, berlin(2024, 3, 4, 12, 0), "4", "600.00"),
                trade(3, TxnType.BUY, SAP, berlin(2024, 3, 4, 16, 0), "1", "-150.00"),
            ]
        )

        assert timeline == {SAP: [(date(2024, 3, 4), D("7"))]}

    def test_a_round_trip_within_one_day_leaves_no_point(self) -> None:
        timeline = quantity_timeline(
            [
                trade(1, TxnType.BUY, SAP, berlin(2024, 3, 4, 9, 0), "10", "-1400.00"),
                trade(2, TxnType.SELL, SAP, berlin(2024, 3, 4, 12, 0), "10", "1450.00"),
            ]
        )

        assert timeline == {}

    def test_a_sold_out_position_ends_at_zero(self) -> None:
        timeline = quantity_timeline(
            [
                trade(1, TxnType.BUY, SAP, berlin(2024, 3, 4, 9, 0), "5", "-700.00"),
                trade(2, TxnType.SELL, SAP, berlin(2024, 3, 5, 12, 0), "5", "750.00"),
            ]
        )

        assert timeline == {SAP: [(date(2024, 3, 4), D("5")), (date(2024, 3, 5), D("0"))]}

    def test_an_oversell_takes_the_timeline_to_zero_and_not_below(self) -> None:
        timeline = quantity_timeline(
            [
                trade(1, TxnType.BUY, SAP, berlin(2024, 3, 4, 9, 0), "10", "-1400.00"),
                trade(2, TxnType.SELL, SAP, berlin(2024, 3, 5, 12, 0), "12", "1700.00"),
            ]
        )

        assert timeline == {SAP: [(date(2024, 3, 4), D("10")), (date(2024, 3, 5), D("0"))]}

    def test_an_unclear_split_leaves_the_timeline_unchanged(self) -> None:
        timeline = quantity_timeline(
            [
                trade(1, TxnType.BUY, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                trade(2, TxnType.SPLIT, NVDA, berlin(2024, 6, 10), "0", new_quantity="20.0137"),
            ]
        )

        assert timeline == {NVDA: [(date(2024, 3, 20), D("2"))]}

    def test_cash_only_transactions_leave_no_point(self) -> None:
        timeline = quantity_timeline(
            [
                LedgerTxn(
                    id=1,
                    type=TxnType.DIVIDEND,
                    isin=SAP,
                    ts_utc=berlin(2024, 5, 15),
                    quantity=D("15"),
                    amount_eur=D("24.30"),
                    fees_eur=D("0.00"),
                    tax_eur=D("8.70"),
                    split_new_quantity=None,
                )
            ]
        )

        assert timeline == {}
