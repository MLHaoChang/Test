"""API contract tests (plan 5.8, 7.4, AC12, AC16, WP11).

Builds the golden portfolio (plan 1.2) once per module with the CLI (`typer.testing.CliRunner`,
the way `tests/golden/test_golden_portfolio.py` does), then serves that same data directory through
`api.app.create_app` with FastAPI's `TestClient` (an in-process ASGI transport: no socket opens,
so this runs under `--disable-socket` like the rest of the suite) and checks that the API's read
endpoints return exactly what the CLI's own `--json` output, and the golden files of
`tests/fixtures/golden/expected/`, already say.

`test_api_errors.py` covers the error envelope and the write endpoints' failure modes (409, 404,
422). It is not named `test_errors.py`, the name plan WP11 gives it, because `tests/core/` already
has a module of that name (`core/errors.py`'s own tests) and this project's test tree has no
`__init__.py` files, so pytest's default import mode needs every test module's basename to be
unique across the whole tree.
"""

import importlib.util
import json
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from playground.api.app import create_app
from playground.cli.main import app as cli_app
from playground.config import Settings

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "golden"
INPUTS = GOLDEN / "inputs"
EXPECTED = GOLDEN / "expected"
ROUND_ONE = [INPUTS / "tr_transactions_2024.csv", *sorted((INPUTS / "pdf").glob("*.pdf"))]
MAPPING = INPUTS / "instrument_mapping.csv"
MANUAL_PRICES = INPUTS / "manual_prices_allianz.csv"
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"
TODAY = "2024-12-31"

OPENAPI_PATHS_GOLDEN = Path(__file__).parent / "openapi_paths.json"
#: 7.4: "no path contains order, trade, broker or execute" (spec 1.5: no order path anywhere).
FORBIDDEN_PATH_WORDS = ("order", "trade", "broker", "execute")


