"""Tests for `pg value`, the value columns of `pg holdings` and the latest value of `pg status` (plan 5.7, 5.9).

`pg value` works out the daily value in EUR, stores it with the price evidence of every holding, and
prints a summary with every flag (a **flag** is a note on a value: a holding left out, a price or
rate that is old, or a check that failed). It never fails on missing data. Market data comes from
the committed fixtures through `--http-replay` and the manual Allianz price file.
"""

import csv
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from playground.cli.main import app
from playground.storage.db import open_registry
from playground.storage.schema import portfolio_values

REPO = Path(__file__).resolve().parents[2]
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"
MAPPING_FILE = REPO / "tests" / "fixtures" / "golden" / "inputs" / "instrument_mapping.csv"
MANUAL_ALLIANZ_FILE = REPO / "tests" / "fixtures" / "golden" / "inputs" / "manual_prices_allianz.csv"

SAP = "DE0007164600"
ALV = "DE0008404005"

CSV_HEADER = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"
# 10 SAP bought on 2024-01-15, and 4 Allianz shares transferred in on 2024-06-20 without a cost.
CSV_ROWS = (
    "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;v-0001",
    "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;v-0002",
)

runner = CliRunner()


def pg(data_dir: str, *args: str, today: str = "2024-12-31", replay: bool = False):
    prefix = ["--data-dir", data_dir] + (["--http-replay", str(HTTP_FIXTURES)] if replay else [])
    return runner.invoke(app, [*prefix, *args], env={"PG_TODAY": today})


def ok(result) -> str:
    assert result.exit_code == 0, result.output
    return result.stdout


@pytest.fixture
def data_dir(tmp_path: Path) -> str:
    path = str(tmp_path / "data")
    ok(pg(path, "init"))
    return path


@pytest.fixture
def imported(data_dir: str, tmp_path: Path) -> str:
    """`data_dir` with the two CSV rows imported and accepted, nothing mapped yet."""
    export = tmp_path / "export.csv"
    export.write_text("\n".join([CSV_HEADER, *CSV_ROWS]) + "\n", encoding="utf-8")
    ok(pg(data_dir, "import", str(export)))
    ok(pg(data_dir, "accept", "latest"))
    return data_dir


@pytest.fixture
def priced(imported: str) -> str:
    """`imported` with the golden mapping, the replayed Stooq and ECB fixtures and the manual Allianz file."""
    ok(pg(imported, "instruments", "map", "--file", str(MAPPING_FILE)))
    ok(pg(imported, "prices", "fetch", "--from", "2024-01-01", "--to", "2024-12-31", replay=True))
    ok(pg(imported, "prices", "import-file", str(MANUAL_ALLIANZ_FILE)))
    ok(pg(imported, "fx", "fetch", replay=True))
    return imported


def stored_series(data_dir: str) -> list[tuple[str, str]]:
    """The stored value series: (date, value in EUR to the cent), oldest day first."""
    engine = open_registry(Path(data_dir))
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.select(portfolio_values).order_by(portfolio_values.c.date)).all()
    finally:
        engine.dispose()
    return [(row.date.isoformat(), f"{row.value_eur:.2f}") for row in rows]


def stored_dates(data_dir: str) -> list[str]:
    return [day for day, _ in stored_series(data_dir)]


# --- pg value ---------------------------------------------------------------------------------


def test_value_before_any_import_says_there_is_nothing_to_value(data_dir: str) -> None:
    text = ok(pg(data_dir, "value"))
    assert "Nothing to value yet" in text

    data = json.loads(ok(pg(data_dir, "value", "--json")))
    assert (data["from"], data["to"], data["days"], data["series"], data["latest"]) == (None, None, 0, [], None)


def test_value_of_unmapped_holdings_is_zero_flagged_and_exits_0(imported: str) -> None:
    data = json.loads(ok(pg(imported, "value", "--json")))

    assert (data["from"], data["to"], data["currency"]) == ("2024-01-15", "2024-12-31", "EUR")
    assert data["days"] == len(data["series"]) == 252
    assert {day["value_eur"] for day in data["series"]} == {"0.00"}
    assert {day["complete"] for day in data["series"]} == {False}
    # A day's flags are sorted by kind, in the order of plan 5.7, then by ISIN.
    assert data["series"][-1]["flags"] == [f"unmapped {SAP}", f"unmapped {ALV}", f"cost_basis_missing {ALV}"]
    unmapped = {(flag["isin"], flag["name"]) for flag in data["flags"] if flag["kind"] == "unmapped"}
    assert unmapped == {(SAP, "SAP SE"), (ALV, "Allianz SE")}
    sap = next(flag for flag in data["flags"] if flag["kind"] == "unmapped" and flag["isin"] == SAP)
    assert (sap["first"], sap["last"], sap["days"]) == ("2024-01-15", "2024-12-31", 252)
    assert "pg instruments map" in sap["detail"]


