"""Tests for the manual transaction format (plan 5.3.3, 5.3.4).

Golden tests over `tests/fixtures/manual_csv/*.csv`, mirroring `test_csv_goldens.py`, plus a few
direct assertions of the rules the goldens would otherwise only pin implicitly: the highest
precedence source uses `source_kind = manual_csv`, an unknown header and a handful of bad rows
each give the right review result while the good rows still import, and a day with no time gives
a day-precision entry.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from playground.core.types import TxnType
from playground.importer.manual_csv import HEADER, parse_manual_csv
from playground.importer.model import SourceKind
from playground.importer.serialise import parse_result_to_dict

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "manual_csv"


def outcome_to_dict(data: bytes) -> dict[str, Any]:
    return parse_result_to_dict(parse_manual_csv(data))


def expected_path(csv_path: Path) -> Path:
    return csv_path.with_name(f"{csv_path.stem}.expected.json")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "csv_fixture" in metafunc.fixturenames:
        paths = sorted(FIXTURES_DIR.glob("*.csv"))
        ids = [path.stem for path in paths]
        metafunc.parametrize("csv_fixture", paths, ids=ids)


def test_manual_csv_golden(csv_fixture: Path, update_goldens: bool) -> None:
    actual = outcome_to_dict(csv_fixture.read_bytes())
    golden = expected_path(csv_fixture)
    if update_goldens:
        golden.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    expected = json.loads(golden.read_text(encoding="utf-8"))
    assert actual == expected


def test_every_manual_csv_fixture_has_a_golden_and_every_golden_has_a_csv() -> None:
    csvs = {path.stem for path in FIXTURES_DIR.glob("*.csv")}
    goldens = {path.name.removesuffix(".expected.json") for path in FIXTURES_DIR.glob("*.expected.json")}
    assert csvs, "no manual CSV fixtures found"
    assert csvs == goldens


def test_every_transaction_is_source_kind_manual_csv() -> None:
    data = (FIXTURES_DIR / "sample.csv").read_bytes()
    result = parse_manual_csv(data)
    assert result.transactions
    assert all(txn.source_kind is SourceKind.MANUAL_CSV for txn in result.transactions)
    # Plan 5.3.4: manual CSV is a source you write yourself, so it carries no report reference.
    assert all(txn.source_ref is None for txn in result.transactions)


def test_note_column_is_carried_in_the_name_field() -> None:
    data = (FIXTURES_DIR / "sample.csv").read_bytes()
    result = parse_manual_csv(data)
    names = [txn.name for txn in result.transactions]
    assert "acquired before the Trade Republic account existed" in names


def test_a_day_with_no_time_gives_a_day_precision_entry() -> None:
    data = (FIXTURES_DIR / "sample.csv").read_bytes()
    result = parse_manual_csv(data)
    transfer = next(txn for txn in result.transactions if txn.type is TxnType.TRANSFER_IN)
    assert transfer.time.precision == "day"


def test_split_row_carries_split_new_quantity_not_quantity() -> None:
    data = (FIXTURES_DIR / "sample.csv").read_bytes()
    result = parse_manual_csv(data)
    split = next(txn for txn in result.transactions if txn.type is TxnType.SPLIT)
    assert split.quantity is None
    assert split.split_new_quantity == 20


def test_bad_rows_are_reported_individually_and_the_good_rows_still_import() -> None:
    data = (FIXTURES_DIR / "bad_rows.csv").read_bytes()
    result = parse_manual_csv(data)
    assert len(result.transactions) == 2
    kinds = sorted(item.kind.value for item in result.review)
    assert kinds == ["invalid_isin", "unparsed_row", "unparsed_row", "unparsed_row"]


def test_a_bad_row_item_holds_only_the_header_and_that_row() -> None:
    # QA P0 round 2, R2-D3: the item used to keep the whole file, so pg review show and pg review
    # export printed every row you typed for one bad row.
    data = (FIXTURES_DIR / "bad_rows.csv").read_bytes()
    lines = data.decode("utf-8").splitlines()

    result = parse_manual_csv(data)

    assert len(result.review) == 4
    for item in result.review:
        row = lines[int(item.fields["line"]) - 1]
        assert item.extracted_text == f"{lines[0]}\n{row}\n"


def test_an_unrecognised_header_gives_unknown_csv_header() -> None:
    data = (FIXTURES_DIR / "unknown_header.csv").read_bytes()
    result = parse_manual_csv(data)
    assert result.transactions == []
    assert len(result.review) == 1
    assert result.review[0].kind.value == "unknown_csv_header"
    assert ";".join(HEADER) in result.review[0].message