def load_assert_golden() -> ModuleType:
    """The comparer of `scripts/assert_golden.py`, loaded as a module (as `test_golden_portfolio.py` does)."""
    if "assert_golden" in __import__("sys").modules:
        return __import__("sys").modules["assert_golden"]
    spec = importlib.util.spec_from_file_location("assert_golden", REPO / "scripts" / "assert_golden.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules["assert_golden"] = module
    spec.loader.exec_module(module)
    return module


def assert_matches_golden(actual: Any, golden_name: str) -> None:
    """`actual` structurally equals `tests/fixtures/golden/expected/<golden_name>`, ids and timestamps ignored."""
    ag = load_assert_golden()
    expected = json.loads((EXPECTED / golden_name).read_text(encoding="utf-8"))
    differences = ag.compare_values(ag.normalize_value(expected), ag.normalize_value(actual))
    assert not differences, "\n".join(differences)


def assert_no_floats(value: Any, *, path: str = "$") -> None:
    """Every money, quantity and price field is a decimal string, never a JSON float (plan 3.3, 5.8).

    A `float` can only reach a `TestClient` response as a genuine JSON number with a decimal point
    or exponent; every count in these responses is a plain integer, so this walk never has to name
    an exception.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        raise AssertionError(f"{path}: found a float ({value!r}); money and quantities must be strings.")
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_floats(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_floats(item, path=f"{path}[{index}]")


def _pg(runner: CliRunner, data_dir: Path, *args: str, today: str = TODAY) -> Any:
    result = runner.invoke(cli_app, ["--data-dir", str(data_dir), *args], env={"PG_TODAY": today})
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture(scope="module")
def golden_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The golden portfolio (plan 1.2), imported, accepted, cost-entered, mapped and priced.

    Built once per module with the CLI, the same way the end-to-end scenario builds it (7.6 steps 4
    to 23), so the read endpoints below have real data to agree with the CLI and the golden files.
    """
    data_dir = tmp_path_factory.mktemp("api_contract") / "data"
    runner = CliRunner()
    _pg(runner, data_dir, "init")
    _pg(runner, data_dir, "import", *[str(path) for path in ROUND_ONE])
    _pg(runner, data_dir, "accept", "latest")
    _pg(
        runner,
        data_dir,
        "transfers",
        "set-cost",
        "--isin",
        "DE0008404005",
        "--acquired",
        "2020-03-02",
        "--cost-eur",
        "800.00",
    )
    _pg(runner, data_dir, "instruments", "map", "--file", str(MAPPING))
    _pg(
        runner,
        data_dir,
        "--http-replay",
        str(HTTP_FIXTURES),
        "prices",
        "fetch",
        "--from",
        "2024-01-01",
        "--to",
        "2024-12-31",
    )
    _pg(runner, data_dir, "prices", "import-file", str(MANUAL_PRICES))
    _pg(runner, data_dir, "--http-replay", str(HTTP_FIXTURES), "fx", "fetch")
    _pg(
        runner,
        data_dir,
        "--http-replay",
        str(HTTP_FIXTURES),
        "benchmarks",
        "fetch",
        "--from",
        "2024-01-01",
        "--to",
        "2024-12-31",
    )
    return data_dir


@pytest.fixture(scope="module")
def client(golden_data_dir: Path) -> TestClient:
    """The API of plan 5.8 on the built golden portfolio, with the clock fixed on 2024-12-31."""
    app = create_app(Settings(data_dir=golden_data_dir, today=date(2024, 12, 31)))
    return TestClient(app)


# --- The contract itself: paths, health, no order path --------------------------------------


def test_openapi_paths_match_the_golden_list(client: TestClient) -> None:
    paths = sorted(client.get("/openapi.json").json()["paths"])
    golden = json.loads(OPENAPI_PATHS_GOLDEN.read_text(encoding="utf-8"))
    assert paths == golden


@pytest.mark.parametrize("path", json.loads(OPENAPI_PATHS_GOLDEN.read_text(encoding="utf-8")))
def test_no_path_reads_like_placing_an_order(path: str) -> None:
    lowered = path.lower()
    assert not any(word in lowered for word in FORBIDDEN_PATH_WORDS), path


def test_health_says_no_real_orders(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["real_orders"] is False
    assert body["portfolio_read_only"] is True
    assert_no_floats(body)


# --- GET /portfolio: the reminder (AC16) ------------------------------------------------------


def test_portfolio_is_not_due_for_a_reminder_right_after_import(client: TestClient) -> None:
    response = client.get("/portfolio")
    assert response.status_code == 200
    body = response.json()
    assert body["portfolio"] == {"name": "Trade Republic", "base_currency": "EUR", "source": "trade_republic"}
    assert body["reminder"]["due"] is False
    assert body["open_review_items"] == 1  # the unknown layout; missing_cost_basis was resolved
    assert_no_floats(body)


def test_portfolio_reminder_is_due_34_days_after_the_last_import(golden_data_dir: Path) -> None:
    """AC16, e2e step 31: the same 30-day reminder `pg status` shows, from `GET /portfolio`."""
    app = create_app(Settings(data_dir=golden_data_dir, today=date(2025, 2, 3)))
    response = TestClient(app).get("/portfolio")
    assert response.status_code == 200
    assert_matches_golden(response.json(), "status_2025-02-03.json")


# --- Read endpoints match the CLI's own golden files (AC12) -----------------------------------


def test_holdings_matches_the_cli_golden(client: TestClient) -> None:
    response = client.get("/portfolio/holdings", params={"as_of": "2024-12-31"})
    assert response.status_code == 200
    assert_no_floats(response.json())
    assert_matches_golden(response.json(), "holdings_2024-12-31.json")


def test_lots_matches_the_cli_golden(client: TestClient) -> None:
    response = client.get("/portfolio/lots")
    assert response.status_code == 200
    assert_no_floats(response.json())
    assert_matches_golden(response.json(), "lots.json")


def test_value_matches_the_cli_golden(client: TestClient) -> None:
    response = client.get("/portfolio/value", params={"from": "2024-01-02", "to": "2024-12-31"})
    assert response.status_code == 200
    assert_no_floats(response.json())
    assert_matches_golden(response.json(), "value.json")


def test_instruments_matches_the_cli_golden(client: TestClient) -> None:
    response = client.get("/instruments")
    assert response.status_code == 200
    assert_matches_golden(response.json(), "instruments.json")


def test_benchmark_series_matches_the_cli_golden(client: TestClient) -> None:
    response = client.get(
        "/benchmarks/sp500/series", params={"from": "2024-01-02", "to": "2024-12-31", "currency": "EUR"}
    )
    assert response.status_code == 200
    assert_no_floats(response.json())
    assert_matches_golden(response.json(), "sp500_eur.json")


def test_benchmarks_lists_both_configured_benchmarks(client: TestClient) -> None:
    body = client.get("/benchmarks").json()
    assert {benchmark["id"] for benchmark in body["benchmarks"]} == {"msci_world_eur", "sp500"}


def test_review_list_defaults_to_open_and_all_shows_the_resolved_one_too(client: TestClient) -> None:
    open_items = client.get("/portfolio/review").json()
    assert open_items["status"] == "open"
    assert [item["kind"] for item in open_items["items"]] == ["unknown_layout"]

    every_item = client.get("/portfolio/review", params={"status": "all"}).json()
    assert every_item["count"] == 2
    assert {item["kind"] for item in every_item["items"]} == {"unknown_layout", "missing_cost_basis"}
    assert next(i for i in every_item["items"] if i["kind"] == "missing_cost_basis")["status"] == "resolved"


def test_transactions_filters_by_type_and_date_and_paginates(client: TestClient) -> None:
    all_transactions = client.get("/portfolio/transactions").json()
    assert all_transactions["count"] == 14

    buys_in_january = client.get(
        "/portfolio/transactions", params={"type": "buy", "from": "2024-01-01", "to": "2024-01-31"}
    ).json()
    assert [txn["source_ref"] for txn in buys_in_january["transactions"]] == ["5d0a-93f1"]

    first_two = client.get("/portfolio/transactions", params={"limit": 2}).json()
    assert len(first_two["transactions"]) == 2
    assert_no_floats(first_two)

    unknown_type = client.get("/portfolio/transactions", params={"type": "nope"})
    assert unknown_type.status_code == 400


def test_instrument_mapping_history_lists_the_file_mapping(client: TestClient) -> None:
    body = client.get("/instruments/DE0007164600/mapping-history").json()
    assert body["isin"] == "DE0007164600"
    assert len(body["history"]) == 1
    assert body["history"][0]["changed_by"] == "file"
    assert body["history"][0]["new"] == {"source": "stooq", "symbol": "sap.de", "currency": "EUR"}


# --- The write path: an upload through the API matches `pg import`, and accept matches `pg accept` -


@pytest.fixture
def fresh_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    CliRunner().invoke(cli_app, ["--data-dir", str(data_dir), "init"], env={"PG_TODAY": TODAY})
    return data_dir


def test_uploading_the_golden_files_yields_the_same_diff_as_pg_import(fresh_data_dir: Path) -> None:
    app = create_app(Settings(data_dir=fresh_data_dir, today=date(2024, 12, 31)))
    client = TestClient(app)

    files = [("files", (path.name, path.read_bytes())) for path in ROUND_ONE]
    response = client.post("/portfolio/imports", files=files)

    assert response.status_code == 201
    diff = response.json()
    assert_no_floats(diff)
    assert_matches_golden(diff, "import1.json")


def test_accept_holdings_lots_and_value_match_the_cli_after_an_api_upload(fresh_data_dir: Path) -> None:
    """The rest of AC12: once an API-staged batch is accepted, holdings, lots and value read back
    exactly as the CLI's own golden files say, the same as `test_holdings_matches_the_cli_golden`
    and its neighbours above do for a CLI-built portfolio."""
    app = create_app(Settings(data_dir=fresh_data_dir, today=date(2024, 12, 31)))
    client = TestClient(app)
    files = [("files", (path.name, path.read_bytes())) for path in ROUND_ONE]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    accept_response = client.post(f"/portfolio/imports/{batch_id}/accept")
    assert accept_response.status_code == 200
    accepted = accept_response.json()
    assert_no_floats(accepted)
    assert accepted["accepted"] == 14
    assert (
        accepted["lots"] == 7
    )  # SAP x2, MSCIW x2, AAPL, NVDA, the ALV transfer (tests/fixtures/golden/expected/lots.json)
    assert accepted["disposals"] == 2  # T12 consumes the first SAP lot fully, the second partially

    # The rest of the golden portfolio (cost basis, mapping, prices and rates) is CLI-only surface
    # (plan 5.8 has no endpoint for any of it); finish building it with the CLI, on the same
    # directory the API just wrote to, exactly as a real session would mix `pg serve` with `pg`.
    runner = CliRunner()
    _pg(
        runner,
        fresh_data_dir,
        "transfers",
        "set-cost",
        "--isin",
        "DE0008404005",
        "--acquired",
        "2020-03-02",
        "--cost-eur",
        "800.00",
    )
    _pg(runner, fresh_data_dir, "instruments", "map", "--file", str(MAPPING))
    _pg(
        runner,
        fresh_data_dir,
        "--http-replay",
        str(HTTP_FIXTURES),
        "prices",
        "fetch",
        "--from",
        "2024-01-01",
        "--to",
        "2024-12-31",
    )
    _pg(runner, fresh_data_dir, "prices", "import-file", str(MANUAL_PRICES))
    _pg(runner, fresh_data_dir, "--http-replay", str(HTTP_FIXTURES), "fx", "fetch")

    assert_matches_golden(
        client.get("/portfolio/holdings", params={"as_of": "2024-12-31"}).json(), "holdings_2024-12-31.json"
    )
    assert_matches_golden(client.get("/portfolio/lots").json(), "lots.json")
    assert_matches_golden(
        client.get("/portfolio/value", params={"from": "2024-01-02", "to": "2024-12-31"}).json(), "value.json"
    )


def test_confirmed_holdings_accepts_a_csv_upload_and_a_json_body(fresh_data_dir: Path) -> None:
    app = create_app(Settings(data_dir=fresh_data_dir, today=date(2024, 12, 31)))
    client = TestClient(app)
    files = [("files", (path.name, path.read_bytes())) for path in ROUND_ONE]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]
    confirmed_csv = INPUTS / "confirmed_holdings_2024-12-31.csv"

    from_csv = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        files={"file": ("confirmed.csv", confirmed_csv.read_bytes())},
    )
    assert from_csv.status_code == 200
    assert_no_floats(from_csv.json())
    assert_matches_golden(from_csv.json(), "reconcile.json")

    from_json = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        json={"as_of": "2024-12-31", "rows": [{"isin": "DE0007164600", "quantity": "3", "as_of": "2024-12-31"}]},
    )
    assert from_json.status_code == 200
    # One row given (SAP, a match) plus one `missing_in_confirmed` row for each of the other four
    # ISINs the staged batch would leave held (5.3.6): "1 of 5 match", not "1 of 1".
    assert from_json.json()["confirmed"]["message"] == "1 of 5 match"
