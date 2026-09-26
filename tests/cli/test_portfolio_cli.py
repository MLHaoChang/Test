"""Tests for the ledger view and transfer commands (cli/portfolio.py, plan 5.9).

`pg holdings [--as-of]` and `pg lots [--isin]` are ledger views on the accepted portfolio; `pg
transfers list` and `pg transfers set-cost` enter the cost basis of a transfer in. Every read
command takes `--json`.
"""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from playground.cli.main import app

SAP = "DE0007164600"
ALV = "DE0008404005"

CSV_HEADER = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"
# A SAP buy, a partial SAP sell (realises a gain of 139.60) and an ALV transfer in without a cost.
CSV_ROWS = (
    "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;pc-0001",
    "12.06.2024;11:20;Verkauf;DE0007164600;SAP SE;4;175,00;700,00;0,00;0,00;EUR;;pc-0002",
    "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;pc-0003",
)

runner = CliRunner()


def pg(data_dir: str, *args: str):
    return runner.invoke(app, ["--data-dir", data_dir, *args], env={"PG_TODAY": "2024-12-31"})


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    path = str(tmp_path / "data")
    assert pg(path, "init").exit_code == 0
    return path


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "export.csv"
    path.write_text("\n".join([CSV_HEADER, *CSV_ROWS]) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def imported(data_dir: str, csv_file: Path) -> str:
    """`data_dir` with the CSV of `csv_file` imported and accepted."""
    assert pg(data_dir, "import", str(csv_file)).exit_code == 0
    assert pg(data_dir, "accept", "latest").exit_code == 0
    return data_dir


# --- holdings ---------------------------------------------------------------------------------


def test_holdings_shows_quantity_and_cost_for_every_isin(imported: str) -> None:
    result = pg(imported, "holdings", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["as_of"] == "2024-12-31"
    by_isin = {row["isin"]: row for row in data["holdings"]}
    assert by_isin[SAP]["quantity"] == "6"
    assert by_isin[SAP]["cost_eur"] == "840.60"
    assert by_isin[SAP]["name"] == "SAP SE"
    assert by_isin[ALV]["quantity"] == "4"
    assert by_isin[ALV]["cost_eur"] is None


def test_holdings_as_of_an_earlier_day_leaves_out_what_had_not_happened_yet(imported: str) -> None:
    result = pg(imported, "holdings", "--as-of", "2024-01-16", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert {row["isin"] for row in data["holdings"]} == {SAP}
    assert next(row for row in data["holdings"] if row["isin"] == SAP)["quantity"] == "10"


def test_holdings_defaults_to_today_and_prints_plain_text(imported: str) -> None:
    result = pg(imported, "holdings")
    assert result.exit_code == 0, result.output
    assert "Holdings on 2024-12-31" in result.stdout
    assert "SAP SE: 6 shares, cost 840.60 EUR" in result.stdout
    assert "cost unknown" in result.stdout


def test_holdings_before_any_import_is_empty(data_dir: str) -> None:
    result = pg(data_dir, "holdings", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["holdings"] == []


def test_holdings_rejects_a_malformed_as_of(imported: str) -> None:
    result = pg(imported, "holdings", "--as-of", "31-12-2024")
    assert result.exit_code == 1
    assert "--as-of" in result.output


# --- lots -------------------------------------------------------------------------------------


def test_lots_lists_every_lot_and_disposal(imported: str) -> None:
    result = pg(imported, "lots", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["isin"] is None
    by_isin = {lot["isin"]: lot for lot in data["lots"]}
    assert by_isin[SAP]["origin"] == "buy"
    assert by_isin[SAP]["quantity_initial"] == "10"
    assert by_isin[SAP]["quantity_open"] == "6"
    assert by_isin[SAP]["cost_eur_initial"] == "1401.00"
    assert by_isin[SAP]["cost_eur_open"] == "840.60"
    assert by_isin[SAP]["cost_missing"] is False
    assert by_isin[ALV]["origin"] == "transfer_in"
    assert by_isin[ALV]["cost_missing"] is True
    assert by_isin[ALV]["cost_eur_initial"] is None

    disposal = data["disposals"][0]
    assert disposal["isin"] == SAP
    assert disposal["kind"] == "sell"
    assert disposal["quantity"] == "4"
    assert disposal["cost_eur"] == "560.40"
    assert disposal["proceeds_eur"] == "700.00"
    assert disposal["realised_eur"] == "139.60"


def test_lots_filters_by_isin(imported: str) -> None:
    result = pg(imported, "lots", "--isin", ALV, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    assert data["isin"] == ALV
    assert [lot["isin"] for lot in data["lots"]] == [ALV]
    assert data["disposals"] == []


def test_lots_rejects_an_invalid_isin(imported: str) -> None:
    result = pg(imported, "lots", "--isin", "DE0000000000")
    assert result.exit_code == 1
    assert "not a valid isin" in result.output.lower()


def test_lots_prints_plain_text(imported: str) -> None:
    result = pg(imported, "lots")
    assert result.exit_code == 0, result.output
    assert "SAP SE" in result.stdout
    assert "Disposals:" in result.stdout
    assert "realised 139.60 EUR" in result.stdout


# --- transfers ----------------------------------------------------------------------------------


def test_transfers_list_shows_no_cost_basis_until_set(imported: str) -> None:
    result = pg(imported, "transfers", "list", "--json")
    assert result.exit_code == 0, result.output
    transfer = json.loads(result.stdout)["transfers"][0]
    assert transfer["isin"] == ALV
    assert transfer["quantity"] == "4"
    assert transfer["cost_basis"] is None

    text = pg(imported, "transfers", "list")
    assert text.exit_code == 0
    assert "no cost basis yet" in text.stdout
    assert "pg transfers set-cost" in text.stdout


def test_transfers_list_with_none_is_a_plain_message(data_dir: str) -> None:
    result = pg(data_dir, "transfers", "list")
    assert result.exit_code == 0, result.output
    assert "No transfers in." in result.stdout


def test_set_cost_enters_the_cost_basis_and_closes_the_review_item(imported: str) -> None:
    result = pg(
        imported, "transfers", "set-cost", "--isin", ALV, "--acquired", "2020-03-02", "--cost-eur", "800.00", "--json"
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["isin"] == ALV
    assert data["cost_eur"] == "800.00"
    assert data["acquired_on"] == "2020-03-02"
    assert data["lots"] == 2
    assert [item["kind"] for item in data["review_closed"]] == ["missing_cost_basis"]

    transfer = json.loads(pg(imported, "transfers", "list", "--json").stdout)["transfers"][0]
    assert transfer["cost_basis"] == {"acquired_on": "2020-03-02", "cost_eur": "800.00"}

    holding = next(
        row for row in json.loads(pg(imported, "holdings", "--json").stdout)["holdings"] if row["isin"] == ALV
    )
    assert holding["cost_eur"] == "800.00"

    all_items = json.loads(pg(imported, "review", "list", "--status", "all", "--json").stdout)["items"]
    missing_cost_items = [item for item in all_items if item["kind"] == "missing_cost_basis"]
    assert missing_cost_items
    assert all(item["status"] == "resolved" for item in missing_cost_items)


def test_set_cost_prints_a_plain_text_confirmation(imported: str) -> None:
    result = pg(imported, "transfers", "set-cost", "--isin", ALV, "--acquired", "2020-03-02", "--cost-eur", "800.00")
    assert result.exit_code == 0, result.output
    assert "800.00 EUR" in result.stdout
    assert "2020-03-02" in result.stdout
    assert "Closed review item" in result.stdout
    assert "missing_cost_basis" in result.stdout
    assert "Lots rebuilt" in result.stdout


def test_set_cost_with_no_transfer_in_of_the_isin_is_an_input_error(imported: str) -> None:
    other_isin = "US0378331005"
    result = pg(imported, "transfers", "set-cost", "--isin", other_isin, "--acquired", "2020-03-02", "--cost-eur", "1")
    assert result.exit_code == 1
    assert "no transfer in" in result.output


def test_set_cost_rejects_a_malformed_cost_or_date(imported: str) -> None:
    bad_cost = pg(imported, "transfers", "set-cost", "--isin", ALV, "--acquired", "2020-03-02", "--cost-eur", "abc")
    assert bad_cost.exit_code == 1
    assert "--cost-eur" in bad_cost.output

    bad_date = pg(imported, "transfers", "set-cost", "--isin", ALV, "--acquired", "not-a-date", "--cost-eur", "1")
    assert bad_date.exit_code == 1
    assert "--acquired" in bad_date.output


def test_set_cost_on_an_ambiguous_isin_needs_txn(data_dir: str, tmp_path: Path) -> None:
    two_transfers = tmp_path / "two.csv"
    rows = [
        "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;tx-1",
        "01.08.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;2;;;;;EUR;;tx-2",
    ]
    two_transfers.write_text("\n".join([CSV_HEADER, *rows]) + "\n", encoding="utf-8")
    assert pg(data_dir, "import", str(two_transfers)).exit_code == 0
    assert pg(data_dir, "accept", "latest").exit_code == 0

    ambiguous = pg(data_dir, "transfers", "set-cost", "--isin", ALV, "--acquired", "2020-03-02", "--cost-eur", "1")
    assert ambiguous.exit_code == 1
    assert "--txn" in ambiguous.output

    transfers = json.loads(pg(data_dir, "transfers", "list", "--json").stdout)["transfers"]
    first_id = min(row["id"] for row in transfers)
    named = pg(
        data_dir,
        "transfers",
        "set-cost",
        "--isin",
        ALV,
        "--acquired",
        "2020-03-02",
        "--cost-eur",
        "1",
        "--txn",
        str(first_id),
    )
    assert named.exit_code == 0, named.output


@pytest.mark.parametrize(
    "command",
    [
        ["holdings", "--help"],
        ["lots", "--help"],
        ["transfers", "--help"],
        ["transfers", "list", "--help"],
        ["transfers", "set-cost", "--help"],
    ],
)
def test_help_never_says_live(command: list[str]) -> None:
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert not re.search(r"\blive\b", result.stdout, re.IGNORECASE)
