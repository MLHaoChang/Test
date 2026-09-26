"""Tests for `pg init` (cli/main.py, plan 5.2, 5.9)."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from playground.cli.main import app
from playground.storage.schema import portfolios

runner = CliRunner()


def test_creates_directory_tree_and_default_portfolio(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "init"])

    assert result.exit_code == 0, result.output
    assert (data_dir / "registry" / "runs.sqlite").exists()
    assert (data_dir / "lake").is_dir()
    assert (data_dir / "uploads").is_dir()

    engine = sa.create_engine(f"sqlite:///{data_dir / 'registry' / 'runs.sqlite'}")
    with engine.connect() as conn:
        row = conn.execute(sa.select(portfolios)).one()
    assert row.name == "Trade Republic"
    assert row.kind == "real"
    assert row.base_currency == "EUR"


def test_respects_pg_today_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PG_TODAY", "2024-12-31")

    result = runner.invoke(app, ["--data-dir", str(data_dir), "init"])

    assert result.exit_code == 0, result.output
    engine = sa.create_engine(f"sqlite:///{data_dir / 'registry' / 'runs.sqlite'}")
    with engine.connect() as conn:
        created_at = conn.execute(sa.select(portfolios.c.created_at)).scalar_one()
    # Noon Berlin on 2024-12-31 (winter, CET = UTC+1) is 11:00 UTC.
    assert created_at == "2024-12-31T11:00:00Z"


def test_rejects_malformed_pg_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PG_TODAY", "not-a-date")

    result = runner.invoke(app, ["--data-dir", str(data_dir), "init"])

    assert result.exit_code == 1
    assert "PG_TODAY" in result.output
    assert not (data_dir / "registry" / "runs.sqlite").exists()


def test_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first = runner.invoke(app, ["--data-dir", str(data_dir), "init"])
    second = runner.invoke(app, ["--data-dir", str(data_dir), "init"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output

    engine = sa.create_engine(f"sqlite:///{data_dir / 'registry' / 'runs.sqlite'}")
    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(portfolios)).scalar_one()
    assert count == 1


def test_default_data_dir_help_still_shown(tmp_path: Path) -> None:
    # Regression guard: adding the today/clock plumbing must not remove --data-dir's help text
    # or otherwise change pg --help.
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--data-dir" in result.output
