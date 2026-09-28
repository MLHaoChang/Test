"""Counts and names in plain English: "1 lot", "2 lots", "DE0007164600 SAP SE".

Every command and message that counts something uses these, so no output says "1 lots" or
"1 new items need your review". Every line that names an instrument uses `isin_and_name` or
`name_and_isin`, so an instrument no document has named yet is shown by its ISIN once.
"""

from decimal import Decimal, InvalidOperation


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """ "1 lot", "0 lots", "2 lots". `plural_form` is for a noun that does not just add an "s"."""
    word = singular if count == 1 else (plural_form if plural_form is not None else f"{singular}s")
    return f"{count} {word}"


def shares(quantity: str | Decimal) -> str:
    """ "1 share", "10 shares", "2.5 shares": the quantity as written, singular only for exactly one."""
    try:
        one = Decimal(quantity) == 1
    except InvalidOperation:
        one = False
    return f"{quantity} {'share' if one else 'shares'}"


def isin_and_name(isin: str, name: str | None) -> str:
    """ "DE0007164600 SAP SE" at the start of a line, or the ISIN alone when there is no name.

    An instrument known only from a manual CSV row or a mapping file is stored with its ISIN as its
    name until a document names it, so a name equal to the ISIN counts as no name
    (QA P0 round 2, minor 7: it printed "DE0005557508 DE0005557508").
    """
    return f"{isin} {name}" if name and name != isin else isin


def name_and_isin(isin: str, name: str | None) -> str:
    """ "SAP SE (DE0007164600)" inside a sentence, or the ISIN alone when there is no name."""
    return f"{name} ({isin})" if name and name != isin else isin
