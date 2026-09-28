"""API error tests (plan 5.8, 7.4, WP11: named `test_api_errors.py` rather than plan WP11's
`test_errors.py`, because `tests/core/test_errors.py` already has that basename and this test tree
has no `__init__.py` files for pytest to tell same-named modules in different directories apart).

Every error the API can return comes back as `{"error": {"code": ..., "message": ...}}`, in plain
English, never a bare traceback or FastAPI's own `{"detail": ...}` shape. 7.4 names four cases
directly (accepting twice, staging while a batch waits, an unknown batch, an upload with no files);
the rest exercise the other domain errors the services raise (plan `core/errors.py`) and a request
FastAPI's own validation rejects.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from playground.api.app import create_app
from playground.cli.main import app as cli_app
from playground.config import Settings

REPO = Path(__file__).resolve().parents[2]
INPUTS = REPO / "tests" / "fixtures" / "golden" / "inputs"
ONE_TRADE_PDF = INPUTS / "pdf" / "2024-01-15_kauf_sap.pdf"
TODAY = "2024-12-31"


def assert_error(response: Any, status_code: int, code: str) -> dict[str, Any]:
    """The response is exactly `{"error": {"code": code, "message": "..."}}` at `status_code`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]
    return body


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """The API on a freshly initialised, otherwise empty data directory."""
    data_dir = tmp_path / "data"
    result = CliRunner().invoke(cli_app, ["--data-dir", str(data_dir), "init"], env={"PG_TODAY": TODAY})
    assert result.exit_code == 0, result.output
    app = create_app(Settings(data_dir=data_dir, today=date(2024, 12, 31)))
    return TestClient(app)


# --- The four cases 7.4 names directly ----------------------------------------------------------


def test_upload_with_no_files_is_422(client: TestClient) -> None:
    assert_error(client.post("/portfolio/imports"), 422, "no_files")
    # An explicit, empty file list is the same refusal as no `files` field at all.
    assert_error(client.post("/portfolio/imports", data={"unrelated": "1"}), 422, "no_files")


