"""Plain CLI wording (QA P0 round 1, M5): help without plan sections, plain file kinds, plurals that
agree, local dates, and a plain message for an empty file.

The house style for everything user-facing: plain English, short sentences, and no internal
names. The plan's section numbers belong in code comments, not in `--help`.
"""

import re
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner

from playground.cli.main import app

REPO = Path(__file__).resolve().parents[2]
GOLDEN_INPUTS = REPO / "tests" / "fixtures" / "golden" / "inputs"
CSV_HEADER = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"

runner = CliRunner()


def pg(*args: str):
    return runner.invoke(app, list(args), env={"PG_TODAY": "2024-12-31", "COLUMNS": "200"})


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    path = str(tmp_path / "data")
    assert pg("--data-dir", path, "init").exit_code == 0
    return path


def import_rows(data_dir: str, tmp_path: Path, *rows: str) -> str:
    """Import a CSV export with `rows` and accept it; return the accept output."""
    path = tmp_path / "export.csv"
    path.write_text("\n".join([CSV_HEADER, *rows]) + "\n", encoding="utf-8")
    staged = pg("--data-dir", data_dir, "import", str(path))
    assert staged.exit_code == 0, staged.output
    accepted = pg("--data-dir", data_dir, "accept", "latest")
    assert accepted.exit_code == 0, accepted.output
    return accepted.stdout


# --- Help text ------------------------------------------------------------------------------------


