"""The shared line grammar of the Trade Republic layouts (plan 5.3.2).

Every Trade Republic document is a short stack of blocks, each a heading, a column line and a
few entry lines. The pieces here read those blocks from the normalised text of `pdf_text.py`
(one line per `str.splitlines()` entry, single spaces). The layout modules only name their own
words: German ("Stk.", GESAMT, ABRECHNUNG, BUCHUNG) or English ("Pcs.", TOTAL, BILLING,
BOOKING), the title line, the column line of the position, and how the execution line reads.

The pieces:

- `DocText`: the text as numbered lines. Line numbers are 1-based, as in messages and in the
  `evidence` of a transaction.
- The header block: the bank line ("TRADE REPUBLIC BANK GMBH ..."), then header lines that mix
  your address with a label and a value at the end ("Ahornweg 7 DATUM 15.01.2024"). Only the
  label and the value are read (`header_value`); the name, address and depot number before or
  beside them are never copied into a parsed field.
- The ISIN line, with or without "ISIN:" (`read_isin`), and the position line with quantity,
  "Stk." or "Pcs.", price and amount, then the position total (`read_position_block`).
- The settlement block, ABRECHNUNG or BILLING: each cost line (the external cost surcharge, a
  settlement fee, the financial transaction tax) and each tax line by name, then the total
  (`read_settlement_block`). Costs are stored as fees. Taxes are stored as positive numbers
  when withheld and as negative numbers when refunded (the "Optimierung" lines of a sale),
  under the keys kapitalertragssteuer, solidaritaetszuschlag, kirchensteuer and quellensteuer.
  A line with another label is not guessed at: it becomes an `unparsed_row` review item.
- The booking block, BUCHUNG or BOOKING: the booking amount and a date under VALUTA,
  WERTSTELLUNG or BUCHUNGSDATUM (VALUE DATE or BOOKING DATE), as dd.mm.yyyy or yyyy-mm-dd
  (`read_booking_block`). The clearing account (your IBAN) on that line is never read.
- The FX line "Zwischensumme 1,102 EUR/USD 5,11 EUR" (`parse_fx_line`): 1 EUR = 1.102 USD, the
  convention of plan 3.3.

`TradeConfirmationParser` puts the pieces together for the trade confirmation layouts. It never
raises for a layout problem. A piece that cannot read its block raises `LayoutProblem`, and the
parser turns it into a review result carrying the full text and the fields read so far. When a
result carries a transaction and a review item together, the item concerns that transaction,
and the import pipeline holds the transaction back until the item is settled (plan 5.3.2).
"""

import contextlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal, Protocol

from playground.core.dates import SourceTime, berlin_to_utc, parse_de_date
from playground.core.errors import DateFormatError, NonExistentLocalTimeError, NumberFormatError, PlaygroundError
from playground.core.isin import is_valid_isin
from playground.core.money import eur2
from playground.core.numbers import parse_de_decimal, parse_en_decimal
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind

Locale = Literal["de", "en"]

SOURCE_TZ = "Europe/Berlin"
BANK_LINE_PREFIX = "TRADE REPUBLIC BANK GMBH"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# How far apart the blocks of a trade confirmation may be, in lines. Real documents put a
# counterparty line or two between the execution line and the position, and up to three name
# lines (share class, notes) between the position line and the ISIN line.
POSITION_HEADER_WINDOW = 5
ISIN_WINDOW = 4
TOTAL_WINDOW = 3

_NUMBER = r"\d[\d.,]*"
_SIGNED_NUMBER = r"[+-]?\d[\d.,]*"
_DATE = r"\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}"
_DOTTED_DATE = r"\d{2}\.\d{2}\.\d{4}"
_CURRENCY = r"[A-Z]{3}"
_ISIN_LINE = re.compile(r"(?:ISIN: ?)?(?P<isin>[A-Z]{2}[A-Z0-9]{9}[0-9])")
_FX_LINE = re.compile(
    rf"Zwischensumme (?P<rate>{_NUMBER}) EUR/(?P<currency>[A-Z]{{3}}) (?P<amount>{_SIGNED_NUMBER}) EUR"
)


