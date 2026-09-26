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
    # An account statement (source_kind pdf_statement, WP4) lists many transactions per file and
    # carries no per-line reference number; its report key is ordinal-based instead (plan 5.3.4).
    text = text_fixture.read_text(encoding="utf-8")
    for transaction in outcome_to_dict(text)["transactions"]:
        if transaction["source_kind"] == "pdf_statement":
            assert transaction["source_ref"] is None, f"{text_fixture.name}: a statement line has a source_ref"
            continue
        assert transaction["source_ref"], f"{text_fixture.name} has no source_ref"
        assert transaction["source_ref"] in text


# --- WP4: the golden H1 account statement (plan 1.2, 6.2, 6.3) ----------------------------


def test_golden_h1_statement_gives_eleven_statement_candidates_dated_as_in_the_golden_portfolio() -> None:
    # Plan 1.2: "Each line of the H1 statement carries the same date as the matching PDF or CSV
    # entry ... so every line matches exactly." T11 (the split) and T13/T14 (after 30 June, or
    # never reported by a statement) are not in this file; only T1 to T10 and T12 are.
    text = (TEXT_DIR / "tr.kontoauszug.de.2024" / "golden_h1_statement.txt").read_text(encoding="utf-8")
    transactions = outcome_to_dict(text)["transactions"]
    assert len(transactions) == 11
    assert all(txn["source_kind"] == "pdf_statement" for txn in transactions)
    assert all(txn["source_ref"] is None for txn in transactions)
    assert [txn["ts_local"][:10] for txn in transactions] == [
        "2024-01-02",
        "2024-01-15",
        "2024-02-01",
        "2024-03-01",
        "2024-03-12",
        "2024-03-20",
        "2024-04-02",
        "2024-04-10",
        "2024-05-15",
        "2024-05-16",
        "2024-06-12",
    ]
    assert [txn["type"] for txn in transactions] == [
        "deposit",
        "buy",
        "buy",
        "buy",
        "buy",
        "buy",
        "deposit",
        "buy",
        "dividend",
        "dividend",
        "sell",
    ]
    assert [txn["amount_eur"] for txn in transactions] == [
        "5000.00",
        "-1401.00",
        "-200.00",
        "-210.00",
        "-801.00",
        "-1701.00",
        "1000.00",
        "-801.00",
        "24.30",
        "0.94",
        "1999.41",
    ]
