"""Tests for the market-data commands (cli/marketdata.py, plan 5.9): `prices`, `fx`, `benchmarks`.

Every command that reaches outside the machine runs here with `--http-replay tests/fixtures/http`
(plan 5.4); nothing in this file, or anywhere else in the suite, touches the real network
(`tests/http/test_no_network.py`).
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from playground.cli.main import app

REPO = Path(__file__).resolve().parents[2]
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"
MAPPING_FILE = REPO / "tests" / "fixtures" / "golden" / "inputs" / "instrument_mapping.csv"
MANUAL_ALLIANZ_FILE = REPO / "tests" / "fixtures" / "golden" / "inputs" / "manual_prices_allianz.csv"

SAP = "DE0007164600"
AAPL = "US0378331005"
ALV = "DE0008404005"

runner = CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    path = str(tmp_path / "data")
    result = runner.invoke(app, ["--data-dir", path, "init"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["--data-dir", path, "instruments", "map", "--file", str(MAPPING_FILE)])
    assert result.exit_code == 0, result.output
    return path


def pg(data_dir: str, *args: str, http_replay: Path | None = None):
    prefix = ["--data-dir", data_dir]
    if http_replay is not None:
        prefix += ["--http-replay", str(http_replay)]
    return runner.invoke(app, [*prefix, *args])


# --- prices -----------------------------------------------------------------------------------


def test_prices_fetch_stores_every_stooq_mapped_instrument(data_dir: str) -> None:
    result = pg(
        data_dir, "prices", "fetch", "--from", "2024-01-01", "--to", "2024-12-31", "--json", http_replay=HTTP_FIXTURES
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    fetched = {row["isin"]: row for row in payload["fetched"]}
    assert set(fetched) == {SAP, "IE00B4L5Y983", AAPL, "US67066G1040"}  # ALV is manual, never fetched
    assert fetched[SAP]["points"] > 0


def test_prices_fetch_with_isin_fetches_only_that_one(data_dir: str) -> None:
    result = pg(data_dir, "prices", "fetch", "--from", "2024-01-01", "--isin", SAP, "--json", http_replay=HTTP_FIXTURES)
    payload = json.loads(result.stdout)
    assert [row["isin"] for row in payload["fetched"]] == [SAP]


def test_prices_fetch_rejects_an_isin_not_mapped_to_stooq(data_dir: str) -> None:
    result = pg(data_dir, "prices", "fetch", "--from", "2024-01-01", "--isin", ALV, http_replay=HTTP_FIXTURES)
    assert result.exit_code == 1
    assert "stooq" in result.output.lower()


def test_prices_fetch_requires_from(data_dir: str) -> None:
    result = pg(data_dir, "prices", "fetch", http_replay=HTTP_FIXTURES)
    assert result.exit_code == 1
    assert "--from" in result.output


def test_prices_import_file_stores_the_manual_series(data_dir: str) -> None:
    result = pg(data_dir, "prices", "import-file", str(MANUAL_ALLIANZ_FILE), "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["series"][0]["symbol"] == "ALV.DE"
    assert payload["series"][0]["points"] == 132


def test_prices_import_file_reports_a_bad_file_plainly(data_dir: str, tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("nope\n", encoding="utf-8")
    result = pg(data_dir, "prices", "import-file", str(bad))
    assert result.exit_code == 1


def test_prices_show_after_fetch(data_dir: str) -> None:
    pg(data_dir, "prices", "fetch", "--from", "2024-01-01", "--isin", SAP, http_replay=HTTP_FIXTURES)

    result = pg(data_dir, "prices", "show", "sap.de", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    stooq_points = payload["series"]["stooq"]
    # Parquet's decimal128(20, 8) columns round-trip the *value*, padded to 8 decimal places
    # (lake.py); "170.00" and "170.00000000" are the same close, plan 1.2's valuation anchor.
    assert any(point["date"] == "2024-05-31" and point["close"] == "170.00000000" for point in stooq_points)


def test_prices_show_reports_an_unknown_symbol_plainly(data_dir: str) -> None:
    result = pg(data_dir, "prices", "show", "nope.de")
    assert result.exit_code == 1


# --- fx ---------------------------------------------------------------------------------------


def test_fx_fetch_stores_the_ecb_rates(data_dir: str) -> None:
    result = pg(data_dir, "fx", "fetch", "--json", http_replay=HTTP_FIXTURES)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["points"] > 0


def test_fx_show_after_fetch_and_the_staleness_boundary(data_dir: str) -> None:
    pg(data_dir, "fx", "fetch", http_replay=HTTP_FIXTURES)

    on_the_25th = json.loads(pg(data_dir, "fx", "show", "--currency", "USD", "--on", "2024-12-25", "--json").stdout)
    # A date well past the fixture's last rate (2024-12-31, TARGET's last trading day of 2024)
    # so the as-of rate is unambiguously more than 5 calendar days old.
    long_after = json.loads(pg(data_dir, "fx", "show", "--currency", "USD", "--on", "2025-01-10", "--json").stdout)

    # plan 1.2: on 2024-12-25 SAP uses "the ECB rate from 2024-12-24" (25 Dec is a TARGET
    # holiday, so the as-of rate is one calendar day old and never flagged as stale).
    assert on_the_25th["date"] == "2024-12-24"
    assert on_the_25th["stale"] is False
    assert long_after["stale"] is True


def test_fx_import_file_reads_the_na_fixture(data_dir: str) -> None:
    result = pg(data_dir, "fx", "import-file", str(HTTP_FIXTURES / "ecb" / "eurofxref-hist-na.csv"), "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["points"] > 0


def test_fx_show_before_any_fetch_is_a_plain_error(data_dir: str) -> None:
    result = pg(data_dir, "fx", "show", "--currency", "USD")
    assert result.exit_code == 1
    assert "pg fx fetch" in result.output


# --- benchmarks ---------------------------------------------------------------------------------


def test_benchmarks_fetch_stores_both_benchmarks(data_dir: str) -> None:
    result = pg(
        data_dir,
        "benchmarks",
        "fetch",
        "--from",
        "2024-01-01",
        "--to",
        "2024-12-31",
        "--json",
        http_replay=HTTP_FIXTURES,
    )
    assert result.exit_code == 0, result.output
    counts = json.loads(result.stdout)["fetched"]
    assert set(counts) == {"msci_world_eur", "sp500"}
    assert all(count > 0 for count in counts.values())


def test_benchmarks_series_in_usd_needs_no_fx_fetch(data_dir: str) -> None:
    pg(data_dir, "benchmarks", "fetch", "--from", "2024-01-01", "--to", "2024-12-31", http_replay=HTTP_FIXTURES)

    result = pg(data_dir, "benchmarks", "series", "sp500", "--from", "2024-01-02", "--to", "2024-12-31", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["currency"] == "USD"
    assert payload["points"][0]["value"] == "4700.00000000"


def test_benchmarks_series_unknown_id_is_a_plain_error(data_dir: str) -> None:
    result = pg(data_dir, "benchmarks", "series", "nasdaq", "--from", "2024-01-01", "--to", "2024-12-31")
    assert result.exit_code == 1
    assert "nasdaq" in result.output.lower()


def test_benchmarks_series_requires_from_and_to(data_dir: str) -> None:
    result = pg(data_dir, "benchmarks", "series", "sp500")
    assert result.exit_code == 1


# --- A fetch that fails: reported per symbol, the rest still fetched (QA P0 round 1, D3) ----------


def _refuse(request):
    import httpx

    raise httpx.ConnectError("[Errno 111] Connection refused", request=request)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every `pg` command below gets a network client whose connections are refused (no real socket)."""
    import httpx

    import playground.cli.main as cli_main
    from playground.http.client import NetworkHttpClient

    monkeypatch.setattr(
        cli_main,
        "make_http_client",
        lambda settings: NetworkHttpClient(transport=httpx.MockTransport(_refuse), sleep=lambda seconds: None),
    )


