"""Property tests for the FIFO lot book (ledger/fifo.py, plan 5.5, 7.2), 500 examples each.

Hypothesis draws random histories of purchases, sales, splits, transfers in (with and without
the cost you enter) and out, and cash-only transactions over three ISINs. Many transactions
share a booking day, and some share the exact moment (a row without a time of day sits at 00:00
Berlin time), so the ordering rules are exercised too. The history reaches the ledger in a
random order, and its transaction ids do not follow time.

Splits are drawn so that their outcome is known in advance: when shares are held, the new total
is the holding times a clean ratio (sometimes with the old quantity stated as well), so the
ledger must apply it; when nothing is held, the ledger must report it as unclear. Unclear ratios
have their own unit tests in `test_fifo.py`.

Each property walks the history in the ledger's documented order (plan 5.5: booking time, then
splits, then purchases and transfers in, then sales and transfers out, then everything else,
then transaction id) keeping nothing but running totals, and checks the lot book against that
walk. The properties are the ones plan 7.2 lists:

- open quantity per ISIN = purchases + transfers in - sales - transfers out, split-scaled;
- no lot ever goes negative;
- initial cost = open cost + consumed cost, for every lot;
- realised gain = proceeds - consumed cost;
- disposals always use the oldest open lot first among the lots booked by then;
- a sale never uses a lot booked after it;
- building twice gives identical results;

plus the holdings, open cost and quantity timeline as of every booking day.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from itertools import pairwise
from math import prod

from hypothesis import given, settings
from hypothesis import strategies as st

from playground.core.dates import BERLIN, berlin_to_utc
from playground.core.types import TxnType
from playground.ledger.fifo import CostInput, LedgerTxn, Lot, LotBook, build_lots

ISINS = ("DE0007164600", "IE00B4L5Y983", "US67066G1040")
FIRST_DAY = date(2024, 3, 1)  # the histories cross the change to summer time on 2024-03-31
LAST_DAY_OFFSET = 90
# None is a row without a time of day, placed at 00:00 Berlin time; the fixed times make
# several transactions at the very same minute likely.
TIMES_OF_DAY = (None, None, time(9, 0), time(12, 30), time(17, 45))
KINDS = (
    TxnType.BUY,
    TxnType.BUY,
    TxnType.BUY,
    TxnType.SELL,
    TxnType.SELL,
    TxnType.TRANSFER_IN,
    TxnType.TRANSFER_OUT,
    TxnType.SPLIT,
    TxnType.DIVIDEND,
)
RATIOS = tuple(Fraction(r) for r in ("2", "3", "4", "5", "10", "3/2", "1/2", "1/5", "1/10"))
WHOLE_RATIOS = tuple(ratio for ratio in RATIOS if ratio.denominator == 1)
MAX_FRACTIONAL_SPLITS = 3
MAX_SPLIT_DECIMALS = 6
MAX_HOLDING = Decimal(10**7)
ZERO = Decimal(0)
CENT = Decimal("0.01")
TINY = Decimal("1E-8")

PROPERTY_SETTINGS = settings(max_examples=500, deadline=None)

_PHASE = {TxnType.SPLIT: 0, TxnType.BUY: 1, TxnType.TRANSFER_IN: 1, TxnType.SELL: 2, TxnType.TRANSFER_OUT: 2}
_ACQUISITIONS = (TxnType.BUY, TxnType.TRANSFER_IN)
_DISPOSALS = (TxnType.SELL, TxnType.TRANSFER_OUT)


def order_key(txn_type: TxnType, ts_utc: datetime, txn_id: int) -> tuple[datetime, int, int]:
    """The ledger's order (plan 5.5), written out here independently of the code under test."""
    return (ts_utc, _PHASE.get(txn_type, 3), txn_id)


def in_ledger_order(txns: list[LedgerTxn]) -> list[LedgerTxn]:
    return sorted(txns, key=lambda txn: order_key(txn.type, txn.ts_utc, txn.id))


def berlin_day(ts_utc: datetime) -> date:
    return ts_utc.astimezone(BERLIN).date()


def decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)
    return max(0, -exponent)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def total(values: list[Decimal]) -> Decimal:
    return sum(values, start=ZERO)


# --- The histories -----------------------------------------------------------------------------

quantities = st.integers(min_value=1, max_value=2000).map(lambda tenths: Decimal(tenths) / 10)
prices = st.integers(min_value=100, max_value=50_000).map(lambda cents: Decimal(cents) / 100)
fees = st.sampled_from((Decimal("0.00"), Decimal("1.00")))
taxes = st.sampled_from((Decimal("0.00"), Decimal("4.20"), Decimal("12.34")))


