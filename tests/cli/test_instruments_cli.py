"""Tests for the instrument master commands (cli/instruments.py, plan 5.9, AC10)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from playground.cli.main import app

REPO = Path(__file__).resolve().parents[2]
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"
MAPPING_FILE = REPO / "tests" / "fixtures" / "golden" / "inputs" / "instrument_mapping.csv"

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"

runner = CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    path = str(tmp_path / "data")
    result = runner.invoke(app, ["--data-dir", path, "init"])
    assert result.exit_code == 0, result.output
    return path


def pg(data_dir: str, *args: str, http_replay: Path | None = None):
    prefix = ["--data-dir", data_dir]
    if http_replay is not None:
        prefix += ["--http-replay", str(http_replay)]
    return runner.invoke(app, [*prefix, *args])


# --- list / map -----------------------------------------------------------------------------


def test_list_is_empty_before_anything_is_mapped(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "list")
    assert result.exit_code == 0
    assert "No instruments yet" in result.output


def test_map_one_instrument_with_explicit_options(data_dir: str) -> None:
    result = pg(
        data_dir, "instruments", "map", SAP, "--source", "stooq", "--symbol", "sap.de", "--currency", "eur", "--json"
    )

    assert result.exit_code == 0, result.output
    record = json.loads(result.stdout)
    assert record["isin"] == SAP
    assert record["data_source"] == "stooq"
    assert record["data_symbol"] == "sap.de"
    assert record["currency"] == "EUR"
    assert record["mapping_status"] == "confirmed"


def test_map_creates_the_instrument_even_though_no_document_has_named_it(data_dir: str) -> None:
    pg(data_dir, "instruments", "map", SAP, "--source", "stooq", "--symbol", "sap.de", "--currency", "EUR")

    listed = json.loads(pg(data_dir, "instruments", "list", "--json").stdout)
    assert listed["instruments"][0]["name"] == SAP


def test_map_requires_source_symbol_and_currency(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "map", SAP, "--source", "stooq")
    assert result.exit_code == 1
    assert "source" in result.output.lower() or "symbol" in result.output.lower() or "currency" in result.output.lower()


def test_map_rejects_an_invalid_isin(data_dir: str) -> None:
    result = pg(
        data_dir, "instruments", "map", "XX0000000000", "--source", "stooq", "--symbol", "x", "--currency", "EUR"
    )
    assert result.exit_code == 1
    assert "isin" in result.output.lower()


def test_map_rejects_an_unknown_source(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "map", SAP, "--source", "yahoo", "--symbol", "sap.de", "--currency", "EUR")
    assert result.exit_code == 1
    assert "source" in result.output.lower()


def test_map_file_and_isin_are_mutually_exclusive(data_dir: str) -> None:
    result = pg(
        data_dir,
        "instruments",
        "map",
        SAP,
        "--source",
        "stooq",
        "--symbol",
        "sap.de",
        "--currency",
        "EUR",
        "--file",
        str(MAPPING_FILE),
    )
    assert result.exit_code == 1
    assert "--file" in result.output


def test_map_file_applies_every_row_of_the_golden_mapping(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "map", "--file", str(MAPPING_FILE), "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["mapped"] == 5

    listed = json.loads(pg(data_dir, "instruments", "list", "--json").stdout)["instruments"]
    by_isin = {record["isin"]: record for record in listed}
    assert by_isin[SAP]["data_source"] == "stooq"
    assert by_isin[SAP]["data_symbol"] == "sap.de"
    assert by_isin[MSCIW]["data_symbol"] == "eunl.de"
    assert by_isin[AAPL]["data_symbol"] == "aapl.us"
    assert by_isin[AAPL]["currency"] == "USD"
    assert by_isin[NVDA]["data_symbol"] == "nvda.us"
    assert by_isin[ALV]["data_source"] == "manual"
    assert by_isin[ALV]["data_symbol"] == "ALV.DE"
    assert all(record["mapping_status"] == "confirmed" for record in listed)


def test_map_file_reports_a_missing_file_plainly(data_dir: str, tmp_path: Path) -> None:
    result = pg(data_dir, "instruments", "map", "--file", str(tmp_path / "nope.csv"))
    assert result.exit_code == 1
    assert "nope.csv" in result.output


# --- history --------------------------------------------------------------------------------


def test_history_reports_a_plain_error_for_a_completely_unknown_isin(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "history", SAP)
    assert result.exit_code == 1
    assert SAP in result.output


def test_history_lists_every_change(data_dir: str) -> None:
    pg(data_dir, "instruments", "map", SAP, "--source", "stooq", "--symbol", "sap.de", "--currency", "EUR")
    pg(data_dir, "instruments", "map", SAP, "--source", "manual", "--symbol", "SAP.OLD", "--currency", "EUR")

    result = pg(data_dir, "instruments", "history", SAP, "--json")
    history = json.loads(result.stdout)["history"]
    assert len(history) == 2
    assert history[0]["new"]["symbol"] == "sap.de"
    assert history[1]["old"]["symbol"] == "sap.de"
    assert history[1]["new"]["symbol"] == "SAP.OLD"


# --- suggest: stores nothing (AC10, 6.7) -----------------------------------------------------


def test_suggest_prints_listings_but_stores_nothing(data_dir: str) -> None:
    before = json.loads(pg(data_dir, "instruments", "list", "--json").stdout)

    result = pg(data_dir, "instruments", "suggest", SAP, "--json", http_replay=HTTP_FIXTURES)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["isin"] == SAP
    assert payload["suggestions"][0]["ticker"] == "SAP"
    assert payload["suggestions"][0]["stooq_symbol"] == "sap.de"

    after = json.loads(pg(data_dir, "instruments", "list", "--json").stdout)
    assert after == before  # nothing was stored, mapped or logged


def test_suggest_prints_a_plain_message_for_an_empty_result(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "suggest", "GB9999999998", http_replay=HTTP_FIXTURES)
    assert result.exit_code == 0
    assert "no listings" in result.output.lower()


def test_suggest_reports_a_rate_limit_as_an_input_error(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "suggest", "US9999999991", http_replay=HTTP_FIXTURES)
    assert result.exit_code == 1
    assert "rate" in result.output.lower()


def test_map_refuses_a_currency_it_cannot_convert_and_stores_nothing(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "map", AAPL, "--source", "stooq", "--symbol", "aapl.us", "--currency", "XYZ")

    assert result.exit_code == 1
    assert "XYZ is not a currency the app can convert to EUR" in result.output
    assert json.loads(pg(data_dir, "instruments", "list", "--json").stdout)["instruments"] == []
