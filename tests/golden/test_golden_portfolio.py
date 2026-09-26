"""The golden portfolio through the CLI, in process (plan 1.2, 7.3, 7.6, AC2, AC5, AC6).

The steps of the end-to-end scenario that WP7 provides run here with Typer's `CliRunner`, on a
temporary data directory, with the clock fixed by `PG_TODAY=2024-12-31`:

- step 5: `pg import` of the CSV export and the nine PDF documents (round 1);
- step 7: `pg reconcile latest --confirmed ... --as-of 2024-12-31 --strict`;
- step 9: `pg accept latest`;
- step 10: `pg import` of the same ten files plus the H1 account statement (round 2);
- step 12: `pg accept latest` of that batch, which adds no transaction;
- step 13: `pg review list`.

The JSON outputs are compared with the golden files in `tests/fixtures/golden/expected/` the way
`scripts/assert_golden.py` compares them (ids and timestamps ignored). Because those files are
written by the code (`pytest --update-goldens`), the figures of plan 1.2 are also asserted
directly: the counts of both rounds, the fourteen transactions field by field, the comparison with
your confirmed holdings, and the review queue. Later packages add lots (WP8) and values (WP10).
"""

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from playground.cli.main import app

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "golden"
INPUTS = GOLDEN / "inputs"
EXPECTED = GOLDEN / "expected"
ROUND_ONE = [INPUTS / "tr_transactions_2024.csv", *sorted((INPUTS / "pdf").glob("*.pdf"))]
STATEMENT = INPUTS / "statement" / "kontoauszug_2024_h1.pdf"
CONFIRMED = INPUTS / "confirmed_holdings_2024-12-31.csv"

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"


