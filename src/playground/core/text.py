"""Counts in plain English: a number and the noun that agrees with it ("1 lot", "2 lots").

Every command and message that counts something uses these, so no output says "1 lots" or
"1 new items need your review".
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
