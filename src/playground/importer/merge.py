"""The fields in use of a transaction, from all the reports of it (plan 5.3.4, the precedence rule).

When several files report the same transaction, one transaction is kept and every report is one
of its sources. Each field is taken from the source with the highest precedence that has it
(manual CSV 4, PDF document 3, CSV export 2, account statement 1; `keys.PRECEDENCE`). Between two
reports of the same kind (a re-export, a corrected manual CSV) the later import wins.

A few fields travel together, so a transaction never mixes halves of two reports:

- the booking date comes from the highest source; the time of day from the highest source that
  prints one for that same date (a statement line gives only a date, so a trade keeps the minute
  of its confirmation);
- the booking amount and its currency;
- the FX rate and where it came from;
- the taxes withheld and their detail.

A manual CSV row carries your note where a document carries the instrument's name, so a manual
row never names the instrument.

The merge is pure: the same reports always give the same fields, whatever order they come in.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from playground.core.types import TxnType
from playground.importer.keys import NO_CASH_TYPES, booking_day
from playground.importer.model import ParsedTransaction, SourceKind
from playground.importer.tr.layouts.common import berlin_source_time

COMPARED_FIELDS = ("quantity", "amount_eur", "fees_eur", "tax_eur", "split_new_quantity")
"""The fields whose disagreement between two kinds of source raises `field_conflict`.

