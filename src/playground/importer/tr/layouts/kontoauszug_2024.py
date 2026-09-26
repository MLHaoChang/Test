"""The account statement layout, 2024 form (plan 6.2, 6.3: `tr.kontoauszug.de.2024`).

"UMSATZÜBERSICHT"; one line per row: the date as a day, a German month name and a year ("01 Apr.
2024", the same form `core.dates.parse_de_date` already reads), a TYP word, a free-text
description (the ISIN and name for a trade or a distribution), then incoming, outgoing and
balance columns, each either "-" or an amount with "€" (plan 6.2: "amounts with '€'"). Only the
non-"-" column of incoming or outgoing becomes the signed amount; the balance column is not read
into any field.

Like the 2023 layout, every row is one `source_kind = pdf_statement` candidate with `fees_eur`
and `tax_eur` left `None` (plan 5.3.1, 6.3) and no separate value date -- this layout prints only
one date per row. The TYP words are the ones Trade Republic's other exports use for the same row
kinds (plan 6.1); a TYP this parser does not know becomes an `unparsed_row` review item for that
row alone, and the rows before and after it still import.
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

TITLE = "UMSATZÜBERSICHT"
HEADER = "DATUM TYP BESCHREIBUNG EINGANG AUSGANG SALDO"
_ROW = re.compile(
    r"(?P<date>\d{1,2} [A-Za-zÄÖÜäöüß]+\.? \d{4}) (?P<typ>\S+) (?P<description>.+) "
    r"(?P<eingang>-|[\d.,]+ €) (?P<ausgang>-|[\d.,]+ €) (?P<saldo>-|[\d.,]+ €)"
)
_ISIN_AND_NAME = re.compile(r"(?P<isin>[A-Z]{2}[A-Z0-9]{9}[0-9]) (?P<name>.+)")

# TYP word -> (transaction type, origin, whether the description carries an ISIN and a name).
_TYPE_WORDS: dict[str, tuple[TxnType, str | None, bool]] = {
    "Einzahlung": (TxnType.DEPOSIT, None, False),
    "Auszahlung": (TxnType.WITHDRAWAL, None, False),
    "Zinsen": (TxnType.INTEREST, None, False),
    "Kauf": (TxnType.BUY, "order", True),
    "Verkauf": (TxnType.SELL, "order", True),
    "Sparplan": (TxnType.BUY, "savings_plan", True),
    "Dividende": (TxnType.DIVIDEND, None, True),
    "Ausschüttung": (TxnType.DIVIDEND, None, True),
}


@dataclass(frozen=True)
class Kontoauszug2024Parser:
    parser_id: str = "tr.kontoauszug.de.2024"
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
            return self._problem_result(text, "This is not an account statement (UMSATZÜBERSICHT).", "title")
        header_index = doc.find(HEADER, title_index + 1)
        if header_index is None:
            return self._problem_result(
                text, "The column header (DATUM TYP BESCHREIBUNG EINGANG AUSGANG SALDO) was not found.", "header"
            )

        transactions: list[ParsedTransaction] = []
        review: list[ReviewNeeded] = []
        for index in range(header_index + 1, len(doc.lines)):
            line = doc.lines[index]
            match = _ROW.fullmatch(line)
            if match is None:
                break  # a footer line, not a row
            self._read_row(index, line, match, transactions, review, text)

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
        index: int,
        line: str,
        match: re.Match[str],
        transactions: list[ParsedTransaction],
        review: list[ReviewNeeded],
        text: str,
    ) -> None:
        line_number = index + 1
        typ = match.group("typ")
        recognised = _TYPE_WORDS.get(typ)
        amount = self._signed_amount(match.group("eingang"), match.group("ausgang"))
        try:
            day = parse_de_date(match.group("date"))
        except DateFormatError:
            recognised = None
            amount = None

        isin = name = None
        if recognised is not None and amount is not None:
            txn_type, origin, has_isin = recognised
            if has_isin:
                isin_match = _ISIN_AND_NAME.fullmatch(match.group("description"))
                if isin_match is None:
                    recognised = None
                else:
                    isin, name = isin_match.group("isin"), isin_match.group("name")

        if recognised is None or amount is None:
            review.append(self._unparsed_row(text, line_number, line, match))
            return
        txn_type, origin, _ = recognised

        try:
            amount_value = parse_de_decimal(amount)
        except NumberFormatError:
            review.append(self._unparsed_row(text, line_number, line, match))
            return

        transactions.append(
            ParsedTransaction(
                type=txn_type,
                isin=isin,
                name=name,
                time=berlin_source_time(day),
                value_date=None,
                quantity=None,
                price=None,
                currency="EUR",
                amount=amount_value,
                amount_eur=amount_value,
                fx_rate=None,
                fx_source="none",
                fees_eur=None,
                tax_eur=None,
                tax_detail={},
                split_new_quantity=None,
                origin=origin,
                source_ref=None,
                source_kind=SourceKind.PDF_STATEMENT,
                evidence=(line_number, line_number),
            )
        )

    def _signed_amount(self, eingang: str, ausgang: str) -> str | None:
        """The one non-"-" column, negated for an outgoing amount; `None` if both or neither are set."""
        incoming = eingang != "-"
        outgoing = ausgang != "-"
        if incoming == outgoing:  # both set, or neither: not a row this parser can read cleanly
            return None
        amount = eingang if incoming else ausgang
        digits = amount.removesuffix(" €")
        return digits if incoming else f"-{digits}"

    def _unparsed_row(self, text: str, line_number: int, line: str, match: re.Match[str]) -> ReviewNeeded:
        return ReviewNeeded(
            kind=ReviewKind.UNPARSED_ROW,
            message=f'Line {line_number} of the account statement is not a known row type: "{line}". Nothing was imported from it.',
            extracted_text=text,
            fields={
                "line": str(line_number),
                "line_text": line,
                "date": match.group("date"),
                "typ": match.group("typ"),
            },
        )


KONTOAUSZUG_2024 = Kontoauszug2024Parser()
