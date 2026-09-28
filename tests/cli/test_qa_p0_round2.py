"""Tests that show defects found in P0 QA round 2.

QA wrote each test here as a strict xfail (`xfail(strict=True)`), so the suite stayed green while
the defect was open. Once a defect is fixed its test passes, strict xfail turns that into a
failure, and whoever fixes it removes the marker; the test then guards the fix. All three are
fixed now, and the markers are gone.

- R2-D1 (fixed): the UAT guide (docs/uat/P0-macos.md, step 5) told you to start the
  confirmed-holdings file with the header `isin;quantity;as_of`, but the parser refused any file
  whose first line was not the `# decimal=,` or `# decimal=.` comment, so step 5 failed as written
  (AC15). That line is now optional (see `test_confirmed_csv.py`), and the guide shows it.
- R2-D2 (fixed): `pg benchmarks series` with a currency it cannot convert to printed the internal
  function name `benchmark_series` (and the API answered with the same text). It now names the
  benchmark and the currencies it can be shown in (see `test_benchmarks.py`).
- R2-D3 (fixed): an `unparsed_row` review item from a CSV export stored the whole file as its
  text, so `pg review show` and `pg review export` printed every row of your export, not the row at
  fault. Such an item (and an `invalid_isin` one, in the export or a manual CSV) now holds the
  header line and that row only (see `test_csv_goldens.py` and `test_manual_csv.py`).
"""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from playground.cli.main import app
from playground.importer.confirmed_csv import parse_confirmed_csv
from playground.importer.model import ReviewKind
from playground.importer.tr.csv_parser import parse_csv

REPO = Path(__file__).resolve().parents[2]
UAT_GUIDE = REPO / "docs" / "uat" / "P0-macos.md"
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"
CSV_HEADER = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"

runner = CliRunner()


def _reconcile_example_from_guide() -> str:
    """The first code block of the guide's "5. Reconcile" section: the file layout it tells you to use."""
    text = UAT_GUIDE.read_text(encoding="utf-8")
    section = text.split("## 5. Reconcile", 1)[1]
    match = re.search(r"```[a-z]*\n(.*?)```", section, flags=re.DOTALL)
    assert match is not None, "step 5 of the UAT guide has no code block"
    return match.group(1)


def test_uat_guide_confirmed_holdings_layout_is_accepted() -> None:
    example = _reconcile_example_from_guide()
    data = example + "DE0007164600;3;2024-12-31\n"

    holdings = parse_confirmed_csv(data.encode("utf-8"))

    assert [(h.isin, str(h.quantity)) for h in holdings] == [("DE0007164600", "3")]


def test_benchmark_series_in_an_unsupported_currency_is_plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    data_dir = str(tmp_path / "data")
    assert runner.invoke(app, ["--data-dir", data_dir, "init"]).exit_code == 0
    fetched = runner.invoke(
        app,
        ["--data-dir", data_dir, "--http-replay", str(HTTP_FIXTURES), "benchmarks", "fetch"]
        + ["--from", "2024-01-01", "--to", "2024-12-31"],
    )
    assert fetched.exit_code == 0, fetched.output

    result = runner.invoke(
        app,
        ["--data-dir", data_dir, "benchmarks", "series", "msci_world_eur", "--currency", "USD"]
        + ["--from", "2024-12-27", "--to", "2024-12-31"],
    )

    assert result.exit_code == 1
    assert "benchmark_series" not in result.output


def test_unparsed_row_review_text_holds_only_the_header_and_that_row() -> None:
    good_row = "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;q-1"
    bad_row = "03.01.2024;;Tauschgeschäft;DE0007164600;SAP SE;1;;;;;EUR;;q-2"
    other_row = "04.04.2024;;Einzahlung;;;;;1000,00;;;EUR;;q-3"
    data = "\n".join([CSV_HEADER, good_row, bad_row, other_row]) + "\n"

    result = parse_csv(data.encode("utf-8"))

    items = [item for item in result.review if item.kind == ReviewKind.UNPARSED_ROW]
    assert len(items) == 1
    text = items[0].extracted_text
    assert bad_row in text
    assert good_row not in text
    assert other_row not in text
