"""ISIN (International Securities Identification Number) helpers (plan 3.3, 5.1).

An ISIN is the instrument key everywhere in this app. It is 12
characters: a 2-letter country code, a 9-character alphanumeric security
identifier, and one decimal check digit computed with the Luhn
algorithm over the numeric value of every character before it (A=10,
B=11, ..., Z=35).
"""

import re

from playground.core.errors import InvalidIsinError

_ISIN_SHAPE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_ISIN_LABEL = re.compile(r"^ISIN:?\s*", re.IGNORECASE)


def _numeric_value(char: str) -> int:
    """Return the Luhn numeric value of one ISIN character: itself if a digit, else A=10..Z=35."""
    if char.isdigit():
        return int(char)
    return ord(char) - ord("A") + 10


def _check_digit(payload: str) -> str:
    """Compute the Luhn check digit for an 11-character ISIN payload (country code + NSIN)."""
    digits = "".join(str(_numeric_value(char)) for char in payload)
    parity = len(digits) % 2
    total = 0
    for index, digit_char in enumerate(digits):
        digit = int(digit_char)
        if index % 2 != parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - (total % 10)) % 10)


def is_valid_isin(value: str) -> bool:
    """Return True iff `value` is exactly 12 characters, in ISIN shape, with a correct check digit.

    `value` is checked exactly as given: not stripped, not upper-cased.
    Use `normalise_isin` first for a value copied out of a document.
    """
    if not _ISIN_SHAPE.match(value):
        return False
    return _check_digit(value[:11]) == value[11]


def normalise_isin(value: str) -> str:
    """Return `value` as a clean, validated ISIN.

    Strips a leading "ISIN" or "ISIN:" label (as some Trade Republic
    layouts print it) and surrounding whitespace, removes internal
    spaces, and upper-cases the rest. Raises `InvalidIsinError` if the
    result is not a valid ISIN (wrong shape or a wrong check digit); a
    caller in the importer should catch this and raise an `invalid_isin`
    review item (5.3.5) rather than let it propagate.
    """
    cleaned = _ISIN_LABEL.sub("", value.strip())
    cleaned = cleaned.replace(" ", "").upper()
    if not is_valid_isin(cleaned):
        raise InvalidIsinError(f"Not a valid ISIN: {value!r}.")
    return cleaned
