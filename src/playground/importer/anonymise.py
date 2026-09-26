"""Turning a document's extracted text into something safe to share (plan 5.3.5).

A Trade Republic document's text carries a few things that name you: your name and address at
the top, an IBAN-shaped account number, your depot (securities account) number, and your order
number. `anonymise_text` masks exactly those and leaves everything else -- the document's own
wording, the instrument, the amounts, the dates, and reference numbers that only ever name a
transaction (AUSFÜHRUNG, REFERENZ, SPARPLAN, EXECUTION and their kin) -- as it is, so the result
still reads like the original and can become a fixture.

`pg review export ID --anonymise` runs this over a review item's extracted text (`cli/review.py`),
so a new, unrecognised layout can be turned into a fixture and shared with us without ever sending
the file itself. The UAT guide (section 8) has you check the written file by eye before sharing it;
this function is a first pass, not a guarantee.
"""

import re
from re import Match

NAME_MASK = "[name]"
ADDRESS_MASK = "[address]"
IBAN_MASK = "[iban]"
DEPOT_MASK = "[depot]"
ORDER_MASK = "[order]"

# The customer's name, at the start of the header's second line: "Jana Beispiel SEITE 1 von 1",
# "Lena Fiktiv PAGE 1 from 1" (the English settlement layout, 6.2). The label and the page numbers
# after it are left alone; only the name is masked.
_NAME_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<label>(?:SEITE|PAGE)\s+\d+\s+(?:von|from)\s+\d+)", re.MULTILINE)

# The street address, at the start of the header's third line, before the document date:
# "Ahornweg 7 DATUM 31.03.2023", "Lindenring 4 DATE 05.02.2024".
_STREET_LINE = re.compile(r"^(?P<street>.+?)\s+(?P<label>(?:DATUM|DATE)\s+\d{2}\.\d{2}\.\d{4})", re.MULTILINE)

# Labels that can follow the postal code and city on the header's fourth line. Only DEPOT and
# SECURITIES ACCOUNT (an account/depot number) and ORDER (an order number) are masked themselves,
# by _DEPOT_NUMBER and _ORDER_NUMBER below; the others (a reference, execution or savings-plan
# number) only mark where the address ends, so the line splits correctly either way.
_ADDRESS_FOLLOWERS = r"ORDER|DEPOT|REFERENZ|AUSFÜHRUNG|SPARPLAN|EXECUTION|SECURITIES ACCOUNT"

# The postal code and city, at the start of the header's fourth line: "12345 Musterstadt DEPOT
# 0000001111", "54321 Beispielheim ORDER 3f8a-0c21". A single-word city is assumed, which is all
# every fixture and every layout in section 6.2 prints; a real multi-word city would keep its
# second word unmasked; the UAT guide (8.4) has you check the exported text by eye regardless.
_CITY_LINE = re.compile(rf"^(?P<postal>\d{{4,5}})\s+(?P<city>\S+)(?=\s+(?:{_ADDRESS_FOLLOWERS})\b|\s*$)", re.MULTILINE)

# An IBAN: two letters, two check digits, then 11 to 30 letters or digits, written either in one
# piece or in groups of four separated by single spaces (matches the shape `test_repo_hygiene.py`
# treats as an IBAN). An ISIN (12 characters, for example "DE0007164600") is always shorter than
# this and is never masked.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[A-Z0-9]{11,30}|(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?)\b")

# The depot (securities account) number after its label: "DEPOT 0000001111", "SECURITIES ACCOUNT
# 0000003333". Only the number is masked; the label stays, so the text still reads like the
# original.
_DEPOT_NUMBER = re.compile(r"\b(?P<label>DEPOT|SECURITIES ACCOUNT)\s+(?P<number>\S+)")

# The order number after its label: "ORDER 3f8a-0c21". AUSFÜHRUNG (execution), REFERENZ and
# SPARPLAN numbers are left alone: they only ever name a transaction, never you.
_ORDER_NUMBER = re.compile(r"\bORDER\s+(?P<number>\S+)")


def anonymise_text(text: str) -> str:
    """Mask what identifies you in `text`: your name, address, IBAN, depot and order numbers.

    Everything else is left exactly as it is. Safe to call on text that has nothing to mask (it
    is then returned unchanged) and on text that is not a Trade Republic document at all: every
    pattern here only ever replaces what it matches, and matches nothing else.
    """

    def name(match: Match[str]) -> str:
        return f"{NAME_MASK} {match.group('label')}"

    def street(match: Match[str]) -> str:
        return f"{ADDRESS_MASK} {match.group('label')}"

    def depot(match: Match[str]) -> str:
        return f"{match.group('label')} {DEPOT_MASK}"

    def order(_match: Match[str]) -> str:
        return f"ORDER {ORDER_MASK}"

    masked = _NAME_LINE.sub(name, text)
    masked = _STREET_LINE.sub(street, masked)
    masked = _CITY_LINE.sub(ADDRESS_MASK, masked)
    masked = _IBAN.sub(IBAN_MASK, masked)
    masked = _DEPOT_NUMBER.sub(depot, masked)
    masked = _ORDER_NUMBER.sub(order, masked)
    return masked
