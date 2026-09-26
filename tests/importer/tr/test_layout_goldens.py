"""Golden tests for the text-to-transaction layer (plan 7.3, AC3).

Every text fixture `tests/fixtures/tr/text/<directory>/<case>.txt` is classified and parsed, and
the outcome is compared with `<case>.expected.json` beside it. The directory is the parser id
the fixture must classify to; `unclassified/` holds documents no single parser may claim.

The expected files were written by hand from the golden portfolio (plan 1.2) and the layout
table (plan 6.2). `pytest --update-goldens` rewrites them from the current output; review any
rewrite in the git diff before committing it.
"""

import json
from pathlib import Path
from typing import Any

from playground.importer.model import ReviewNeeded
from playground.importer.serialise import parse_result_to_dict, review_to_dict
from playground.importer.tr.classify import classify

TEXT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "tr" / "text"


def outcome_to_dict(text: str) -> dict[str, Any]:
    """Classify and parse `text` the way the import pipeline will, as JSON-ready data."""
    outcome = classify(text)
    if isinstance(outcome, ReviewNeeded):
        return {
            "parser_id": None,
            "parser_version": None,
            "doc_type": None,
            "transactions": [],
            "review": [review_to_dict(outcome)],
            "confirmed_holdings": [],
        }
    return parse_result_to_dict(outcome.parse(text))


def expected_path(text_path: Path) -> Path:
    return text_path.with_name(f"{text_path.stem}.expected.json")


def test_layout_golden(text_fixture: Path, update_goldens: bool) -> None:
    actual = outcome_to_dict(text_fixture.read_text(encoding="utf-8"))
    golden = expected_path(text_fixture)
    if update_goldens:
        golden.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert actual == expected


def test_every_text_fixture_has_a_golden_and_every_golden_has_a_text() -> None:
    texts = {path.with_suffix("") for path in TEXT_DIR.glob("*/*.txt")}
    goldens = {path.with_name(path.name.removesuffix(".expected.json")) for path in TEXT_DIR.glob("*/*.expected.json")}
    assert texts, "no text fixtures found"
    assert texts == goldens


def test_fixture_directories_are_parser_ids_or_unclassified() -> None:
    from playground.importer.tr.classify import PARSERS

    known = {parser.parser_id for parser in PARSERS} | {"unclassified"}
    directories = {path.name for path in TEXT_DIR.iterdir() if path.is_dir()}
    assert directories <= known


def test_every_parser_has_at_least_two_cases() -> None:
    # Plan 6.2: at least two cases per parser, the golden portfolio documents plus variants.
    from playground.importer.tr.classify import PARSERS

    for parser in PARSERS:
        cases = sorted((TEXT_DIR / parser.parser_id).glob("*.txt"))
        assert len(cases) >= 2, f"{parser.parser_id} has {len(cases)} text fixtures"


def test_every_case_with_an_order_or_execution_number_has_it_in_source_ref(text_fixture: Path) -> None:
    # Plan 6.2: every case that prints an order, execution or reference number carries it in source_ref.
    text = text_fixture.read_text(encoding="utf-8")
    for transaction in outcome_to_dict(text)["transactions"]:
        assert transaction["source_ref"], f"{text_fixture.name} has no source_ref"
        assert transaction["source_ref"] in text
