"""Locale-specific decimal number parsing (plan 3.3, 5.1).

Trade Republic documents mix locales: the German layouts use "." to
group thousands and "," as the decimal point; the one English layout
(6.2, `tr.settlement.en.2023`) uses the opposite. Neither function here
guesses the other's format when its own does not match -- the document's
own layout decides which one to call.
"""

import re
from decimal import Decimal

from playground.core.errors import NumberFormatError

# A run of 1 to 3 digits, then zero or more ".ddd" groups of exactly 3 digits (German
# thousands separator), OR any run of digits with no separator at all; then an optional
# ",ddd..." decimal part. An optional leading sign. Anchored, so nothing is left over.
_DE_NUMBER = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})*|\d+)(,\d+)?$")

# The mirror image: "," groups thousands, "." is the decimal point.
_EN_NUMBER = re.compile(r"^[+-]?(\d{1,3}(,\d{3})*|\d+)(\.\d+)?$")


def parse_de_decimal(text: str) -> Decimal:
    """Parse a German-formatted decimal: "." groups thousands, "," is the decimal point.

    Examples: "1.825,60" -> `Decimal("1825.60")`; "-0,890" ->
    `Decimal("-0.890")`; "1.000" -> `Decimal("1000")` (no decimal part).
    Trailing zeros are kept exactly as written (3.3: parsed values are
    kept exactly as written in the document).

    Raises `NumberFormatError` on anything that is not unambiguously a
    German-formatted number, including an English-formatted one such as
    "1,234.5"; this function never falls back to guessing another locale.
    """
    cleaned = text.strip()
    if not _DE_NUMBER.match(cleaned):
        raise NumberFormatError(f"Not a German-formatted number: {text!r}.")
    normalised = cleaned.replace(".", "").replace(",", ".")
    return Decimal(normalised)


def parse_en_decimal(text: str) -> Decimal:
    """Parse an English-formatted decimal: "," groups thousands, "." is the decimal point.

    Example: "1,825.60" -> `Decimal("1825.60")`. Raises
    `NumberFormatError` on anything that is not unambiguously an
    English-formatted number.
    """
    cleaned = text.strip()
    if not _EN_NUMBER.match(cleaned):
        raise NumberFormatError(f"Not an English-formatted number: {text!r}.")
    normalised = cleaned.replace(",", "")
    return Decimal(normalised)
