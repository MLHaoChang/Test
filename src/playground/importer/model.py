"""The parser output contract every document and CSV parser returns (plan 5.3.1).

Every parser -- one per Trade Republic document layout, plus the CSV and
manual formats -- returns a `ParseResult`. A parser never raises for a
problem with the *document* (an unknown layout, a missing field, numbers
that do not add up); it returns what it could read, plus a
`ReviewNeeded` for what it could not (5.3.2, 5.3.5). Raising is reserved
for a genuine programming error, never for bad input.

This module has no parsing logic of its own; it is only the shared data
contract, built first (in WP2) so every later parser package can start
from it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from playground.core.dates import SourceTime
from playground.core.types import TxnType


class SourceKind(StrEnum):
    """Which kind of file a candidate transaction was read from (5.3.4)."""

    PDF_DOCUMENT = "pdf_document"
    CSV_EXPORT = "csv_export"
    PDF_STATEMENT = "pdf_statement"
    MANUAL_CSV = "manual_csv"


class ReviewKind(StrEnum):
    """Every reason a document, row or transaction can land in the review queue (5.3.5)."""

    UNKNOWN_LAYOUT = "unknown_layout"
    AMBIGUOUS_LAYOUT = "ambiguous_layout"
    UNKNOWN_CSV_HEADER = "unknown_csv_header"
    UNPARSED_ROW = "unparsed_row"
    MISSING_FIELD = "missing_field"
    AMOUNTS_DO_NOT_ADD_UP = "amounts_do_not_add_up"
    FIELD_CONFLICT = "field_conflict"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    MISSING_COST_BASIS = "missing_cost_basis"
    OVERSELL = "oversell"
    SPLIT_UNCLEAR = "split_unclear"
    CORPORATE_ACTION = "corporate_action"
    INVALID_ISIN = "invalid_isin"


@dataclass(frozen=True)
class ParsedTransaction:
    """One transaction as read from one document or row, before matching (5.3.4: a "candidate").

    `amount` is the signed booking amount as printed, in `currency`;
    `amount_eur` is that amount converted to EUR (3.3: the document's own
    EUR amount wins when it states one). `tax_eur` is `None` when the
    source simply does not say (a line known only from an account
    statement), which is different from a known tax of zero.
    """

    type: TxnType
    isin: str | None
    name: str | None
    time: SourceTime
    value_date: date | None
    quantity: Decimal | None
    price: Decimal | None
    currency: str
    amount: Decimal | None
    amount_eur: Decimal | None
    fx_rate: Decimal | None
    fx_source: Literal["document", "ecb", "none"]
    fees_eur: Decimal | None
    tax_eur: Decimal | None
    tax_detail: Mapping[str, Decimal]
    split_new_quantity: Decimal | None
    origin: str | None
    source_ref: str | None
    source_kind: SourceKind
    evidence: tuple[int, int]


@dataclass(frozen=True)
class ReviewNeeded:
    """A problem a parser found that it cannot resolve on its own (5.3.5).

    `extracted_text` is the full text (or raw row) the problem came from,
    shown to you unedited next to `fields`, whatever could be read before
    the problem stopped the parser.

    `evidence` links the item to one transaction of a file that lists
    many, such as a CSV row whose amounts do not add up: it equals that
    transaction's `evidence`, and the pipeline holds exactly that
    transaction back while the item is open. It is `None` for an item
    about a row that gave no transaction, or about the whole file. The
    item of a document with one transaction concerns that transaction
    without it.
    """

    kind: ReviewKind
    message: str
    extracted_text: str
    fields: Mapping[str, str]
    evidence: tuple[int, int] | None = None


@dataclass(frozen=True)
class ParseResult:
    """Everything one parser run produced: zero or more transactions, zero or more review items.

    `confirmed_holdings` is reserved for a securities account statement
    parser (none ships in P0); it defaults to empty for every other
    parser.
    """

    doc_type: str
    parser_id: str
    parser_version: int
    transactions: list[ParsedTransaction]
    review: list[ReviewNeeded]
    confirmed_holdings: tuple[tuple[str, Decimal], ...] = ()
