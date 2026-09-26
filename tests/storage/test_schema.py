"""Tests for the registry schema: every P0 table, versioning and foreign keys (schema.py, plan 4.1, 7.2)."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from playground.storage.db import open_registry
from playground.storage.schema import ensure_schema, instrument_mapping_log, metadata, schema_version

EXPECTED_TABLES = {
    "schema_version",
    "portfolios",
    "instruments",
    "instrument_mapping_log",
    "import_batches",
    "imports",
    "transactions",
    "transaction_sources",
    "review_items",
    "cost_basis_inputs",
    "lots",
    "disposals",
    "confirmed_holdings",
    "holdings_snapshots",
    "portfolio_values",
    "benchmarks",
}


def test_metadata_declares_every_p0_table() -> None:
    assert set(metadata.tables.keys()) == EXPECTED_TABLES


def test_ensure_schema_creates_every_table(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)

    table_names = set(sa.inspect(engine).get_table_names())
    assert table_names >= EXPECTED_TABLES


def test_ensure_schema_seeds_version_one(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)

    with engine.connect() as conn:
        version = conn.execute(sa.select(schema_version.c.version)).scalar_one()
    assert version == 1


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)
    ensure_schema(engine)  # must not fail, and must not duplicate the schema_version row

    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(schema_version)).scalar_one()
    assert count == 1


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        # instrument_mapping_log.instrument_id references instruments.id, which is empty here.
        conn.execute(
            sa.insert(instrument_mapping_log).values(
                instrument_id=999,
                changed_at="2024-12-31T12:00:00Z",
                changed_by="cli",
            )
        )


def test_instruments_isin_is_unique(tmp_path: Path) -> None:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)
    instruments = metadata.tables["instruments"]

    with engine.begin() as conn:
        conn.execute(
            sa.insert(instruments).values(isin="DE0007164600", name="SAP SE", type="stock", mapping_status="unmapped")
        )

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            sa.insert(instruments).values(
                isin="DE0007164600", name="SAP SE (duplicate)", type="stock", mapping_status="unmapped"
            )
        )
