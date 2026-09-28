"""The FIFO lot book (plan 5.5): lots, disposals, splits and transfers, built from transactions.

A **lot** is one purchase or one transfer in of a security, with its own quantity, dates and
cost. **FIFO** (first in, first out) is the cost basis rule of German tax law: a sale uses up
the oldest lot first. A **disposal** is the part of one lot that one sale or transfer out used.

The ledger is pure. It depends only on `core`, never reads or writes the registry, and gives
the same result for the same transactions in whatever order they are passed. The import
pipeline feeds it the accepted transactions (plus the staged ones, for the reconciliation
diff), leaves held-back ones out, stores the lots and disposals, and turns the issues into
review items (plan 5.3.5, 5.3.6).

The rules:

- **Order.** Transactions are applied by booking time (`ts_utc`). At the same moment, splits
  come first, then purchases and transfers in, then sales and transfers out, then everything
  else; the transaction id breaks any tie that is left. (Plan 5.5 names only "splits before
  trades". Purchases before sales at the same moment let a sale use a lot booked at that very
  moment, which "booked on or before the sell" allows: two CSV rows of one day without a time
  of day both sit at 00:00.)
- **Booking date and acquisition date.** `booked_ts` is when a lot entered the account: the
  trade time, or the day a transfer was booked in. `opened_ts` is the acquisition time FIFO
  order uses: the trade time, or 00:00 Berlin time on the `acquired_on` day you entered for a
  transfer in. Holdings and open cost count a lot from its booking date (`holdings.py`).
- **Buy.** Opens a lot. Its cost is the absolute EUR booking amount, which includes the
  purchase fee (quantity x price + fees). A buy without a booking amount opens a lot whose cost
  is unknown (`cost_missing`); the pipeline normally holds such a buy back.
- **Sell.** Uses up the open lots of its ISIN that were booked on or before it, oldest
  acquisition first (then booking time, then the id of the opening transaction). A lot booked
  later is never used, even if it was acquired earlier. Proceeds are the gross amount minus
  fees, which is the EUR booking amount plus the taxes withheld: taxes are stored on the
  transaction, never in the cost basis. Proceeds and fees are shared out over the disposals by
  quantity with `allocate`, so the parts add up exactly. Realised gain is proceeds minus cost,
  before tax. A sale whose taxes are unknown has unknown proceeds and gain; a sale whose fees
  are unknown gives its disposals a fee of 0 (its proceeds come from the booking amount, which
  is already after fees).
- **Partial sell.** The lot keeps its quantity and cost reduced by exactly the part used: its
  open cost is split with `allocate` between the shares sold and the shares kept.
- **Split.** Every open lot of the ISIN has its quantity multiplied by the ratio; cost does not
  change. The ratio is the new quantity (`split_new_quantity`) divided by the quantity held just
  before the split. It must be a clean ratio p:q of whole numbers from 1 to 1000 (10 for 1,
  3 for 2, 1 for 10) that gives the new quantity to 6 decimals, the precision Trade Republic
  prints, and something must be held. Otherwise the ledger reports `split_unclear` and does not
  apply the split. When the split also states the old quantity (`quantity`), it must agree with
  the holding to 6 decimals, or the split is unclear too. The youngest lot takes any rounding,
  so the holding after the split is exactly the new quantity the document books. A lot's
  initial quantity moves to the new share basis as well.
- **Transfer in.** Opens a lot with `origin = transfer_in`, booked on the transfer's date, with
  the quantity from the document and the cost and acquisition date you entered (`cost_input`).
  Without your input the lot has `cost_missing`, is ordered by its booking date, and its cost
  and realised figures are unknown. The review item for the missing cost comes from the
  pipeline at staging, not from here.
- **Transfer out.** Uses up lots FIFO like a sale, with `kind = transfer_out` and no proceeds
  or gain.
- **Oversell.** A sale or transfer out larger than the open quantity uses what exists and
  reports `oversell`; lots never go negative. The shares that had no lot keep their part of
  the proceeds and fees out of the disposals.
- **Figures.** Quantities stay exactly as written. A computed figure is quantised to 8 decimals
  only when it does not come out exact (plan 3.3): 801.00 x 2 / 5 is 320.40, while 2,099.00 x
  10 / 12 is 1,749.16666667.

`build_lots` refuses, with `ValueError`, what the pipeline must never pass: two transactions
with the same id, a booking time that is not aware UTC, a buy, sell, split or transfer without
an ISIN, a buy, sell or transfer without a positive quantity, and a cost input on anything but
a transfer in.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from playground.core.dates import berlin_to_utc
from playground.core.money import allocate, q8
from playground.core.text import name_and_isin
from playground.core.types import TxnType
from playground.ledger.holdings import (
    PositionPoint,
    PositionRecorder,
    booking_date,
    holdings_on,
    open_cost_on,
    timeline_of,
)

LotOrigin = Literal["buy", "transfer_in"]
DisposalKind = Literal["sell", "transfer_out"]
IssueKind = Literal["oversell", "split_unclear"]

MAX_SPLIT_TERM = 1000
"""The largest whole number either side of a split ratio may have (1000 for 1, 1 for 1000)."""
SPLIT_TOLERANCE = Decimal("0.000001")
"""A split's quantities must agree to 6 decimals, the precision Trade Republic prints."""

