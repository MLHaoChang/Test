"""Golden tests for the Trade Republic CSV export parser (plan 6.1, 7.3, AC3).

Every fixture under `tests/fixtures/tr/csv/**/*.csv` is parsed with `parse_csv` and the outcome
is compared with `<case>.expected.json` beside it, exactly as `test_layout_goldens.py` does for
the PDF layouts. `unknown_header.csv` sits directly under `tr/csv/`; every other fixture sits
under `tr_csv_synthetic_v1/`, the one profile P0 ships (6.1).

The expected files were written by hand from plan 1.2 and 6.1, then checked against the code.
`pytest --update-goldens` rewrites them from the current output; review any rewrite in the git
diff before committing it.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from playground.importer.serialise import parse_result_to_dict
from playground.importer.tr.csv_parser import parse_csv
from playground.importer.tr.csv_profiles import PROFILES

CSV_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tr" / "csv"
GOLDEN_INPUTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "inputs"


def outcome_to_dict(data: bytes) -> dict[str, Any]:
    return parse_result_to_dict(parse_csv(data))


def expected_path(csv_path: Path) -> Path:
    return csv_path.with_name(f"{csv_path.stem}.expected.json")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise `csv_fixture` over every `.csv` fixture under `CSV_DIR`, one test node each."""
    if "csv_fixture" in metafunc.fixturenames:
        paths = sorted(CSV_DIR.glob("**/*.csv"))
        ids = [path.relative_to(CSV_DIR).as_posix() for path in paths]
        metafunc.parametrize("csv_fixture", paths, ids=ids)


def test_csv_golden(csv_fixture: Path, update_goldens: bool) -> None:
    actual = outcome_to_dict(csv_fixture.read_bytes())
    golden = expected_path(csv_fixture)
    if update_goldens:
        golden.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert actual == expected


def test_every_csv_fixture_has_a_golden_and_every_golden_has_a_csv() -> None:
    csvs = {path.with_suffix("") for path in CSV_DIR.glob("**/*.csv")}
    goldens = {path.with_name(path.name.removesuffix(".expected.json")) for path in CSV_DIR.glob("**/*.expected.json")}
    assert csvs, "no CSV fixtures found"
    assert csvs == goldens


def test_all_types_fixture_covers_every_row_type_the_profile_knows() -> None:
    # Plan 6.1: "all_types.csv (every row type)".
    profile = PROFILES[0]
    data = (CSV_DIR / "tr_csv_synthetic_v1" / "all_types.csv").read_bytes()
    result = parse_csv(data)
    seen_types = {txn.type for txn in result.transactions}
    expected_types = {row_type.type for row_type in profile.row_types.values()}
    assert seen_types == expected_types
    assert result.review == []


def test_referenz_becomes_source_ref_for_every_transaction() -> None:
    # Plan 6.1/5.3.2: "Referenz becomes source_ref".
    data = (CSV_DIR / "tr_csv_synthetic_v1" / "all_types.csv").read_bytes()
    result = parse_csv(data)
    assert result.transactions, "fixture produced no transactions"
    for txn in result.transactions:
        assert txn.source_ref is not None
        assert txn.source_kind.value == "csv_export"


def test_an_unknown_header_shows_the_header_and_first_rows() -> None:
    result = parse_csv((CSV_DIR / "unknown_header.csv").read_bytes())
    assert result.transactions == []
    assert len(result.review) == 1
    item = result.review[0]
    assert item.kind.value == "unknown_csv_header"
    assert "Date;Time;Type" in item.fields["header"]
    assert "Date;Time;Type" in item.extracted_text


def test_an_unknown_row_type_does_not_stop_the_other_rows_importing() -> None:
    # Plan WP5 done-when: "an unknown row type ... give the right review results while the
    # other rows still import."
    data = (CSV_DIR / "tr_csv_synthetic_v1" / "unknown_row_type.csv").read_bytes()
    result = parse_csv(data)
    assert len(result.transactions) == 2
    assert len(result.review) == 1
    assert result.review[0].kind.value == "unparsed_row"


def test_a_bad_number_does_not_stop_the_other_rows_importing() -> None:
    # Plan WP5 done-when: "a bad number ... give the right review results while the other rows
    # still import."
    data = (CSV_DIR / "tr_csv_synthetic_v1" / "bad_number.csv").read_bytes()
    result = parse_csv(data)
    assert len(result.transactions) == 2
    assert len(result.review) == 1
    assert result.review[0].kind.value == "unparsed_row"


def test_windows1252_and_utf8_bom_decode_to_the_same_transactions() -> None:
    # Plan 5.3.3: "sniffs the encoding (UTF-8 with or without BOM, then Windows-1252)".
    profile_dir = CSV_DIR / "tr_csv_synthetic_v1"
    bom = parse_csv((profile_dir / "bom_and_crlf.csv").read_bytes())
    cp1252 = parse_csv((profile_dir / "windows1252.csv").read_bytes())
    assert [txn.amount_eur for txn in bom.transactions] == [txn.amount_eur for txn in cp1252.transactions]
    assert [txn.type for txn in bom.transactions] == [txn.type for txn in cp1252.transactions]
    assert bom.review == cp1252.review == []


# --- WP5: the golden CSV gives the 11 candidates of plan 1.2 --------------------------------


def test_golden_csv_gives_eleven_candidates_matching_the_golden_portfolio() -> None:
    data = (GOLDEN_INPUTS_DIR / "tr_transactions_2024.csv").read_bytes()
    result = parse_csv(data)
    assert result.review == []
    assert len(result.transactions) == 11
    assert [txn.time.ts_local.date().isoformat() for txn in result.transactions] == [
        "2024-01-02",
        "2024-01-15",
        "2024-02-01",
        "2024-03-01",
        "2024-03-12",
        "2024-03-20",
        "2024-04-02",
        "2024-05-15",
        "2024-06-12",
        "2024-06-20",
        "2024-07-01",
    ]
    assert [txn.type.value for txn in result.transactions] == [
        "deposit",
        "buy",
        "buy",
        "buy",
        "buy",
        "buy",
        "deposit",
        "dividend",
        "sell",
        "transfer_in",
        "interest",
    ]
    assert [None if txn.amount_eur is None else format(txn.amount_eur, "f") for txn in result.transactions] == [
        "5000.00",
        "-1401.00",
        "-200.00",
        "-210.00",
        "-801.00",
        "-1701.00",
        "1000.00",
        "24.30",
        "1999.41",
        None,
        "3.12",
    ]
    assert all(txn.source_kind.value == "csv_export" for txn in result.transactions)
    assert all(txn.source_ref for txn in result.transactions)