def _map_allianz_to_an_unrecorded_stooq_symbol(data_dir: str) -> None:
    result = pg(data_dir, "instruments", "map", ALV, "--source", "stooq", "--symbol", "alv.de", "--currency", "EUR")
    assert result.exit_code == 0, result.output


def test_prices_fetch_reports_an_unrecorded_symbol_and_still_fetches_the_rest(data_dir: str) -> None:
    _map_allianz_to_an_unrecorded_stooq_symbol(data_dir)

    result = pg(data_dir, "prices", "fetch", "--from", "2024-01-01", "--to", "2024-12-31", http_replay=HTTP_FIXTURES)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    lines = result.stdout.splitlines()
    assert next(line for line in lines if line.startswith(ALV)).startswith(f"{ALV} (alv.de): No recorded response")
    # The instruments after the failing one (ISIN order) are fetched too.
    for isin in (SAP, "IE00B4L5Y983", AAPL, "US67066G1040"):
        assert "daily closes stored" in next(line for line in lines if line.startswith(isin))
    assert "1 of 5 instruments could not be fetched." in result.stderr
    assert pg(data_dir, "prices", "show", "nvda.us").exit_code == 0


def test_prices_fetch_json_lists_the_failure_and_exits_1(data_dir: str) -> None:
    _map_allianz_to_an_unrecorded_stooq_symbol(data_dir)

    result = pg(
        data_dir, "prices", "fetch", "--from", "2024-01-01", "--to", "2024-12-31", "--json", http_replay=HTTP_FIXTURES
    )

    assert result.exit_code == 1
    fetched = {row["isin"]: row for row in json.loads(result.stdout)["fetched"]}
    assert fetched[ALV]["error"].startswith("No recorded response")
    assert fetched[SAP]["points"] > 0


