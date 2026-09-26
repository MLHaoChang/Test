"""Corporate actions P0 recognises but does not convert (plan 5.3.5, 6.2).

An exchange (UMTAUSCH) or a subscription rights notice (BEZUG), and any other document that
carries one of `TITLES`, is a real Trade Republic document this app can recognise -- unlike a
truly unknown layout (`unknown_layout`), which nothing about the text explains. Recognising it
is still not converting it: P0 raises a `corporate_action` review item with the full text and no
transaction, so nothing is guessed and the file is never mistaken for one no parser understands
(plan 1.5: "Corporate actions other than splits ... go to the review queue").

`CorporateActionRecognizer` never raises: called on text that does not carry one of its titles
(the personal-data and "never raise" tests in `test_layout_units.py` call every parser on every
fixture), it gives a plain `missing_field` result instead, exactly like a real layout parser
handed a foreign document.
"""

from dataclasses import dataclass
from typing import Literal

from playground.importer.model import ParseResult, ReviewKind, ReviewNeeded
from playground.importer.tr.layouts.common import DocText, is_trade_republic_document


@dataclass(frozen=True)
class CorporateActionRecognizer:
    """Detects one of `titles` and raises a `corporate_action` review item; never a transaction."""

    parser_id: str
    doc_type: str
    titles: tuple[str, ...]
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        doc = DocText(text)
        return is_trade_republic_document(doc) and doc.find(self.titles) is not None

    def parse(self, text: str) -> ParseResult:
        doc = DocText(text)
        title_index = doc.find(self.titles)
        if title_index is None:
            review = ReviewNeeded(
                kind=ReviewKind.MISSING_FIELD,
                message="This is not a corporate action notice.",
                extracted_text=text,
                fields={"missing": "title"},
            )
        else:
            review = ReviewNeeded(
                kind=ReviewKind.CORPORATE_ACTION,
                message=(
                    "This document describes a corporate action (an exchange or a subscription) "
                    "that P0 does not turn into a transaction. Review it by hand; nothing was "
                    "imported from it."
                ),
                extracted_text=text,
                fields={"title": doc.lines[title_index]},
            )
        return ParseResult(
            doc_type=self.doc_type,
            parser_id=self.parser_id,
            parser_version=self.version,
            transactions=[],
            review=[review],
        )


CORPORATE_ACTION = CorporateActionRecognizer(
    parser_id="tr.corporate_action.de",
    doc_type="corporate_action_notice",
    titles=("UMTAUSCH", "BEZUG"),
)