class DocumentParser(Protocol):
    """One parser per document layout (plan 5.3.2). `classify` picks the only one whose `detect` is true."""

    @property
    def parser_id(self) -> str:
        """For example "tr.wertpapierabrechnung.de.2023"; stored with every import."""
        ...

    @property
    def version(self) -> int:
        """Raised when the parser changes what it reads."""
        ...

    @property
    def doc_type(self) -> str:
        """For example "trade_confirmation"."""
        ...

    @property
    def locale(self) -> Locale:
        """The number format of the layout: German or English."""
        ...

    def detect(self, text: str) -> bool:
        """True when `text` is a document of this layout. Never raises."""
        ...

    def parse(self, text: str) -> ParseResult:
        """What the document says, plus a review result for anything that could not be read. Never raises."""
        ...


class LayoutProblem(PlaygroundError):
    """A block of a document could not be read. The parser turns this into a review result.

    `missing` names the field or block that is missing or unreadable, for example "booking" or
    "settlement_total"; it goes into the review item's fields. It is `None` for a problem that
    is not about a missing field, such as an invalid ISIN.
    """

    def __init__(self, kind: ReviewKind, message: str, *, missing: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.missing = missing


class DocText:
    """A normalised document text as a tuple of lines, split on line feeds and page breaks."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines: tuple[str, ...] = tuple(text.splitlines())

    def find(self, wanted: str | Iterable[str], start: int = 0, stop: int | None = None) -> int | None:
        """The index of the first line equal to `wanted` (or to one of them) in `[start, stop)`."""
        targets = {wanted} if isinstance(wanted, str) else set(wanted)
        for index in self._indexes(start, stop):
            if self.lines[index] in targets:
                return index
        return None

    def search(
        self, pattern: re.Pattern[str], start: int = 0, stop: int | None = None
    ) -> tuple[int, re.Match[str]] | None:
        """The first line in `[start, stop)` that `pattern` matches in full, with its match."""
        for index in self._indexes(start, stop):
            match = pattern.fullmatch(self.lines[index])
            if match is not None:
                return index, match
        return None

    def _indexes(self, start: int, stop: int | None) -> range:
        end = len(self.lines) if stop is None else min(stop, len(self.lines))
        return range(max(start, 0), end)


# --- Vocabulary -----------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Vocabulary:
    """The words and number format of one document language, from which the block patterns are built.

    Compared and hashed by identity (`eq=False`): it holds dictionaries and compiled patterns.
    """

    locale: Locale
    unit: str
    total_label: str
    settlement_heading: str
    settlement_columns: str
    booking_heading: str
    booking_columns: str
    booking_date_columns: tuple[str, ...]
    booking_amount_column: str
    execution_label: str
    order_label: str
    date_label: str
    fee_labels: Mapping[str, str]
    tax_labels: Mapping[str, str]
    position_line: re.Pattern[str] = field(init=False, repr=False, compare=False)
    total_line: re.Pattern[str] = field(init=False, repr=False, compare=False)
    settlement_entry: re.Pattern[str] = field(init=False, repr=False, compare=False)
    booking_columns_line: re.Pattern[str] = field(init=False, repr=False, compare=False)
    booking_line: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        unit = re.escape(self.unit)
        dates = "|".join(re.escape(column) for column in self.booking_date_columns)
        patterns = {
            "position_line": (
                rf"(?P<name>.+) (?P<quantity>{_NUMBER}) {unit} (?P<price>{_NUMBER}) (?P<price_currency>{_CURRENCY}) "
                rf"(?P<amount>{_NUMBER}) (?P<currency>{_CURRENCY})"
            ),
            "total_line": rf"{re.escape(self.total_label)} (?P<amount>{_SIGNED_NUMBER}) (?P<currency>{_CURRENCY})",
            "settlement_entry": rf"(?P<label>\D.*?) (?P<amount>{_SIGNED_NUMBER}) EUR",
            "booking_columns_line": (
                rf"{re.escape(self.booking_columns)} (?P<label>{dates}) {re.escape(self.booking_amount_column)}"
            ),
            "booking_line": rf"(?P<account>.+) (?P<date>{_DATE}) (?P<amount>{_SIGNED_NUMBER}) EUR",
        }
        for name, pattern in patterns.items():
            object.__setattr__(self, name, re.compile(pattern))

    def label_key(self, label: str, labels: Mapping[str, str]) -> str | None:
        """The key for a fee or tax label, compared without regard to case."""
        wanted = label.casefold()
        for known, key in labels.items():
            if known.casefold() == wanted:
                return key
        return None


GERMAN = Vocabulary(
    locale="de",
    unit="Stk.",
    total_label="GESAMT",
    settlement_heading="ABRECHNUNG",
    settlement_columns="POSITION BETRAG",
    booking_heading="BUCHUNG",
    booking_columns="VERRECHNUNGSKONTO",
    booking_date_columns=("VALUTA", "WERTSTELLUNG", "BUCHUNGSDATUM"),
    booking_amount_column="BETRAG",
    execution_label="AUSFÜHRUNG",
    order_label="ORDER",
    date_label="DATUM",
    fee_labels={
        "Fremdkostenzuschlag": "fremdkostenzuschlag",
        "Abwicklungspauschale": "abwicklungspauschale",
        # A tax on buying some French and Italian shares: a cost of the purchase, so a fee here,
        # not a tax withheld on income or gains.
        "Finanztransaktionssteuer": "finanztransaktionssteuer",
        "Frz. Finanztransaktionssteuer": "finanztransaktionssteuer",
    },
    tax_labels={
        # Both spellings appear in real documents.
        "Kapitalertragssteuer": "kapitalertragssteuer",
        "Kapitalertragsteuer": "kapitalertragssteuer",
        "Solidaritätszuschlag": "solidaritaetszuschlag",
        "Kirchensteuer": "kirchensteuer",
        "Quellensteuer": "quellensteuer",
        # Tax refunded at a sale through loss offsetting, printed as a positive amount.
        "Kapitalertragssteuer Optimierung": "kapitalertragssteuer",
        "Kapitalertragsteuer Optimierung": "kapitalertragssteuer",
        "Solidaritätszuschlag Optimierung": "solidaritaetszuschlag",
        "Kirchensteuer Optimierung": "kirchensteuer",
    },
)

ENGLISH = Vocabulary(
    locale="en",
    unit="Pcs.",
    total_label="TOTAL",
    settlement_heading="BILLING",
    settlement_columns="POSITION AMOUNT",
    booking_heading="BOOKING",
    booking_columns="CLEARING ACCOUNT",
    booking_date_columns=("VALUE DATE", "BOOKING DATE"),
    booking_amount_column="AMOUNT",
    execution_label="EXECUTION",
    order_label="ORDER",
    date_label="DATE",
    fee_labels={"External cost surcharge": "external_cost_surcharge"},
    tax_labels={
        "Capital Gains Tax": "kapitalertragssteuer",
        "Solidarity Surcharge": "solidaritaetszuschlag",
        "Church Tax": "kirchensteuer",
        "Withholding Tax": "quellensteuer",
    },
)


# --- Small pieces ---------------------------------------------------------------------------


def parse_number(text: str, locale: Locale, line_number: int, *, missing: str) -> Decimal:
    """Read a number in the layout's own format; the locale is never guessed (plan 5.1)."""
    try:
        return parse_de_decimal(text) if locale == "de" else parse_en_decimal(text)
    except NumberFormatError as exc:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD, f'The number "{text}" in line {line_number} could not be read.', missing=missing
        ) from exc


