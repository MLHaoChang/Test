"""The Trade Republic CSV export parser (plan 5.3.3, 6.1).

`parse_csv(data)` sniffs the encoding, matches the file's header row against every known profile
(`csv_profiles.py`) and reads each data row into a `ParsedTransaction` of `source_kind =
csv_export`. Nothing here is specific to one profile: the delimiter, the column order, the
row-type vocabulary and the number locale all come from the profile that matched, so a new
export generation is a new profile, not a rewrite of this module (6.1).

A header that matches no known profile puts the whole file into one `unknown_csv_header` review
item, showing the header and the first five data rows so a new profile can be written from what
you see. Given a matching header, an unknown `Typ` value or a row with a number, date or time
that cannot be read becomes one `unparsed_row` review item for that row alone (or `invalid_isin`
for an ISIN whose check digit is wrong); the rows before and after it still import (5.3.3): one
bad row in an otherwise good export must not lose the rest of it.

`Referenz`, the export's own per-row reference, becomes `source_ref` (5.3.2): matching (5.3.4,
built in WP7) uses it only to tell rows of the same file apart, never to match a CSV row against
a PDF document, which is done by the semantic key instead.

**The amount check.** Like every parser (5.3.2), this one checks that a row's numbers add up,
for a purchase, a sale and a dividend: quantity x price (divided by `Wechselkurs` when `Währung`,
the currency of the price, is not EUR), plus the fees and taxes for a purchase, or minus them for
a sale or a dividend, must give the booking amount `Betrag`, within the rounding of the printed
figures (`converted_tolerance`, `position_tolerance`). A row that does not add up is still read,
together with an `amounts_do_not_add_up` item that shows the row under its header and names the
row's transaction (`ReviewNeeded.evidence`), so the pipeline holds that transaction back until
you settle the item. This is also what finds an export whose `Betrag` means something else than
this profile assumes, for example an amount without the fees. A row that lacks a figure the check
needs (the quantity, the price, the amount, or the rate for a price in another currency) is not
checked; the pipeline holds back a purchase or sale without its quantity, and any row that moves
cash without its amount, as a missing field (5.3.4).
"""

import csv
import io
from collections.abc import Sequence
from decimal import Decimal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError, InvalidIsinError, NonExistentLocalTimeError, NumberFormatError
from playground.core.isin import normalise_isin
from playground.core.money import eur2
from playground.core.numbers import parse_de_decimal, parse_en_decimal
from playground.core.types import TxnType
from playground.importer.csv_common import decode_csv_bytes, parse_hh_mm
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.tr.csv_profiles import NO_CASH_TYPES, PROFILES, CsvProfile, Locale
from playground.importer.tr.layouts.common import (
    ZERO,
    berlin_source_time,
    converted_tolerance,
    position_tolerance,
)

PARSER_ID = "tr.csv_export"
DOC_TYPE = "csv_export"
VERSION = 1
_PREVIEW_LINES = 6
# The row types whose amounts can be checked: they carry a quantity and a price (5.3.2).
_CHECKED_TYPES = frozenset({TxnType.BUY, TxnType.SELL, TxnType.DIVIDEND})


class _RowProblem(Exception):
    """A single CSV row could not be read; the caller turns this into a review result."""

    def __init__(self, kind: ReviewKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def parse_csv(data: bytes, profiles: Sequence[CsvProfile] = PROFILES) -> ParseResult:
    """Parse a Trade Republic transaction CSV export, as `stage_files` calls it for every CSV upload.

    `profiles` are the export generations to try, in order; a test passes its own list to add one.
    """
    text = decode_csv_bytes(data)
    for profile in profiles:
        rows = _read_rows(text, profile.delimiter)
        if rows and tuple(rows[0]) == profile.header:
            return _parse_rows(profile, rows[1:], text)
    return _result([], [_unknown_header_review(text)])


def _read_rows(text: str, delimiter: str) -> list[list[str]]:
    """Every non-blank row of `text`, split on `delimiter` (a blank line is not a row at all)."""
    return [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter) if row]


def _unknown_header_review(text: str) -> ReviewNeeded:
    lines = text.splitlines()
    return ReviewNeeded(
        kind=ReviewKind.UNKNOWN_CSV_HEADER,
        message=(
            "This CSV file's header does not match any known Trade Republic export format. "
            "Nothing was imported from it."
        ),
        extracted_text="\n".join(lines[:_PREVIEW_LINES]),
        fields={"header": lines[0] if lines else ""},
    )


def _parse_rows(profile: CsvProfile, rows: list[list[str]], text: str) -> ParseResult:
    transactions: list[ParsedTransaction] = []
    review: list[ReviewNeeded] = []
    width = len(profile.header)
    for line_number, raw_row in enumerate(rows, start=2):  # the header is line 1
        if len(raw_row) != width:
            review.append(
                _row_review(
                    text,
                    ReviewKind.UNPARSED_ROW,
                    f"Line {line_number} has {len(raw_row)} columns, but the header has {width}.",
                    line_number,
                    raw_row,
                    profile.delimiter,
                )
            )
            continue
        values = dict(zip(profile.columns, raw_row, strict=True))
        try:
            transaction, currency = _read_row(profile, values, line_number)
        except _RowProblem as problem:
            review.append(_row_review(text, problem.kind, problem.message, line_number, raw_row, profile.delimiter))
            continue
        transactions.append(transaction)
        mismatch = _amount_problem(transaction, currency, line_number)
        if mismatch is not None:
            message, expected = mismatch
            line_text = profile.delimiter.join(raw_row)
            review.append(
                ReviewNeeded(
                    kind=ReviewKind.AMOUNTS_DO_NOT_ADD_UP,
                    message=message,
                    extracted_text=f"{profile.delimiter.join(profile.header)}\n{line_text}\n",
                    fields={"line": str(line_number), "line_text": line_text, "expected_amount": expected},
                    evidence=transaction.evidence,
                )
            )
    return _result(transactions, review)


