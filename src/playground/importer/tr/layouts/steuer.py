"""The tax correction and advance lump sum layout (plan 6.2: `tr.steuer.de`).

STEUERKORREKTUR (a correction to a tax already withheld, for example through loss offsetting) or
VORABPAUSCHALE (the advance lump sum German law taxes on an accumulating fund every January, even
without a sale): the same shape. There is no position (no quantity or price), just the instrument
name and its ISIN, then a tax-only settlement block (ABRECHNUNG, common.py) whose GESAMT is the
whole point of the document -- positive when it credits you (a refund), negative when it debits
you. Unlike a dividend note, there is nothing else to check the total against, so the only check
here is that the settlement entries add up to that total, and that the booking matches it.
"""

from dataclasses import dataclass
from typing import Literal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError
from playground.core.isin import is_valid_isin
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.tr.layouts.common import (
    CENT,
    GERMAN,
    ZERO,
    DocText,
    LayoutProblem,
    berlin_source_time,
    header_value,
    is_trade_republic_document,
    read_booking_block,
    read_isin,
    read_settlement_block,
)

TITLES = ("STEUERKORREKTUR", "VORABPAUSCHALE")
REFERENCE_LABEL = "REFERENZ"
DATE_LABEL = "DATUM"
_DOTTED_DATE = r"\d{2}\.\d{2}\.\d{4}"


@dataclass(frozen=True)
class SteuerParser:
    parser_id: str = "tr.steuer.de"
    doc_type: str = "tax_notice"
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return is_trade_republic_document(doc) and doc.find(TITLES) is not None

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
        title_index = doc.find(TITLES)
        if title_index is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "This is not a tax correction or advance lump sum document.", missing="title"
            )

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

        name_index = title_index + 1
        if name_index >= len(doc.lines):
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "The instrument name and ISIN were not found.", missing="isin"
            )
        name = doc.lines[name_index]
        isin_index = name_index + 1
        isin = read_isin(doc.lines[isin_index]) if isin_index < len(doc.lines) else None
        if isin is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "The ISIN line was not found below the instrument name.", missing="isin"
            )
        if not is_valid_isin(isin):
            raise LayoutProblem(
                ReviewKind.INVALID_ISIN,
                f"The ISIN {isin} in this document is not valid: its check digit does not match.",
            )
        fields.update(name=name, isin=isin)

        settlement = read_settlement_block(doc, GERMAN, start=isin_index + 1)
        if settlement is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "The tax settlement block (ABRECHNUNG) was not found.", missing="settlement"
            )
        fields.update(tax_eur=format(settlement.tax_eur, "f"), total=format(settlement.total, "f"))

        booking = read_booking_block(doc, GERMAN, start=settlement.total_index + 1)
        fields.update(booking_amount=format(booking.amount, "f"), value_date=booking.booking_date.isoformat())

        problems = []
        if abs(settlement.total - settlement.entries_sum) > CENT:
            problems.append(
                f"The tax lines add up to {settlement.entries_sum:f} EUR, but the settlement total "
                f"(GESAMT) is {settlement.total:f} EUR."
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
            type=TxnType.TAX,
            isin=isin,
            name=name,
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
            evidence=(name_index + 1, booking.line_number),
        )
        return transaction, review


STEUER = SteuerParser()