def negated_sum(amounts: Iterable[Decimal]) -> Decimal:
    """Minus the sum of printed amounts: "-1,00 EUR" of fees is a fee of 1.00. Never gives -0."""
    return ZERO - sum(amounts, start=ZERO)


def is_trade_republic_document(doc: DocText) -> bool:
    """True when a line starts with the bank's name, as every Trade Republic document's first line does."""
    return any(line.startswith(BANK_LINE_PREFIX) for line in doc.lines)


def header_value(doc: DocText, label: str, *, stop: int | None = None, value: str = r"\S+") -> str | None:
    """The value after `label` at the end of a header line ("Ahornweg 7 DATUM 15.01.2024" gives "15.01.2024").

    Only the lines before `stop` are searched, and the first match wins. Whatever stands before
    the label on that line (your name or address) is never returned.
    """
    pattern = re.compile(rf"(?:.* )?{re.escape(label)} (?P<value>{value})")
    found = doc.search(pattern, 0, stop)
    return found[1].group("value") if found is not None else None


def read_isin(line: str) -> str | None:
    """The ISIN on an ISIN line ("ISIN: DE0007164600" or "DE0007164600"), check digit not yet tested."""
    match = _ISIN_LINE.fullmatch(line)
    return match.group("isin") if match is not None else None


