"""The document classifier (plan 5.3.2, layer 2): which parser, if any, reads this text.

`classify(text)` asks every registered parser whether the text is its layout. Exactly one yes
returns that parser; the import pipeline then calls its `parse`. No yes gives an
`unknown_layout` review result, and more than one gives `ambiguous_layout`: nothing is guessed,
and the review result carries the full extracted text so you can see what the document was.

A new layout is a new parser in `layouts/` and one more entry in `PARSERS` (plan 6.2).
"""

from collections.abc import Sequence

from playground.importer.model import ReviewKind, ReviewNeeded
from playground.importer.tr.layouts.common import DocumentParser
from playground.importer.tr.layouts.settlement_en import SETTLEMENT_EN_2023
from playground.importer.tr.layouts.wertpapierabrechnung import (
    SPARPLAN,
    WERTPAPIERABRECHNUNG_2019,
    WERTPAPIERABRECHNUNG_2023,
)

PARSERS: tuple[DocumentParser, ...] = (
    WERTPAPIERABRECHNUNG_2019,
    WERTPAPIERABRECHNUNG_2023,
    SPARPLAN,
    SETTLEMENT_EN_2023,
)


def classify(text: str, parsers: Sequence[DocumentParser] | None = None) -> DocumentParser | ReviewNeeded:
    """Return the one parser whose `detect` accepts `text`, or a review result saying why there is none.

    `parsers` defaults to `PARSERS`; tests pass their own list.
    """
    candidates = PARSERS if parsers is None else parsers
    matching = [parser for parser in candidates if parser.detect(text)]
    if len(matching) == 1:
        return matching[0]
    if not matching:
        return ReviewNeeded(
            kind=ReviewKind.UNKNOWN_LAYOUT,
            message="No parser recognises this document layout. Nothing was imported from it.",
            extracted_text=text,
            fields={},
        )
    names = ", ".join(parser.parser_id for parser in matching)
    return ReviewNeeded(
        kind=ReviewKind.AMBIGUOUS_LAYOUT,
        message=f"More than one parser recognises this document ({names}). Nothing was imported from it.",
        extracted_text=text,
        fields={"parsers": names},
    )