def test_value_uses_the_mapped_prices_and_stores_every_day(priced: str) -> None:
    data = json.loads(ok(pg(priced, "value", "--json")))

    # 10 SAP at the 2024-12-30 close of 236.00 (Xetra is closed on the 31st), and 4 Allianz at the
    # manual file's last price of 290.00 from 2024-12-20, which is stale by then.
    assert data["latest"]["date"] == "2024-12-31"
    assert data["latest"]["value_eur"] == "3520.00"
    assert data["latest"]["cost_basis_eur"] is None
    assert data["latest"]["complete"] is True
    kinds = {(flag["kind"], flag["isin"]) for flag in data["flags"]}
    assert ("stale_price", ALV) in kinds
    assert ("cost_basis_missing", ALV) in kinds
    assert ("dividend_adjusted_prices", SAP) in kinds
    assert data["complete_days"] == data["days"]
    assert stored_dates(priced)[0] == "2024-01-15"
    assert stored_dates(priced)[-1] == "2024-12-31"


def test_value_prints_a_summary_with_every_flag(priced: str) -> None:
    text = ok(pg(priced, "value"))

    assert "2024-01-15 to 2024-12-31" in text
    assert "3520.00 EUR on 2024-12-31" in text
    assert "stale_price" in text
    assert "cost_basis_missing" in text
    assert "—" not in text


def test_value_writes_a_csv_file_with_one_row_per_weekday(priced: str, tmp_path: Path) -> None:
    out = tmp_path / "value.csv"

    ok(pg(priced, "value", "--from", "2024-12-23", "--to", "2024-12-31", "--csv", str(out)))

    rows = list(csv.reader(out.read_text(encoding="utf-8").splitlines(), delimiter=";"))
    assert rows[0] == ["date", "value_eur", "cost_basis_eur", "complete", "flags"]
    assert [row[0] for row in rows[1:]] == [
        "2024-12-23",
        "2024-12-24",
        "2024-12-25",
        "2024-12-26",
        "2024-12-27",
        "2024-12-30",
        "2024-12-31",
    ]
    assert rows[-1][1] == "3520.00"
    assert f"stale_price {ALV}" in rows[-1][4]


def test_value_with_an_explicit_range_uses_it(priced: str) -> None:
    data = json.loads(ok(pg(priced, "value", "--from", "2024-06-03", "--to", "2024-06-07", "--json")))

    assert (data["from"], data["to"], data["days"]) == ("2024-06-03", "2024-06-07", 5)
    assert [day["date"] for day in data["series"]] == [
        "2024-06-03",
        "2024-06-04",
        "2024-06-05",
        "2024-06-06",
        "2024-06-07",
    ]


def test_value_from_a_day_runs_to_the_last_weekday_up_to_today(priced: str) -> None:
    data = json.loads(ok(pg(priced, "value", "--from", "2024-12-27", "--json", today="2024-12-29")))

    # PG_TODAY is a Sunday: the range ends on the Friday before.
    assert (data["from"], data["to"], data["days"]) == ("2024-12-27", "2024-12-27", 1)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--from", "2024-06-07", "--to", "2024-06-03"), "--from"),
        (("--from", "07.06.2024"), "--from"),
        (("--to", "2025-01-31"), "--to"),
    ],
)
def test_value_refuses_a_range_it_cannot_value(imported: str, args: tuple[str, ...], message: str) -> None:
    result = pg(imported, "value", *args)
    assert result.exit_code == 1
    assert message in result.output


def test_value_needs_pg_init_first(tmp_path: Path) -> None:
    result = pg(str(tmp_path / "nothing"), "value")
    assert result.exit_code == 1
    assert "pg init" in result.output


# --- pg holdings --------------------------------------------------------------------------------


def test_holdings_shows_value_price_evidence_and_flags(priced: str) -> None:
    data = json.loads(ok(pg(priced, "holdings", "--as-of", "2024-12-31", "--json")))

    assert (data["as_of"], data["currency"], data["value_eur"], data["complete"]) == (
        "2024-12-31",
        "EUR",
        "3520.00",
        True,
    )
    assert data["cost_basis_eur"] is None
    rows = {row["isin"]: row for row in data["holdings"]}
    sap = rows[SAP]
    assert (sap["quantity"], sap["cost_eur"], sap["value_eur"]) == ("10", "1401.00", "2360.00")
    assert sap["pricing_quantity"] == "10"
    assert sap["price"] == {
        "close": "236.00",
        "date": "2024-12-30",
        "currency": "EUR",
        "source": "stooq",
        "symbol": "sap.de",
        "adjustment": "split_dividend",
    }
    assert sap["fx"] is None
    assert sap["mapping"] == {"status": "confirmed", "source": "stooq", "symbol": "sap.de", "currency": "EUR"}
    assert [flag["kind"] for flag in sap["flags"]] == ["dividend_adjusted_prices"]
    alv = rows[ALV]
    assert (alv["value_eur"], alv["cost_eur"]) == ("1160.00", None)
    assert alv["price"]["date"] == "2024-12-20"
    assert {flag["kind"] for flag in alv["flags"]} == {"stale_price", "cost_basis_missing"}


