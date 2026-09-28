"""QA phase P0, round 1, D3: a network failure in a fetch command ends in a Python traceback.

The UAT guide (docs/uat/P0-macos.md, step 8) runs `pg prices fetch`, `pg fx fetch` and
`pg benchmarks fetch` against the real network on your Mac. When the connection fails (no
network, a DNS error, a proxy that refuses, a timeout after the retries), `NetworkHttpClient`
lets the httpx exception escape and every one of these commands prints a traceback instead of a
plain-English message. Plan 6.4 says a source that cannot be reached is `SourceUnavailable`, with
a message that points to the manual price file.

The test builds a `NetworkHttpClient` on an httpx mock transport that fails to connect, so no
socket is ever opened. QA wrote it as a strict xfail while the defect was open; D3 is fixed, the
marker is gone, and it now guards the fix (see also `test_marketdata_cli.py`).
"""

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import playground.cli.main as cli_main
from playground.cli.main import app
from playground.http.client import NetworkHttpClient

REPO = Path(__file__).resolve().parents[2]
MAPPING_FILE = REPO / "tests" / "fixtures" / "golden" / "inputs" / "instrument_mapping.csv"

runner = CliRunner()


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 111] Connection refused", request=request)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    path = str(tmp_path / "data")
    assert runner.invoke(app, ["--data-dir", path, "init"]).exit_code == 0
    result = runner.invoke(app, ["--data-dir", path, "instruments", "map", "--file", str(MAPPING_FILE)])
    assert result.exit_code == 0, result.output
    monkeypatch.setattr(
        cli_main,
        "make_http_client",
        lambda settings: NetworkHttpClient(transport=httpx.MockTransport(_refuse), sleep=lambda seconds: None),
    )
    return path


@pytest.mark.parametrize(
    "command",
    [
        ["prices", "fetch", "--from", "2024-12-01", "--to", "2024-12-31"],
        ["fx", "fetch"],
        ["benchmarks", "fetch", "--from", "2024-12-01", "--to", "2024-12-31"],
        ["instruments", "suggest", "US0378331005"],
    ],
    ids=["prices", "fx", "benchmarks", "suggest"],
)
def test_a_connection_error_is_a_plain_message_not_a_traceback(data_dir: str, command: list[str]) -> None:
    result = runner.invoke(app, ["--data-dir", data_dir, *command])

    assert result.exit_code == 1
    # A handled failure exits through typer (SystemExit); an unhandled one leaves the httpx error here.
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert result.output.strip() != ""
