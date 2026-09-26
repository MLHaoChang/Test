"""Tests for the document classifier (plan 5.3.2, 7.2: "every text fixture matches exactly one parser").

`classify(text)` runs every registered parser's `detect`. Exactly one match returns that parser.
No match gives an `unknown_layout` review result, more than one an `ambiguous_layout` one; both
carry the full extracted text so you can see what the document was.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from playground.importer.model import ParseResult, ReviewKind, ReviewNeeded
from playground.importer.tr.classify import PARSERS, classify

TEXT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "tr" / "text"


def _expected(text_path: Path) -> dict[str, object]:
    return json.loads(text_path.with_name(f"{text_path.stem}.expected.json").read_text(encoding="utf-8"))


def test_every_fixture_in_a_parser_directory_matches_exactly_that_parser(text_fixture: Path) -> None:
    directory = text_fixture.parent.name
    text = text_fixture.read_text(encoding="utf-8")
    matching = [parser.parser_id for parser in PARSERS if parser.detect(text)]
    if directory == "unclassified":
        assert len(matching) != 1, f"{text_fixture.name} should not match exactly one parser: {matching}"
    else:
        assert matching == [directory]
        outcome = classify(text)
        assert not isinstance(outcome, ReviewNeeded)
        assert outcome.parser_id == directory


def test_unknown_cost_information_is_an_unknown_layout() -> None:
    text = (TEXT_DIR / "unclassified" / "unknown_cost_information.txt").read_text(encoding="utf-8")
    outcome = classify(text)
    assert isinstance(outcome, ReviewNeeded)
    assert outcome.kind is ReviewKind.UNKNOWN_LAYOUT
    assert outcome.extracted_text == text
    assert outcome.fields == {}


def test_two_layouts_mixed_is_ambiguous_and_names_both_parsers() -> None:
    # Plan 9, WP3: two_layouts_mixed mixes the 2019 and 2023 trade layouts, so it is ambiguous.
    text = (TEXT_DIR / "unclassified" / "two_layouts_mixed.txt").read_text(encoding="utf-8")
    outcome = classify(text)
    assert isinstance(outcome, ReviewNeeded)
    assert outcome.kind is ReviewKind.AMBIGUOUS_LAYOUT
    assert outcome.extracted_text == text
    assert outcome.fields == {"parsers": "tr.wertpapierabrechnung.de.2019, tr.wertpapierabrechnung.de.2023"}
    assert "tr.wertpapierabrechnung.de.2019" in outcome.message
    assert "tr.wertpapierabrechnung.de.2023" in outcome.message


def test_negative_fixtures_give_the_review_kind_in_their_golden(text_fixture: Path) -> None:
    if text_fixture.parent.name != "unclassified":
        return
    expected = _expected(text_fixture)
    outcome = classify(text_fixture.read_text(encoding="utf-8"))
    assert isinstance(outcome, ReviewNeeded)
    assert [outcome.kind.value] == [item["kind"] for item in expected["review"]]  # type: ignore[union-attr, index]


def test_empty_text_is_an_unknown_layout() -> None:
    outcome = classify("")
    assert isinstance(outcome, ReviewNeeded)
    assert outcome.kind is ReviewKind.UNKNOWN_LAYOUT
    assert outcome.extracted_text == ""


def test_another_banks_document_with_the_same_title_is_not_claimed() -> None:
    # Without the Trade Republic bank line a document is never claimed, even with a known title.
    text = (TEXT_DIR / "tr.wertpapierabrechnung.de.2023" / "golden_t02_kauf_sap.txt").read_text(encoding="utf-8")
    other_bank = text.replace(
        "TRADE REPUBLIC BANK GMBH BRUNNENSTRASSE 19-21 10119 BERLIN", "BEISPIELBANK AG POSTFACH 1"
    )
    outcome = classify(other_bank)
    assert isinstance(outcome, ReviewNeeded)
    assert outcome.kind is ReviewKind.UNKNOWN_LAYOUT


def test_a_savings_plan_is_not_claimed_by_the_order_layouts() -> None:
    text = (TEXT_DIR / "tr.sparplan.de" / "kurs_iso_valuta.txt").read_text(encoding="utf-8")
    # This savings plan uses the KURS column, like the 2019 order layout; only its title differs.
    assert "POSITION ANZAHL KURS BETRAG" in text
    assert [parser.parser_id for parser in PARSERS if parser.detect(text)] == ["tr.sparplan.de"]


def test_parser_registry_is_well_formed() -> None:
    ids = [parser.parser_id for parser in PARSERS]
    assert len(ids) == len(set(ids)), "parser ids must be unique"
    assert set(ids) >= {
        "tr.wertpapierabrechnung.de.2019",
        "tr.wertpapierabrechnung.de.2023",
        "tr.sparplan.de",
        "tr.settlement.en.2023",
        "tr.dividende.de",
        "tr.steuer.de",
        "tr.zinsen.de",
        "tr.split.de",
        "tr.kontoauszug.de.2023",
        "tr.kontoauszug.de.2024",
        "tr.corporate_action.de",
    }
    for parser in PARSERS:
        assert parser.version >= 1
        assert parser.doc_type
        assert parser.locale in ("de", "en")


@dataclass(frozen=True)
class _AlwaysDetects:
    parser_id: str
    version: int = 1
    doc_type: str = "test"
    locale: str = "de"

    def detect(self, text: str) -> bool:
        return True

    def parse(self, text: str) -> ParseResult:
        return ParseResult(
            doc_type=self.doc_type, parser_id=self.parser_id, parser_version=1, transactions=[], review=[]
        )


def test_classify_takes_the_parser_list_to_use() -> None:
    first, second = _AlwaysDetects("test.first"), _AlwaysDetects("test.second")

    assert classify("anything", parsers=[first]) is first

    ambiguous = classify("anything", parsers=[first, second])
    assert isinstance(ambiguous, ReviewNeeded)
    assert ambiguous.kind is ReviewKind.AMBIGUOUS_LAYOUT
    assert ambiguous.fields == {"parsers": "test.first, test.second"}

    unknown = classify("anything", parsers=[])
    assert isinstance(unknown, ReviewNeeded)
    assert unknown.kind is ReviewKind.UNKNOWN_LAYOUT


@given(st.text())
def test_detect_and_classify_never_raise(text: str) -> None:
    for parser in PARSERS:
        assert parser.detect(text) in (True, False)
    classify(text)