def test_holdings_before_mapping_shows_the_holding_without_a_value(imported: str) -> None:
    data = json.loads(ok(pg(imported, "holdings", "--json")))

    rows = {row["isin"]: row for row in data["holdings"]}
    assert (rows[SAP]["value_eur"], rows[SAP]["price"], rows[SAP]["fx"]) == (None, None, None)
    assert rows[SAP]["mapping"]["status"] == "unmapped"
    assert [flag["kind"] for flag in rows[SAP]["flags"]] == ["unmapped"]
    assert (data["value_eur"], data["complete"]) == ("0.00", False)

    text = ok(pg(imported, "holdings"))
    assert "SAP SE: 10 shares, cost 1401.00 EUR" in text
    assert "pg instruments map" in text


def test_holdings_prints_the_value_and_the_price_it_comes_from(priced: str) -> None:
    text = ok(pg(priced, "holdings", "--as-of", "2024-12-31"))

    assert "SAP SE: 10 shares, cost 1401.00 EUR, value 2360.00 EUR" in text
    assert "236.00 EUR on 2024-12-30" in text
    assert "3520.00 EUR" in text


# --- pg accept, pg transfers set-cost and pg status -----------------------------------------------


def test_accept_says_the_value_series_was_rebuilt(data_dir: str, tmp_path: Path) -> None:
    export = tmp_path / "export.csv"
    export.write_text("\n".join([CSV_HEADER, *CSV_ROWS]) + "\n", encoding="utf-8")
    ok(pg(data_dir, "import", str(export)))

    text = ok(pg(data_dir, "accept", "latest"))

    assert "Value series rebuilt" in text
    assert stored_dates(data_dir)[0] == "2024-01-15"


def test_set_cost_rebuilds_the_value_series(priced: str) -> None:
    data = json.loads(
        ok(
            pg(
                priced,
                "transfers",
                "set-cost",
                "--isin",
                ALV,
                "--acquired",
                "2020-03-02",
                "--cost-eur",
                "800.00",
                "--json",
            )
        )
    )
    assert data["values"]["latest"]["cost_basis_eur"] == "2201.00"

    status = json.loads(ok(pg(priced, "status", "--json")))
    assert status["latest_value"]["cost_basis_eur"] == "2201.00"


def test_a_resolution_through_pg_review_rebuilds_the_value_series(priced: str, tmp_path: Path) -> None:
    # A manual row one day after the CSV purchase is held back as a possible duplicate (plan 5.3.4,
    # rule c). Keeping both releases it, so from then on the series counts 20 SAP shares.
    manual = tmp_path / "manual.csv"
    manual.write_text(
        "date;time;type;isin;quantity;price;currency;amount_eur;fees_eur;tax_eur;note\n"
        "2024-01-16;10:05;buy;DE0007164600;10;140.00;EUR;-1401.00;1.00;;a second purchase\n",
        encoding="utf-8",
    )
    ok(pg(priced, "import", str(manual)))
    ok(pg(priced, "accept", "latest"))
    items = json.loads(ok(pg(priced, "review", "list", "--json")))["items"]
    duplicate = next(item for item in items if item["kind"] == "possible_duplicate")
    assert stored_series(priced)[-1] == ("2024-12-31", "3520.00")

    ok(pg(priced, "review", "resolve", str(duplicate["id"]), "--keep-both"))

    # 20 SAP at 236.00 and 4 Allianz at 290.00.
    assert stored_series(priced)[-1] == ("2024-12-31", "5880.00")


def test_status_shows_the_latest_value(priced: str) -> None:
    status = json.loads(ok(pg(priced, "status", "--json")))
    assert status["latest_value"]["date"] == "2024-12-31"
    assert status["latest_value"]["value_eur"] == "3520.00"
    assert status["latest_value"]["complete"] is True

    text = ok(pg(priced, "status"))
    assert "Latest value: 3520.00 EUR on 2024-12-31" in text


def test_status_values_the_last_weekday_on_or_before_today(priced: str) -> None:
    # 2025-02-02 is a Sunday: the value is the one of Friday 2025-01-31, every price by then old.
    status = json.loads(ok(pg(priced, "status", "--json", today="2025-02-02")))

    assert status["latest_value"]["date"] == "2025-01-31"
    assert status["latest_value"]["value_eur"] == "3520.00"
    assert f"stale_price {SAP}" in status["latest_value"]["flags"]


def test_status_before_any_import_has_no_latest_value(data_dir: str) -> None:
    status = json.loads(ok(pg(data_dir, "status", "--json")))
    assert status["latest_value"] is None
    assert "Latest value: none yet" in ok(pg(data_dir, "status"))
