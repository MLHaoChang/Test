"""The golden portfolio through the CLI, in process (plan 1.2, 7.3, 7.6, AC2, AC5 to AC8, AC10, AC16).

The steps of the end-to-end scenario run here with Typer's `CliRunner`, on a temporary data
directory, in the order of plan 7.6, with the clock fixed by `PG_TODAY=2024-12-31`:

- step 5: `pg import` of the CSV export and the nine PDF documents (round 1);
- step 7: `pg reconcile latest --confirmed ... --as-of 2024-12-31 --strict`;
- step 9: `pg accept latest`;
- step 10: `pg import` of the same ten files plus the H1 account statement (round 2);
- step 12: `pg accept latest` of that batch, which adds no transaction;
- step 13: `pg review list`;
- step 15: `pg transfers set-cost` for the Allianz transfer in;
- step 16: `pg value` before any mapping;
- steps 18 and 19: `pg instruments map --file` and `pg instruments list`;
- steps 20 to 23: prices, the manual Allianz file, ECB rates and benchmarks, through replay;
- steps 24 to 27: `pg holdings`, `pg lots`, `pg value` and the S&P 500 in EUR;
- step 31: `pg status` with the clock moved to 2025-02-03, 34 days after the last import.

The JSON outputs are compared with the golden files in `tests/fixtures/golden/expected/` the way
`scripts/assert_golden.py` compares them (ids and timestamps ignored). Because those files are
written by the code (`pytest --update-goldens`), the figures of plan 1.2 are also asserted
directly: the counts of both rounds, the fourteen transactions field by field, the comparison with
your confirmed holdings, the review queue, the lots, and the value on the four hand-computed days.
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
MAPPING = INPUTS / "instrument_mapping.csv"
MANUAL_PRICES = INPUTS / "manual_prices_allianz.csv"
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"

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
        self.csv_path: Path | None = None

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
    """Steps 4 to 27, then 31, of plan 7.6, run once for this module."""
    base = tmp_path_factory.mktemp("golden")
    run = GoldenRun(base / "data")
    replay = ("--http-replay", str(HTTP_FIXTURES))
    run.pg("init")
    run.pg_json("import1", "import", *map(str, ROUND_ONE))
    run.pg_json("reconcile", "reconcile", "latest", "--confirmed", str(CONFIRMED), "--as-of", "2024-12-31", "--strict")
    run.pg_json("accept1", "accept", "latest")
    run.pg_json("transactions1", "transactions")
    run.pg_json("import2", "import", *map(str, ROUND_ONE), str(STATEMENT))
    run.pg("accept", "latest")
    run.pg_json("transactions2", "transactions")
    run.pg_json("review", "review", "list")
    # Step 15: enter the cost basis of the ALV transfer in, which closes missing_cost_basis and
    # rebuilds the lots and the value series.
    run.pg_json("set_cost", "transfers", "set-cost", "--isin", ALV, "--acquired", "2020-03-02", "--cost-eur", "800.00")
    # Step 16: the value before any mapping.
    run.pg_json("value_unmapped", "value", "--from", "2024-01-02", "--to", "2024-12-31")
    # Steps 18 to 23: map, then fetch prices, the manual file, rates and benchmarks.
    run.pg("instruments", "map", "--file", str(MAPPING))
    run.pg_json("instruments", "instruments", "list")
    run.pg(*replay, "prices", "fetch", "--from", "2024-01-01", "--to", "2024-12-31")
    run.pg("prices", "import-file", str(MANUAL_PRICES))
    run.pg(*replay, "fx", "fetch")
    run.pg(*replay, "benchmarks", "fetch", "--from", "2024-01-01", "--to", "2024-12-31")
    # Steps 24 to 27.
    run.pg_json("holdings_2024-12-31", "holdings", "--as-of", "2024-12-31")
    run.pg_json("lots", "lots")
    run.csv_path = base / "value.csv"
    run.pg_json("value", "value", "--from", "2024-01-02", "--to", "2024-12-31", "--csv", str(run.csv_path))
    run.pg_json(
        "sp500_eur", "benchmarks", "series", "sp500", "--currency", "EUR", "--from", "2024-01-02", "--to", "2024-12-31"
    )
    # Step 31: 34 days after the last import.
    run.pg_json("status_2025-02-03", "status", today="2025-02-03")
    return run


@pytest.mark.parametrize(
    "name",
    [
        "import1",
        "reconcile",
        "import2",
        "review",
        "value_unmapped",
        "instruments",
        "holdings_2024-12-31",
        "lots",
        "value",
        "sp500_eur",
        "status_2025-02-03",
    ],
)
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


# --- WP8: cost basis and lots ------------------------------------------------------------------


def test_set_cost_resolves_the_missing_cost_basis_review_item(golden: GoldenRun) -> None:
    items = json.loads(golden.pg("review", "list", "--status", "all", "--json").stdout)["items"]
    cost = next(item for item in items if item["kind"] == "missing_cost_basis")
    assert cost["status"] == "resolved"
    assert cost["resolution"] == {"how": "cost entered"}
    assert cost["transaction"]["isin"] == ALV
    # The unknown layout is untouched: it stays open until a parser reads it.
    assert next(item for item in items if item["kind"] == "unknown_layout")["status"] == "open"


def _lots_by_isin(golden: GoldenRun) -> dict[str, list[dict[str, Any]]]:
    by_isin: dict[str, list[dict[str, Any]]] = {}
    for lot in golden.outputs["lots"]["lots"]:
        by_isin.setdefault(lot["isin"], []).append(lot)
    return by_isin


def test_lots_match_the_hand_computed_figures(golden: GoldenRun) -> None:
    # Plan 1.2: "Open lots on 2024-12-31: SAP 3 shares, cost 480.60 (opened 2024-04-10); MSCIW
    # 2.5 at 200.00 and 2.5 at 210.00; AAPL 5 at 801.00; NVDA 20 at 1,701.00 (opened 2024-03-20,
    # quantity scaled by the 10-for-1 split, cost unchanged); ALV 4 at 800.00 (acquired
    # 2020-03-02, cost typed in by the user). Total open cost basis 4,192.60." Plus the exhausted
    # SAP lot T2 sold in full: 7 lot rows in all (accept1 already asserts this count).
    by_isin = _lots_by_isin(golden)
    assert sum(len(lots) for lots in by_isin.values()) == 7

    sap_first, sap_second = sorted(by_isin[SAP], key=lambda lot: lot["booked_ts"])
    assert (sap_first["quantity_open"], sap_first["cost_eur_open"]) == ("0", "0.00")
    assert (sap_second["quantity_open"], sap_second["cost_eur_open"]) == ("3", "480.60")
    assert sap_second["booked_ts"].startswith("2024-04-10")

    assert {lot["cost_eur_initial"] for lot in by_isin[MSCIW]} == {"200.00", "210.00"}
    assert all(lot["quantity_open"] == "2.5" for lot in by_isin[MSCIW])
    assert all(lot["cost_eur_open"] == lot["cost_eur_initial"] for lot in by_isin[MSCIW])

    (aapl,) = by_isin[AAPL]
    assert (aapl["quantity_open"], aapl["cost_eur_open"]) == ("5", "801.00")

    (nvda,) = by_isin[NVDA]
    assert (nvda["quantity_initial"], nvda["quantity_open"]) == ("20", "20")
    assert (nvda["cost_eur_initial"], nvda["cost_eur_open"]) == ("1701.00", "1701.00")
    assert nvda["opened_ts"].startswith("2024-03-20")  # the split moves quantity, never the date

    (alv,) = by_isin[ALV]
    assert alv["origin"] == "transfer_in"
    assert alv["cost_missing"] is False
    assert (alv["quantity_initial"], alv["quantity_open"]) == ("4", "4")
    assert (alv["cost_eur_initial"], alv["cost_eur_open"]) == ("800.00", "800.00")
    # 2020-03-02 00:00 Berlin time (winter, CET = UTC+1) is 23:00 UTC the day before.
    assert alv["opened_ts"] == "2020-03-01T23:00:00Z"

    total_open_cost = sum((Decimal(lot["cost_eur_open"]) for lots in by_isin.values() for lot in lots), Decimal(0))
    assert total_open_cost == Decimal("4192.60")


def test_disposals_match_the_hand_computed_fifo_split(golden: GoldenRun) -> None:
    # Plan 1.2: "lot T2 (10 shares, cost 1,401.00) is consumed fully; lot T8 (5 shares, cost
    # 801.00) gives 2 shares at 801.00 x 2 / 5 = 320.40. Consumed cost 1,721.40. Proceeds after
    # fee 2,099.00. Realised gain 377.60 (before tax). Proceeds are split across the two
    # disposals by quantity: 1,749.16666667 and 349.83333333."
    disposals = golden.outputs["lots"]["disposals"]
    assert len(disposals) == 2
    assert {disposal["isin"] for disposal in disposals} == {SAP}
    assert {disposal["kind"] for disposal in disposals} == {"sell"}
    assert {disposal["transaction"]["id"] for disposal in disposals} == {disposals[0]["transaction"]["id"]}

    full, partial = sorted(disposals, key=lambda disposal: -Decimal(disposal["quantity"]))
    assert (full["quantity"], full["cost_eur"]) == ("10", "1401.00")
    assert (full["proceeds_eur"], full["realised_eur"]) == ("1749.16666667", "348.16666667")
    assert (partial["quantity"], partial["cost_eur"]) == ("2", "320.40")
    assert (partial["proceeds_eur"], partial["realised_eur"]) == ("349.83333333", "29.43333333")

    consumed_cost = Decimal(full["cost_eur"]) + Decimal(partial["cost_eur"])
    proceeds_before_tax = Decimal(full["proceeds_eur"]) + Decimal(partial["proceeds_eur"])
    realised = Decimal(full["realised_eur"]) + Decimal(partial["realised_eur"])
    assert (consumed_cost, proceeds_before_tax, realised) == (Decimal("1721.40"), Decimal("2099.00"), Decimal("377.60"))


def test_status_reminds_you_to_import_again_after_34_days(golden: GoldenRun) -> None:
    status = golden.outputs["status_2025-02-03"]
    assert status["last_import_at"] == "2024-12-31T11:00:00Z"
    assert status["reminder"]["due"] is True
    assert status["reminder"]["days_since_last_import"] == 34
    # `pg transfers set-cost` (above) closed the missing cost basis; the unknown layout is still open.
    assert status["open_review_items"] == 1


# --- WP10: values -----------------------------------------------------------------------------


def _day(output: dict[str, Any], day: str) -> dict[str, Any]:
    return next(entry for entry in output["series"] if entry["date"] == day)


def _holdings(golden: GoldenRun, day: str) -> dict[str, dict[str, Any]]:
    output = json.loads(golden.pg("holdings", "--as-of", day, "--json").stdout)
    return {row["isin"]: row for row in output["holdings"]}


def test_set_cost_rebuilds_the_value_series_over_the_golden_range(golden: GoldenRun) -> None:
    values = golden.outputs["set_cost"]["values"]
    assert (values["from"], values["to"], values["days"]) == ("2024-01-02", "2024-12-31", 261)


def test_before_mapping_every_holding_is_unmapped_with_a_value_of_zero(golden: GoldenRun) -> None:
    # Plan 1.2: "Before any mapping, the value series reports every held instrument as unmapped,
    # a value of 0.00 and complete: false, and exits with status 0."
    output = golden.outputs["value_unmapped"]
    assert (output["from"], output["to"], output["days"]) == ("2024-01-02", "2024-12-31", 261)
    assert {entry["value_eur"] for entry in output["series"]} == {"0.00"}
    held = [entry for entry in output["series"] if entry["date"] >= "2024-01-15"]
    assert {entry["complete"] for entry in held} == {False}
    last = _day(output, "2024-12-31")
    assert sorted(last["flags"]) == sorted(f"unmapped {isin}" for isin in (SAP, MSCIW, AAPL, NVDA, ALV))
    assert last["cost_basis_eur"] == "4192.60"
    assert {flag["kind"] for flag in output["flags"]} == {"unmapped"}


def test_the_mapping_is_shown_and_every_instrument_is_confirmed(golden: GoldenRun) -> None:
    instruments = {row["isin"]: row for row in golden.outputs["instruments"]["instruments"]}
    assert set(instruments) == {SAP, MSCIW, AAPL, NVDA, ALV}
    assert {row["mapping_status"] for row in instruments.values()} == {"confirmed"}
    # The names come from the documents, not from the mapping file.
    assert instruments[SAP]["name"] == "SAP SE"
    assert instruments[NVDA]["name"] == "NVIDIA Corp."
    assert (instruments[ALV]["data_source"], instruments[ALV]["data_symbol"]) == ("manual", "ALV.DE")


def test_2024_05_31_is_5916_67(golden: GoldenRun) -> None:
    day = _day(golden.outputs["value"], "2024-05-31")
    assert (day["value_eur"], day["cost_basis_eur"], day["complete"]) == ("5916.67", "5114.00", True)
    holdings = _holdings(golden, "2024-05-31")
    assert set(holdings) == {SAP, MSCIW, AAPL, NVDA}
    assert holdings[NVDA]["pricing_quantity"] == "20"  # 2 shares on the split-adjusted basis
    assert {isin: row["value_eur"] for isin, row in holdings.items()} == {
        SAP: "2550.00",
        MSCIW: "450.00",
        AAPL: "879.63",
        NVDA: "2037.04",
    }


def test_2024_12_31_is_6146_85(golden: GoldenRun) -> None:
    day = _day(golden.outputs["value"], "2024-12-31")
    assert (day["value_eur"], day["cost_basis_eur"], day["complete"]) == ("6146.85", "4192.60", True)
    assert f"stale_price {ALV}" in day["flags"]
    assert golden.outputs["value"]["latest"] == day

    holdings = golden.outputs["holdings_2024-12-31"]
    assert (holdings["value_eur"], holdings["cost_basis_eur"], holdings["complete"]) == ("6146.85", "4192.60", True)
    rows = {row["isin"]: row for row in holdings["holdings"]}
    assert rows[SAP]["price"]["date"] == "2024-12-30"  # Xetra is closed on the 31st
    assert rows[MSCIW]["price"]["date"] == "2024-12-30"
    assert rows[AAPL]["fx"] == {"currency": "USD", "rate": "1.0400", "date": "2024-12-31"}
    assert (rows[ALV]["value_eur"], rows[ALV]["price"]["date"]) == ("1160.00", "2024-12-20")
    assert "stale_price" in {flag["kind"] for flag in rows[ALV]["flags"]}


def test_2024_12_25_and_2024_12_27_show_the_price_dates_rate_dates_and_the_stale_boundary(golden: GoldenRun) -> None:
    christmas = _holdings(golden, "2024-12-25")
    assert christmas[SAP]["price"]["date"] == "2024-12-23"
    assert christmas[AAPL]["fx"]["date"] == "2024-12-24"
    assert christmas[ALV]["price"]["date"] == "2024-12-20"  # 5 days old: not stale
    assert not any(flag["kind"].startswith("stale") for row in christmas.values() for flag in row["flags"])

    friday = _holdings(golden, "2024-12-27")
    assert friday[ALV]["price"]["date"] == "2024-12-20"  # 7 days old: stale
    assert [flag["kind"] for flag in friday[ALV]["flags"]] == ["stale_price"]
    assert not any(
        flag["kind"].startswith("stale") for isin, row in friday.items() if isin != ALV for flag in row["flags"]
    )


def test_the_value_output_lists_every_flag_of_the_year(golden: GoldenRun) -> None:
    output = golden.outputs["value"]
    assert (output["from"], output["to"], output["days"], output["complete_days"]) == (
        "2024-01-02",
        "2024-12-31",
        261,
        261,
    )
    flags = {(flag["kind"], flag["isin"]): flag for flag in output["flags"]}
    assert set(flags) == {
        ("stale_price", ALV),
        ("dividend_adjusted_prices", SAP),
        ("dividend_adjusted_prices", MSCIW),
        ("dividend_adjusted_prices", AAPL),
        ("dividend_adjusted_prices", NVDA),
    }
    stale = flags[("stale_price", ALV)]
    assert (stale["first"], stale["last"], stale["days"]) == ("2024-12-26", "2024-12-31", 4)


def test_the_value_csv_has_one_row_per_weekday(golden: GoldenRun) -> None:
    assert golden.csv_path is not None
    lines = golden.csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date;value_eur;cost_basis_eur;complete;flags"
    assert len(lines) == 1 + 261
    assert next(line for line in lines if line.startswith("2024-05-31;")).startswith("2024-05-31;5916.67;5114.00;true;")


def test_status_shows_the_latest_value_with_every_price_old_by_then(golden: GoldenRun) -> None:
    latest = golden.outputs["status_2025-02-03"]["latest_value"]
    assert (latest["date"], latest["value_eur"], latest["cost_basis_eur"], latest["complete"]) == (
        "2025-02-03",
        "6146.85",
        "4192.60",
        True,
    )
    assert {f"stale_price {isin}" for isin in (SAP, MSCIW, AAPL, NVDA, ALV)} <= set(latest["flags"])
    assert {f"stale_fx {isin}" for isin in (AAPL, NVDA)} <= set(latest["flags"])