def load_assert_golden() -> ModuleType:
    """The comparer of `scripts/assert_golden.py`, loaded as a module."""
    if "assert_golden" in sys.modules:
        return sys.modules["assert_golden"]
    spec = importlib.util.spec_from_file_location("assert_golden", REPO / "scripts" / "assert_golden.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["assert_golden"] = module
    spec.loader.exec_module(module)
    return module


class GoldenRun:
    """Runs `pg` commands on one data directory with the clock of the scenario."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.runner = CliRunner()
        self.outputs: dict[str, Any] = {}

    def pg(self, *args: str, today: str = "2024-12-31", expect: int = 0) -> Any:
        result = self.runner.invoke(app, ["--data-dir", str(self.data_dir), *args], env={"PG_TODAY": today})
        assert result.exit_code == expect, result.output
        return result

    def pg_json(self, name: str, *args: str, today: str = "2024-12-31") -> Any:
        result = self.pg(*args, "--json", today=today)
        self.outputs[name] = json.loads(result.stdout)
        return self.outputs[name]


@pytest.fixture(scope="module")
def golden(tmp_path_factory: pytest.TempPathFactory) -> GoldenRun:
    """Steps 4 to 13 of plan 7.6, run once for this module."""
    run = GoldenRun(tmp_path_factory.mktemp("golden") / "data")
    run.pg("init")
    run.pg_json("import1", "import", *map(str, ROUND_ONE))
    run.pg_json("reconcile", "reconcile", "latest", "--confirmed", str(CONFIRMED), "--as-of", "2024-12-31", "--strict")
    run.pg_json("accept1", "accept", "latest")
    run.pg_json("transactions1", "transactions")
    run.pg_json("import2", "import", *map(str, ROUND_ONE), str(STATEMENT))
    run.pg("accept", "latest")
    run.pg_json("transactions2", "transactions")
    run.pg_json("review", "review", "list")
    return run


@pytest.mark.parametrize("name", ["import1", "reconcile", "import2", "review"])
def test_the_output_matches_its_golden_file(golden: GoldenRun, update_goldens: bool, name: str) -> None:
    path = EXPECTED / f"{name}.json"
    actual = golden.outputs[name]
    if update_goldens:
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    comparer = load_assert_golden()
    expected = json.loads(path.read_text(encoding="utf-8"))
    differences = comparer.compare_values(comparer.normalize_value(expected), comparer.normalize_value(actual))
    assert differences == [], "\n".join(differences)


# --- Round 1 ----------------------------------------------------------------------------------


def test_round_one_counts(golden: GoldenRun) -> None:
    # Plan 1.2: 19 candidates (11 CSV rows, 8 PDF transactions), 14 unique transactions, 5 merged
    # pairs, 0 held back, 2 review items raised at staging.
    assert golden.outputs["import1"]["counts"] == {
        "files": 10,
        "duplicate_files": 0,
        "candidates": 19,
        "new": 14,
        "merged": 5,
        "already_known": 0,
        "held_back": 0,
        "completed": 0,
        "review_new": 2,
        "review_closed": 0,
    }


def test_round_one_merges_the_five_pairs(golden: GoldenRun) -> None:
    merged = golden.outputs["import1"]["merged"]
    assert {
        (entry["transaction"]["type"], entry["transaction"]["isin"], entry["transaction"]["date"]) for entry in merged
    } == {
        ("buy", SAP, "2024-01-15"),
        ("buy", MSCIW, "2024-02-01"),
        ("buy", NVDA, "2024-03-20"),
        ("dividend", SAP, "2024-05-15"),
        ("sell", SAP, "2024-06-12"),
    }
    assert {entry["file_name"] for entry in merged} == {"tr_transactions_2024.csv"}
    assert {entry["rule"] for entry in merged} == {"same_key"}


def test_round_one_raises_the_unknown_layout_and_the_missing_cost_basis(golden: GoldenRun) -> None:
    items = golden.outputs["import1"]["review_new"]
    assert sorted((item["kind"], item["file_name"]) for item in items) == [
        ("missing_cost_basis", "tr_transactions_2024.csv"),
        ("unknown_layout", "unbekannt_kosteninformation.pdf"),
    ]
    files = {entry["file_name"]: entry["status"] for entry in golden.outputs["import1"]["files"]}
    assert files["unbekannt_kosteninformation.pdf"] == "needs_review"
    assert files["tr_transactions_2024.csv"] == "parsed"
    assert files["2024-06-10_split_nvda.pdf"] == "parsed"


# Plan 1.2, field by field: type, ISIN, local time, precision, quantity, price, booking amount,
# fees, taxes, split total, origin, reference, value date and the kinds of file that report it.
T = {
    "T1": (
        "deposit",
        None,
        "2024-01-02T00:00:00",
        "day",
        None,
        None,
        "5000.00",
        "0.00",
        "0.00",
        None,
        None,
        "csv-2024-0001",
        None,
        ["csv_export"],
    ),
    "T2": (
        "buy",
        SAP,
        "2024-01-15T10:05:00",
        "minute",
        "10",
        "140.00",
        "-1401.00",
        "1.00",
        "0.00",
        None,
        "order",
        "5d0a-93f1",
        "2024-01-17",
        ["csv_export", "pdf_document"],
    ),
    "T3": (
        "buy",
        MSCIW,
        "2024-02-01T00:00:00",
        "day",
        "2.5",
        "80.00",
        "-200.00",
        "0.00",
        "0.00",
        None,
        "savings_plan",
        "2c61-7e0b",
        "2024-02-05",
        ["csv_export", "pdf_document"],
    ),
    "T4": (
        "buy",
        MSCIW,
        "2024-03-01T00:00:00",
        "day",
        "2.5",
        "84.00",
        "-210.00",
        "0.00",
        "0.00",
        None,
        "savings_plan",
        "csv-2024-0004",
        None,
        ["csv_export"],
    ),
    "T5": (
        "buy",
        AAPL,
        "2024-03-12T14:30:00",
        "minute",
        "5",
        "160.00",
        "-801.00",
        "1.00",
        "0.00",
        None,
        "order",
        "csv-2024-0005",
        None,
        ["csv_export"],
    ),
    "T6": (
        "buy",
        NVDA,
        "2024-03-20T15:45:00",
        "minute",
        "2",
        "850.00",
        "-1701.00",
        "1.00",
        "0.00",
        None,
        "order",
        "83ab-d4c6",
        "2024-03-22",
        ["csv_export", "pdf_document"],
    ),
    "T7": (
        "deposit",
        None,
        "2024-04-02T00:00:00",
        "day",
        None,
        None,
        "1000.00",
        "0.00",
        "0.00",
        None,
        None,
        "csv-2024-0007",
        None,
        ["csv_export"],
    ),
    "T8": (
        "buy",
        SAP,
        "2024-04-10T09:12:00",
        "minute",
        "5",
        "160.00",
        "-801.00",
        "1.00",
        "0.00",
        None,
        "order",
        "f1c3-58e2",
        "2024-04-12",
        ["pdf_document"],
    ),
    "T9": (
        "dividend",
        SAP,
        "2024-05-15T00:00:00",
        "day",
        "15",
        "2.20",
        "24.30",
        "0.00",
        "8.70",
        None,
        None,
        "5f2e-9a04",
        "2024-05-15",
        ["csv_export", "pdf_document"],
    ),
    "T10": (
        "dividend",
        AAPL,
        "2024-05-16T00:00:00",
        "day",
        "5",
        "0.24",
        "0.94",
        "0.00",
        "0.17",
        None,
        None,
        "3b7a-88ce",
        "2024-05-16",
        ["pdf_document"],
    ),
    "T11": (
        "split",
        NVDA,
        "2024-06-10T00:00:00",
        "day",
        None,
        None,
        None,
        "0.00",
        "0.00",
        "20",
        None,
        "9c3f-51ad",
        None,
        ["pdf_document"],
    ),
    "T12": (
        "sell",
        SAP,
        "2024-06-12T11:20:00",
        "minute",
        "12",
        "175.00",
        "1999.41",
        "1.00",
        "99.59",
        None,
        "order",
        "c2e8-7195",
        "2024-06-14",
        ["csv_export", "pdf_document"],
    ),
    "T13": (
        "transfer_in",
        ALV,
        "2024-06-20T00:00:00",
        "day",
        "4",
        None,
        None,
        "0.00",
        "0.00",
        None,
        "transfer",
        "csv-2024-0010",
        None,
        ["csv_export"],
    ),
    "T14": (
        "interest",
        None,
        "2024-07-01T00:00:00",
        "day",
        None,
        None,
        "3.12",
        "0.00",
        "0.00",
        None,
        None,
        "csv-2024-0011",
        None,
        ["csv_export"],
    ),
}
FIELDS = (
    "type",
    "isin",
    "ts_local",
    "ts_precision",
    "quantity",
    "price",
    "amount_eur",
    "fees_eur",
    "tax_eur",
    "split_new_quantity",
    "origin",
    "source_ref",
    "value_date",
)


def as_row(record: dict[str, Any]) -> tuple[Any, ...]:
    return (*(record[field] for field in FIELDS), sorted(source["kind"] for source in record["sources"]))


def test_round_one_gives_the_fourteen_transactions_field_by_field(golden: GoldenRun) -> None:
    records = golden.outputs["transactions1"]["transactions"]
    assert [as_row(record) for record in records] == list(T.values())
    assert {record["state"] for record in records} == {"accepted"}


def test_round_one_keeps_the_tax_detail_and_the_document_rate(golden: GoldenRun) -> None:
    records = {record["source_ref"]: record for record in golden.outputs["transactions1"]["transactions"]}
    assert records["5f2e-9a04"]["tax_detail"] == {"kapitalertragssteuer": "8.25", "solidaritaetszuschlag": "0.45"}
    assert records["3b7a-88ce"]["tax_detail"] == {"quellensteuer": "0.17"}
    assert records["3b7a-88ce"]["fx_rate"] == "1.0800"
    assert records["c2e8-7195"]["tax_detail"] == {"kapitalertragssteuer": "94.40", "solidaritaetszuschlag": "5.19"}
    assert records["c2e8-7195"]["ts_utc"] == "2024-06-12T09:20:00Z"  # summer time


def test_taxes_withheld_and_gross_dividends_in_2024(golden: GoldenRun) -> None:
    records = golden.outputs["transactions1"]["transactions"]
    taxes = sum(Decimal(record["tax_eur"]) for record in records if record["tax_eur"] is not None)
    gross = sum(
        Decimal(record["amount_eur"]) + Decimal(record["tax_eur"]) for record in records if record["type"] == "dividend"
    )
    assert taxes == Decimal("108.46")
    assert gross == Decimal("34.11")


def test_the_diff_shows_five_of_five_match_before_accept(golden: GoldenRun) -> None:
    diff = golden.outputs["reconcile"]
    assert diff["batch"]["status"] == "staged"
    confirmed = diff["confirmed"]
    assert confirmed["message"] == "5 of 5 match"
    assert {row["isin"]: row["computed"] for row in confirmed["rows"]} == {
        SAP: "3",
        MSCIW: "5",
        AAPL: "5",
        NVDA: "20",  # after the split
        ALV: "4",
    }
    assert {row["status"] for row in confirmed["rows"]} == {"match"}


def test_accept_takes_the_fourteen_transactions_and_rebuilds_the_lots(golden: GoldenRun) -> None:
    accepted = golden.outputs["accept1"]
    assert accepted["accepted"] == 14
    assert accepted["held_back"] == 0
    assert accepted["lots"] == 7
    assert accepted["disposals"] == 2
    assert accepted["open_review_items"] == 2


# --- Round 2 ----------------------------------------------------------------------------------


def test_round_two_counts(golden: GoldenRun) -> None:
    # Plan 1.2: 10 files skipped as already imported (the unknown sheet is classified again and
    # still finds no parser), 11 statement lines all matched to known transactions, 0 new
    # transactions, 0 new review items.
    assert golden.outputs["import2"]["counts"] == {
        "files": 11,
        "duplicate_files": 10,
        "candidates": 11,
        "new": 0,
        "merged": 0,
        "already_known": 11,
        "held_back": 0,
        "completed": 0,
        "review_new": 0,
        "review_closed": 0,
    }
    statuses = {entry["file_name"]: entry["status"] for entry in golden.outputs["import2"]["files"]}
    assert statuses.pop("kontoauszug_2024_h1.pdf") == "parsed"
    assert set(statuses.values()) == {"duplicate_file"}


def test_round_two_adds_a_statement_source_to_every_transaction_the_statement_lists(golden: GoldenRun) -> None:
    before = golden.outputs["transactions1"]["transactions"]
    after = golden.outputs["transactions2"]["transactions"]
    assert [as_row(record)[:-1] for record in after] == [as_row(record)[:-1] for record in before]
    with_statement = [
        name
        for name, record in zip(T, after, strict=True)
        if any(s["kind"] == "pdf_statement" for s in record["sources"])
    ]
    assert with_statement == ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T12"]


def test_the_review_queue_holds_the_unknown_layout_and_the_missing_cost_basis(golden: GoldenRun) -> None:
    items = golden.outputs["review"]["items"]
    assert sorted((item["kind"], item["status"]) for item in items) == [
        ("missing_cost_basis", "open"),
        ("unknown_layout", "open"),
    ]
    cost = next(item for item in items if item["kind"] == "missing_cost_basis")
    assert cost["transaction"]["isin"] == ALV


def test_status_reminds_you_to_import_again_after_34_days(golden: GoldenRun) -> None:
    status = json.loads(golden.pg("status", "--json", today="2025-02-03").stdout)
    assert status["last_import_at"] == "2024-12-31T11:00:00Z"
    assert status["reminder"]["due"] is True
    assert status["reminder"]["days_since_last_import"] == 34
    # WP8's `pg transfers set-cost` closes the missing cost basis; until then both items are open.
    assert status["open_review_items"] == 2
