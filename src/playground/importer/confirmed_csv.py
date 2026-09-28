"""The confirmed-holdings format (plan 5.3.3, 5.3.6): what you say you actually hold.

`isin;quantity;as_of`, semicolon delimited: one header row, then one line per position. A quantity
may have a fractional part left over from a savings plan, written with a decimal comma (`4,5`, as
the Trade Republic app shows it) or a decimal point (`4.5`). An optional first line says which,
`# decimal=,` or `# decimal=.`, and every quantity is then read that way. Without that line a
quantity is read only when it can mean one number: `4,5` and `4.5` can (a thousands group always
has three digits), but `1.234` could be 1234 or 1.234, so it is refused, and the message says to
add the line. Nothing is guessed (QA P0 round 2, R2-D1: the UAT guide and the import page describe
the file without that line, so it cannot be required).

Used only by `pg reconcile --confirmed FILE` and the API's confirmed-holdings upload (5.8) to
compare what you typed against the ledger's own computed holdings; a row here is never staged,
matched or stored as a transaction, so this module does not return the shared parse model of 5.3.1.

Unlike the transaction formats, a bad confirmed-holdings file is refused outright, the error
naming the line: it is read once, immediately before computing a reconciliation diff, there is
no review queue to put a per-row problem in, and a diff computed against a half-read file would
misreport your holdings rather than merely miss one transaction (the same reasoning as the
manual price file of plan 6.5, which is refused the same way).
"""

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from playground.core.dates import parse_de_date
from playground.core.errors import DateFormatError, InvalidIsinError, NumberFormatError, PlaygroundError
from playground.core.isin import normalise_isin
from playground.core.numbers import parse_de_decimal, parse_en_decimal
from playground.importer.csv_common import decode_csv_bytes

HEADER: tuple[str, ...] = ("isin", "quantity", "as_of")
_DECIMAL_DIRECTIVE = "# decimal="
# The optional first line, as written in the docs ("# decimal=,"), or without the spaces, or in capitals.
_DIRECTIVE_LINE = re.compile(r"#\s*decimal\s*=(?P<separator>.*)", re.IGNORECASE)

DecimalParser = Callable[[str], Decimal]


class ConfirmedCsvError(PlaygroundError):
    """A confirmed-holdings file is not in its documented format. The message names the line."""


@dataclass(frozen=True)
class ConfirmedHolding:
    """One row of a confirmed-holdings file: what you say you hold of `isin`, as of `as_of`."""

    isin: str
    quantity: Decimal
    as_of: date


def parse_confirmed_csv(data: bytes) -> list[ConfirmedHolding]:
    """Read a confirmed-holdings file (5.3.3). Raises `ConfirmedCsvError` naming the bad line."""
    try:
        lines = decode_csv_bytes(data).splitlines()
    except UnicodeDecodeError as exc:
        raise ConfirmedCsvError(
            "The file is not text in UTF-8 or Windows-1252, the two character encodings the importer reads."
        ) from exc
    body = [(number, line) for number, line in enumerate(lines, start=1) if line.strip()]
    if not body:
        raise ConfirmedCsvError("The confirmed-holdings file is empty. It needs the header isin;quantity;as_of.")

    decimal_parser: DecimalParser | None = None
    first_number, first_line = body[0]
    if first_line.strip().startswith("#"):
        decimal_parser = _decimal_parser(first_number, first_line)
        body = body[1:]
    if not body:
        raise ConfirmedCsvError("The confirmed-holdings file has no header row.")

    header_number, header_line = body[0]
    header = tuple(part.strip().lower() for part in header_line.split(";"))
    if header != HEADER:
        raise ConfirmedCsvError(
            f'Line {header_number}: the header must be "isin;quantity;as_of", found "{header_line}".'
        )

    return [_read_row(number, line, decimal_parser) for number, line in body[1:]]


def _decimal_parser(line_number: int, directive_line: str) -> DecimalParser:
    match = _DIRECTIVE_LINE.fullmatch(directive_line.strip())
    if match is None:
        raise ConfirmedCsvError(
            f'Line {line_number}: a first line that starts with "#" must be "{_DECIMAL_DIRECTIVE},"'
            f' or "{_DECIMAL_DIRECTIVE}.", found "{directive_line.strip()}".'
        )
    separator = match.group("separator").strip()
    if separator == ",":
        return parse_de_decimal
    if separator == ".":
        return parse_en_decimal
    raise ConfirmedCsvError(f'Line {line_number}: "{_DECIMAL_DIRECTIVE}" must be "," or ".", found "{separator}".')


def _read_quantity(line_number: int, text: str, decimal_parser: DecimalParser | None) -> Decimal:
    """The quantity as the file's first line declares it, or, without that line, its one possible reading."""
    if decimal_parser is not None:
        return decimal_parser(text)
    readings: list[Decimal] = []
    for parse in (parse_de_decimal, parse_en_decimal):
        with suppress(NumberFormatError):
            readings.append(parse(text))
    if not readings:
        raise NumberFormatError(f"Not a number with a decimal comma or a decimal point: {text!r}.")
    if len(readings) == 2 and readings[0] != readings[1]:
        with_comma, with_point = readings
        raise ConfirmedCsvError(
            f'Line {line_number}: the quantity "{text}" can be read as {with_comma:f} or as {with_point:f}. '
            f'Make the first line of the file "{_DECIMAL_DIRECTIVE}," if you write decimals with a comma, '
            f'or "{_DECIMAL_DIRECTIVE}." if you write them with a point.'
        )
    return readings[0]


def _read_row(line_number: int, line: str, decimal_parser: DecimalParser | None) -> ConfirmedHolding:
    parts = line.split(";")
    if len(parts) != len(HEADER):
        raise ConfirmedCsvError(f'Line {line_number}: expected "isin;quantity;as_of", found "{line}".')
    isin_text, quantity_text, as_of_text = (part.strip() for part in parts)

    try:
        isin = normalise_isin(isin_text)
    except InvalidIsinError as exc:
        raise ConfirmedCsvError(f"Line {line_number}: {isin_text!r} is not a valid ISIN.") from exc
    try:
        quantity = _read_quantity(line_number, quantity_text, decimal_parser)
    except NumberFormatError as exc:
        raise ConfirmedCsvError(f'Line {line_number}: the quantity "{quantity_text}" could not be read.') from exc
    try:
        as_of = parse_de_date(as_of_text)
    except DateFormatError as exc:
        raise ConfirmedCsvError(f'Line {line_number}: the date "{as_of_text}" could not be read.') from exc
    return ConfirmedHolding(isin=isin, quantity=quantity, as_of=as_of)
