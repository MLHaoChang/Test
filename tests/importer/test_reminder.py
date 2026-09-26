"""Tests for the reminder to import again (plan 5.3.6, spec 3.10, AC16).

`pg status` (and later `GET /portfolio`) shows a reminder when the last import is more than 30
days before today, and not before. "Today" comes from the injectable clock (plan 3.3), so the
tests fix it; the day of the last import is its Europe/Berlin calendar date.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from playground.cli.main import app
from playground.core.clock import FixedClock
from playground.importer.pipeline import REMINDER_AFTER_DAYS, import_reminder, portfolio_status

GOLDEN_INPUTS = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "inputs"


def test_the_reminder_starts_after_thirty_days() -> None:
    assert REMINDER_AFTER_DAYS == 30

    thirty = import_reminder("2024-12-31T11:00:00Z", date(2025, 1, 30))
    thirty_one = import_reminder("2024-12-31T11:00:00Z", date(2025, 1, 31))

    assert (thirty.due, thirty.days_since_last_import) == (False, 30)
    assert (thirty_one.due, thirty_one.days_since_last_import) == (True, 31)
    assert "31 days" in thirty_one.message


def test_the_golden_clock_shows_the_reminder_34_days_after_the_last_import() -> None:
    reminder = import_reminder("2024-12-31T11:00:00Z", date(2025, 2, 3))
    assert reminder.due is True
    assert reminder.days_since_last_import == 34


def test_the_day_of_the_last_import_is_its_berlin_calendar_date() -> None:
    # 23:30 UTC on 31 December is already 1 January in Berlin.
    reminder = import_reminder("2024-12-31T23:30:00Z", date(2025, 1, 31))
    assert reminder.days_since_last_import == 30
    assert reminder.due is False


def test_before_any_import_the_reminder_asks_you_to_import() -> None:
    reminder = import_reminder(None, date(2025, 2, 3))
    assert reminder.due is True
    assert reminder.days_since_last_import is None
    assert "pg import" in reminder.message


def test_status_after_an_import_uses_the_time_accept_stored(harness, docs) -> None:
    harness.run(docs.trade("kauf_sap.pdf"))

    with harness.engine.connect() as conn:
        later = portfolio_status(conn, harness.portfolio_id, clock=FixedClock(date(2025, 2, 3)))
        sooner = portfolio_status(conn, harness.portfolio_id, clock=FixedClock(date(2025, 1, 30)))

    assert later["last_import_at"] == "2024-12-31T11:00:00Z"
    assert later["reminder"]["due"] is True
    assert later["reminder"]["days_since_last_import"] == 34
    assert sooner["reminder"]["due"] is False
    assert later["open_review_items"] == 0
    assert later["staged_batch"] is None
    assert later["portfolio"]["name"] == "Trade Republic"


def test_a_staged_batch_does_not_count_as_an_import(harness, docs) -> None:
    summary = harness.stage(docs.trade("kauf_sap.pdf"))

    with harness.engine.connect() as conn:
        status = portfolio_status(conn, harness.portfolio_id, clock=harness.clock)

    assert status["last_import_at"] is None
    assert status["reminder"]["due"] is True
    assert status["staged_batch"] == summary.batch_id


def test_pg_status_shows_the_reminder_with_the_clock_of_pg_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    assert runner.invoke(app, ["--data-dir", data_dir, "init"]).exit_code == 0
    imported = runner.invoke(
        app, ["--data-dir", data_dir, "import", str(GOLDEN_INPUTS / "pdf" / "2024-01-15_kauf_sap.pdf")]
    )
    assert imported.exit_code == 0, imported.output
    assert runner.invoke(app, ["--data-dir", data_dir, "accept", "latest"]).exit_code == 0

    monkeypatch.setenv("PG_TODAY", "2025-01-30")
    quiet = runner.invoke(app, ["--data-dir", data_dir, "status", "--json"])
    monkeypatch.setenv("PG_TODAY", "2025-02-03")
    reminded = runner.invoke(app, ["--data-dir", data_dir, "status", "--json"])
    text = runner.invoke(app, ["--data-dir", data_dir, "status"])

    assert quiet.exit_code == 0, quiet.output
    assert json.loads(quiet.stdout)["reminder"]["due"] is False
    assert reminded.exit_code == 0, reminded.output
    status = json.loads(reminded.stdout)
    assert status["reminder"]["due"] is True
    assert status["reminder"]["days_since_last_import"] == 34
    assert status["today"] == "2025-02-03"
    assert "34 days" in text.stdout
