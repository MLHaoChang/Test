"""Tests for opening the registry database (storage/db.py, plan 5.2, 7.2)."""

from pathlib import Path

import sqlalchemy as sa

from playground.storage.db import open_registry


def test_creates_the_data_directory_tree(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    open_registry(data_dir)

    assert (data_dir / "registry" / "runs.sqlite").exists()
    assert (data_dir / "lake").is_dir()
    assert (data_dir / "uploads").is_dir()


def test_is_safe_to_call_twice_on_the_same_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    open_registry(data_dir)
    open_registry(data_dir)  # must not raise or recreate the file destructively

    assert (data_dir / "registry" / "runs.sqlite").exists()


def test_enables_foreign_keys(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    with engine.connect() as conn:
        value = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
    assert value == 1


def test_enables_wal_journal_mode(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    with engine.connect() as conn:
        value = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert value is not None
    assert value.lower() == "wal"


def test_returns_a_working_engine(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT 1")).scalar_one() == 1