@dataclass(frozen=True)
class Slot:
    """Where and when one transaction of a history happens; its amounts are drawn later."""

    txn_type: TxnType
    isin: str
    ts_utc: datetime


def make_slot(txn_type: TxnType, isin: str, day_offset: int, at: time | None) -> Slot:
    local = datetime.combine(FIRST_DAY + timedelta(days=day_offset), at or time(0, 0))
    return Slot(txn_type, isin, berlin_to_utc(local))


slots = st.builds(
    make_slot,
    st.sampled_from(KINDS),
    st.sampled_from(ISINS),
    st.integers(min_value=0, max_value=LAST_DAY_OFFSET),
    st.sampled_from(TIMES_OF_DAY),
)


@dataclass
class Held:
    """What the history holds of each ISIN so far, while its transactions are drawn in ledger order."""

    quantity: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(ISINS, ZERO))
    # Each split by a ratio that is not a whole number adds up to one decimal place to a
    # quantity. At most three per ISIN keep every quantity within 4 decimals, so the ledger's
    # results stay exact and the checks below can compare them exactly.
    fractional_splits: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ISINS, 0))


def draw_txn(draw: st.DrawFn, txn_id: int, slot: Slot, held: Held) -> LedgerTxn:
    """One transaction for `slot`, given what is `held` just before it; updates `held`."""
    isin = slot.isin
    now = held.quantity[isin]

    def make(
        txn_type: TxnType,
        *,
        quantity: Decimal | None = None,
        amount_eur: Decimal | None = None,
        fees_eur: Decimal = Decimal("0.00"),
        tax_eur: Decimal = Decimal("0.00"),
        split_new_quantity: Decimal | None = None,
        cost_input: CostInput | None = None,
    ) -> LedgerTxn:
        return LedgerTxn(
            id=txn_id,
            type=txn_type,
            isin=isin,
            ts_utc=slot.ts_utc,
            quantity=quantity,
            amount_eur=amount_eur,
            fees_eur=fees_eur,
            tax_eur=tax_eur,
            split_new_quantity=split_new_quantity,
            cost_input=cost_input,
        )

    if slot.txn_type is TxnType.BUY:
        quantity, price, fee = draw(quantities), draw(prices), draw(fees)
        held.quantity[isin] = now + quantity
        return make(TxnType.BUY, quantity=quantity, amount_eur=-(money(quantity * price) + fee), fees_eur=fee)

    if slot.txn_type in _DISPOSALS:
        # Sometimes exactly what is held, so positions close; otherwise any size, so some oversell.
        quantity = now if now > 0 and draw(st.booleans()) else draw(quantities)
        held.quantity[isin] = max(now - quantity, ZERO)
        if slot.txn_type is TxnType.TRANSFER_OUT:
            return make(TxnType.TRANSFER_OUT, quantity=quantity)
        price, fee, tax = draw(prices), draw(fees), draw(taxes)
        booked = money(quantity * price) - fee - tax
        return make(TxnType.SELL, quantity=quantity, amount_eur=booked, fees_eur=fee, tax_eur=tax)

    if slot.txn_type is TxnType.TRANSFER_IN:
        quantity = draw(quantities)
        cost_input = None
        if draw(st.booleans()):
            acquired_on = berlin_day(slot.ts_utc) - timedelta(days=draw(st.integers(min_value=0, max_value=3000)))
            cost_input = CostInput(acquired_on=acquired_on, cost_eur=money(quantity * draw(prices)))
        held.quantity[isin] = now + quantity
        return make(TxnType.TRANSFER_IN, quantity=quantity, cost_input=cost_input)

    if slot.txn_type is TxnType.SPLIT:
        if now == 0:
            return make(TxnType.SPLIT, split_new_quantity=draw(quantities))  # nothing held: unclear
        choices = RATIOS if held.fractional_splits[isin] < MAX_FRACTIONAL_SPLITS else WHOLE_RATIOS
        ratio = draw(st.sampled_from(choices))
        new_total = now * ratio.numerator / ratio.denominator
        if decimal_places(new_total) <= MAX_SPLIT_DECIMALS and new_total <= MAX_HOLDING:
            held.quantity[isin] = new_total
            if ratio.denominator != 1:
                held.fractional_splits[isin] += 1
            old_quantity = now if draw(st.booleans()) else None
            return make(TxnType.SPLIT, quantity=old_quantity, split_new_quantity=new_total)
        # This ratio would make the holding too large or too finely divided; a cash-only row
        # takes its place.

    return make(TxnType.DIVIDEND, amount_eur=draw(prices))


