"""The split notice layout (plan 6.2: `tr.split.de`).

"NR. BUCHUNG WERTPAPIER BETRAG" table; "Einbuchung ... n Stk."; the document shows only the new
total (T11: NVDA books in "20 Stk.", the total held after the 10-for-1 split, not the 18 new
shares). `split_new_quantity` carries that total; the ledger (WP6) divides it by the quantity
held just before the split to get the ratio, so this layout never needs to say the old quantity.

No amount is booked for a split, so `amount` and `amount_eur` are always `None` here -- not a
known zero, but "no cash moved" (plan 1.2: "Booking amount (EUR): none"). The document does print
a BETRAG column (0,00 EUR for a split), but that column is only for the table's shape and is not
read into a field.
"""

import re
from dataclasses import dataclass
from typing import Literal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError
from playground.core.isin import is_valid_isin
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.tr.layouts.common import (
    ZERO,
    DocText,
    LayoutProblem,
    berlin_source_time,
    header_value,
    is_trade_republic_document,
    parse_number,
    read_isin,
)

TITLE = "SPLIT"
TABLE_HEADER = "NR. BUCHUNG WERTPAPIER BETRAG"
DATE_LABEL = "DATUM"
REFERENCE_LABEL = "REFERENZ"
_DOTTED_DATE = r"\d{2}\.\d{2}\.\d{4}"
_ENTRY = re.compile(r"\d+ Einbuchung (?P<name>.+) (?P<quantity>\d[\d.,]*) Stk\. (?P<amount>[+-]?\d[\d.,]*) EUR")


@dataclass(frozen=True)
class SplitParser:
    parser_id: str = "tr.split.de"
    doc_type: str = "split_notice"
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return is_trade_republic_document(doc) and doc.find(TITLE) is not None and doc.find(TABLE_HEADER) is not None

    def parse(self, text: str) -> ParseResult:
        doc = DocText(text)
        fields: dict[str, str] = {}
        try:
            transaction = self._read(doc, fields)
        except LayoutProblem as problem:
            if problem.missing is not None:
                fields["missing"] = problem.missing
            review = ReviewNeeded(kind=problem.kind, message=problem.message, extracted_text=text, fields=fields)
            return self._result([], [review])
        return self._result([transaction], [])

    def _result(self, transactions: list[ParsedTransaction], review: list[ReviewNeeded]) -> ParseResult:
        return ParseResult(
            doc_type=self.doc_type,
            parser_id=self.parser_id,
            parser_version=self.version,
            transactions=transactions,
            review=review,
        )

    def _read(self, doc: DocText, fields: dict[str, str]) -> ParsedTransaction:
        title_index = doc.find(TITLE)
        if title_index is None:
            raise LayoutProblem(ReviewKind.MISSING_FIELD, "This is not a SPLIT document.", missing="title")

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

        header_index = doc.find(TABLE_HEADER, title_index + 1)
        entry_index = header_index + 1 if header_index is not None else len(doc.lines)
        match = _ENTRY.fullmatch(doc.lines[entry_index]) if entry_index < len(doc.lines) else None
        if match is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD,
                "The split entry line (Einbuchung ... Stk.) was not found.",
                missing="split_entry",
            )
        name = match.group("name")
        quantity = parse_number(match.group("quantity"), "de", entry_index + 1, missing="split_entry")
        fields.update(name=name, split_new_quantity=format(quantity, "f"))

        isin_index = entry_index + 1
        isin = read_isin(doc.lines[isin_index]) if isin_index < len(doc.lines) else None
        if isin is None:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, "The ISIN line was not found below the split entry.", missing="isin"
            )
        if not is_valid_isin(isin):
            raise LayoutProblem(
                ReviewKind.INVALID_ISIN,
                f"The ISIN {isin} in this document is not valid: its check digit does not match.",
            )
        fields["isin"] = isin

        return ParsedTransaction(
            type=TxnType.SPLIT,
            isin=isin,
            name=name,
            time=berlin_source_time(document_date),
            value_date=None,
            quantity=None,
            price=None,
            currency="EUR",
            amount=None,
            amount_eur=None,
            fx_rate=None,
            fx_source="none",
            fees_eur=ZERO,
            tax_eur=ZERO,
            tax_detail={},
            split_new_quantity=quantity,
            origin=None,
            source_ref=source_ref,
            source_kind=SourceKind.PDF_DOCUMENT,
            evidence=(entry_index + 1, isin_index + 1),
        )


SPLIT = SplitParser()