@dataclass(frozen=True)
class FxLine:
    """The FX line of a document in a foreign currency: `rate` units of `currency` per 1 EUR."""

    rate: Decimal
    currency: str
    amount_eur: Decimal


def parse_fx_line(line: str) -> FxLine | None:
    """Read "Zwischensumme 1,102 EUR/USD 5,11 EUR"; any other line gives `None`."""
    match = _FX_LINE.fullmatch(line)
    if match is None:
        return None
    try:
        rate = parse_de_decimal(match.group("rate"))
        amount = parse_de_decimal(match.group("amount"))
    except NumberFormatError:
        return None
    return FxLine(rate=rate, currency=match.group("currency"), amount_eur=amount)


def berlin_source_time(day: date, at: time | None = None) -> SourceTime:
    """The source time of a document entry (plan 3.3): Europe/Berlin, minute precision, or 00:00 for a day.

    Raises `NonExistentLocalTimeError` for a time in the hour skipped when summer time starts.
    """
    local = datetime.combine(day, at if at is not None else time(0, 0))
    return SourceTime(
        ts_utc=berlin_to_utc(local),
        ts_local=local,
        source_tz=SOURCE_TZ,
        precision="day" if at is None else "minute",
    )


def position_tolerance(quantity: Decimal, price: Decimal) -> Decimal:
    """How far quantity x price may be from the printed position amount.

    0.01 (plan 5.3.2), plus the rounding of the printed figures: half a unit of the last printed
    digit of the price times the quantity, and of a fractional quantity times the price. A
    whole-number quantity is exact. Real documents need this: a savings plan of 5.5520 shares
    at 27.02 EUR is 150.015 EUR and is printed as 150.00 EUR.
    """
    tolerance = CENT + abs(quantity) * _half_unit(price)
    if _exponent(quantity) < 0:
        tolerance += abs(price) * _half_unit(quantity)
    return tolerance


def converted_tolerance(quantity: Decimal, price: Decimal, rate: Decimal) -> Decimal:
    """How far quantity x price / rate, in EUR, may be from a printed EUR amount.

    `rate` is units of the price's currency per 1 EUR (plan 3.3). The rounding of the printed
    quantity and price (`position_tolerance`, less its cent) is converted at `rate`, the rounding
    of the printed rate itself is added (half a unit of its last digit, times the gross over the
    rate squared), and 0.01 for the EUR amount. Real notes need this: 5 x 0.24 USD at 1.0800 is
    1.1111 EUR and is printed as 1.11 EUR.
    """
    gross = abs(quantity * price)
    return CENT + (position_tolerance(quantity, price) - CENT) / rate + gross * _half_unit(rate) / (rate * rate)