@st.composite
def histories(draw: st.DrawFn) -> list[LedgerTxn]:
    history_slots = draw(st.lists(slots, min_size=1, max_size=30))
    ids = draw(st.permutations(range(1, len(history_slots) + 1)))
    # Amounts are drawn in ledger order, so a split's new total and a sale of "everything held"
    # can depend on what is held at that point.
    ordered = sorted(zip(ids, history_slots, strict=True), key=lambda pair: order_key(*_slot_key(pair)))
    held = Held()
    txns = [draw_txn(draw, txn_id, slot, held) for txn_id, slot in ordered]
    return draw(st.permutations(txns))


def _slot_key(pair: tuple[int, Slot]) -> tuple[TxnType, datetime, int]:
    txn_id, slot = pair
    return (slot.txn_type, slot.ts_utc, txn_id)


# --- The walk: running totals only ---------------------------------------------------------


@dataclass
class Walk:
    final: dict[str, Decimal] = field(default_factory=dict)
    end_of_day: dict[date, dict[str, Decimal]] = field(default_factory=dict)
    held_before: dict[int, Decimal] = field(default_factory=dict)
    oversold: set[int] = field(default_factory=set)
    applied_ratios: dict[int, Fraction] = field(default_factory=dict)
    unclear_splits: set[int] = field(default_factory=set)
    rank: dict[int, int] = field(default_factory=dict)


def walk(txns: list[LedgerTxn]) -> Walk:
    result = Walk()
    running = dict.fromkeys(ISINS, ZERO)
    for position, txn in enumerate(in_ledger_order(txns)):
        result.rank[txn.id] = position
        assert txn.isin is not None
        now = running[txn.isin]
        result.held_before[txn.id] = now
        if txn.type in _ACQUISITIONS:
            assert txn.quantity is not None
            running[txn.isin] = now + txn.quantity
        elif txn.type in _DISPOSALS:
            assert txn.quantity is not None
            if txn.quantity > now:
                result.oversold.add(txn.id)
            running[txn.isin] = max(now - txn.quantity, ZERO)
        elif txn.type is TxnType.SPLIT:
            assert txn.split_new_quantity is not None
            if now > 0:
                result.applied_ratios[txn.id] = Fraction(txn.split_new_quantity) / Fraction(now)
                running[txn.isin] = txn.split_new_quantity
            else:
                result.unclear_splits.add(txn.id)
        result.end_of_day[berlin_day(txn.ts_utc)] = {isin: q for isin, q in running.items() if q != 0}
    result.final = {isin: q for isin, q in running.items() if q != 0}
    return result


def fifo_key(lot: Lot) -> tuple[datetime, datetime, int]:
    """Oldest acquisition first; then booking time; then the id of the opening transaction."""
    return (lot.opened_ts, lot.booked_ts, lot.open_txn_id)


def last_disposal_rank(book: LotBook, rank: dict[int, int]) -> dict[int, int]:
    """For each lot that was ever used, the ledger position of the last transaction that used it."""
    last: dict[int, int] = {}
    for disposal in book.disposals:
        last[disposal.lot_open_txn_id] = max(last.get(disposal.lot_open_txn_id, -1), rank[disposal.txn_id])
    return last


# --- The properties ----------------------------------------------------------------------------


@PROPERTY_SETTINGS
@given(histories())
def test_open_quantity_is_what_came_in_minus_what_went_out_split_scaled(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)
    expected = walk(txns)

    open_quantity: dict[str, Decimal] = defaultdict(Decimal)
    for lot in book.lots:
        open_quantity[lot.isin] += lot.quantity_open
    assert {isin: q for isin, q in open_quantity.items() if q != 0} == expected.final

    assert {i.txn_id for i in book.issues if i.kind == "oversell"} == expected.oversold
    assert {s.txn_id: s.ratio for s in book.splits} == expected.applied_ratios
    assert {i.txn_id for i in book.issues if i.kind == "split_unclear"} == expected.unclear_splits

    for day, holdings in expected.end_of_day.items():
        assert book.holdings(day) == holdings
    assert book.holdings(min(expected.end_of_day) - timedelta(days=1)) == {}