def _command_paths(command: click.Command, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    paths = [prefix]
    if isinstance(command, click.Group):
        for name, sub in sorted(command.commands.items()):
            paths += _command_paths(sub, (*prefix, name))
    return paths


PLAN_REFERENCE = re.compile(r"\bplan\s+\d|\(\d+\.\d+|:\s\d+\.\d+\)")


def test_no_help_text_cites_a_section_of_the_plan() -> None:
    cited = {}
    for path in _command_paths(typer.main.get_command(app)):
        result = pg(*path, "--help")
        assert result.exit_code == 0, (path, result.output)
        found = PLAN_REFERENCE.findall(result.stdout)
        if found:
            cited[" ".join(path) or "pg"] = found
    assert cited == {}


# --- The import diff ------------------------------------------------------------------------------


def test_the_diff_says_what_each_file_is_in_plain_words(data_dir: str) -> None:
    result = pg(
        "--data-dir",
        data_dir,
        "import",
        str(GOLDEN_INPUTS / "tr_transactions_2024.csv"),
        str(GOLDEN_INPUTS / "pdf" / "2024-01-15_kauf_sap.pdf"),
        str(GOLDEN_INPUTS / "pdf" / "2024-02-01_sparplan_msciw.pdf"),
        str(GOLDEN_INPUTS / "pdf" / "unbekannt_kosteninformation.pdf"),
    )

    assert result.exit_code == 0, result.output
    assert "  tr_transactions_2024.csv (Trade Republic CSV export): read, 11 transactions" in result.stdout
    assert "  2024-01-15_kauf_sap.pdf (trade confirmation): read, 1 transaction" in result.stdout
    assert "  2024-02-01_sparplan_msciw.pdf (savings plan execution): read, 1 transaction" in result.stdout
    assert "  unbekannt_kosteninformation.pdf: not read, see the review items" in result.stdout
    assert "tr.csv_export" not in result.stdout
    assert "tr.wertpapierabrechnung" not in result.stdout


def test_one_review_item_is_one_item_that_needs_review(data_dir: str) -> None:
    result = pg("--data-dir", data_dir, "import", str(GOLDEN_INPUTS / "pdf" / "unbekannt_kosteninformation.pdf"))

    assert result.exit_code == 0, result.output
    assert "Review: 1 new item needs your review, 0 will close" in result.stdout


# --- Plurals --------------------------------------------------------------------------------------

ONE_SHARE_BUY = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;1;140,00;-141,00;1,00;;EUR;;w-1"
ONE_SHARE_SELL = "20.02.2024;10:05;Verkauf;DE0007164600;SAP SE;1;150,00;149,00;1,00;;EUR;;w-2"


def test_one_lot_and_one_transaction_are_counted_in_the_singular(data_dir: str, tmp_path: Path) -> None:
    accepted = import_rows(data_dir, tmp_path, ONE_SHARE_BUY)

    assert "Accepted import batch 1: 1 transaction, 0 held back, 0 released." in accepted
    assert "Lots rebuilt: 1 lot, 0 disposals." in accepted
    listed = pg("--data-dir", data_dir, "imports", "list")
    assert "Batch 1: accepted, 1 file, 1 new transaction, 0 new review items" in listed.stdout
    holdings = pg("--data-dir", data_dir, "holdings")
    assert "SAP SE: 1 share, cost 141.00 EUR" in holdings.stdout
    transactions = pg("--data-dir", data_dir, "transactions")
    assert ", 1 share: -141.00 EUR" in transactions.stdout


# --- pg lots --------------------------------------------------------------------------------------

TRANSFER_IN = "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;w-3"


def test_lots_show_local_dates_and_plain_origins(data_dir: str, tmp_path: Path) -> None:
    import_rows(data_dir, tmp_path, ONE_SHARE_BUY, ONE_SHARE_SELL, TRANSFER_IN)

    result = pg("--data-dir", data_dir, "lots")

    assert result.exit_code == 0, result.output
    # 20.06.2024 at 00:00 in Berlin is 2024-06-19T22:00:00Z: the lot was booked on the 20th.
    assert "DE0008404005 Allianz SE: transfer in, booked 2024-06-20, 4 of 4 open" in result.stdout
    assert "DE0007164600 SAP SE: purchase, booked 2024-01-15, 0 of 1 open" in result.stdout
    assert "DE0007164600 SAP SE: sale on 2024-02-20, 1 share, realised 8.00 EUR" in result.stdout
    assert "T09:05:00Z" not in result.stdout
    assert "transfer_in" not in result.stdout


# 3 shares for 100.00 EUR, then 1 of them sold for 39.00 EUR: the sold share's cost is a third of
# 100.00, which the ledger keeps to 8 decimals (plan 3.3).
THREE_SHARE_BUY = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;3;33,00;-100,00;1,00;;EUR;;w-4"
ONE_OF_THREE_SELL = "20.02.2024;10:05;Verkauf;DE0007164600;SAP SE;1;40,00;39,00;1,00;;EUR;;w-5"


def test_lots_show_euro_amounts_to_the_cent(data_dir: str, tmp_path: Path) -> None:
    import_rows(data_dir, tmp_path, THREE_SHARE_BUY, ONE_OF_THREE_SELL)

    result = pg("--data-dir", data_dir, "lots")

    # Plan 3.3: EUR figures are rounded to 2 decimals, half up, when displayed.
    assert result.exit_code == 0, result.output
    assert "SAP SE: purchase, booked 2024-01-15, 2 of 3 open, cost 66.67 EUR open of 100.00 EUR initial" in (
        result.stdout
    )
    assert "SAP SE: sale on 2024-02-20, 1 share, realised 5.67 EUR" in result.stdout
    assert "6666" not in result.stdout
    # The JSON output keeps the stored figures, so totals are still made from unrounded parts.
    as_json = pg("--data-dir", data_dir, "lots", "--json")
    assert '"cost_eur_open": "66.66666667"' in as_json.stdout
    assert '"realised_eur": "5.66666667"' in as_json.stdout


# --- pg transactions (QA P0 round 2, minor 5) ----------------------------------------------------

DEPOSIT = "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;w-6"


def test_transactions_show_types_and_kinds_of_file_in_plain_words(data_dir: str, tmp_path: Path) -> None:
    import_rows(data_dir, tmp_path, DEPOSIT, ONE_SHARE_BUY, TRANSFER_IN)

    result = pg("--data-dir", data_dir, "transactions")

    assert result.exit_code == 0, result.output
    assert "2024-01-02 deposit: 5000.00 EUR [accepted] ref w-6 from CSV export" in result.stdout
    assert "2024-01-15 purchase SAP SE, 1 share: -141.00 EUR [accepted] ref w-1 from CSV export" in result.stdout
    assert "2024-06-20 transfer in Allianz SE, 4 shares: no cash [accepted] ref w-3 from CSV export" in result.stdout
    for code in ("transfer_in", "csv_export", " :"):
        assert code not in result.stdout


def test_transactions_name_every_kind_of_file_and_a_held_back_one(data_dir: str) -> None:
    files = [
        GOLDEN_INPUTS / "tr_transactions_2024.csv",
        GOLDEN_INPUTS / "pdf" / "2024-01-15_kauf_sap.pdf",
        GOLDEN_INPUTS / "statement" / "kontoauszug_2024_h1.pdf",
    ]
    assert pg("--data-dir", data_dir, "import", *map(str, files)).exit_code == 0

    result = pg("--data-dir", data_dir, "transactions")

    assert result.exit_code == 0, result.output
    assert (
        "2024-01-15 purchase SAP SE, 10 shares: -1401.00 EUR [staged] ref 5d0a-93f1 "
        "from PDF document, CSV export, account statement"
    ) in result.stdout
    # The purchase of 2024-04-10 is known only from its statement line, which gives no quantity.
    assert "2024-04-10 purchase SAP SE: -801.00 EUR [held back]" in result.stdout
    assert "from account statement" in result.stdout
    for code in ("pdf_document", "pdf_statement", "csv_export", "[held]"):
        assert code not in result.stdout


def test_the_confirmed_holdings_comparison_says_its_status_in_words(data_dir: str, tmp_path: Path) -> None:
    assert pg("--data-dir", data_dir, "import", str(GOLDEN_INPUTS / "pdf" / "2024-01-15_kauf_sap.pdf")).exit_code == 0
    confirmed = tmp_path / "confirmed.csv"
    confirmed.write_text("isin;quantity;as_of\nIE00B4L5Y983;5;2024-12-31\n", encoding="utf-8")

    result = pg("--data-dir", data_dir, "reconcile", "latest", "--confirmed", str(confirmed), "--as-of", "2024-12-31")

    assert result.exit_code == 0, result.output
    assert "computed 10, yours 0 on 2024-12-31: missing in confirmed" in result.stdout
    assert "computed 0, yours 5 on 2024-12-31: missing in import" in result.stdout
    assert "missing_in" not in result.stdout


# --- An empty file --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["leer.pdf", "leer.csv"])
def test_an_empty_file_is_refused_as_empty(data_dir: str, tmp_path: Path, name: str) -> None:
    empty = tmp_path / name
    empty.write_bytes(b"")

    result = pg("--data-dir", data_dir, "import", str(empty))

    assert result.exit_code == 1
    assert result.stderr.strip() == f"{name} is empty. Nothing was imported."
