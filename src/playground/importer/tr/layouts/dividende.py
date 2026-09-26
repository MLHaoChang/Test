"""The dividend and fund distribution layout (plan 6.2: `tr.dividende.de`).

DIVIDENDE (a share) or AUSSCHÜTTUNG (a fund distribution): the same shape, told apart only by
title. The position is read like a trade's ("POSITION ANZAHL ERTRÄGNIS BETRAG"), but in the
paying currency, which may not be EUR (T10: Apple pays in USD). When it is not EUR, the document
also prints an FX line, "Zwischensumme <rate> EUR/<CCY> <amount> EUR" (`parse_fx_line`, common.py),
whose `amount_eur` is the gross dividend already converted -- the document's own EUR amount always
wins (plan 3.3), so nothing here computes a rate itself. The settlement block (ABRECHNUNG) is
optional, exactly as a savings plan's is (a distribution with nothing withheld may have none);
when present its lines are taxes, never fees, and its GESAMT is what the booking must match.

`amount` and `amount_eur` are always the final EUR amount credited (the settlement total, or the
gross when there is no settlement block) -- never the foreign-currency gross -- so `currency` is
always "EUR", the same convention the trade layouts use for what is actually booked to the EUR
cash account. `quantity` and `price` stay in the position's own currency for reference.
"""

from dataclasses import dataclass
from decimal import Decimal
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
    BookingEntry,
    DocText,
    FxLine,
    LayoutProblem,
    PositionBlock,
    SettlementBlock,
    SettlementEntry,
    berlin_source_time,
    header_value,
    is_trade_republic_document,
    parse_fx_line,
    position_tolerance,
    read_booking_block,
    read_position_block,
    read_settlement_block,
)

TITLES = ("DIVIDENDE", "AUSSCHÜTTUNG")
POSITION_HEADERS = ("POSITION ANZAHL ERTRÄGNIS BETRAG",)
CURRENCIES = ("EUR", "USD", "GBP")
REFERENCE_LABEL = "REFERENZ"
DATE_LABEL = "DATUM"
_DOTTED_DATE = r"\d{2}\.\d{2}\.\d{4}"
_FX_WINDOW = 3


@dataclass(frozen=True)
class DividendeParser:
    parser_id: str = "tr.dividende.de"
    doc_type: str = "dividend_note"
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return (
            is_trade_republic_document(doc) and doc.find(TITLES) is not None and doc.find(POSITION_HEADERS) is not None
        )

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
                ReviewKind.MISSING_FIELD, "This is not a dividend or distribution document.", missing="title"
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
        source_time = berlin_source_time(document_date)

        position = read_position_block(
            doc, GERMAN, POSITION_HEADERS, start=title_index + 1, currencies=CURRENCIES, fields=fields
        )
        if not is_valid_isin(position.isin):
            raise LayoutProblem(
                ReviewKind.INVALID_ISIN,
                f"The ISIN {position.isin} in this document is not valid: its check digit does not match.",
            )

        fx: FxLine | None = None
        gross_eur = position.total
        if position.currency != "EUR":
            fx = self._find_fx_line(doc, position.total_index + 1)
            if fx is None:
                raise LayoutProblem(
                    ReviewKind.MISSING_FIELD,
                    f"The document shows the position in {position.currency} but has no EUR "
                    "conversion line (Zwischensumme).",
                    missing="fx",
                )
            gross_eur = fx.amount_eur
            fields.update(fx_rate=format(fx.rate, "f"), fx_currency=fx.currency)

        settlement = read_settlement_block(doc, GERMAN, start=position.total_index + 1)
        taxes = settlement.tax_eur if settlement is not None else ZERO
        fields["tax_eur"] = format(taxes, "f")
        if settlement is not None:
            fields["total"] = format(settlement.total, "f")

        booking_start = settlement.total_index + 1 if settlement is not None else position.total_index + 1
        booking = read_booking_block(doc, GERMAN, start=booking_start)
        fields.update(booking_amount=format(booking.amount, "f"), value_date=booking.booking_date.isoformat())

        review = [
            self._unknown_line(doc.text, entry, fields) for entry in (settlement.unknown_entries if settlement else ())
        ]
        amount_problem = self._amount_problems(doc.text, position, gross_eur, settlement, booking, fields)
        if amount_problem is not None:
            review.append(amount_problem)

        transaction = ParsedTransaction(
            type=TxnType.DIVIDEND,
            isin=position.isin,
            name=position.name,
            time=source_time,
            value_date=booking.booking_date,
            quantity=position.quantity,
            price=position.price,
            currency="EUR",
            amount=booking.amount,
            amount_eur=booking.amount,
            fx_rate=fx.rate if fx is not None else None,
            fx_source="document" if fx is not None else "none",
            fees_eur=ZERO,
            tax_eur=taxes,
            tax_detail=settlement.tax_detail if settlement is not None else {},
            split_new_quantity=None,
            origin=None,
            source_ref=source_ref,
            source_kind=SourceKind.PDF_DOCUMENT,
            evidence=(position.line_index + 1, booking.line_number),
        )
        return transaction, review

    def _find_fx_line(self, doc: DocText, start: int) -> FxLine | None:
        for index in range(start, min(start + _FX_WINDOW, len(doc.lines))):
            fx = parse_fx_line(doc.lines[index])
            if fx is not None:
                return fx
        return None

    def _unknown_line(self, text: str, entry: SettlementEntry, fields: dict[str, str]) -> ReviewNeeded:
        return ReviewNeeded(
            kind=ReviewKind.UNPARSED_ROW,
            message=(
                f"Line {entry.line_number} of the settlement block (ABRECHNUNG) is not a known cost "
                f'or tax: "{entry.text}". Its amount is not counted as a fee or a tax.'
            ),
            extracted_text=text,
            fields={**fields, "line": str(entry.line_number), "line_text": entry.text},
        )

    def _amount_problems(
        self,
        text: str,
        position: PositionBlock,
        gross_eur: Decimal,
        settlement: SettlementBlock | None,
        booking: BookingEntry,
        fields: dict[str, str],
    ) -> ReviewNeeded | None:
        problems = []
        product = position.quantity * position.price
        if abs(product - position.amount) > position_tolerance(position.quantity, position.price):
            problems.append(
                f"Quantity x price is {product:f} {position.currency}, but the position shows "
                f"{position.amount:f} {position.currency}."
            )
        if abs(position.total - position.amount) > CENT:
            problems.append(
                f"The position total (GESAMT) is {position.total:f} {position.currency}, but the "
                f"position shows {position.amount:f} {position.currency}."
            )
        entries_sum = settlement.entries_sum if settlement is not None else ZERO
        expected_total = gross_eur + entries_sum
        settlement_total = settlement.total if settlement is not None else expected_total
        if settlement is not None and abs(settlement.total - expected_total) > CENT:
            problems.append(
                f"The gross amount and the tax lines give {expected_total:f} EUR, but the "
                f"settlement total (GESAMT) is {settlement.total:f} EUR."
            )
        if abs(booking.amount - settlement_total) > CENT:
            problems.append(
                f"The settlement total is {settlement_total:f} EUR, but the booking shows {booking.amount:f} EUR."
            )
        if not problems:
            return None
        return ReviewNeeded(
            kind=ReviewKind.AMOUNTS_DO_NOT_ADD_UP,
            message="The amounts in this document do not add up. " + " ".join(problems),
            extracted_text=text,
            fields={**fields, "expected_total": format(expected_total, "f")},
        )


DIVIDENDE = DividendeParser()
