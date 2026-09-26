"""Unit tests for the FIFO lot book (ledger/fifo.py, plan 5.5, 1.2, 7.2).

First the golden portfolio of plan 1.2, built from in-memory transactions and checked figure by
figure against the hand computation. Then each rule of plan 5.5 on its own small history:
purchases, partial and full sales, splits (with and without the old quantity, clean and
unclear ratios), transfers in (with and without the cost you enter, including a sale booked
before a transfer in whose acquisition date is older), transfers out, oversells, the order of
transactions at the same moment, and the input contract.

A lot is one purchase or one transfer in, with its own quantity, dates and cost. FIFO (first in,
first out) means a sale uses up the oldest lot first.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from playground.core.dates import BERLIN, berlin_to_utc
from playground.core.types import TxnType
from playground.ledger.fifo import AppliedSplit, CostInput, Disposal, LedgerTxn, Lot, LotBook, build_lots

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"


def D(text: str) -> Decimal:
    """A `Decimal` from its exact text, so every figure below reads like the plan writes it."""
    return Decimal(text)


def berlin(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """A Europe/Berlin local time as a document prints it, converted to aware UTC."""
    return berlin_to_utc(datetime(year, month, day, hour, minute))  # noqa: DTZ001


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _dec(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def buy(txn_id: int, isin: str, ts: datetime, quantity: str, amount: str | None, *, fees: str = "0.00") -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=TxnType.BUY,
        isin=isin,
        ts_utc=ts,
        quantity=D(quantity),
        amount_eur=_dec(amount),
        fees_eur=D(fees),
        tax_eur=D("0.00"),
        split_new_quantity=None,
    )


def sell(
    txn_id: int,
    isin: str,
    ts: datetime,
    quantity: str,
    amount: str | None,
    *,
    fees: str | None = "0.00",
    tax: str | None = "0.00",
) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=TxnType.SELL,
        isin=isin,
        ts_utc=ts,
        quantity=D(quantity),
        amount_eur=_dec(amount),
        fees_eur=_dec(fees),
        tax_eur=_dec(tax),
        split_new_quantity=None,
    )


def split(
    txn_id: int, isin: str, ts: datetime, new_quantity: str | None, *, old_quantity: str | None = None
) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=TxnType.SPLIT,
        isin=isin,
        ts_utc=ts,
        quantity=_dec(old_quantity),
        amount_eur=None,
        fees_eur=D("0.00"),
        tax_eur=D("0.00"),
        split_new_quantity=_dec(new_quantity),
    )


def transfer_in(
    txn_id: int, isin: str, ts: datetime, quantity: str, *, acquired_on: date | None = None, cost: str | None = None
) -> LedgerTxn:
    cost_input = None if acquired_on is None or cost is None else CostInput(acquired_on=acquired_on, cost_eur=D(cost))
    return LedgerTxn(
        id=txn_id,
        type=TxnType.TRANSFER_IN,
        isin=isin,
        ts_utc=ts,
        quantity=D(quantity),
        amount_eur=None,
        fees_eur=D("0.00"),
        tax_eur=D("0.00"),
        split_new_quantity=None,
        cost_input=cost_input,
    )


def transfer_out(txn_id: int, isin: str, ts: datetime, quantity: str) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=TxnType.TRANSFER_OUT,
        isin=isin,
        ts_utc=ts,
        quantity=D(quantity),
        amount_eur=None,
        fees_eur=D("0.00"),
        tax_eur=D("0.00"),
        split_new_quantity=None,
    )


def cash(txn_id: int, txn_type: TxnType, ts: datetime, amount: str, *, isin: str | None = None) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=txn_type,
        isin=isin,
        ts_utc=ts,
        quantity=None,
        amount_eur=D(amount),
        fees_eur=D("0.00"),
        tax_eur=D("0.00"),
        split_new_quantity=None,
    )


def lot_of(book: LotBook, txn_id: int) -> Lot:
    (lot,) = [lot for lot in book.lots if lot.open_txn_id == txn_id]
    return lot


def taken(book: LotBook) -> list[tuple[int, Decimal, Decimal | None]]:
    """Each disposal as (the transaction that opened its lot, quantity, cost)."""
    return [(d.lot_open_txn_id, d.quantity, d.cost_eur) for d in book.disposals]


def issues(book: LotBook) -> list[tuple[str, int, str]]:
    return [(issue.kind, issue.txn_id, issue.isin) for issue in book.issues]


class TestGoldenPortfolio:
    """Plan 1.2: the lots, the sale T12, the split T11 and the transfer in T13, to the cent."""

    def test_lots_match_the_hand_computation(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        t2_time = utc(2024, 1, 15, 9, 5)  # 10:05 Berlin, winter time
        t3_time = utc(2024, 1, 31, 23, 0)  # 2024-02-01 00:00 Berlin: a savings plan has a date only
        t4_time = utc(2024, 2, 29, 23, 0)
        t5_time = utc(2024, 3, 12, 13, 30)
        t6_time = utc(2024, 3, 20, 14, 45)
        t8_time = utc(2024, 4, 10, 7, 12)  # 09:12 Berlin, summer time
        assert book.lots == [
            Lot(SAP, 2, "buy", t2_time, t2_time, D("10"), D("0"), D("1401.00"), D("0.00"), cost_missing=False),
            Lot(MSCIW, 3, "buy", t3_time, t3_time, D("2.5"), D("2.5"), D("200.00"), D("200.00"), cost_missing=False),
            Lot(MSCIW, 4, "buy", t4_time, t4_time, D("2.5"), D("2.5"), D("210.00"), D("210.00"), cost_missing=False),
            Lot(AAPL, 5, "buy", t5_time, t5_time, D("5"), D("5"), D("801.00"), D("801.00"), cost_missing=False),
            # 2 shares bought; the 10-for-1 split T11 makes them 20, at the same cost.
            Lot(NVDA, 6, "buy", t6_time, t6_time, D("20"), D("20"), D("1701.00"), D("1701.00"), cost_missing=False),
            Lot(SAP, 8, "buy", t8_time, t8_time, D("5"), D("3"), D("801.00"), D("480.60"), cost_missing=False),
            # Booked in on 2024-06-20, acquired on 2020-03-02 (the date and cost you entered).
            Lot(
                ALV,
                13,
                "transfer_in",
                utc(2024, 6, 19, 22, 0),
                utc(2020, 3, 1, 23, 0),
                D("4"),
                D("4"),
                D("800.00"),
                D("800.00"),
                cost_missing=False,
            ),
        ]

    def test_total_open_cost_basis_is_4192_60(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)
        open_costs = [lot.cost_eur_open for lot in book.lots if lot.quantity_open > 0]
        assert sum(cost for cost in open_costs if cost is not None) == D("4192.60")
        assert None not in open_costs

    def test_sale_t12_uses_up_t2_then_takes_two_shares_of_t8(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        sold_at = utc(2024, 6, 12, 9, 20)  # 11:20 Berlin, summer time
        assert book.disposals == [
            Disposal(
                isin=SAP,
                lot_open_txn_id=2,
                txn_id=12,
                kind="sell",
                ts_utc=sold_at,
                quantity=D("10"),
                cost_eur=D("1401.00"),
                proceeds_eur=D("1749.16666667"),
                fees_eur=D("0.83333333"),
                realised_eur=D("348.16666667"),
            ),
            Disposal(
                isin=SAP,
                lot_open_txn_id=8,
                txn_id=12,
                kind="sell",
                ts_utc=sold_at,
                quantity=D("2"),
                cost_eur=D("320.40"),
                proceeds_eur=D("349.83333333"),
                fees_eur=D("0.16666667"),
                realised_eur=D("29.43333333"),
            ),
        ]

    def test_realised_gain_on_t12_is_377_60_before_tax(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        assert sum((d.cost_eur for d in book.disposals if d.cost_eur is not None), start=D("0")) == D("1721.40")
        assert sum((d.proceeds_eur for d in book.disposals if d.proceeds_eur is not None), start=D("0")) == D("2099.00")
        assert sum((d.realised_eur for d in book.disposals if d.realised_eur is not None), start=D("0")) == D("377.60")
        assert sum((d.fees_eur for d in book.disposals), start=D("0")) == D("1.00")

    def test_split_t11_is_recorded_with_its_ratio(self, golden_txns: list[LedgerTxn]) -> None:
        book = build_lots(golden_txns)

        assert book.splits == [
            AppliedSplit(
                txn_id=11,
                isin=NVDA,
                ts_utc=utc(2024, 6, 9, 22, 0),
                quantity_before=D("2"),
                quantity_after=D("20"),
                numerator=10,
                denominator=1,
            )
        ]
        assert book.splits[0].ratio == Fraction(10)

    def test_the_golden_portfolio_raises_no_issue(self, golden_txns: list[LedgerTxn]) -> None:
        assert build_lots(golden_txns).issues == []

    def test_without_your_cost_the_transfer_in_has_cost_missing_and_its_booking_date(
        self, golden_txns: list[LedgerTxn]
    ) -> None:
        txns = [replace(txn, cost_input=None) if txn.id == 13 else txn for txn in golden_txns]

        lot = lot_of(build_lots(txns), 13)

        assert lot.cost_missing is True
        assert lot.cost_eur_initial is None
        assert lot.cost_eur_open is None
        assert lot.booked_ts == utc(2024, 6, 19, 22, 0)
        assert lot.opened_ts == lot.booked_ts
        assert lot.quantity_open == D("4")

    def test_figures_keep_the_digits_of_the_hand_computation(self, golden_txns: list[LedgerTxn]) -> None:
        # A computed figure that comes out exact keeps its natural digits; only a share that does
        # not come out exact carries 8 decimals (plan 3.3). So the golden files of later packages
        # read like plan 1.2: 480.60, not 480.60000000.
        book = build_lots(golden_txns)

        assert format(lot_of(book, 8).cost_eur_open, "f") == "480.60"
        assert format(lot_of(book, 2).cost_eur_open, "f") == "0.00"
        assert format(lot_of(book, 6).quantity_open, "f") == "20"
        assert [format(d.cost_eur, "f") for d in book.disposals] == ["1401.00", "320.40"]
        assert [format(d.proceeds_eur, "f") for d in book.disposals] == ["1749.16666667", "349.83333333"]
        assert [format(d.fees_eur, "f") for d in book.disposals] == ["0.83333333", "0.16666667"]
        assert [format(d.realised_eur, "f") for d in book.disposals] == ["348.16666667", "29.43333333"]


class TestBuy:
    def test_a_buy_opens_a_lot_whose_cost_is_the_booking_amount_with_fees(self) -> None:
        at = berlin(2024, 1, 15, 10, 5)

        book = build_lots([buy(1, SAP, at, "10", "-1401.00", fees="1.00")])

        assert book.lots == [Lot(SAP, 1, "buy", at, at, D("10"), D("10"), D("1401.00"), D("1401.00"), False)]
        assert book.disposals == []
        assert book.issues == []

    def test_a_buy_without_a_booking_amount_has_an_unknown_cost(self) -> None:
        lot = build_lots([buy(1, SAP, berlin(2024, 1, 15, 10, 5), "10", None)]).lots[0]

        assert lot.cost_missing is True
        assert lot.cost_eur_initial is None
        assert lot.cost_eur_open is None
        assert lot.quantity_open == D("10")

    def test_cash_only_transactions_open_no_lot(self) -> None:
        at = berlin(2024, 5, 15)
        txns = [
            cash(1, TxnType.DEPOSIT, at, "5000.00"),
            cash(2, TxnType.DIVIDEND, at, "24.30", isin=SAP),
            cash(3, TxnType.INTEREST, at, "3.12"),
            cash(4, TxnType.FEE, at, "-2.00"),
            cash(5, TxnType.TAX, at, "-12.50", isin=SAP),
            cash(6, TxnType.WITHDRAWAL, at, "-500.00"),
        ]

        book = build_lots(txns)

        assert book.lots == []
        assert book.disposals == []
        assert book.issues == []
        assert book.holdings(date(2024, 12, 31)) == {}


class TestSell:
    def test_a_partial_sell_reduces_the_lot_by_exactly_the_allocated_share(self) -> None:
        # T8 in plan 1.2: 2 of 5 shares give 801.00 x 2 / 5 = 320.40; the lot keeps 3 shares and 480.60.
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 4, 10, 9, 12), "5", "-801.00", fees="1.00"),
                sell(2, SAP, berlin(2024, 6, 12, 11, 20), "2", "349.00", fees="1.00"),
            ]
        )

        lot = book.lots[0]
        assert lot.quantity_open == D("3")
        assert lot.cost_eur_open == D("480.60")
        assert taken(book) == [(1, D("2"), D("320.40"))]
        assert lot.cost_eur_initial == D("480.60") + D("320.40")

    def test_each_partial_sell_takes_the_cost_of_the_shares_it_sells(self) -> None:
        # 10 shares for 1,000.00: every share carries 100.00, however many sales take a part.
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 10, 10, 0), "10", "-1000.00"),
                sell(2, SAP, berlin(2024, 2, 1, 10, 0), "2", "240.00"),
                sell(3, SAP, berlin(2024, 3, 1, 10, 0), "3", "390.00"),
                sell(4, SAP, berlin(2024, 4, 2, 10, 0), "5", "700.00"),
            ]
        )

        assert taken(book) == [(1, D("2"), D("200.00")), (1, D("3"), D("300.00")), (1, D("5"), D("500.00"))]
        assert (book.lots[0].quantity_open, book.lots[0].cost_eur_open) == (D("0"), D("0.00"))

    def test_a_sell_uses_up_the_oldest_acquisition_first(self) -> None:
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 10, 10, 0), "4", "-400.00"),
                buy(2, SAP, berlin(2024, 2, 10, 10, 0), "4", "-480.00"),
                sell(3, SAP, berlin(2024, 3, 10, 10, 0), "6", "780.00"),
            ]
        )

        assert taken(book) == [(1, D("4"), D("400.00")), (2, D("2"), D("240.00"))]
        assert lot_of(book, 1).quantity_open == D("0")
        assert lot_of(book, 2).quantity_open == D("2")
        assert lot_of(book, 2).cost_eur_open == D("240.00")

    def test_proceeds_are_the_gross_amount_minus_fees_so_taxes_withheld_count(self) -> None:
        # T12: 2,100.00 gross, a fee of 1.00 and 99.59 taxes withheld, so 1,999.41 is booked.
        # Proceeds are 2,099.00; the taxes are not a cost of the sale (plan 5.5).
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 15, 10, 5), "12", "-1680.00"),
                sell(2, SAP, berlin(2024, 6, 12, 11, 20), "12", "1999.41", fees="1.00", tax="99.59"),
            ]
        )

        (disposal,) = book.disposals
        assert disposal.proceeds_eur == D("2099.00")
        assert disposal.fees_eur == D("1.00")
        assert disposal.cost_eur == D("1680.00")
        assert disposal.realised_eur == D("419.00")

    def test_proceeds_and_fees_are_shared_by_quantity_and_add_up_exactly(self) -> None:
        at = berlin(2024, 1, 10, 10, 0)
        book = build_lots(
            [
                buy(1, SAP, at, "1", "-10.00"),
                buy(2, SAP, at, "1", "-10.00"),
                buy(3, SAP, at, "1", "-10.00"),
                sell(4, SAP, berlin(2024, 2, 1, 10, 0), "3", "100.00", fees="1.00"),
            ]
        )

        proceeds = [d.proceeds_eur for d in book.disposals]
        fees = [d.fees_eur for d in book.disposals]
        assert proceeds == [D("33.33333333"), D("33.33333333"), D("33.33333334")]
        assert fees == [D("0.33333333"), D("0.33333333"), D("0.33333334")]
        assert sum(fees, start=D("0")) == D("1.00")

    def test_a_sell_whose_taxes_are_unknown_has_unknown_proceeds_and_gain(self) -> None:
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 15, 10, 5), "10", "-1401.00"),
                sell(2, SAP, berlin(2024, 6, 12, 11, 20), "10", "1600.00", tax=None),
            ]
        )

        (disposal,) = book.disposals
        assert disposal.proceeds_eur is None
        assert disposal.realised_eur is None
        assert disposal.cost_eur == D("1401.00")

    def test_a_sell_whose_fees_are_unknown_stores_a_fee_of_zero(self) -> None:
        # Proceeds come from the booking amount, which is already after fees, so they stay exact.
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 15, 10, 5), "10", "-1401.00"),
                sell(2, SAP, berlin(2024, 6, 12, 11, 20), "10", "1600.00", fees=None),
            ]
        )

        (disposal,) = book.disposals
        assert disposal.fees_eur == D("0")
        assert disposal.proceeds_eur == D("1600.00")
        assert disposal.realised_eur == D("199.00")

    def test_a_sell_from_a_lot_with_unknown_cost_has_unknown_cost_and_gain(self) -> None:
        book = build_lots(
            [
                transfer_in(1, ALV, berlin(2024, 6, 20), "4"),
                sell(2, ALV, berlin(2024, 7, 1, 10, 0), "1", "290.00"),
            ]
        )

        (disposal,) = book.disposals
        assert disposal.cost_eur is None
        assert disposal.realised_eur is None
        assert disposal.proceeds_eur == D("290.00")
        assert lot_of(book, 1).cost_eur_open is None
        assert lot_of(book, 1).quantity_open == D("3")

    def test_a_sell_can_use_a_purchase_booked_at_the_same_moment(self) -> None:
        # Two CSV rows of one day without a time of day both sit at 00:00 Berlin time. The
        # purchase is applied first, so the sale is not an oversell, whatever the ids say.
        same = berlin(2024, 3, 1)
        book = build_lots([sell(1, SAP, same, "5", "550.00"), buy(2, SAP, same, "5", "-500.00")])

        assert book.issues == []
        assert taken(book) == [(2, D("5"), D("500.00"))]


class TestSplit:
    def test_a_split_scales_every_open_lot_and_keeps_its_cost(self) -> None:
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 1, 10, 10, 0), "3", "-300.00"),
                buy(2, NVDA, berlin(2024, 1, 20, 10, 0), "10", "-1000.00"),
                sell(3, NVDA, berlin(2024, 2, 1, 10, 0), "5", "550.00"),
                split(4, NVDA, berlin(2024, 3, 1), "16"),
            ]
        )

        first, second = book.lots
        # The first lot was used up before the split, so the split leaves it as it was.
        assert (first.quantity_initial, first.quantity_open) == (D("3"), D("0"))
        assert (first.cost_eur_initial, first.cost_eur_open) == (D("300.00"), D("0.00"))
        # The second lot had 8 of its 10 shares left: 16 after the 2-for-1 split. Its initial
        # quantity moves to the new share basis too (the 2 shares sold count as 4). Cost stays.
        assert (second.quantity_initial, second.quantity_open) == (D("20"), D("16"))
        assert (second.cost_eur_initial, second.cost_eur_open) == (D("1000.00"), D("800.00"))
        assert book.splits == [AppliedSplit(4, NVDA, berlin(2024, 3, 1), D("8"), D("16"), 2, 1)]
        assert book.issues == []

    def test_a_split_that_states_the_old_quantity_is_applied_when_it_agrees(self) -> None:
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                split(2, NVDA, berlin(2024, 6, 10), "20", old_quantity="2"),
            ]
        )

        assert book.lots[0].quantity_open == D("20")
        assert [s.ratio for s in book.splits] == [Fraction(10)]
        assert book.issues == []

    def test_a_split_that_states_a_different_old_quantity_is_unclear(self) -> None:
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                split(2, NVDA, berlin(2024, 6, 10), "20", old_quantity="3"),
            ]
        )

        assert book.lots[0].quantity_open == D("2")
        assert book.splits == []
        assert issues(book) == [("split_unclear", 2, NVDA)]
        assert "3 shares" in book.issues[0].message
        assert "the ledger holds 2" in book.issues[0].message

    def test_a_split_when_nothing_is_held_is_unclear(self) -> None:
        book = build_lots([split(1, NVDA, berlin(2024, 6, 10), "20")])

        assert book.lots == []
        assert book.splits == []
        assert issues(book) == [("split_unclear", 1, NVDA)]

    def test_a_split_after_everything_was_sold_is_unclear(self) -> None:
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                sell(2, NVDA, berlin(2024, 5, 2, 10, 0), "2", "1800.00"),
                split(3, NVDA, berlin(2024, 6, 10), "20"),
            ]
        )

        assert book.splits == []
        assert issues(book) == [("split_unclear", 3, NVDA)]

    @pytest.mark.parametrize("new_quantity", [None, "0", "-20"])
    def test_a_split_without_a_positive_new_quantity_is_unclear(self, new_quantity: str | None) -> None:
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                split(2, NVDA, berlin(2024, 6, 10), new_quantity),
            ]
        )

        assert book.lots[0].quantity_open == D("2")
        assert issues(book) == [("split_unclear", 2, NVDA)]

    @pytest.mark.parametrize(
        ("held", "new_total"),
        [
            ("2.5", "20.0137"),  # 8.00548 is no ratio of whole numbers up to 1000
            ("1", "1001"),  # 1001 for 1 is beyond 1000 for 1
            ("1001", "1"),  # so is 1 for 1001
            ("7", "0.0000001"),
        ],
    )
    def test_a_ratio_that_is_not_clean_is_unclear(self, held: str, new_total: str) -> None:
        book = build_lots(
            [buy(1, NVDA, berlin(2024, 3, 20, 15, 45), held, "-100.00"), split(2, NVDA, berlin(2024, 6, 10), new_total)]
        )

        assert book.lots[0].quantity_open == D(held)
        assert book.splits == []
        assert issues(book) == [("split_unclear", 2, NVDA)]
        assert "not applied" in book.issues[0].message

    @pytest.mark.parametrize(
        ("held", "new_total", "ratio"),
        [
            ("2", "20", Fraction(10)),
            ("10", "15", Fraction(3, 2)),
            ("25", "2.5", Fraction(1, 10)),  # a reverse split
            ("1", "1000", Fraction(1000)),
            ("1000", "1", Fraction(1, 1000)),
            ("2.5", "7.5", Fraction(3)),
        ],
    )
    def test_a_clean_ratio_of_whole_numbers_up_to_1000_is_applied(
        self, held: str, new_total: str, ratio: Fraction
    ) -> None:
        book = build_lots(
            [buy(1, NVDA, berlin(2024, 3, 20, 15, 45), held, "-100.00"), split(2, NVDA, berlin(2024, 6, 10), new_total)]
        )

        assert book.lots[0].quantity_open == D(new_total)
        assert book.lots[0].cost_eur_open == D("100.00")
        assert [(s.ratio, s.quantity_before, s.quantity_after) for s in book.splits] == [(ratio, D(held), D(new_total))]
        assert book.issues == []

    def test_a_new_total_rounded_to_six_decimals_still_gives_a_clean_ratio(self) -> None:
        # 0.333333 shares are 0.4999995 after a 3-for-2 split; the document prints 0.500000.
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "0.333333", "-10.00"),
                split(2, NVDA, berlin(2024, 6, 10), "0.5"),
            ]
        )

        assert [s.ratio for s in book.splits] == [Fraction(3, 2)]
        assert book.lots[0].quantity_open == D("0.5")  # what the document books, exactly

    def test_a_new_total_cut_to_six_decimals_still_gives_a_clean_ratio(self) -> None:
        # 0.333333 shares are 0.0333333 after a 1-for-10 reverse split; the document prints 0.033333.
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "0.333333", "-10.00"),
                split(2, NVDA, berlin(2024, 6, 10), "0.033333"),
            ]
        )

        assert [s.ratio for s in book.splits] == [Fraction(1, 10)]
        assert book.lots[0].quantity_open == D("0.033333")

    def test_the_youngest_lot_takes_the_rounding_so_the_new_total_matches_exactly(self) -> None:
        # 1.333333 shares in two lots are 1.9999995 after a 3-for-2 split; the document books 2.
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "0.333333", "-10.00"),
                buy(2, NVDA, berlin(2024, 4, 2, 15, 45), "1", "-30.00"),
                split(3, NVDA, berlin(2024, 6, 10), "2"),
            ]
        )

        older, younger = book.lots
        assert older.quantity_open == D("0.4999995")  # 0.333333 x 3 / 2, exactly
        assert younger.quantity_open == D("1.5000005")
        assert older.quantity_open + younger.quantity_open == D("2")

    def test_a_split_that_would_round_a_lot_to_nothing_is_unclear(self) -> None:
        # 0.000001 shares after a 1-for-1000 reverse split are 0.000000001, which 8 decimals
        # cannot hold. The lot would vanish, so the split is not applied.
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 1, 10, 10, 0), "0.000001", "-0.01"),
                buy(2, NVDA, berlin(2024, 1, 20, 10, 0), "1000", "-5000.00"),
                split(3, NVDA, berlin(2024, 6, 10), "1.000001"),
            ]
        )

        assert [lot.quantity_open for lot in book.lots] == [D("0.000001"), D("1000")]
        assert book.splits == []
        assert issues(book) == [("split_unclear", 3, NVDA)]

    def test_a_split_is_applied_before_a_trade_at_the_same_moment(self) -> None:
        # The split and a savings plan of the same day both sit at 00:00 Berlin time. The split
        # sees only the 2 shares held before it (ratio 10); the purchase comes after it.
        day = berlin(2024, 6, 10)
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                buy(2, NVDA, day, "5", "-600.00"),
                split(3, NVDA, day, "20"),
            ]
        )

        assert [lot.quantity_open for lot in book.lots] == [D("20"), D("5")]
        assert [s.ratio for s in book.splits] == [Fraction(10)]
        assert book.issues == []

    def test_a_split_leaves_other_instruments_alone(self) -> None:
        book = build_lots(
            [
                buy(1, NVDA, berlin(2024, 3, 20, 15, 45), "2", "-1701.00"),
                buy(2, AAPL, berlin(2024, 3, 12, 14, 30), "5", "-801.00"),
                split(3, NVDA, berlin(2024, 6, 10), "20"),
            ]
        )

        assert lot_of(book, 2).quantity_open == D("5")
        assert lot_of(book, 1).quantity_open == D("20")


class TestTransferIn:
    def test_a_transfer_in_with_your_cost_is_ordered_by_its_acquisition_date(self) -> None:
        book = build_lots(
            [
                buy(1, ALV, berlin(2024, 1, 10, 10, 0), "10", "-2500.00"),
                transfer_in(2, ALV, berlin(2024, 3, 1), "5", acquired_on=date(2020, 5, 5), cost="800.00"),
                sell(3, ALV, berlin(2024, 4, 2, 10, 0), "6", "1700.00"),
            ]
        )

        lot = lot_of(book, 2)
        assert lot.origin == "transfer_in"
        assert lot.booked_ts == berlin(2024, 3, 1)
        assert lot.opened_ts == berlin(2020, 5, 5)
        assert (lot.cost_eur_initial, lot.cost_missing) == (D("800.00"), False)
        # The sale takes the shares acquired in 2020 first, although they were booked in later.
        assert taken(book) == [(2, D("5"), D("800.00")), (1, D("1"), D("250.00"))]

    def test_a_transfer_in_without_your_cost_is_ordered_by_its_booking_date(self) -> None:
        book = build_lots(
            [
                buy(1, ALV, berlin(2024, 1, 10, 10, 0), "10", "-2500.00"),
                transfer_in(2, ALV, berlin(2024, 3, 1), "5"),
                sell(3, ALV, berlin(2024, 4, 2, 10, 0), "12", "3400.00"),
            ]
        )

        lot = lot_of(book, 2)
        assert lot.cost_missing is True
        assert lot.opened_ts == lot.booked_ts == berlin(2024, 3, 1)
        assert taken(book) == [(1, D("10"), D("2500.00")), (2, D("2"), None)]
        assert [d.realised_eur is None for d in book.disposals] == [False, True]
        assert lot.quantity_open == D("3")

    def test_a_sell_before_a_transfer_in_with_an_older_acquisition_uses_only_lots_booked_before_it(self) -> None:
        # The transfer in was acquired in 2020, before everything else, but booked in only after
        # the sale. A sale never uses a lot booked after it (plan 5.5).
        book = build_lots(
            [
                buy(1, ALV, berlin(2024, 1, 10, 10, 0), "10", "-2500.00"),
                sell(2, ALV, berlin(2024, 2, 1, 10, 0), "4", "1100.00"),
                transfer_in(3, ALV, berlin(2024, 3, 1), "5", acquired_on=date(2020, 5, 5), cost="800.00"),
            ]
        )

        assert taken(book) == [(1, D("4"), D("1000.00"))]
        assert lot_of(book, 3).quantity_open == D("5")
        assert lot_of(book, 3).cost_eur_open == D("800.00")
        assert book.issues == []

    def test_a_sell_larger_than_the_lots_booked_before_it_is_an_oversell_even_with_a_later_transfer_in(
        self,
    ) -> None:
        book = build_lots(
            [
                buy(1, ALV, berlin(2024, 1, 10, 10, 0), "2", "-500.00"),
                sell(2, ALV, berlin(2024, 2, 1, 10, 0), "4", "1100.00"),
                transfer_in(3, ALV, berlin(2024, 3, 1), "5", acquired_on=date(2020, 5, 5), cost="800.00"),
            ]
        )

        assert taken(book) == [(1, D("2"), D("500.00"))]
        assert issues(book) == [("oversell", 2, ALV)]
        assert lot_of(book, 3).quantity_open == D("5")


class TestTransferOut:
    def test_a_transfer_out_uses_lots_fifo_without_proceeds_or_gain(self) -> None:
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 10, 10, 0), "10", "-1000.00"),
                buy(2, SAP, berlin(2024, 2, 10, 10, 0), "10", "-1200.00"),
                transfer_out(3, SAP, berlin(2024, 3, 1), "15"),
            ]
        )

        assert [(d.kind, d.lot_open_txn_id, d.quantity, d.cost_eur) for d in book.disposals] == [
            ("transfer_out", 1, D("10"), D("1000.00")),
            ("transfer_out", 2, D("5"), D("600.00")),
        ]
        assert [(d.proceeds_eur, d.realised_eur) for d in book.disposals] == [(None, None), (None, None)]
        assert [d.fees_eur for d in book.disposals] == [D("0"), D("0")]
        assert (lot_of(book, 2).quantity_open, lot_of(book, 2).cost_eur_open) == (D("5"), D("600.00"))
        assert book.issues == []

    def test_a_transfer_out_larger_than_the_holding_is_an_oversell(self) -> None:
        book = build_lots(
            [buy(1, SAP, berlin(2024, 1, 10, 10, 0), "10", "-1000.00"), transfer_out(2, SAP, berlin(2024, 3, 1), "12")]
        )

        assert taken(book) == [(1, D("10"), D("1000.00"))]
        assert issues(book) == [("oversell", 2, SAP)]
        assert "transfer out" in book.issues[0].message


class TestOversell:
    def test_an_oversell_uses_what_is_held_and_reports_the_rest(self) -> None:
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 10, 10, 0), "10", "-1000.00"),
                sell(2, SAP, berlin(2024, 6, 12, 11, 20), "12", "1199.00", fees="1.00"),
            ]
        )

        (disposal,) = book.disposals
        assert disposal.quantity == D("10")
        # The 2 shares with no lot keep their share out of the ledger: 10 / 12 of 1,199.00 and of the fee.
        assert disposal.proceeds_eur == D("999.16666667")
        assert disposal.fees_eur == D("0.83333333")
        assert disposal.realised_eur == D("-0.83333333")
        assert book.lots[0].quantity_open == D("0")
        assert book.lots[0].cost_eur_open == D("0.00")
        assert issues(book) == [("oversell", 2, SAP)]

    def test_a_sell_with_nothing_held_is_an_oversell_that_disposes_of_nothing(self) -> None:
        book = build_lots([sell(1, SAP, berlin(2024, 6, 12, 11, 20), "12", "1999.41")])

        assert book.lots == []
        assert book.disposals == []
        assert issues(book) == [("oversell", 1, SAP)]

    def test_the_oversell_message_says_what_happened_in_plain_english(self) -> None:
        book = build_lots(
            [
                buy(1, SAP, berlin(2024, 1, 10, 10, 0), "10", "-1000.00"),
                sell(2, SAP, berlin(2024, 6, 12, 11, 20), "12", "1199.00"),
            ]
        )

        message = book.issues[0].message
        assert "2024-06-12" in message
        assert "12 shares" in message
        assert "10" in message
        assert SAP in message
        assert "\u2014" not in message  # no em dash
        assert "\u2013" not in message  # no en dash


class TestOrder:
    def test_the_order_of_the_input_does_not_matter(self, golden_txns: list[LedgerTxn]) -> None:
        assert build_lots(list(reversed(golden_txns))) == build_lots(golden_txns)

    def test_building_twice_gives_identical_results(self, golden_txns: list[LedgerTxn]) -> None:
        assert build_lots(golden_txns) == build_lots(golden_txns)

    def test_lots_acquired_at_the_same_moment_are_used_in_transaction_id_order(self) -> None:
        same = berlin(2024, 3, 1)
        book = build_lots(
            [
                buy(5, SAP, same, "10", "-1000.00"),
                buy(3, SAP, same, "10", "-1200.00"),
                sell(7, SAP, berlin(2024, 4, 1, 10, 0), "10", "1300.00"),
            ]
        )

        assert taken(book) == [(3, D("10"), D("1200.00"))]

    def test_lots_are_listed_in_booking_order(self) -> None:
        book = build_lots(
            [
                transfer_in(1, ALV, berlin(2024, 3, 1), "5", acquired_on=date(2020, 5, 5), cost="800.00"),
                buy(2, ALV, berlin(2024, 1, 10, 10, 0), "10", "-2500.00"),
                buy(3, SAP, berlin(2024, 2, 1, 10, 0), "1", "-140.00"),
            ]
        )

        assert [lot.open_txn_id for lot in book.lots] == [2, 3, 1]


class TestInputContract:
    """The pipeline hands the ledger complete transactions (plan 5.5); anything else is refused."""

    def test_two_transactions_with_the_same_id_are_refused(self) -> None:
        at = berlin(2024, 1, 10, 10, 0)
        with pytest.raises(ValueError, match="id 1"):
            build_lots([buy(1, SAP, at, "1", "-10.00"), buy(1, SAP, at, "1", "-10.00")])

    def test_a_booking_time_without_a_time_zone_is_refused(self) -> None:
        naive = datetime(2024, 1, 15, 9, 5)  # noqa: DTZ001 (the mistake under test)
        with pytest.raises(ValueError, match="UTC"):
            build_lots([buy(1, SAP, naive, "1", "-10.00")])

    def test_a_booking_time_in_another_time_zone_is_refused(self) -> None:
        local = datetime(2024, 1, 15, 10, 5, tzinfo=BERLIN)
        with pytest.raises(ValueError, match="UTC"):
            build_lots([buy(1, SAP, local, "1", "-10.00")])

    @pytest.mark.parametrize("txn_type", [TxnType.BUY, TxnType.SELL, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT])
    @pytest.mark.parametrize("quantity", [None, "0", "-1"])
    def test_a_trade_or_transfer_without_a_positive_quantity_is_refused(
        self, txn_type: TxnType, quantity: str | None
    ) -> None:
        txn = replace(buy(1, SAP, berlin(2024, 1, 10, 10, 0), "1", "-10.00"), type=txn_type, quantity=_dec(quantity))
        with pytest.raises(ValueError, match="quantity"):
            build_lots([txn])

    @pytest.mark.parametrize(
        "txn_type", [TxnType.BUY, TxnType.SELL, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT, TxnType.SPLIT]
    )
    def test_a_transaction_that_moves_shares_without_an_isin_is_refused(self, txn_type: TxnType) -> None:
        txn = LedgerTxn(
            id=1,
            type=txn_type,
            isin=None,
            ts_utc=berlin(2024, 1, 10, 10, 0),
            quantity=D("1"),
            amount_eur=D("-10.00"),
            fees_eur=D("0.00"),
            tax_eur=D("0.00"),
            split_new_quantity=D("2"),
        )
        with pytest.raises(ValueError, match="ISIN"):
            build_lots([txn])

    def test_a_cost_input_on_anything_but_a_transfer_in_is_refused(self) -> None:
        txn = replace(
            buy(1, SAP, berlin(2024, 1, 10, 10, 0), "1", "-10.00"),
            cost_input=CostInput(acquired_on=date(2020, 1, 2), cost_eur=D("5.00")),
        )
        with pytest.raises(ValueError, match="cost"):
            build_lots([txn])