def _row_review(
    text: str, kind: ReviewKind, message: str, line_number: int, raw_row: Sequence[str], delimiter: str
) -> ReviewNeeded:
    return ReviewNeeded(
        kind=kind,
        message=message,
        extracted_text=text,
        fields={"line": str(line_number), "line_text": delimiter.join(raw_row)},
    )


def _result(transactions: list[ParsedTransaction], review: list[ReviewNeeded]) -> ParseResult:
    return ParseResult(
        doc_type=DOC_TYPE, parser_id=PARSER_ID, parser_version=VERSION, transactions=transactions, review=review
    )


def _read_row(profile: CsvProfile, values: dict[str, str], line_number: int) -> tuple[ParsedTransaction, str]:
    """The transaction of one row, and the currency of its price (`Währung`, EUR when empty)."""
    type_text = values["type"].strip()
    row_type = profile.row_types.get(type_text)
    if row_type is None:
        raise _RowProblem(ReviewKind.UNPARSED_ROW, f'Line {line_number}: unknown transaction type "{type_text}".')

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

    quantity = _decimal(values["quantity"], profile.locale, line_number, "Anzahl")
    price = _decimal(values["price"], profile.locale, line_number, "Kurs")
    amount = _decimal(values["amount"], profile.locale, line_number, "Betrag")
    fees = _decimal(values["fees"], profile.locale, line_number, "Gebühren")
    taxes = _decimal(values["taxes"], profile.locale, line_number, "Steuern")
    fx_rate = _decimal(values["fx_rate"], profile.locale, line_number, "Wechselkurs")
    currency = values["currency"].strip() or "EUR"
    name = values["name"].strip() or None
    reference = values["reference"].strip() or None

    is_split = row_type.type is TxnType.SPLIT
    no_cash = row_type.type in NO_CASH_TYPES
    has_fx = fx_rate is not None and currency != "EUR"

    transaction = ParsedTransaction(
        type=row_type.type,
        isin=isin,
        name=name,
        time=source_time,
        value_date=None,
        quantity=None if is_split else quantity,
        price=None if is_split else price,
        currency="EUR",
        amount=None if no_cash else amount,
        amount_eur=None if no_cash else amount,
        fx_rate=fx_rate if has_fx else None,
        fx_source="document" if has_fx else "none",
        fees_eur=ZERO if fees is None else fees,
        tax_eur=ZERO if taxes is None else taxes,
        tax_detail={},
        split_new_quantity=quantity if is_split else None,
        origin=row_type.origin,
        source_ref=reference,
        source_kind=SourceKind.CSV_EXPORT,
        evidence=(line_number, line_number),
    )
    return transaction, currency


def _amount_problem(txn: ParsedTransaction, currency: str, line_number: int) -> tuple[str, str] | None:
    """Why the amounts of a row do not add up, and the booking amount they give; `None` when they do.

    A purchase books minus quantity x price plus fees plus taxes; a sale or a dividend books
    quantity x price minus fees minus taxes (a tax refund is a negative tax). A price in another
    currency is converted at the row's rate first. A row that lacks a figure the check needs is
    not checked (see the module docstring).
    """
    quantity, price, amount = txn.quantity, txn.price, txn.amount_eur
    if txn.type not in _CHECKED_TYPES or quantity is None or quantity <= 0 or price is None or amount is None:
        return None
    rate = txn.fx_rate
    if currency != "EUR" and (rate is None or rate <= 0):
        return None
    gross = quantity * price
    if rate is not None:
        tolerance = converted_tolerance(quantity, price, rate)
        gross_eur = gross / rate
    else:
        tolerance = position_tolerance(quantity, price)
        gross_eur = gross
    fees = txn.fees_eur if txn.fees_eur is not None else ZERO
    taxes = txn.tax_eur if txn.tax_eur is not None else ZERO
    buying = txn.type is TxnType.BUY
    expected = -(gross_eur + fees + taxes) if buying else gross_eur - fees - taxes
    if abs(amount - expected) <= tolerance:
        return None

    what = f"{quantity:f} x {price:f} {currency}"
    if rate is not None:
        what += f" at {rate:f} {currency} per EUR"
    if fees:
        what += " " + _cost_term(fees, "fees", adds=buying)
    if taxes:
        what += " " + _cost_term(taxes, "taxes" if taxes > 0 else "tax refund", adds=buying)
    shown = eur2(expected)
    message = (
        f"Line {line_number}: the amounts do not add up. {what} gives a booking amount of {shown:f} EUR, "
        f"but the Betrag column says {amount:f} EUR. The transaction is held back until you check it."
    )
    return message, f"{shown:f}"


def _cost_term(value: Decimal, label: str, *, adds: bool) -> str:
    """ "plus 1.00 EUR fees" for a purchase's costs, "minus 1.00 EUR fees" for a sale's."""
    return f"{'plus' if (value > 0) == adds else 'minus'} {abs(value):f} EUR {label}"


def _optional_isin(text: str, line_number: int) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return normalise_isin(cleaned)
    except InvalidIsinError as exc:
        raise _RowProblem(ReviewKind.INVALID_ISIN, f"Line {line_number}: {cleaned!r} is not a valid ISIN.") from exc


def _decimal(text: str, locale: Locale, line_number: int, column: str) -> Decimal | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return parse_de_decimal(cleaned) if locale == "de" else parse_en_decimal(cleaned)
    except NumberFormatError as exc:
        raise _RowProblem(
            ReviewKind.UNPARSED_ROW, f'Line {line_number}: the {column} value "{text}" could not be read.'
        ) from exc