def test_a_second_upload_while_one_is_staged_is_409(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    first = client.post("/portfolio/imports", files=files)
    assert first.status_code == 201

    second = client.post("/portfolio/imports", files=files)
    body = assert_error(second, 409, "batch_staged")
    assert "accept" in body["error"]["message"]
    assert "discard" in body["error"]["message"]


def test_an_unknown_batch_is_404_everywhere_a_batch_id_appears(client: TestClient) -> None:
    assert_error(client.get("/portfolio/imports/999"), 404, "not_found")
    assert_error(client.post("/portfolio/imports/999/accept"), 404, "not_found")
    assert_error(client.post("/portfolio/imports/999/discard"), 404, "not_found")
    assert_error(client.post("/portfolio/imports/999/confirmed-holdings", json={"rows": []}), 404, "not_found")


def test_accepting_twice_is_409(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    first_accept = client.post(f"/portfolio/imports/{batch_id}/accept")
    assert first_accept.status_code == 200

    second_accept = client.post(f"/portfolio/imports/{batch_id}/accept")
    assert_error(second_accept, 409, "batch_not_staged")


def test_discarding_an_accepted_batch_is_409(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]
    assert client.post(f"/portfolio/imports/{batch_id}/accept").status_code == 200

    assert_error(client.post(f"/portfolio/imports/{batch_id}/discard"), 409, "batch_not_staged")


# --- File-shape errors ----------------------------------------------------------------------------


def test_an_unsupported_file_is_422(client: TestClient) -> None:
    files = [("files", ("notes.txt", b"not an export"))]
    assert_error(client.post("/portfolio/imports", files=files), 422, "unsupported_file")


def test_an_unreadable_csv_file_is_422(client: TestClient) -> None:
    files = [("files", ("export.csv", b"Datum;Uhrzeit;Typ\n\x81\x8d\x90\n"))]
    assert_error(client.post("/portfolio/imports", files=files), 422, "unsupported_file")


# --- Instruments: a bad ISIN, an unknown one, a bad mapping field ---------------------------------


def test_a_malformed_isin_in_the_path_is_400(client: TestClient) -> None:
    assert_error(client.get("/instruments/not-an-isin/mapping-history"), 400, "invalid_request")
    assert_error(
        client.put("/instruments/not-an-isin/mapping", json={"source": "stooq", "symbol": "sap.de", "currency": "EUR"}),
        400,
        "invalid_request",
    )


def test_a_syntactically_valid_but_unknown_isin_history_is_404(client: TestClient) -> None:
    # DE0005190003 (BMW) has a correct ISIN check digit but was never named by a document or a mapping.
    assert_error(client.get("/instruments/DE0005190003/mapping-history"), 404, "not_found")


def test_an_unknown_mapping_source_is_400(client: TestClient) -> None:
    body = {"source": "yahoo", "symbol": "SAP.DE", "currency": "EUR"}
    assert_error(client.put("/instruments/DE0007164600/mapping", json=body), 400, "invalid_request")


def test_a_listing_currency_the_app_cannot_convert_is_400(client: TestClient) -> None:
    body = {"source": "stooq", "symbol": "aapl.us", "currency": "XYZ"}
    error = assert_error(client.put("/instruments/US0378331005/mapping", json=body), 400, "invalid_request")
    assert error["error"]["message"].startswith("XYZ is not a currency the app can convert to EUR.")


def test_a_mapping_request_missing_a_required_field_is_422(client: TestClient) -> None:
    # `symbol` and `currency` are required (plan schemas.MappingRequest, AC10: nothing is guessed).
    response = client.put("/instruments/DE0007164600/mapping", json={"source": "stooq"})
    assert_error(response, 422, "invalid_request")


# --- Benchmarks -------------------------------------------------------------------------------


def test_an_unknown_benchmark_is_404(client: TestClient) -> None:
    response = client.get("/benchmarks/nope/series", params={"from": "2024-01-01", "to": "2024-01-31"})
    assert_error(response, 404, "not_found")


def test_a_benchmark_series_in_a_currency_it_cannot_be_shown_in_is_a_plain_400(client: TestClient) -> None:
    # QA P0 round 2, R2-D2: the message named the internal function benchmark_series.
    response = client.get(
        "/benchmarks/msci_world_eur/series", params={"from": "2024-12-27", "to": "2024-12-31", "currency": "USD"}
    )

    body = assert_error(response, 400, "invalid_request")
    assert body["error"]["message"] == (
        "MSCI World in EUR (iShares Core MSCI World UCITS ETF on Xetra) can be shown in EUR only, not in USD."
    )


def test_a_benchmark_series_without_from_and_to_is_422(client: TestClient) -> None:
    assert_error(client.get("/benchmarks/sp500/series"), 422, "invalid_request")


# --- General request shape --------------------------------------------------------------------


def test_a_malformed_query_date_is_422(client: TestClient) -> None:
    response = client.get("/portfolio/holdings", params={"as_of": "not-a-date"})
    assert_error(response, 422, "invalid_request")


def test_an_unmatched_route_still_uses_the_error_envelope(client: TestClient) -> None:
    assert_error(client.get("/nope"), 404, "http_error")


def test_confirmed_holdings_needs_multipart_or_json(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    response = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        content=b"isin,quantity,as_of",
        headers={"content-type": "text/plain"},
    )
    assert_error(response, 400, "invalid_request")


def test_confirmed_holdings_json_rows_are_validated(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    bad_quantity = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        json={"rows": [{"isin": "DE0007164600", "quantity": "not-a-number", "as_of": "2024-12-31"}]},
    )
    assert_error(bad_quantity, 400, "invalid_request")

    bad_isin = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        json={"rows": [{"isin": "not-an-isin", "quantity": "1", "as_of": "2024-12-31"}]},
    )
    assert_error(bad_isin, 400, "invalid_request")


def test_a_bad_confirmed_holdings_csv_is_400(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    response = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        files={"file": ("bad.csv", b"isin;quantity;as_of\nDE0007164600;1.234;2024-12-31\n")},
    )
    assert_error(response, 400, "invalid_request")
    assert "can be read as 1234 or as 1.234" in response.json()["error"]["message"]


def test_a_confirmed_holdings_csv_without_the_decimal_line_is_read(client: TestClient) -> None:
    # QA P0 round 2, R2-D1: the layout the page's hint describes, with no "# decimal=" line.
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    response = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings",
        files={"file": ("confirmed.csv", b"isin;quantity;as_of\nDE0007164600;10;2024-12-31\n")},
    )

    assert response.status_code == 200, response.json()
    rows = response.json()["confirmed"]["rows"]
    assert [(row["isin"], row["confirmed"]) for row in rows] == [("DE0007164600", "10")]