@PROPERTY_SETTINGS
@given(histories())
def test_the_quantity_timeline_steps_exactly_where_holdings_change(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)
    expected = walk(txns)
    timeline = book.quantity_timeline()

    for isin, points in timeline.items():
        days = [day for day, _ in points]
        assert days == sorted(set(days))
        assert points[0][1] != 0
        assert all(before[1] != after[1] for before, after in pairwise(points))
        for day, quantity in points:
            assert expected.end_of_day[day].get(isin, ZERO) == quantity

    for day, holdings in expected.end_of_day.items():
        from_timeline = {}
        for isin, points in timeline.items():
            quantity = next((q for d, q in reversed(points) if d <= day), ZERO)
            if quantity != 0:
                from_timeline[isin] = quantity
        assert from_timeline == holdings


@PROPERTY_SETTINGS
@given(histories())
def test_no_lot_and_no_holding_ever_goes_negative(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)

    for lot in book.lots:
        assert lot.quantity_initial > 0
        assert 0 <= lot.quantity_open <= lot.quantity_initial
        if lot.cost_missing:
            assert lot.cost_eur_initial is None
            assert lot.cost_eur_open is None
        else:
            assert lot.cost_eur_initial is not None
            assert lot.cost_eur_open is not None
            assert 0 <= lot.cost_eur_open <= lot.cost_eur_initial
    for disposal in book.disposals:
        assert disposal.quantity > 0
        assert disposal.fees_eur >= 0
        assert disposal.cost_eur is None or disposal.cost_eur >= 0
    for points in book.quantity_timeline().values():
        assert all(quantity >= 0 for _, quantity in points)
    for day in walk(txns).end_of_day:
        assert all(quantity > 0 for quantity in book.holdings(day).values())


@PROPERTY_SETTINGS
@given(histories())
def test_every_lot_keeps_its_cost_and_quantity_accounted_for(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)
    expected = walk(txns)
    rank = expected.rank
    by_id = {txn.id: txn for txn in txns}
    last_used = last_disposal_rank(book, rank)

    for lot in book.lots:
        used = [d for d in book.disposals if d.lot_open_txn_id == lot.open_txn_id]

        # Initial cost = open cost + consumed cost, exactly; a lot without cost stays unknown.
        if lot.cost_missing:
            assert all(d.cost_eur is None for d in used)
        else:
            assert lot.cost_eur_initial is not None
            assert lot.cost_eur_open is not None
            assert lot.cost_eur_initial == lot.cost_eur_open + total(
                [d.cost_eur for d in used if d.cost_eur is not None]
            )
            assert all(d.cost_eur is not None for d in used)

        # A split scales a lot that was booked before it and still open at it (a lot is open
        # after a split exactly when it still has shares at the end or is used later).
        splits_on_lot = [
            s
            for s in book.splits
            if s.isin == lot.isin
            and rank[lot.open_txn_id] < rank[s.txn_id]
            and (lot.quantity_open > 0 or last_used.get(lot.open_txn_id, -1) > rank[s.txn_id])
        ]
        opening_quantity = by_id[lot.open_txn_id].quantity
        assert opening_quantity is not None
        assert Fraction(lot.quantity_initial) == Fraction(opening_quantity) * prod(s.ratio for s in splits_on_lot)
        # Initial quantity = open quantity + what was used, each use scaled by the splits after it.
        scaled_used = sum(
            (
                Fraction(d.quantity) * prod(s.ratio for s in splits_on_lot if rank[s.txn_id] > rank[d.txn_id])
                for d in used
            ),
            start=Fraction(0),
        )
        assert Fraction(lot.quantity_initial) == Fraction(lot.quantity_open) + scaled_used


