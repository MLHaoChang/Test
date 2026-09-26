"""The interest statement layout (plan 6.2: `tr.zinsen.de`).

"ABRECHNUNG ZINSEN"; a "ZINSABRECHNUNG ZUM <date>" line for context; an optional gross line,
"GUTSCHRIFT VOR STEUERN <amount> EUR", read directly (never through the shared settlement block,
since it is not itself a cost or tax line); then the tax-only settlement block (ABRECHNUNG,
common.py again) whose own total line reads "GUTSCHRIFT NACH STEUERN" instead of "GESAMT" --
`dataclasses.replace` on the shared German vocabulary gives that one word without touching
common.py. There is no instrument (`isin` and `name` are always `None`) and no quantity or price:
interest is paid on the cash balance, not on a security. When the gross line is present, it is
checked against the net credit after tax; when it is not (no tax was withheld), the net credit is
trusted as printed, exactly as a savings plan trusts its booking when there is no settlement block.
"""

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.tr.layouts.common import (
    CENT,
    GERMAN,
    ZERO,
    DocText,
    LayoutProblem,
    Vocabulary,
    berlin_source_time,
    header_value,
    is_trade_republic_document,
    parse_number,
    read_booking_block,
    read_settlement_block,
)

TITLE = "ABRECHNUNG ZINSEN"
REFERENCE_LABEL = "REFERENZ"
DATE_LABEL = "DATUM"
_DOTTED_DATE = r"\d{2}\.\d{2}\.\d{4}"
_GROSS_LINE = re.compile(r"GUTSCHRIFT VOR STEUERN (?P<amount>[+-]?\d[\d.,]*) EUR")

# Same blocks as every other German layout, but the settlement total is worded as a credit, not
# a bare GESAMT (plan 6.2: "'GUTSCHRIFT NACH STEUERN'").
INTEREST_VOCAB: Vocabulary = replace(GERMAN, total_label="GUTSCHRIFT NACH STEUERN")


@dataclass(frozen=True)
class ZinsenParser:
    parser_id: str = "tr.zinsen.de"
    doc_type: str = "interest_statement"
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return is_trade_republic_document(doc) and doc.find(TITLE) is not None

    def parse(self, text: str) -> ParseResult:
        doc = DocText(text)
        fields: dict[str, str] = {}
        try:
            transaction, review = self._read(doc, fields)
        except LayoutProblem as problem:
            if problem.missing is not None:
                fields["missing"] = problem.missing
            item = ReviewNeeded(kind=problem.kind, message=problem.message, extracted_text=text, fields=fields)
            return self._result([], [item])
        return self._result([transaction], review)

    def _result(self, transactions: list[ParsedTransaction], review: list[ReviewNeeded]) -> ParseResult:
        return ParseResult(
            doc_type=self.doc_type,
            parser_id=self.parser_id,
            parser_version=self.version,
            transactions=transactions,
            review=review,
        )

    def _read(self, doc: DocText, fields: dict[str, str]) -> tuple[ParsedTransaction, list[ReviewNeeded]]:
        title_index = doc.find(TITLE)
        if title_index is None:
            raise LayoutProblem(ReviewKind.MISSING_FIELD, "This is not an interest statement.", missing="title")

        source_ref = header_value(doc, REFERENCE_LABEL, stop=title_index)
        if source_ref is not None:
            fields["source_ref"] = source_ref
        document_date_text = header_value(doc, DATE_LABEL, stop=title_index, value=_DOTTED_DATE)
        if document_date_text is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "The document date (DATUM) was not found.", missing="document_date"
            )
        try:
            document_date = parse_de_date(document_date_text)
        except DateFormatError as exc:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD,
                f"The document date {document_date_text!r} is not a real calendar date.",
                missing="document_date",
            ) from exc
        fields["document_date"] = document_date.isoformat()

        heading_index = doc.find(INTEREST_VOCAB.settlement_heading, title_index + 1)
        gross_stop = heading_index if heading_index is not None else len(doc.lines)
        gross_eur = self._find_gross_line(doc, title_index + 1, gross_stop)
        if gross_eur is not None:
            fields["gross_eur"] = format(gross_eur, "f")

        settlement = read_settlement_block(doc, INTEREST_VOCAB, start=title_index + 1)
        if settlement is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "The tax settlement block (ABRECHNUNG) was not found.", missing="settlement"
            )
        fields.update(tax_eur=format(settlement.tax_eur, "f"), total=format(settlement.total, "f"))

        booking = read_booking_block(doc, INTEREST_VOCAB, start=settlement.total_index + 1)
        fields.update(booking_amount=format(booking.amount, "f"), value_date=booking.booking_date.isoformat())

        problems = []
        if gross_eur is not None:
            expected_total = gross_eur + settlement.entries_sum
            if abs(settlement.total - expected_total) > CENT:
                problems.append(
                    f"The gross interest and the tax lines give {expected_total:f} EUR, but the "
                    f"settlement total (GUTSCHRIFT NACH STEUERN) is {settlement.total:f} EUR."
                )
        if abs(booking.amount - settlement.total) > CENT:
            problems.append(
                f"The settlement total is {settlement.total:f} EUR, but the booking shows {booking.amount:f} EUR."
            )

        review = [
            ReviewNeeded(
                kind=ReviewKind.UNPARSED_ROW,
                message=(
                    f"Line {entry.line_number} of the settlement block (ABRECHNUNG) is not a known cost "
                    f'or tax: "{entry.text}". Its amount is not counted as a fee or a tax.'
                ),
                extracted_text=doc.text,
                fields={**fields, "line": str(entry.line_number), "line_text": entry.text},
            )
            for entry in settlement.unknown_entries
        ]
        if problems:
            review.append(
                ReviewNeeded(
                    kind=ReviewKind.AMOUNTS_DO_NOT_ADD_UP,
                    message="The amounts in this document do not add up. " + " ".join(problems),
                    extracted_text=doc.text,
                    fields=fields,
                )
            )

        transaction = ParsedTransaction(
            type=TxnType.INTEREST,
            isin=None,
            name=None,
            time=berlin_source_time(document_date),
            value_date=booking.booking_date,
            quantity=None,
            price=None,
            currency="EUR",
            amount=booking.amount,
            amount_eur=booking.amount,
            fx_rate=None,
            fx_source="none",
            fees_eur=ZERO,
            tax_eur=settlement.tax_eur,
            tax_detail=settlement.tax_detail,
            split_new_quantity=None,
            origin=None,
            source_ref=source_ref,
            source_kind=SourceKind.PDF_DOCUMENT,
            evidence=(title_index + 2, booking.line_number),
        )
        return transaction, review

    def _find_gross_line(self, doc: DocText, start: int, stop: int) -> Decimal | None:
        for index in range(start, stop):
            match = _GROSS_LINE.fullmatch(doc.lines[index])
            if match is not None:
                return parse_number(match.group("amount"), "de", index + 1, missing="gross")
        return None


ZINSEN = ZinsenParser()
