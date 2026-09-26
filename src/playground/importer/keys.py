"""The semantic key, the report key and the content hash of a transaction (plan 5.3.4).

Different files report the same transaction: the PDF note, the CSV row, the account statement
line. Each parsed transaction, a **candidate**, gets two keys.

The **semantic key** is built only from fields every source carries:

- cash-moving types: ``type | isin or "-" | booking date | signed EUR amount in cents | EUR``;
- types without cash (split, transfer in and out): ``type | isin | booking date | quantity``, the
  new total for a split.

The booking date is the Europe/Berlin calendar date the source gives the transaction (the
execution date of a trade, the document date of a dividend, interest or tax note, the booking day
of a statement line, the date of a CSV row), which every parser puts in `time.ts_local`. The
time of day is never part of the key, because account statements carry only a date. The amount
is always the EUR amount booked on the cash account, never a foreign-currency figure.

The **report key** says which report a candidate is within its kind of file, so the same report
arriving in another file is recognised:

- files that list many transactions (the CSV export, a manual CSV file, an account statement):
  ``kind | semantic key | time of day or "-" | ordinal``, where the ordinal counts the rows with
  that key and time inside the file (1 for the first, 2 for a second identical row);
- a PDF document: ``pdf_document | source_ref``, the order, execution or reference number it
  prints, or ``pdf_document | text | <SHA-256 of the extracted text>`` for a layout that prints
  none.

The **content hash** is the SHA-256 of ``semantic key | occurrence``, where the occurrence is 1
for the first transaction with a key and 2 for a second one with the same key. It is unique per
portfolio.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, SourceKind

PRECEDENCE: Mapping[SourceKind, int] = {
    SourceKind.MANUAL_CSV: 4,
    SourceKind.PDF_DOCUMENT: 3,
    SourceKind.CSV_EXPORT: 2,
    SourceKind.PDF_STATEMENT: 1,
}
"""Which source wins a field (plan 5.3.4): what you typed, then the document, the export, the statement."""

NO_CASH_TYPES = frozenset({TxnType.SPLIT, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
"""Types that book no cash: their key carries the quantity instead of an amount."""

LISTING_KINDS = frozenset({SourceKind.CSV_EXPORT, SourceKind.MANUAL_CSV, SourceKind.PDF_STATEMENT})
"""Kinds of file that list many transactions, whose report key counts identical rows."""

NEAR_DATE_DAYS = 3
"""Rule c of plan 5.3.4: keys that differ only in the date, by up to this many calendar days."""

UNKNOWN = "?"
"""Stands for an amount or quantity the source does not give, so the key can still be built."""

_CENT = Decimal(100)


@dataclass(frozen=True)
class KeyParts:
    """A semantic key taken apart: `prefix` is ``type|isin``, `tail` is the amount part or the quantity."""

    prefix: str
    day: date
    tail: str

    @property
    def key(self) -> str:
        return f"{self.prefix}|{self.day.isoformat()}|{self.tail}"

    @property
    def without_day(self) -> tuple[str, str]:
        """What two keys must share to be near-date matches of each other (rule c)."""
        return (self.prefix, self.tail)


def booking_day(txn: ParsedTransaction) -> date:
    """The Europe/Berlin calendar date the source books `txn` on: the date part of `ts_local`."""
    return txn.time.ts_local.date()


def cents(amount: Decimal) -> str:
    """A signed EUR amount in whole cents: -1401.00 gives "-140100", 0.94 gives "94"."""
    whole = (amount * _CENT).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return str(int(whole))


def quantity_text(quantity: Decimal) -> str:
    """A quantity without padding zeros or an exponent: 20.000 gives "20", 0.4798 stays "0.4798"."""
    if quantity == 0:
        return "0"
    return format(quantity.normalize(), "f")


def key_parts(txn: ParsedTransaction) -> KeyParts:
    """The parts of `txn`'s semantic key."""
    prefix = f"{txn.type.value}|{txn.isin or '-'}"
    if txn.type in NO_CASH_TYPES:
        quantity = txn.split_new_quantity if txn.type is TxnType.SPLIT else txn.quantity
        tail = quantity_text(quantity) if quantity is not None else UNKNOWN
    else:
        tail = f"{cents(txn.amount_eur) if txn.amount_eur is not None else UNKNOWN}|EUR"
    return KeyParts(prefix=prefix, day=booking_day(txn), tail=tail)


def semantic_key(txn: ParsedTransaction) -> str:
    """The semantic key of `txn` (see the module docstring)."""
    return key_parts(txn).key


def parse_key(key: str) -> KeyParts:
    """Take a semantic key apart again. Raises `ValueError` for text that is not one."""
    fields = key.split("|")
    if len(fields) < 4:
        raise ValueError(f"Not a semantic key: {key!r}.")
    return KeyParts(prefix="|".join(fields[:2]), day=date.fromisoformat(fields[2]), tail="|".join(fields[3:]))


def time_of_day(txn: ParsedTransaction) -> str:
    """The time of day a source prints, as "HH:MM", or "-" when it gives only a date."""
    if txn.time.precision != "minute":
        return "-"
    return txn.time.ts_local.strftime("%H:%M")


def report_keys(txns: Sequence[ParsedTransaction], *, text: str | None = None) -> list[str]:
    """The report key of every candidate of one file, in file order (see the module docstring).

    `text` is the extracted text of a PDF document; it is needed only for a document that prints
    no reference number.
    """
    seen: dict[tuple[str, str], int] = {}
    keys = []
    for txn in txns:
        if txn.source_kind in LISTING_KINDS:
            key = semantic_key(txn)
            at = time_of_day(txn)
            ordinal = seen.get((key, at), 0) + 1
            seen[(key, at)] = ordinal
            keys.append(f"{txn.source_kind.value}|{key}|{at}|{ordinal}")
        elif txn.source_ref:
            keys.append(f"{txn.source_kind.value}|{txn.source_ref}")
        elif text is not None:
            keys.append(f"{txn.source_kind.value}|text|{sha256_text(text)}")
        else:
            raise ValueError("A PDF document without a reference number needs its text for the report key.")
    return keys


def content_hash(key: str, occurrence: int) -> str:
    """The SHA-256 of ``semantic key | occurrence``, unique per portfolio (plan 4.1)."""
    return hashlib.sha256(f"{key}|{occurrence}".encode()).hexdigest()


def sha256_text(text: str) -> str:
    """The SHA-256 of a text, encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