# --- A data directory nobody ran `pg init` on ---------------------------------------------------


def test_before_pg_init_every_portfolio_route_says_so_plainly(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", today=date(2024, 12, 31)))
    client = TestClient(app)

    # /health needs no portfolio: it is true before pg init and after it.
    assert client.get("/health").status_code == 200

    body = assert_error(client.get("/portfolio"), 400, "not_initialised")
    assert "pg init" in body["error"]["message"]
    assert_error(client.get("/portfolio/holdings"), 400, "not_initialised")
    assert_error(client.get("/instruments"), 400, "not_initialised")


# --- A request FastAPI cannot read: one plain sentence per problem (QA P0 round 1, M6) -----------


def assert_plain(message: str) -> None:
    """No pydantic dump, no server path, no line number, no link."""
    for leak in ("File ", ".py", "line ", "/home/", "validation error", "{'type'", "pydantic"):
        assert leak not in message, (leak, message)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("/portfolio/value?from=2024-13-01", "The from date 2024-13-01 could not be read. Use YYYY-MM-DD."),
        ("/portfolio/holdings?as_of=garbage", "The as_of date garbage could not be read. Use YYYY-MM-DD."),
        (
            "/benchmarks/sp500/series",
            "The from query parameter is missing. The to query parameter is missing.",
        ),
        (
            "/portfolio/transactions?limit=0",
            "The limit value 0 is not valid: it should be greater than or equal to 1.",
        ),
        ("/portfolio/transactions?limit=abc", "The limit value abc is not a whole number."),
    ],
    ids=["bad_date", "not_a_date", "missing", "out_of_range", "not_a_number"],
)
def test_an_unreadable_query_value_is_one_plain_sentence(client: TestClient, url: str, message: str) -> None:
    body = assert_error(client.get(url), 422, "invalid_request")

    assert body["error"]["message"] == message
    assert_plain(body["error"]["message"])


def test_a_mapping_body_with_missing_fields_is_one_plain_sentence_per_field(client: TestClient) -> None:
    body = assert_error(
        client.put("/instruments/DE0007164600/mapping", json={"source": "stooq"}), 422, "invalid_request"
    )

    assert body["error"]["message"] == "The request body has no symbol. The request body has no currency."


def test_a_body_that_is_not_json_says_so(client: TestClient) -> None:
    response = client.put(
        "/instruments/DE0007164600/mapping", content=b"not json", headers={"content-type": "application/json"}
    )

    body = assert_error(response, 422, "invalid_request")
    assert body["error"]["message"] == "The request body is not valid JSON."


def test_a_confirmed_holdings_row_without_its_isin_is_a_plain_400(client: TestClient) -> None:
    files = [("files", (ONE_TRADE_PDF.name, ONE_TRADE_PDF.read_bytes()))]
    batch_id = client.post("/portfolio/imports", files=files).json()["batch"]["id"]

    response = client.post(
        f"/portfolio/imports/{batch_id}/confirmed-holdings", json={"rows": [{"quantity": "10", "as_of": "2024-12-31"}]}
    )

    body = assert_error(response, 400, "invalid_request")
    assert body["error"]["message"] == "The request body has no isin."
    assert_plain(body["error"]["message"])