These are the figures the ledger books. The price is left out: a CSV may state it in EUR where
the document states it in the paying currency, and nothing is computed from it.
"""

FIELD_LABELS = {
    "quantity": "quantity",
    "amount_eur": "booking amount",
    "fees_eur": "fees",
    "tax_eur": "taxes",
    "split_new_quantity": "number of shares after the split",
}

KIND_LABELS = {
    SourceKind.MANUAL_CSV: "your manual CSV file",
    SourceKind.PDF_DOCUMENT: "the PDF document",
    SourceKind.CSV_EXPORT: "the CSV export",
    SourceKind.PDF_STATEMENT: "the account statement",
}

NEEDS_ISIN = frozenset({TxnType.BUY, TxnType.SELL, TxnType.SPLIT, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
NEEDS_QUANTITY = frozenset({TxnType.BUY, TxnType.SELL, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
NEEDS_AMOUNT = frozenset(set(TxnType) - NO_CASH_TYPES)
"""Every type that moves cash: nothing is booked without its amount (plan 5.3.4)."""


@dataclass(frozen=True)
class Report:
    """One source of a transaction as the merge sees it: its kind, precedence, order and fields.

    `order` grows with every import (the later import has the higher order), so it decides
    between two reports of the same kind. `batch_id` is the import batch the report came in.
    """

    kind: SourceKind
    precedence: int
    order: int
    txn: ParsedTransaction
    file_name: str
    report_key: str
    batch_id: int = 0


@dataclass(frozen=True)
class MergedView:
    """The fields in use of one transaction, the highest precedence among its sources, and who gave what.

    `txn` holds the fields in use; its `source_kind` and `evidence` are those of the highest
    source. `name` is the instrument name from the highest source that is not a manual row.
    `providers` maps each field (and "time" and "date") to the report it was taken from.
    """

    txn: ParsedTransaction
    precedence: int
    name: str | None
    providers: Mapping[str, Report]

    @property
    def top(self) -> Report:
        return self.providers["date"]


@dataclass(frozen=True)
class FieldConflict:
    """Two kinds of source that disagree on a field, and the value in use (the higher precedence)."""

    field: str
    values: Mapping[SourceKind, Decimal]
    used: Decimal
    used_kind: SourceKind

    def message(self, subject: str) -> str:
        label = FIELD_LABELS[self.field]
        parts = [f"{KIND_LABELS[kind]} says {_show(value)}" for kind, value in self.values.items()]
        return (
            f"The sources of {subject} disagree on the {label}: {', '.join(parts)}. "
            f"The value from {KIND_LABELS[self.used_kind]} is used. "
            "Import a manual CSV row with the right value, or dismiss this item if the value in use is right."
        )


def ordered(reports: Sequence[Report]) -> list[Report]:
    """Highest precedence first; between two reports of one kind, the later import first."""
    return sorted(reports, key=lambda report: (-report.precedence, -report.order))


def merge(reports: Sequence[Report]) -> MergedView:
    """The fields in use from `reports` (at least one), field by field by precedence."""
    if not reports:
        raise ValueError("merge needs at least one report.")
    chain = ordered(reports)
    top = chain[0]
    providers: dict[str, Report] = {"date": top}

    def first(field: str) -> Report | None:
        for report in chain:
            if getattr(report.txn, field) is not None:
                providers[field] = report
                return report
        return None

    day = booking_day(top.txn)
    timed = next(
        (report for report in chain if report.txn.time.precision == "minute" and booking_day(report.txn) == day), None
    )
    if timed is not None:
        time, providers["time"] = timed.txn.time, timed
    elif top.txn.time.precision == "day":
        time, providers["time"] = top.txn.time, top
    else:
        time, providers["time"] = berlin_source_time(day), top

    isin = first("isin")
    value_date = first("value_date")
    quantity = first("quantity")
    price = first("price")
    amount = first("amount_eur")
    fx = first("fx_rate")
    fees = first("fees_eur")
    tax = first("tax_eur")
    split = first("split_new_quantity")
    origin = first("origin")
    source_ref = first("source_ref")

    fields = replace(
        top.txn,
        isin=isin.txn.isin if isin else None,
        name=None,
        time=time,
        value_date=value_date.txn.value_date if value_date else None,
        quantity=quantity.txn.quantity if quantity else None,
        price=price.txn.price if price else None,
        currency=amount.txn.currency if amount else top.txn.currency,
        amount=amount.txn.amount if amount else None,
        amount_eur=amount.txn.amount_eur if amount else None,
        fx_rate=fx.txn.fx_rate if fx else None,
        fx_source=fx.txn.fx_source if fx else "none",
        fees_eur=fees.txn.fees_eur if fees else None,
        tax_eur=tax.txn.tax_eur if tax else None,
        tax_detail=dict(tax.txn.tax_detail) if tax else {},
        split_new_quantity=split.txn.split_new_quantity if split else None,
        origin=origin.txn.origin if origin else None,
        source_ref=source_ref.txn.source_ref if source_ref else None,
    )
    name = next(
        (report.txn.name for report in chain if report.kind is not SourceKind.MANUAL_CSV and report.txn.name), None
    )
    return MergedView(txn=fields, precedence=top.precedence, name=name, providers=providers)


def conflicts(reports: Sequence[Report]) -> list[FieldConflict]:
    """The fields on which two kinds of source disagree, unless a manual row sets that field.

    Each kind speaks through its latest report that has the field. The value in use is the one
    of the highest kind.
    """
    found = []
    chain = ordered(reports)
    for field in COMPARED_FIELDS:
        values: dict[SourceKind, Decimal] = {}
        for report in chain:
            value = getattr(report.txn, field)
            if value is not None and report.kind not in values:
                values[report.kind] = value
        if SourceKind.MANUAL_CSV in values or len(set(values.values())) < 2:
            continue
        used_kind = next(iter(values))
        found.append(FieldConflict(field=field, values=values, used=values[used_kind], used_kind=used_kind))
    return found


def missing_fields(txn: ParsedTransaction) -> list[str]:
    """The fields `txn` cannot be booked without: an ISIN, a positive quantity of shares, or the
    booking amount of a transaction that moves cash (a purchase without it would open a lot whose
    cost nobody knows, a dividend without it would book nothing)."""
    missing = []
    if txn.type in NEEDS_ISIN and not txn.isin:
        missing.append("isin")
    if txn.type in NEEDS_QUANTITY and (txn.quantity is None or txn.quantity <= 0):
        missing.append("quantity")
    if txn.type in NEEDS_AMOUNT and txn.amount_eur is None:
        missing.append("amount")
    return missing


def _show(value: Decimal) -> str:
    return format(value, "f")