def test_prices_fetch_without_a_network_reports_every_symbol_plainly(data_dir: str, no_network: None) -> None:
    result = pg(data_dir, "prices", "fetch", "--from", "2024-12-01", "--to", "2024-12-31")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert f"{SAP} (sap.de): Could not reach stooq.com to fetch sap.de: the connection failed" in result.stdout
    assert "Check your connection, or load its closes from a file with pg prices import-file." in result.stdout
    assert "4 of 4 instruments could not be fetched." in result.stderr
    assert "Traceback" not in result.output


def test_fx_fetch_without_a_network_is_one_plain_message(data_dir: str, no_network: None) -> None:
    result = pg(data_dir, "fx", "fetch")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.stderr.strip() == (
        "Could not reach www.ecb.europa.eu to fetch the ECB rates: the connection failed ([Errno 111] Connection "
        "refused). Check your connection, or load the rates CSV from a file with pg fx import-file."
    )


def test_benchmarks_fetch_reports_each_benchmark_that_could_not_be_fetched(data_dir: str, no_network: None) -> None:
    result = pg(data_dir, "benchmarks", "fetch", "--from", "2024-12-01", "--to", "2024-12-31")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "msci_world_eur: Could not reach stooq.com to fetch eunl.de" in result.stdout
    assert "sp500: Could not reach stooq.com to fetch ^spx" in result.stdout
    assert "2 of 2 benchmarks could not be fetched." in result.stderr
    # A benchmark is read from its configured series, so the manual price file is no way out here.
    assert "import-file" not in result.output


def test_benchmarks_fetch_json_lists_the_errors(data_dir: str) -> None:
    result = pg(
        data_dir,
        "benchmarks",
        "fetch",
        "--from",
        "2024-01-01",
        "--to",
        "2024-12-31",
        "--json",
        http_replay=HTTP_FIXTURES,
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["errors"] == {}
