"""Tests for the import commands (cli/imports.py, cli/review.py, plan 5.9).

`pg import FILE...` stages one batch and prints the diff; `pg imports list` and `pg imports show
BATCH` show batches (`latest` is accepted for BATCH); `pg reconcile BATCH` adds the comparison
with your confirmed holdings; `pg accept` and `pg discard` settle a staged batch; `pg review`
lists, shows, dismisses and resolves review items; `pg transactions` lists the transactions; `pg
status` shows the portfolio and the reminder to import again. Every read command takes `--json`.
Exit status: 0 on success, 1 on a usage or input error, 3 when `reconcile --strict` finds a
mismatch.
"""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from playground.cli.main import app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INPUTS = FIXTURES / "golden" / "inputs"
T2 = INPUTS / "pdf" / "2024-01-15_kauf_sap.pdf"
UNKNOWN = INPUTS / "pdf" / "unbekannt_kosteninformation.pdf"
CSV = INPUTS / "tr_transactions_2024.csv"
AMOUNTS_OFF = FIXTURES / "tr" / "pdf" / "tr.wertpapierabrechnung.de.2023" / "amounts_off_by_one_euro.pdf"

runner = CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    path = str(tmp_path / "data")
    result = runner.invoke(app, ["--data-dir", path, "init"])
    assert result.exit_code == 0, result.output
    return path


def pg(data_dir: str, *args: str):
    return runner.invoke(app, ["--data-dir", data_dir, *args])


def test_import_prints_the_diff_and_what_to_do_next(data_dir: str) -> None:
    result = pg(data_dir, "import", str(T2), str(CSV))

    assert result.exit_code == 0, result.output
    assert "not accepted yet" in result.stdout
    assert "pg accept 1" in result.stdout
    assert "pg discard 1" in result.stdout
    assert re.search(r"\b11 new transactions\b", result.stdout)
    assert "tr_transactions_2024.csv" in result.stdout


def test_import_json_is_the_diff(data_dir: str) -> None:
    result = pg(data_dir, "import", str(T2), "--json")

    assert result.exit_code == 0, result.output
    diff = json.loads(result.stdout)
    assert diff["counts"]["new"] == 1
    assert diff["batch"]["status"] == "staged"
    assert diff["new"][0]["source_ref"] == "5d0a-93f1"


def test_import_without_files_is_an_input_error(data_dir: str) -> None:
    result = pg(data_dir, "import")
    assert result.exit_code == 1
    assert "file" in result.output.lower()


def test_import_of_a_missing_or_unsupported_file_is_an_input_error(data_dir: str, tmp_path: Path) -> None:
    missing = pg(data_dir, "import", str(tmp_path / "nope.pdf"))
    notes = tmp_path / "notes.txt"
    notes.write_text("not an export\n")
    unsupported = pg(data_dir, "import", str(notes))

    assert missing.exit_code == 1
    assert "nope.pdf" in missing.output
    assert unsupported.exit_code == 1
    assert "notes.txt" in unsupported.output
    assert json.loads(pg(data_dir, "imports", "list", "--json").stdout)["batches"] == []


def test_a_second_import_while_one_is_staged_is_refused(data_dir: str) -> None:
    assert pg(data_dir, "import", str(T2)).exit_code == 0
    result = pg(data_dir, "import", str(CSV))

    assert result.exit_code == 1
    assert "accept" in result.output
    assert "discard" in result.output


def test_commands_before_init_ask_you_to_run_pg_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    result = runner.invoke(app, ["--data-dir", str(tmp_path / "empty"), "status"])
    assert result.exit_code == 1
    assert "pg init" in result.output


def test_accept_and_discard_take_a_batch_number_or_latest(data_dir: str) -> None:
    assert pg(data_dir, "import", str(T2)).exit_code == 0
    accepted = pg(data_dir, "accept", "latest", "--json")
    assert accepted.exit_code == 0, accepted.output
    assert json.loads(accepted.stdout)["accepted"] == 1

    again = pg(data_dir, "accept", "1")
    assert again.exit_code == 1
    assert "already accepted" in again.output

    unknown = pg(data_dir, "accept", "99")
    assert unknown.exit_code == 1
    assert "99" in unknown.output

    assert pg(data_dir, "import", str(CSV)).exit_code == 0
    discarded = pg(data_dir, "discard", "latest", "--json")
    assert discarded.exit_code == 0, discarded.output
    batches = json.loads(pg(data_dir, "imports", "list", "--json").stdout)["batches"]
    assert [(batch["id"], batch["status"]) for batch in batches] == [(1, "accepted"), (2, "discarded")]


def test_imports_show_prints_a_stored_diff(data_dir: str) -> None:
    assert pg(data_dir, "import", str(T2), str(CSV)).exit_code == 0
    shown = pg(data_dir, "imports", "show", "latest", "--json")

    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["counts"]["merged"] == 1
    assert pg(data_dir, "imports", "show", "7").exit_code == 1


