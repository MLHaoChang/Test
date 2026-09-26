"""The manual transaction format (plan 5.3.3, 5.3.4): whatever the automatic parsers cannot read.

`date;time;type;isin;quantity;price;currency;amount_eur;fees_eur;tax_eur;note`, semicolon
delimited, dot decimals, one header row with exactly these eleven column names. `type` is one of
the values `core.types.TxnType` stores (`buy`, `sell`, ..., `transfer_in`, `transfer_out`);
`time` may be empty for a day-precision entry, exactly as the CSV export's `Uhrzeit` can be
(6.1). This is the source you write on purpose (5.3.4: it has the highest precedence of any
source), so it goes through the same review queue as everything else: an unrecognised header is
one `unknown_csv_header` item for the whole file, and an unknown `type` value or a number, date
or time that cannot be read is one `unparsed_row` item for that row alone; the rows before and
after it still import.

The shared parse model (5.3.1) has a field for an instrument's name, read from a document, but
none for free text about a transaction; `note` is carried in `name`, exactly where a document's
own instrument name would otherwise go.
"""

import csv
import io
from collections.abc import Sequence
from decimal import Decimal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError, InvalidIsinError, NonExistentLocalTimeError, NumberFormatError
from playground.core.isin import normalise_isin
from playground.core.numbers import parse_en_decimal
from playground.core.types import TxnType
from playground.importer.csv_common import decode_csv_bytes, parse_hh_mm
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.tr.layouts.common import berlin_source_time

PARSER_ID = "manual_csv"
DOC_TYPE = "manual_transaction"
VERSION = 1
DELIMITER = ";"
HEADER: tuple[str, ...] = (
    "date",
    "time",
    "type",
    "isin",
    "quantity",
    "price",
    "currency",
    "amount_eur",
    "fees_eur",
    "tax_eur",
    "note",
)
_PREVIEW_LINES = 6


class _RowProblem(Exception):
    """A single manual-CSV row could not be read; the caller turns this into a review result."""

    def __init__(self, kind: ReviewKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def parse_manual_csv(data: bytes) -> ParseResult:
    """Parse a manual transaction file (5.3.3), as `stage_files` calls it for every upload."""
    text = decode_csv_bytes(data)
    rows = [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITER) if row]
    if not rows or tuple(rows[0]) != HEADER:
        return _result([], [_unknown_header_review(text)])

    transactions: list[ParsedTransaction] = []
    review: list[ReviewNeeded] = []
    for line_number, raw_row in enumerate(rows[1:], start=2):  # the header is line 1
        if len(raw_row) != len(HEADER):
            review.append(
                _row_review(
                    text,
                    ReviewKind.UNPARSED_ROW,
                    f"Line {line_number} has {len(raw_row)} columns, but the header has {len(HEADER)}.",
                    line_number,
                    raw_row,
                )
            )
            continue
        values = dict(zip(HEADER, raw_row, strict=True))
        try:
            transactions.append(_read_row(values, line_number))
        except _RowProblem as problem:
            review.append(_row_review(text, problem.kind, problem.message, line_number, raw_row))
    return _result(transactions, review)


def _result(transactions: list[ParsedTransaction], review: list[ReviewNeeded]) -> ParseResult:
    return ParseResult(
        doc_type=DOC_TYPE, parser_id=PARSER_ID, parser_version=VERSION, transactions=transactions, review=review
    )


def _unknown_header_review(text: str) -> ReviewNeeded:
    lines = text.splitlines()
    return ReviewNeeded(
        kind=ReviewKind.UNKNOWN_CSV_HEADER,
        message=(
            "This file's header does not match the manual transaction format "
            f'("{DELIMITER.join(HEADER)}"). Nothing was imported from it.'
        ),
        extracted_text="\n".join(lines[:_PREVIEW_LINES]),
        fields={"header": lines[0] if lines else ""},
    )


def _row_review(text: str, kind: ReviewKind, message: str, line_number: int, raw_row: Sequence[str]) -> ReviewNeeded:
    return ReviewNeeded(
        kind=kind,
        message=message,
        extracted_text=text,
        fields={"line": str(line_number), "line_text": DELIMITER.join(raw_row)},
    )


def _read_row(values: dict[str, str], line_number: int) -> ParsedTransaction:
    type_text = values["type"].strip()
    try:
        txn_type = TxnType(type_text)
    except ValueError as exc:
        raise _RowProblem(
            ReviewKind.UNPARSED_ROW, f'Line {line_number}: unknown transaction type "{type_text}".'
        ) from exc

    isin = _optional_isin(values["isin"], line_number)

    try:
        day = parse_de_date(values["date"].strip())
    except DateFormatError as exc:
        raise _RowProblem(
            ReviewKind.UNPARSED_ROW, f'Line {line_number}: the date "{values["date"]}" could not be read.'
        ) from exc

    at = None
    time_text = values["time"].strip()
    if time_text:
        try:
            at = parse_hh_mm(time_text)
        except ValueError as exc:
            raise _RowProblem(
                ReviewKind.UNPARSED_ROW, f'Line {line_number}: the time "{time_text}" could not be read.'
            ) from exc
    try:
        source_time = berlin_source_time(day, at)
    except NonExistentLocalTimeError as exc:
        raise _RowProblem(
            ReviewKind.UNPARSED_ROW,
            f"Line {line_number}: this time does not exist in Europe/Berlin "
            "(it falls in the hour skipped when summer time starts).",
        ) from exc

    quantity = _optional_decimal(values["quantity"], line_number, "quantity")
    price = _optional_decimal(values["price"], line_number, "price")
    amount = _optional_decimal(values["amount_eur"], line_number, "amount_eur")
    fees = _optional_decimal(values["fees_eur"], line_number, "fees_eur")
    tax = _optional_decimal(values["tax_eur"], line_number, "tax_eur")
    note = values["note"].strip() or None
    is_split = txn_type is TxnType.SPLIT

    # `currency` here describes quantity and price, e.g. "USD" for a foreign holding; the model's
    # `currency` field always describes `amount`/`amount_eur` instead (3.3), which this format's
    # own `amount_eur` column already says is EUR, so that value is not carried into the model.
    return ParsedTransaction(
        type=txn_type,
        isin=isin,
        name=note,
        time=source_time,
        value_date=None,
        quantity=None if is_split else quantity,
        price=None if is_split else price,
        currency="EUR",
        amount=amount,
        amount_eur=amount,
        fx_rate=None,
        fx_source="none",
        fees_eur=fees,
        tax_eur=tax,
        tax_detail={},
        split_new_quantity=quantity if is_split else None,
        origin=None,
        source_ref=None,
        source_kind=SourceKind.MANUAL_CSV,
        evidence=(line_number, line_number),
    )


def _optional_isin(text: str, line_number: int) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return normalise_isin(cleaned)
    except InvalidIsinError as exc:
        raise _RowProblem(ReviewKind.INVALID_ISIN, f"Line {line_number}: {cleaned!r} is not a valid ISIN.") from exc


def _optional_decimal(text: str, line_number: int, field_name: str) -> Decimal | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return parse_en_decimal(cleaned)
    except NumberFormatError as exc:
        raise _RowProblem(
            ReviewKind.UNPARSED_ROW, f'Line {line_number}: the {field_name} "{text}" could not be read.'
        ) from exc