def _exponent(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return exponent if isinstance(exponent, int) else 0


def _half_unit(value: Decimal) -> Decimal:
    """Half a unit in the last printed digit: 0.005 for "140.00", 0.00005 for "0.4534"."""
    return Decimal(5).scaleb(_exponent(value) - 1)


# --- Blocks ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionBlock:
    """The position: the line with name, quantity, price and amount, the ISIN line and the position total.

    `currency` is the currency of the price, the amount and the total: EUR for a trade, the
    paying currency for a dividend note.
    """

    name: str
    quantity: Decimal
    price: Decimal
    amount: Decimal
    currency: str
    isin: str
    total: Decimal
    line_index: int
    total_index: int


def read_position_block(
    doc: DocText,
    vocabulary: Vocabulary,
    headers: Iterable[str],
    *,
    start: int,
    stop: int | None = None,
    currencies: Iterable[str] = ("EUR",),
    fields: dict[str, str] | None = None,
) -> PositionBlock:
    """Read the position whose column line (one of `headers`) is the first in `[start, stop)`.

    The price, the amount and the position total must all be in one of `currencies`, the same
    for all three. Fills `fields` with what it has read, so a review result can show it. Raises
    `LayoutProblem` when the position line, the ISIN line or the position total is missing.
    """
    read = fields if fields is not None else {}
    allowed = tuple(currencies)
    header_index = doc.find(headers, start, stop)
    position_index = header_index + 1 if header_index is not None else len(doc.lines)
    match = vocabulary.position_line.fullmatch(doc.lines[position_index]) if position_index < len(doc.lines) else None
    if (
        match is None
        or match.group("currency") not in allowed
        or match.group("price_currency") != match.group("currency")
    ):
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            f"The position line with quantity, price and amount in {' or '.join(allowed)} was not found.",
            missing="position",
        )
    currency = match.group("currency")
    number = position_index + 1
    quantity = parse_number(match.group("quantity"), vocabulary.locale, number, missing="position")
    price = parse_number(match.group("price"), vocabulary.locale, number, missing="position")
    amount = parse_number(match.group("amount"), vocabulary.locale, number, missing="position")
    name = match.group("name")
    read.update(
        name=name, quantity=format(quantity, "f"), price=format(price, "f"), position_amount=format(amount, "f")
    )

    isin_index, isin = _find_isin(doc, vocabulary, position_index + 1)
    if isin is None:
        raise LayoutProblem(ReviewKind.MISSING_FIELD, "The ISIN line was not found below the position.", missing="isin")
    read["isin"] = isin

    found = doc.search(vocabulary.total_line, isin_index + 1, isin_index + 1 + TOTAL_WINDOW)
    heading = doc.find((vocabulary.settlement_heading, vocabulary.booking_heading), isin_index + 1)
    if found is None or (heading is not None and heading < found[0]) or found[1].group("currency") != currency:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            f"The position total ({vocabulary.total_label}) was not found.",
            missing="position_total",
        )
    total_index, total_match = found
    total = parse_number(total_match.group("amount"), vocabulary.locale, total_index + 1, missing="position_total")
    read["position_total"] = format(total, "f")
    return PositionBlock(
        name=name,
        quantity=quantity,
        price=price,
        amount=amount,
        currency=currency,
        isin=isin,
        total=total,
        line_index=position_index,
        total_index=total_index,
    )


def _find_isin(doc: DocText, vocabulary: Vocabulary, start: int) -> tuple[int, str | None]:
    """The ISIN line among the few lines after the position line, before any total or block heading."""
    stops = (vocabulary.settlement_heading, vocabulary.booking_heading)
    for index in range(start, min(start + ISIN_WINDOW, len(doc.lines))):
        line = doc.lines[index]
        isin = read_isin(line)
        if isin is not None:
            return index, isin
        if line in stops or vocabulary.total_line.fullmatch(line):
            break
    return start, None


@dataclass(frozen=True)
class SettlementEntry:
    """One line of the settlement block, its amount signed as printed ("-1,00 EUR" is -1.00)."""

    line_number: int
    text: str
    label: str
    amount: Decimal | None
    category: Literal["fee", "tax", "unknown"]
    key: str | None


@dataclass(frozen=True)
class SettlementBlock:
    """The settlement block (ABRECHNUNG, BILLING): costs and taxes, then the settlement total."""

    entries: tuple[SettlementEntry, ...]
    total: Decimal
    total_index: int

    @property
    def fees_eur(self) -> Decimal:
        """The fees, as a positive number."""
        return negated_sum(
            entry.amount for entry in self.entries if entry.category == "fee" and entry.amount is not None
        )

    @property
    def tax_detail(self) -> dict[str, Decimal]:
        """Each tax by key: positive when withheld, negative when refunded."""
        keys = dict.fromkeys(entry.key for entry in self.entries if entry.category == "tax" and entry.key is not None)
        return {
            key: negated_sum(entry.amount for entry in self.entries if entry.key == key and entry.amount is not None)
            for key in keys
        }

    @property
    def tax_eur(self) -> Decimal:
        """All taxes together: positive when withheld, negative when refunded."""
        return negated_sum(
            entry.amount for entry in self.entries if entry.category == "tax" and entry.amount is not None
        )

    @property
    def entries_sum(self) -> Decimal:
        """The sum of every entry amount as printed, the unknown lines included."""
        return sum((entry.amount for entry in self.entries if entry.amount is not None), start=ZERO)

    @property
    def unknown_entries(self) -> tuple[SettlementEntry, ...]:
        """Lines that are not a known fee or tax."""
        return tuple(entry for entry in self.entries if entry.category == "unknown")