def test_reconcile_strict_exits_3_on_a_mismatch(data_dir: str, tmp_path: Path) -> None:
    assert pg(data_dir, "import", str(T2)).exit_code == 0
    confirmed = tmp_path / "confirmed.csv"
    confirmed.write_text("# decimal=,\nisin;quantity;as_of\nDE0007164600;12;2024-12-31\n", encoding="utf-8")

    lenient = pg(data_dir, "reconcile", "latest", "--confirmed", str(confirmed))
    strict = pg(data_dir, "reconcile", "latest", "--confirmed", str(confirmed), "--strict", "--json")

    assert lenient.exit_code == 0, lenient.output
    assert "0 of 1 match" in lenient.stdout
    assert strict.exit_code == 3
    assert json.loads(strict.stdout)["confirmed"]["rows"][0]["status"] == "mismatch"


def test_reconcile_refuses_a_bad_confirmed_file(data_dir: str, tmp_path: Path) -> None:
    assert pg(data_dir, "import", str(T2)).exit_code == 0
    bad = tmp_path / "bad.csv"
    bad.write_text("isin;quantity;as_of\n", encoding="utf-8")

    result = pg(data_dir, "reconcile", "latest", "--confirmed", str(bad))

    assert result.exit_code == 1
    assert "decimal" in result.output


def test_review_commands(data_dir: str) -> None:
    assert pg(data_dir, "import", str(UNKNOWN), str(AMOUNTS_OFF)).exit_code == 0
    assert pg(data_dir, "accept", "latest").exit_code == 0
    items = json.loads(pg(data_dir, "review", "list", "--json").stdout)["items"]
    by_kind = {item["kind"]: item for item in items}
    assert set(by_kind) == {"unknown_layout", "amounts_do_not_add_up"}

    shown = json.loads(pg(data_dir, "review", "show", str(by_kind["unknown_layout"]["id"]), "--json").stdout)
    assert "KOSTENINFORMATION" in shown["extracted_text"]

    dismissed = pg(data_dir, "review", "dismiss", str(by_kind["unknown_layout"]["id"]), "--reason", "only costs")
    assert dismissed.exit_code == 0, dismissed.output
    resolved = pg(data_dir, "review", "resolve", str(by_kind["amounts_do_not_add_up"]["id"]), "--use-parsed")
    assert resolved.exit_code == 0, resolved.output

    assert json.loads(pg(data_dir, "review", "list", "--json").stdout)["items"] == []
    everything = json.loads(pg(data_dir, "review", "list", "--status", "all", "--json").stdout)["items"]
    assert sorted(item["status"] for item in everything) == ["dismissed", "resolved"]

    two_ways = pg(data_dir, "review", "resolve", "1", "--merge", "--keep-both")
    assert two_ways.exit_code == 1
    no_way = pg(data_dir, "review", "resolve", "1")
    assert no_way.exit_code == 1
    unknown_item = pg(data_dir, "review", "show", "999")
    assert unknown_item.exit_code == 1


def test_transactions_lists_every_transaction_with_its_sources(data_dir: str) -> None:
    assert pg(data_dir, "import", str(T2), str(CSV)).exit_code == 0
    assert pg(data_dir, "accept", "latest").exit_code == 0

    listing = json.loads(pg(data_dir, "transactions", "--json").stdout)
    buys = json.loads(
        pg(data_dir, "transactions", "--type", "buy", "--from", "2024-01-01", "--to", "2024-01-31", "--json").stdout
    )
    text = pg(data_dir, "transactions")

    assert listing["count"] == 11
    assert [record["source_ref"] for record in buys["transactions"]] == ["5d0a-93f1"]
    assert text.exit_code == 0
    assert "5d0a-93f1" in text.stdout


def test_status_shows_the_portfolio_the_queue_and_the_staged_batch(data_dir: str) -> None:
    assert pg(data_dir, "import", str(UNKNOWN)).exit_code == 0
    status = json.loads(pg(data_dir, "status", "--json").stdout)

    assert status["portfolio"] == {"name": "Trade Republic", "base_currency": "EUR", "source": "trade_republic"}
    assert status["staged_batch"] == 1
    assert status["open_review_items"] == 1
    assert status["last_import_at"] is None
    assert status["reminder"]["due"] is True


@pytest.mark.parametrize(
    "command",
    [
        ["import", "--help"],
        ["imports", "--help"],
        ["reconcile", "--help"],
        ["accept", "--help"],
        ["discard", "--help"],
        ["review", "--help"],
        ["review", "resolve", "--help"],
        ["transactions", "--help"],
        ["status", "--help"],
    ],
)
def test_help_never_says_live(command: list[str]) -> None:
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert not re.search(r"\blive\b", result.stdout, re.IGNORECASE)