_ZERO = Decimal(0)
_COMPUTED_PLACES = 8
_NEEDS_ISIN = frozenset({TxnType.BUY, TxnType.SELL, TxnType.SPLIT, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
_NEEDS_QUANTITY = frozenset({TxnType.BUY, TxnType.SELL, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
# Position at the same booking time: splits, then lots coming in, then lots going out, then the rest.
_PHASE = {
    TxnType.SPLIT: 0,
    TxnType.BUY: 1,
    TxnType.TRANSFER_IN: 1,
    TxnType.SELL: 2,
    TxnType.TRANSFER_OUT: 2,
}
_OTHER_PHASE = 3


@dataclass(frozen=True)
class CostInput:
    """The cost basis you entered for a transfer in (the `cost_basis_inputs` table, plan 4.1)."""

    acquired_on: date
    cost_eur: Decimal


@dataclass(frozen=True)
class LedgerTxn:
    """An accepted or staged transaction, as the ledger sees it (plan 5.5).

    `ts_utc` is the booking time, aware and in UTC. `quantity` is a number of shares, always
    positive; for a split it is the quantity held before it, when the document states one.
    `amount_eur` is the signed EUR booking amount (negative for a purchase). `fees_eur` and
    `tax_eur` are the fees and the taxes withheld, `None` when no source says. The ledger needs
    `tax_eur` to get a sale's proceeds (booking amount plus taxes withheld), which is why it is
    here in addition to the fields plan 5.5 lists. `split_new_quantity` is the quantity held
    after a split. `cost_input` is the cost you entered, for a transfer in only. `name` is the
    instrument's name, `None` when no document names it: it is used only to word a
    `LedgerIssue`'s message ("SAP SE (DE0007164600)"), never to book anything.
    """

    id: int
    type: TxnType
    isin: str | None
    ts_utc: datetime
    quantity: Decimal | None
    amount_eur: Decimal | None
    fees_eur: Decimal | None
    tax_eur: Decimal | None
    split_new_quantity: Decimal | None
    cost_input: CostInput | None = None
    name: str | None = None


@dataclass(frozen=True)
class LedgerIssue:
    """Something the ledger could not book as stated; the pipeline turns it into a review item."""

    kind: IssueKind
    txn_id: int
    isin: str
    message: str


@dataclass(frozen=True)
class Lot:
    """One lot as it stands after every transaction (the `lots` table, plan 4.1).

    Quantities are in the current share basis: a split scales `quantity_initial` together with
    `quantity_open`. `cost_eur_initial` and `cost_eur_open` are `None` exactly when
    `cost_missing` is true.
    """

    isin: str
    open_txn_id: int
    origin: LotOrigin
    booked_ts: datetime
    opened_ts: datetime
    quantity_initial: Decimal
    quantity_open: Decimal
    cost_eur_initial: Decimal | None
    cost_eur_open: Decimal | None
    cost_missing: bool


@dataclass(frozen=True)
class Disposal:
    """The part of one lot that one sale or transfer out used up (the `disposals` table, plan 4.1).

    The lot is the one opened by transaction `lot_open_txn_id`. `quantity` is in the share basis
    of the day of the disposal. `proceeds_eur` and `realised_eur` are `None` for a transfer out,
    and whenever they cannot be known.
    """

    isin: str
    lot_open_txn_id: int
    txn_id: int
    kind: DisposalKind
    ts_utc: datetime
    quantity: Decimal
    cost_eur: Decimal | None
    proceeds_eur: Decimal | None
    fees_eur: Decimal
    realised_eur: Decimal | None


@dataclass(frozen=True)
class AppliedSplit:
    """A split the ledger applied, with its clean ratio `numerator` for `denominator` (plan 5.7 uses it)."""

    txn_id: int
    isin: str
    ts_utc: datetime
    quantity_before: Decimal
    quantity_after: Decimal
    numerator: int
    denominator: int

    @property
    def ratio(self) -> Fraction:
        """The exact ratio: new shares per old share."""
        return Fraction(self.numerator, self.denominator)


@dataclass
class LotBook:
    """Everything `build_lots` found: lots, disposals, issues, applied splits and daily positions."""

    lots: list[Lot]
    disposals: list[Disposal]
    issues: list[LedgerIssue]
    splits: list[AppliedSplit]
    positions: dict[str, list[PositionPoint]]

    def holdings(self, as_of: date) -> dict[str, Decimal]:
        """The quantity held of each ISIN at the end of `as_of`, lots counted from their booking date."""
        return holdings_on(self.positions, as_of)

    def open_cost(self, as_of: date) -> dict[str, Decimal | None]:
        """The open cost basis of each ISIN held at the end of `as_of`; `None` while an open lot has no cost."""
        return open_cost_on(self.positions, as_of)

    def quantity_timeline(self) -> dict[str, list[tuple[date, Decimal]]]:
        """Per ISIN, the quantity at the end of every booking day on which it changed, splits applied."""
        return timeline_of(self.positions)


def build_lots(txns: Sequence[LedgerTxn]) -> LotBook:
    """Build the lot book from `txns` (plan 5.5). Pure and deterministic; the input order does not matter."""
    _check(txns)
    builder = _Builder()
    for txn in sorted(txns, key=_order_key):
        builder.apply(txn)
    return builder.book()


def quantity_timeline(txns: Sequence[LedgerTxn]) -> dict[str, list[tuple[date, Decimal]]]:
    """Per ISIN, `(booking date, quantity at the end of that day)` whenever it changed, splits applied."""
    return build_lots(txns).quantity_timeline()


def _order_key(txn: LedgerTxn) -> tuple[datetime, int, int]:
    return (txn.ts_utc, _PHASE.get(txn.type, _OTHER_PHASE), txn.id)


def _check(txns: Sequence[LedgerTxn]) -> None:
    seen: set[int] = set()
    for txn in txns:
        if txn.id in seen:
            raise ValueError(f"Transaction id {txn.id} appears twice. Every transaction needs its own id.")
        seen.add(txn.id)
        if txn.ts_utc.tzinfo is None or txn.ts_utc.utcoffset() != timedelta(0):
            raise ValueError(f"Transaction {txn.id}: ts_utc must be an aware UTC datetime, got {txn.ts_utc!r}.")
        if txn.type in _NEEDS_ISIN and not txn.isin:
            raise ValueError(
                f"Transaction {txn.id} ({txn.type.value}) has no ISIN. "
                "The ledger needs one for every buy, sell, split and transfer."
            )
        if txn.type in _NEEDS_QUANTITY and (txn.quantity is None or txn.quantity <= 0):
            raise ValueError(f"Transaction {txn.id} ({txn.type.value}) needs a positive quantity, got {txn.quantity}.")
        if txn.cost_input is not None and txn.type is not TxnType.TRANSFER_IN:
            raise ValueError(
                f"Transaction {txn.id} ({txn.type.value}) has a cost input, but only a transfer in takes one."
            )


@dataclass
class _OpenLot:
    """A lot while the book is being built; `Lot` is its final, frozen form."""

    isin: str
    open_txn_id: int
    origin: LotOrigin
    booked_ts: datetime
    opened_ts: datetime
    quantity_initial: Decimal
    quantity_open: Decimal
    cost_initial: Decimal | None
    cost_open: Decimal | None

    def fifo_key(self) -> tuple[datetime, datetime, int]:
        """Oldest acquisition first, then the earlier booking, then the lower transaction id."""
        return (self.opened_ts, self.booked_ts, self.open_txn_id)

    def take(self, quantity: Decimal) -> Decimal | None:
        """Use up `quantity` of the open shares and return the cost they carry (`None` if unknown)."""
        used_cost: Decimal | None
        if self.cost_open is None:
            used_cost = None
        elif quantity == self.quantity_open:
            used_cost = self.cost_open
            self.cost_open = self.cost_open - used_cost
        else:
            used_cost, self.cost_open = _shares(self.cost_open, [quantity, self.quantity_open - quantity])
        self.quantity_open -= quantity
        return used_cost

    def freeze(self) -> Lot:
        return Lot(
            isin=self.isin,
            open_txn_id=self.open_txn_id,
            origin=self.origin,
            booked_ts=self.booked_ts,
            opened_ts=self.opened_ts,
            quantity_initial=self.quantity_initial,
            quantity_open=self.quantity_open,
            cost_eur_initial=self.cost_initial,
            cost_eur_open=self.cost_open,
            cost_missing=self.cost_initial is None,
        )


class _Builder:
    """Applies transactions in ledger order and keeps the running state of the lot book."""

    def __init__(self) -> None:
        self._lots: list[_OpenLot] = []
        self._lots_by_isin: dict[str, list[_OpenLot]] = {}
        self._disposals: list[Disposal] = []
        self._issues: list[LedgerIssue] = []
        self._splits: list[AppliedSplit] = []
        self._positions = PositionRecorder()

    def apply(self, txn: LedgerTxn) -> None:
        if txn.isin is None:
            return  # a cash-only transaction; `_check` makes sure anything else has an ISIN
        if txn.type is TxnType.BUY:
            self._open(txn, txn.isin, _quantity(txn), "buy")
        elif txn.type is TxnType.TRANSFER_IN:
            self._open(txn, txn.isin, _quantity(txn), "transfer_in")
        elif txn.type is TxnType.SELL:
            self._dispose(txn, txn.isin, _quantity(txn), "sell")
        elif txn.type is TxnType.TRANSFER_OUT:
            self._dispose(txn, txn.isin, _quantity(txn), "transfer_out")
        elif txn.type is TxnType.SPLIT:
            self._split(txn, txn.isin)
        else:
            return  # dividends, interest, fees, taxes and cash movements leave the lots alone
        self._record(txn.isin, booking_date(txn.ts_utc))

    def book(self) -> LotBook:
        return LotBook(
            lots=[lot.freeze() for lot in self._lots],
            disposals=list(self._disposals),
            issues=list(self._issues),
            splits=list(self._splits),
            positions=self._positions.history(),
        )

    def _open_lots(self, isin: str) -> list[_OpenLot]:
        """The lots of `isin` that still have shares, oldest acquisition first."""
        return sorted((lot for lot in self._lots_by_isin.get(isin, []) if lot.quantity_open > 0), key=_OpenLot.fifo_key)

    def _record(self, isin: str, day: date) -> None:
        open_lots = self._open_lots(isin)
        quantity = sum((lot.quantity_open for lot in open_lots), start=_ZERO)
        costs = [lot.cost_open for lot in open_lots]
        known = [cost for cost in costs if cost is not None]
        open_cost = sum(known, start=_ZERO) if len(known) == len(costs) else None
        self._positions.record(isin, day, quantity, open_cost)

    def _open(self, txn: LedgerTxn, isin: str, quantity: Decimal, origin: LotOrigin) -> None:
        cost: Decimal | None
        opened_ts = txn.ts_utc
        if origin == "buy":
            cost = None if txn.amount_eur is None else abs(txn.amount_eur)
        elif txn.cost_input is None:
            cost = None
        else:
            cost = txn.cost_input.cost_eur
            opened_ts = berlin_to_utc(datetime.combine(txn.cost_input.acquired_on, time(0, 0)))
        lot = _OpenLot(
            isin=isin,
            open_txn_id=txn.id,
            origin=origin,
            booked_ts=txn.ts_utc,
            opened_ts=opened_ts,
            quantity_initial=quantity,
            quantity_open=quantity,
            cost_initial=cost,
            cost_open=cost,
        )
        self._lots.append(lot)
        self._lots_by_isin.setdefault(isin, []).append(lot)

    def _dispose(self, txn: LedgerTxn, isin: str, quantity: Decimal, kind: DisposalKind) -> None:
        parts: list[tuple[_OpenLot, Decimal]] = []
        missing = quantity
        for lot in self._open_lots(isin):
            if missing == 0:
                break
            part = min(lot.quantity_open, missing)
            parts.append((lot, part))
            missing -= part
        if missing > 0:
            self._issues.append(_oversell(txn, isin, kind, wanted=quantity, held=quantity - missing))
        if not parts:
            return

        # Shares with no lot (an oversell) keep their part of the proceeds and fees: it is the
        # last weight, so allocate gives it the remainder and the disposals their exact shares.
        weights = [part for _, part in parts] + ([missing] if missing > 0 else [])
        fee_parts = _shares(txn.fees_eur if txn.fees_eur is not None else _ZERO, weights)
        proceeds_parts: list[Decimal] | None = None
        if kind == "sell" and txn.amount_eur is not None and txn.tax_eur is not None:
            proceeds_parts = _shares(txn.amount_eur + txn.tax_eur, weights)

        for index, (lot, part) in enumerate(parts):
            cost = lot.take(part)
            proceeds = None if proceeds_parts is None else proceeds_parts[index]
            self._disposals.append(
                Disposal(
                    isin=isin,
                    lot_open_txn_id=lot.open_txn_id,
                    txn_id=txn.id,
                    kind=kind,
                    ts_utc=txn.ts_utc,
                    quantity=part,
                    cost_eur=cost,
                    proceeds_eur=proceeds,
                    fees_eur=fee_parts[index],
                    realised_eur=None if proceeds is None or cost is None else proceeds - cost,
                )
            )

    def _split(self, txn: LedgerTxn, isin: str) -> None:
        day = booking_date(txn.ts_utc)
        new_total = txn.split_new_quantity
        open_lots = self._open_lots(isin)
        held = sum((lot.quantity_open for lot in open_lots), start=_ZERO)

        def unclear(reason: str) -> None:
            message = f"The split of {name_and_isin(isin, txn.name)} on {day.isoformat()} {reason}"
            self._issues.append(LedgerIssue(kind="split_unclear", txn_id=txn.id, isin=isin, message=message))

        if new_total is None or new_total <= 0:
            unclear("does not give the number of shares held after it, so it was not applied.")
            return
        if held == 0:
            unclear(f"books in {_show(new_total)} shares, but none were held before it, so it was not applied.")
            return
        if txn.quantity is not None and abs(txn.quantity - held) >= SPLIT_TOLERANCE:
            unclear(
                f"says {_show(txn.quantity)} shares were held before it, "
                f"but the ledger holds {_show(held)}. It was not applied."
            )
            return
        ratio = _clean_ratio(held, new_total)
        if ratio is None:
            unclear(
                f"books in {_show(new_total)} shares where {_show(held)} were held. That is not a clean "
                f"ratio of whole numbers up to {MAX_SPLIT_TERM}, such as 10 for 1 or 3 for 2, "
                "so it was not applied."
            )
            return

        numerator, denominator = ratio.numerator, ratio.denominator
        # Every lot is scaled by the ratio; the youngest takes the rounding of the document's total.
        new_open = [_scaled(lot.quantity_open, numerator, denominator) for lot in open_lots[:-1]]
        new_open.append(new_total - sum(new_open, start=_ZERO))
        if any(quantity <= 0 for quantity in new_open):
            unclear("could not be spread over the lots held, so it was not applied.")
            return
        for lot, quantity in zip(open_lots, new_open, strict=True):
            used = lot.quantity_initial - lot.quantity_open
            lot.quantity_initial = quantity if used == 0 else quantity + _scaled(used, numerator, denominator)
            lot.quantity_open = quantity
        self._splits.append(
            AppliedSplit(
                txn_id=txn.id,
                isin=isin,
                ts_utc=txn.ts_utc,
                quantity_before=held,
                quantity_after=new_total,
                numerator=numerator,
                denominator=denominator,
            )
        )


def _quantity(txn: LedgerTxn) -> Decimal:
    """The quantity of a buy, sell or transfer, which `_check` has already made sure is there."""
    if txn.quantity is None:
        raise ValueError(f"Transaction {txn.id} ({txn.type.value}) needs a positive quantity, got None.")
    return txn.quantity


def _oversell(txn: LedgerTxn, isin: str, kind: DisposalKind, *, wanted: Decimal, held: Decimal) -> LedgerIssue:
    what = "sale" if kind == "sell" else "transfer out"
    security = name_and_isin(isin, txn.name)
    booked = f"On {booking_date(txn.ts_utc).isoformat()} a {what} of {_show(wanted)} shares of {security} was booked"
    if held == 0:
        message = f"{booked}, but none were held then. A purchase or a transfer in may be missing from your imports."
    else:
        message = (
            f"{booked}, but only {_show(held)} were held then. The ledger used the {_show(held)} it knows. "
            f"For the other {_show(wanted - held)} it has no purchase or transfer in, "
            "so one may be missing from your imports."
        )
    return LedgerIssue(kind="oversell", txn_id=txn.id, isin=isin, message=message)


def _clean_ratio(held: Decimal, new_total: Decimal) -> Fraction | None:
    """The ratio p/q, with 1 <= p, q <= 1000, that turns `held` into `new_total` to 6 decimals.

    Two candidates are tried: the closest fraction with a denominator up to 1000, and the
    closest with a numerator up to 1000 (found through the inverse). Of those within bounds
    that give `new_total` to 6 decimals, the closer one wins; `None` if neither does.
    """
    exact = Fraction(new_total) / Fraction(held)
    candidates = [exact.limit_denominator(MAX_SPLIT_TERM)]
    inverse = (1 / exact).limit_denominator(MAX_SPLIT_TERM)
    if inverse != 0:
        candidates.append(1 / inverse)
    tolerance = Fraction(SPLIT_TOLERANCE)
    clean = [
        candidate
        for candidate in candidates
        if 1 <= candidate.numerator <= MAX_SPLIT_TERM
        and 1 <= candidate.denominator <= MAX_SPLIT_TERM
        and abs(Fraction(held) * candidate - Fraction(new_total)) < tolerance
    ]
    return min(clean, key=lambda candidate: (abs(candidate - exact), candidate.denominator), default=None)


def _scaled(quantity: Decimal, numerator: int, denominator: int) -> Decimal:
    """`quantity` x numerator / denominator: exact when that fits 8 decimals, else quantised (plan 3.3)."""
    scaled = quantity * numerator / denominator
    return scaled if _places(scaled) <= _COMPUTED_PLACES else q8(scaled)


def _shares(total: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """`allocate(total, weights)`, with each part written to `total`'s own decimals when it is exact there.

    `allocate` quantises every part to 8 decimals, so 801.00 split 2:3 comes back as 320.40000000
    and 480.60000000; this keeps them as 320.40 and 480.60. Values do not change, only padding.
    """
    places = _places(total)
    if places > _COMPUTED_PLACES:
        return allocate(total, weights)
    unit = Decimal(1).scaleb(-places)
    parts = []
    for part in allocate(total, weights):
        shorter = part.quantize(unit)
        parts.append(shorter if shorter == part else part)
    return parts


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _show(value: Decimal) -> str:
    """A quantity for a message: plain digits, no exponent, no padding zeros ("20", "2.5")."""
    return format(value.normalize(), "f")