def read_settlement_block(
    doc: DocText, vocabulary: Vocabulary, *, start: int, stop: int | None = None
) -> SettlementBlock | None:
    """Read the settlement block whose heading is the first in `[start, stop)`, or `None` if there is none.

    The entries and the total are EUR amounts, as in every trade confirmation. A line in another
    currency, or any other line that is not "<label> <amount> EUR", is an unknown entry without
    an amount. Raises `LayoutProblem` when the block has no EUR total line before the booking
    block or the end of the text: the document is probably cut off.
    """
    heading_index = doc.find(vocabulary.settlement_heading, start, stop)
    if heading_index is None:
        return None
    index = heading_index + 1
    if index < len(doc.lines) and doc.lines[index] == vocabulary.settlement_columns:
        index += 1

    entries: list[SettlementEntry] = []
    while index < len(doc.lines) and doc.lines[index] != vocabulary.booking_heading:
        line = doc.lines[index]
        total = vocabulary.total_line.fullmatch(line)
        if total is not None and total.group("currency") == "EUR":
            amount = parse_number(total.group("amount"), vocabulary.locale, index + 1, missing="settlement_total")
            return SettlementBlock(entries=tuple(entries), total=amount, total_index=index)
        entries.append(_settlement_entry(vocabulary, line, index + 1))
        index += 1
    raise LayoutProblem(
        ReviewKind.MISSING_FIELD,
        f"The settlement block ({vocabulary.settlement_heading}) has no {vocabulary.total_label} line, "
        "so the total cannot be checked. The document may be cut off.",
        missing="settlement_total",
    )


def _settlement_entry(vocabulary: Vocabulary, line: str, line_number: int) -> SettlementEntry:
    match = vocabulary.settlement_entry.fullmatch(line)
    if match is None:
        return SettlementEntry(line_number, line, line, None, "unknown", None)
    label = match.group("label")
    try:
        amount = parse_number(match.group("amount"), vocabulary.locale, line_number, missing="settlement")
    except LayoutProblem:
        return SettlementEntry(line_number, line, label, None, "unknown", None)
    fee = vocabulary.label_key(label, vocabulary.fee_labels)
    if fee is not None:
        return SettlementEntry(line_number, line, label, amount, "fee", fee)
    tax = vocabulary.label_key(label, vocabulary.tax_labels)
    if tax is not None:
        return SettlementEntry(line_number, line, label, amount, "tax", tax)
    return SettlementEntry(line_number, line, label, amount, "unknown", None)


@dataclass(frozen=True)
class BookingEntry:
    """The booking line: the amount booked on the cash account and the date in its date column."""

    line_number: int
    date_label: str
    booking_date: date
    amount: Decimal


def read_booking_block(doc: DocText, vocabulary: Vocabulary, *, start: int) -> BookingEntry:
    """Read the booking block (heading, column line, booking line) that starts at or after `start`.

    The account on the booking line (your IBAN) is matched but never kept. Raises
    `LayoutProblem` when the block is missing or incomplete.
    """
    problem = LayoutProblem(
        ReviewKind.MISSING_FIELD,
        f"The booking block ({vocabulary.booking_heading}) with the booking date and amount was not found. "
        "The document may be cut off.",
        missing="booking",
    )
    heading_index = doc.find(vocabulary.booking_heading, start)
    if heading_index is None or heading_index + 2 >= len(doc.lines):
        raise problem
    columns = vocabulary.booking_columns_line.fullmatch(doc.lines[heading_index + 1])
    entry = vocabulary.booking_line.fullmatch(doc.lines[heading_index + 2])
    if columns is None or entry is None:
        raise problem
    line_number = heading_index + 3
    try:
        booking_date = parse_de_date(entry.group("date"))
    except DateFormatError as exc:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            f'The booking date "{entry.group("date")}" in line {line_number} is not a real calendar date.',
            missing="booking",
        ) from exc
    amount = parse_number(entry.group("amount"), vocabulary.locale, line_number, missing="booking")
    return BookingEntry(
        line_number=line_number, date_label=columns.group("label"), booking_date=booking_date, amount=amount
    )