@PROPERTY_SETTINGS
@given(histories())
def test_realised_gain_is_proceeds_minus_consumed_cost(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)
    expected = walk(txns)

    for txn in txns:
        if txn.type not in _DISPOSALS:
            continue
        assert txn.quantity is not None
        used = [d for d in book.disposals if d.txn_id == txn.id]
        disposed = total([d.quantity for d in used])
        assert disposed == min(txn.quantity, expected.held_before[txn.id])
        assert all(d.kind == txn.type.value for d in used)
        assert all(d.ts_utc == txn.ts_utc for d in used)

        if txn.type is TxnType.TRANSFER_OUT:
            assert all(d.proceeds_eur is None and d.realised_eur is None for d in used)
            continue

        assert txn.amount_eur is not None
        assert txn.tax_eur is not None
        assert txn.fees_eur is not None
        proceeds = [d.proceeds_eur for d in used]
        assert None not in proceeds
        proceeds_total = total([p for p in proceeds if p is not None])
        fees_total = total([d.fees_eur for d in used])
        # Proceeds are the gross amount minus fees: the booking amount plus the taxes withheld.
        sale_proceeds = txn.amount_eur + txn.tax_eur
        if txn.id in expected.oversold:
            # Shares that had no lot keep their part of the proceeds and fees out of the ledger.
            assert abs(proceeds_total - sale_proceeds * disposed / txn.quantity) <= TINY * (len(used) + 1)
            assert abs(fees_total - txn.fees_eur * disposed / txn.quantity) <= TINY * (len(used) + 1)
        else:
            assert proceeds_total == sale_proceeds
            assert fees_total == txn.fees_eur

        for d in used:
            if d.cost_eur is None:
                assert d.realised_eur is None
            else:
                assert d.proceeds_eur is not None
                assert d.realised_eur == d.proceeds_eur - d.cost_eur
        if all(d.cost_eur is not None for d in used):
            realised = total([d.realised_eur for d in used if d.realised_eur is not None])
            assert realised == proceeds_total - total([d.cost_eur for d in used if d.cost_eur is not None])


@PROPERTY_SETTINGS
@given(histories())
def test_disposals_use_the_oldest_lot_booked_by_then_and_never_a_later_one(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)
    expected = walk(txns)
    rank = expected.rank
    lots = {lot.open_txn_id: lot for lot in book.lots}
    last_used = last_disposal_rank(book, rank)

    def empty_after(lot: Lot, position: int) -> bool:
        """The lot had no shares left once the transaction at `position` was applied."""
        return lot.quantity_open == 0 and last_used.get(lot.open_txn_id, -1) <= position

    for txn in txns:
        if txn.type not in _DISPOSALS:
            continue
        position = rank[txn.id]
        used = [lots[d.lot_open_txn_id] for d in book.disposals if d.txn_id == txn.id]

        # Never a lot booked after the sale: only lots applied before it in the ledger's order.
        for lot in used:
            assert lot.isin == txn.isin
            assert lot.booked_ts <= txn.ts_utc
            assert rank[lot.open_txn_id] < position

        # Oldest first: in FIFO order, and every lot but the last one used up.
        keys = [fifo_key(lot) for lot in used]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)
        for lot in used[:-1]:
            assert empty_after(lot, position)

        # No older lot booked by then was skipped; after an oversell nothing booked by then is left.
        used_ids = {lot.open_txn_id for lot in used}
        for lot in book.lots:
            if lot.isin != txn.isin or rank[lot.open_txn_id] > position or lot.open_txn_id in used_ids:
                continue
            if txn.id in expected.oversold or (keys and fifo_key(lot) < keys[-1]):
                assert empty_after(lot, position)


@PROPERTY_SETTINGS
@given(histories(), st.data())
def test_building_is_deterministic_and_ignores_the_input_order(txns: list[LedgerTxn], data: st.DataObject) -> None:
    first = build_lots(txns)

    assert build_lots(txns) == first
    assert build_lots(data.draw(st.permutations(txns))) == first


@PROPERTY_SETTINGS
@given(histories())
def test_open_cost_sums_the_lots_open_on_each_day(txns: list[LedgerTxn]) -> None:
    book = build_lots(txns)
    expected = walk(txns)
    last_used_day: dict[int, date] = {}
    for disposal in book.disposals:
        day = berlin_day(disposal.ts_utc)
        last_used_day[disposal.lot_open_txn_id] = max(last_used_day.get(disposal.lot_open_txn_id, day), day)

    for day in [*expected.end_of_day, date(2030, 1, 1)]:
        cost = book.open_cost(day)
        assert list(cost) == list(book.holdings(day))
        for isin, value in cost.items():
            booked = [lot for lot in book.lots if lot.isin == isin and berlin_day(lot.booked_ts) <= day]
            still_open = [
                lot for lot in booked if lot.quantity_open > 0 or last_used_day.get(lot.open_txn_id, date.min) > day
            ]
            if any(lot.cost_missing for lot in still_open):
                assert value is None
                continue
            # Every lot booked by then, less the cost already used: lots used up count zero.
            known = [lot for lot in booked if not lot.cost_missing]
            used = [
                d.cost_eur
                for d in book.disposals
                if d.isin == isin and berlin_day(d.ts_utc) <= day and d.cost_eur is not None
            ]
            assert value == total([lot.cost_eur_initial for lot in known if lot.cost_eur_initial is not None]) - total(
                used
            )
