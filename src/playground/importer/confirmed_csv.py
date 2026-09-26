"""The confirmed-holdings format (plan 5.3.3, 5.3.6): what you say you actually hold.

`isin;quantity;as_of`, semicolon delimited, with one comment line before the header declaring
the decimal separator, `# decimal=,` or `# decimal=.` (a quantity may have a fractional part
left over from a savings plan, so the file must say which convention it uses). Used only by
`pg reconcile --confirmed FILE` and the API's confirmed-holdings upload (5.8) to compare what
you typed against the ledger's own computed holdings; a row here is never staged, matched or
stored as a transaction, so this module does not return the shared parse model of 5.3.1.

Unlike the transaction formats, a bad confirmed-holdings file is refused outright, the error
naming the line: it is read once, immediately before computing a reconciliation diff, there is
no review queue to put a per-row problem in, and a diff computed against a half-read file would
misreport your holdings rather than merely miss one transaction (the same reasoning as the
manual price file of plan 6.5, which is refused the same way).
"""

from collections.abc import Callable
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
    first_line = lines[0] if lines else ""
    if not first_line.strip().startswith(_DECIMAL_DIRECTIVE):
        raise ConfirmedCsvError(
            f'Line 1 must declare the decimal separator, "{_DECIMAL_DIRECTIVE},"'
            f' or "{_DECIMAL_DIRECTIVE}.", found {first_line!r}.'
        )
    decimal_parser = _decimal_parser(lines[0])

    body = [(number, line) for number, line in enumerate(lines, start=1) if number > 1 and line.strip()]
    if not body:
        raise ConfirmedCsvError("The confirmed-holdings file has no header row.")

    header_number, header_line = body[0]
    header = tuple(part.strip() for part in header_line.split(";"))
    if header != HEADER:
        raise ConfirmedCsvError(
            f'Line {header_number}: the header must be "isin;quantity;as_of", found "{header_line}".'
        )

    return [_read_row(number, line, decimal_parser) for number, line in body[1:]]


def _decimal_parser(directive_line: str) -> DecimalParser:
    separator = directive_line.strip().removeprefix(_DECIMAL_DIRECTIVE).strip()
    if separator == ",":
        return parse_de_decimal
    if separator == ".":
        return parse_en_decimal
    raise ConfirmedCsvError(f'Line 1: "{_DECIMAL_DIRECTIVE}" must be "," or ".", found "{separator}".')


def _read_row(line_number: int, line: str, decimal_parser: DecimalParser) -> ConfirmedHolding:
    parts = line.split(";")
    if len(parts) != len(HEADER):
        raise ConfirmedCsvError(f'Line {line_number}: expected "isin;quantity;as_of", found "{line}".')
    isin_text, quantity_text, as_of_text = (part.strip() for part in parts)

    try:
        isin = normalise_isin(isin_text)
    except InvalidIsinError as exc:
        raise ConfirmedCsvError(f"Line {line_number}: {isin_text!r} is not a valid ISIN.") from exc
    try:
        quantity = decimal_parser(quantity_text)
    except NumberFormatError as exc:
        raise ConfirmedCsvError(f'Line {line_number}: the quantity "{quantity_text}" could not be read.') from exc
    try:
        as_of = parse_de_date(as_of_text)
    except DateFormatError as exc:
        raise ConfirmedCsvError(f'Line {line_number}: the date "{as_of_text}" could not be read.') from exc
    return ConfirmedHolding(isin=isin, quantity=quantity, as_of=as_of)
