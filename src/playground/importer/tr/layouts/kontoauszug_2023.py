"""The account statement layout, 2023 form (plan 6.2, 6.3: `tr.kontoauszug.de.2023`).

"BUCHUNGSTAG / WERTSTELLUNG BUCHUNGSTEXT BETRAG IN EUR"; a two-line entry per row: the
description (plus the ISIN and name for a trade or a distribution) and the amount on one line,
the booking day and the value day on the next ("Ausführung Handel Direktkauf Kauf <ISIN> <name>"
then the value date, plan 6.2). Amounts have no unit of their own; the column heading already
says "IN EUR".

Each row becomes one `source_kind = pdf_statement` candidate (plan 6.3): a date, a type, an ISIN
when the row has one, and an amount -- nothing else. `fees_eur` and `tax_eur` are `None`, not a
known zero, because the source genuinely does not say (plan 5.3.1). The key time (plan 5.3.4)
is BUCHUNGSTAG, the booking day; WERTSTELLUNG is kept only as `value_date`.

A row whose description does not match a known type becomes one `unparsed_row` review item for
that row alone (mirroring the CSV rule, plan 5.3.3); the rows before and after it still import.
Anything after the last two-line row (a signature line, a footer) is not itself a row and simply
ends the scan.
"""

import re
from dataclasses import dataclass
from typing import Literal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError, NumberFormatError
from playground.core.numbers import parse_de_decimal
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.tr.layouts.common import DocText, berlin_source_time, is_trade_republic_document

TITLE = "KONTOAUSZUG"
HEADER = "BUCHUNGSTAG / WERTSTELLUNG BUCHUNGSTEXT BETRAG IN EUR"
_ENTRY_LINE = re.compile(r"(?P<description>.+) (?P<amount>[+-]?\d[\d.,]*)")
_DATE_PAIR = re.compile(r"(?P<buchungstag>\d{2}\.\d{2}\.\d{4}) / (?P<wertstellung>\d{2}\.\d{2}\.\d{4})")
_ISIN_AND_NAME = re.compile(r"(?P<isin>[A-Z]{2}[A-Z0-9]{9}[0-9]) (?P<name>.+)")

# A row with no instrument: the whole description is one of these words.
_NO_ISIN_TYPES: dict[str, tuple[TxnType, str | None]] = {
    "Bareinzahlung": (TxnType.DEPOSIT, None),
    "Barauszahlung": (TxnType.WITHDRAWAL, None),
    "Zinsertrag": (TxnType.INTEREST, None),
}
# A row with an instrument: the description starts with one of these phrases, then "<ISIN> <name>".
_WITH_ISIN_PREFIXES: tuple[tuple[str, TxnType, str | None], ...] = (
    ("Ausführung Handel Direktkauf Kauf ", TxnType.BUY, "order"),
    ("Ausführung Handel Direktkauf Verkauf ", TxnType.SELL, "order"),
    ("Ausführung Sparplan Kauf ", TxnType.BUY, "savings_plan"),
    ("Ausschüttung ", TxnType.DIVIDEND, None),
)


@dataclass(frozen=True)
class Kontoauszug2023Parser:
    parser_id: str = "tr.kontoauszug.de.2023"
    doc_type: str = "account_statement"
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return is_trade_republic_document(doc) and doc.find(TITLE) is not None and doc.find(HEADER) is not None

    def parse(self, text: str) -> ParseResult:
        doc = DocText(text)
        title_index = doc.find(TITLE)
        if title_index is None:
            return self._problem_result(text, "This is not an account statement (KONTOAUSZUG).", "title")
        header_index = doc.find(HEADER, title_index + 1)
        if header_index is None:
            return self._problem_result(
                text,
                "The column header (BUCHUNGSTAG / WERTSTELLUNG BUCHUNGSTEXT BETRAG IN EUR) was not found.",
                "header",
            )

        transactions: list[ParsedTransaction] = []
        review: list[ReviewNeeded] = []
        index = header_index + 1
        while index + 1 < len(doc.lines):
            entry_match = _ENTRY_LINE.fullmatch(doc.lines[index])
            if entry_match is None:
                break
            date_match = _DATE_PAIR.fullmatch(doc.lines[index + 1])
            if date_match is None:
                break
            self._read_row(doc, index, entry_match, date_match, transactions, review, text)
            index += 2

        return ParseResult(
            doc_type=self.doc_type,
            parser_id=self.parser_id,
            parser_version=self.version,
            transactions=transactions,
            review=review,
        )

    def _problem_result(self, text: str, message: str, missing: str) -> ParseResult:
        review = ReviewNeeded(
            kind=ReviewKind.MISSING_FIELD, message=message, extracted_text=text, fields={"missing": missing}
        )
        return ParseResult(
            doc_type=self.doc_type,
            parser_id=self.parser_id,
            parser_version=self.version,
            transactions=[],
            review=[review],
        )

    def _read_row(
        self,
        doc: DocText,
        index: int,
        entry_match: re.Match[str],
        date_match: re.Match[str],
        transactions: list[ParsedTransaction],
        review: list[ReviewNeeded],
        text: str,
    ) -> None:
        description = entry_match.group("description")
        line_number = index + 1
        try:
            amount = parse_de_decimal(entry_match.group("amount"))
            buchungstag = parse_de_date(date_match.group("buchungstag"))
            wertstellung = parse_de_date(date_match.group("wertstellung"))
        except (NumberFormatError, DateFormatError):
            review.append(self._unparsed_row(text, line_number, doc.lines[index], date_match))
            return

        kind = self._recognise(description)
        if kind is None:
            review.append(self._unparsed_row(text, line_number, doc.lines[index], date_match))
            return
        txn_type, origin, isin, name = kind

        transactions.append(
            ParsedTransaction(
                type=txn_type,
                isin=isin,
                name=name,
                time=berlin_source_time(buchungstag),
                value_date=wertstellung,
                quantity=None,
                price=None,
                currency="EUR",
                amount=amount,
                amount_eur=amount,
                fx_rate=None,
                fx_source="none",
                fees_eur=None,
                tax_eur=None,
                tax_detail={},
                split_new_quantity=None,
                origin=origin,
                source_ref=None,
                source_kind=SourceKind.PDF_STATEMENT,
                evidence=(line_number, index + 2),
            )
        )

    def _recognise(self, description: str) -> tuple[TxnType, str | None, str | None, str | None] | None:
        no_isin = _NO_ISIN_TYPES.get(description)
        if no_isin is not None:
            txn_type, origin = no_isin
            return txn_type, origin, None, None
        for prefix, txn_type, origin in _WITH_ISIN_PREFIXES:
            if description.startswith(prefix):
                match = _ISIN_AND_NAME.fullmatch(description.removeprefix(prefix))
                if match is not None:
                    return txn_type, origin, match.group("isin"), match.group("name")
        return None

    def _unparsed_row(self, text: str, line_number: int, line_text: str, date_match: re.Match[str]) -> ReviewNeeded:
        return ReviewNeeded(
            kind=ReviewKind.UNPARSED_ROW,
            message=(
                f'Line {line_number} of the account statement is not a known row type: "{line_text}". '
                "Nothing was imported from it."
            ),
            extracted_text=text,
            fields={
                "line": str(line_number),
                "line_text": line_text,
                "buchungstag": date_match.group("buchungstag"),
                "wertstellung": date_match.group("wertstellung"),
            },
        )


KONTOAUSZUG_2023 = Kontoauszug2023Parser()