# --- Trade confirmations --------------------------------------------------------------------


@dataclass(frozen=True)
class Execution:
    """What the execution line says: buy or sell, the day, and the time of day when it is printed."""

    type: TxnType
    day: date
    at: time | None
    printed: str
    line_index: int


# Reads the execution line in the lines from `start` on; raises `LayoutProblem` if it cannot.
ExecutionReader = Callable[[DocText, int], Execution]


def execution_from_match(
    match: re.Match[str], line_index: int, sides: Mapping[str, TxnType], *, what: str = "trade"
) -> Execution:
    """Build an `Execution` from a match with the groups `date`, and optionally `side`, `time` and `zone`.

    Raises `LayoutProblem` for a date that does not exist, an impossible time or a time zone
    other than Europe/Berlin (plan 3.3: every source time is Europe/Berlin).
    """
    groups = match.groupdict()
    date_text = groups["date"]
    time_text = groups.get("time")
    printed = date_text if time_text is None else f"{date_text} {time_text}"
    zone = groups.get("zone")
    if zone is not None and zone != SOURCE_TZ:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            f"The {what} time is given in the time zone {zone}. Only {SOURCE_TZ} is supported.",
            missing="time_zone",
        )
    try:
        day = parse_de_date(date_text)
    except DateFormatError as exc:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD, f"The {what} date {date_text} is not a real calendar date.", missing="execution"
        ) from exc
    at = None
    if time_text is not None:
        hours, minutes = (int(part) for part in time_text.split(":"))
        if hours > 23 or minutes > 59:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD, f"The {what} time {printed} is not a valid time of day.", missing="execution"
            )
        at = time(hours, minutes)
    # A savings plan execution line names no side: it is always a buy.
    side = groups.get("side")
    txn_type = sides[side] if side is not None else TxnType.BUY
    return Execution(type=txn_type, day=day, at=at, printed=printed, line_index=line_index)


