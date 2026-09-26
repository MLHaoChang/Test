"""Tests for storage/repos.py (plan 5.2)."""

from datetime import date
from pathlib import Path

import sqlalchemy as sa

from playground.core.clock import FixedClock
from playground.storage.db import open_registry
from playground.storage.repos import get_or_create_portfolio
from playground.storage.schema import ensure_schema, portfolios


def test_creates_the_default_portfolio(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)
    clock = FixedClock(date(2024, 12, 31))

    with engine.begin() as conn:
        portfolio_id = get_or_create_portfolio(conn, clock=clock)

    with engine.connect() as conn:
        row = conn.execute(sa.select(portfolios).where(portfolios.c.id == portfolio_id)).one()

    assert row.name == "Trade Republic"
    assert row.kind == "real"
    assert row.base_currency == "EUR"
    assert row.source == "trade_republic"
    assert row.last_import_at is None
    # Noon Berlin on 2024-12-31 (winter, CET = UTC+1) is 11:00 UTC.
    assert row.created_at == "2024-12-31T11:00:00Z"


def test_is_idempotent_and_returns_the_same_id(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)
    clock = FixedClock(date(2024, 12, 31))

    with engine.begin() as conn:
        first_id = get_or_create_portfolio(conn, clock=clock)
    with engine.begin() as conn:
        second_id = get_or_create_portfolio(conn, clock=clock)

    assert first_id == second_id
    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(portfolios)).scalar_one()
    assert count == 1


def test_accepts_custom_name_and_kind(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)
    clock = FixedClock(date(2024, 12, 31))

    with engine.begin() as conn:
        portfolio_id = get_or_create_portfolio(conn, clock=clock, name="Scratch", kind="virtual")

    with engine.connect() as conn:
        row = conn.execute(sa.select(portfolios).where(portfolios.c.id == portfolio_id)).one()
    assert row.name == "Scratch"
    assert row.kind == "virtual"