@dataclass(frozen=True)
class TradeConfirmationParser:
    """A trade confirmation layout: title, position columns, execution line and vocabulary (plan 6.2).

    `detect` needs the bank line, the exact title line and one of the position column lines.
    `parse` reads, in this order: the header values (execution or order number, document date),
    the execution line, the position, the settlement block (optional: a savings plan has none),
    and the booking block. Then it checks that the numbers add up (`_amount_problems`).
    """

    parser_id: str
    doc_type: str
    vocabulary: Vocabulary
    title: str
    position_headers: tuple[str, ...]
    read_execution: ExecutionReader
    origin: str
    version: int = 1

    @property
    def locale(self) -> Locale:
        return self.vocabulary.locale

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return (
            is_trade_republic_document(doc)
            and doc.find(self.title) is not None
            and doc.find(self.position_headers) is not None
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
        vocabulary = self.vocabulary
        title_index = doc.find(self.title)
        if title_index is None:
            raise LayoutProblem(ReviewKind.MISSING_FIELD, f"This is not a {self.title} document.", missing="title")

        # The execution number tells two documents of one order apart; the order number is the fallback.
        source_ref = header_value(doc, vocabulary.execution_label, stop=title_index) or header_value(
            doc, vocabulary.order_label, stop=title_index
        )
        if source_ref is not None:
            fields["source_ref"] = source_ref
        document_date = header_value(doc, vocabulary.date_label, stop=title_index, value=_DOTTED_DATE)
        if document_date is not None:
            # The document date is only shown for context; an unreadable one is left out.
            with contextlib.suppress(DateFormatError):
                fields["document_date"] = parse_de_date(document_date).isoformat()

        execution = self.read_execution(doc, title_index + 1)
        fields["type"] = execution.type.value
        local = datetime.combine(execution.day, execution.at) if execution.at is not None else None
        fields["executed_at"] = local.isoformat() if local is not None else execution.day.isoformat()
        try:
            source_time = berlin_source_time(execution.day, execution.at)
        except NonExistentLocalTimeError as exc:
            raise LayoutProblem(
                ReviewKind.MISSING_FIELD,
                f"The trade time {execution.printed} does not exist in {SOURCE_TZ} "
                "(it falls in the hour skipped when summer time starts).",
                missing="execution",
            ) from exc

        position = read_position_block(
            doc,
            vocabulary,
            self.position_headers,
            start=execution.line_index + 1,
            stop=execution.line_index + 1 + POSITION_HEADER_WINDOW,
            fields=fields,
        )
        if not is_valid_isin(position.isin):
            raise LayoutProblem(
                ReviewKind.INVALID_ISIN,
                f"The ISIN {position.isin} in this document is not valid: its check digit does not match.",
            )

        booking_heading = doc.find(vocabulary.booking_heading, position.total_index + 1)
        settlement = read_settlement_block(doc, vocabulary, start=position.total_index + 1, stop=booking_heading)
        fees = settlement.fees_eur if settlement is not None else ZERO
        taxes = settlement.tax_eur if settlement is not None else ZERO
        fields.update(fees_eur=format(fees, "f"), tax_eur=format(taxes, "f"))
        if settlement is not None:
            fields["total"] = format(settlement.total, "f")

        booking_start = settlement.total_index + 1 if settlement is not None else position.total_index + 1
        booking = read_booking_block(doc, vocabulary, start=booking_start)
        fields.update(booking_amount=format(booking.amount, "f"), value_date=booking.booking_date.isoformat())

        review = [
            self._unknown_line(doc.text, entry, fields) for entry in (settlement.unknown_entries if settlement else ())
        ]
        amount_problem = self._amount_problems(doc.text, execution.type, position, settlement, booking, fields)
        if amount_problem is not None:
            review.append(amount_problem)

        transaction = ParsedTransaction(
            type=execution.type,
            isin=position.isin,
            name=position.name,
            time=source_time,
            value_date=booking.booking_date,
            quantity=position.quantity,
            price=position.price,
            currency="EUR",
            amount=booking.amount,
            amount_eur=booking.amount,
            fx_rate=None,
            fx_source="none",
            fees_eur=fees,
            tax_eur=taxes,
            tax_detail=settlement.tax_detail if settlement is not None else {},
            split_new_quantity=None,
            origin=self.origin,
            source_ref=source_ref,
            source_kind=SourceKind.PDF_DOCUMENT,
            evidence=(execution.line_index + 1, booking.line_number),
        )
        return transaction, review

    def _unknown_line(self, text: str, entry: SettlementEntry, fields: Mapping[str, str]) -> ReviewNeeded:
        return ReviewNeeded(
            kind=ReviewKind.UNPARSED_ROW,
            message=(
                f"Line {entry.line_number} of the settlement block ({self.vocabulary.settlement_heading}) "
                f'is not a known cost or tax: "{entry.text}". Its amount is not counted as a fee or a tax.'
            ),
            extracted_text=text,
            fields={**fields, "line": str(entry.line_number), "line_text": entry.text},
        )

    def _amount_problems(
        self,
        text: str,
        txn_type: TxnType,
        position: PositionBlock,
        settlement: SettlementBlock | None,
        booking: BookingEntry,
        fields: Mapping[str, str],
    ) -> ReviewNeeded | None:
        """Check that the numbers add up (plan 5.3.2) and describe every figure that does not.

        For a buy, quantity x price plus costs and taxes is what the booking takes from the
        account; for a sell, quantity x price minus costs and taxes is what it pays in. The
        settlement lines are printed with their sign, so both come to the same sum: minus (buy)
        or plus (sell) the position amount, plus every settlement line.
        """
        total_label = self.vocabulary.total_label
        signed_position = -position.amount if txn_type is TxnType.BUY else position.amount
        expected_total = signed_position + (settlement.entries_sum if settlement is not None else ZERO)
        settlement_total = settlement.total if settlement is not None else expected_total

        problems = []
        product = position.quantity * position.price
        if abs(product - position.amount) > position_tolerance(position.quantity, position.price):
            problems.append(
                f"Quantity x price is {eur2(product):f} EUR, but the position shows {position.amount:f} EUR."
            )
        if abs(position.total - position.amount) > CENT:
            problems.append(
                f"The position total ({total_label}) is {position.total:f} EUR, "
                f"but the position shows {position.amount:f} EUR."
            )
        if settlement is not None and abs(settlement.total - expected_total) > CENT:
            problems.append(
                f"The position amount and the costs and taxes give {expected_total:f} EUR, "
                f"but the settlement total ({total_label}) is {settlement.total:f} EUR."
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
