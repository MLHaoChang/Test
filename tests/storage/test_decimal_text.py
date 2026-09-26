"""Tests for the DecimalText column type (storage/db.py, plan 5.2, 7.2).

`DecimalText` is exercised two ways: directly, calling its bind/result
methods (fast, and immune to SQLAlchemy wrapping a bind-time error in its
own `StatementError`), and through a real round trip via a scratch table
on an in-memory SQLite database, so the type is also checked wired into
SQLAlchemy Core the way the real registry tables use it.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa

from playground.storage.db import DecimalText


class TestProcessBindParam:
    def test_decimal_becomes_its_exact_string(self) -> None:
        assert DecimalText().process_bind_param(Decimal("1825.60"), None) == "1825.60"

    def test_keeps_trailing_zeros(self) -> None:
        assert DecimalText().process_bind_param(Decimal("0.453400"), None) == "0.453400"

    def test_none_stays_none(self) -> None:
        assert DecimalText().process_bind_param(None, None) is None

    def test_a_valid_decimal_string_passes_through_unchanged(self) -> None:
        assert DecimalText().process_bind_param("1825.60", None) == "1825.60"

    def test_refuses_a_float(self) -> None:
        with pytest.raises(TypeError):
            DecimalText().process_bind_param(1825.60, None)

    def test_refuses_an_unparseable_string(self) -> None:
        with pytest.raises(TypeError):
            DecimalText().process_bind_param("not-a-number", None)

    def test_refuses_an_unrelated_type(self) -> None:
        with pytest.raises(TypeError):
            DecimalText().process_bind_param(object(), None)


class TestProcessResultValue:
    def test_string_becomes_decimal(self) -> None:
        result = DecimalText().process_result_value("1825.60", None)
        assert result == Decimal("1825.60")
        assert isinstance(result, Decimal)

    def test_keeps_trailing_zeros(self) -> None:
        assert str(DecimalText().process_result_value("0.453400", None)) == "0.453400"

    def test_none_stays_none(self) -> None:
        assert DecimalText().process_result_value(None, None) is None


class TestRoundTripThroughSqlite:
    @pytest.fixture
    def scratch_table(self) -> tuple[sa.Engine, sa.Table]:
        engine = sa.create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        table = sa.Table(
            "scratch",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("amount", DecimalText),
        )
        metadata.create_all(engine)
        return engine, table

    def test_round_trips_a_decimal_exactly(self, scratch_table: tuple[sa.Engine, sa.Table]) -> None:
        engine, table = scratch_table
        with engine.begin() as conn:
            conn.execute(sa.insert(table).values(id=1, amount=Decimal("1825.60")))
        with engine.connect() as conn:
            result = conn.execute(sa.select(table.c.amount).where(table.c.id == 1)).scalar_one()
        assert result == Decimal("1825.60")
        assert isinstance(result, Decimal)

    def test_round_trips_null(self, scratch_table: tuple[sa.Engine, sa.Table]) -> None:
        engine, table = scratch_table
        with engine.begin() as conn:
            conn.execute(sa.insert(table).values(id=1, amount=None))
        with engine.connect() as conn:
            result = conn.execute(sa.select(table.c.amount).where(table.c.id == 1)).scalar_one()
        assert result is None

    def test_refuses_a_float_end_to_end(self, scratch_table: tuple[sa.Engine, sa.Table]) -> None:
        engine, table = scratch_table
        with pytest.raises(sa.exc.StatementError) as exc_info, engine.begin() as conn:
            conn.execute(sa.insert(table).values(id=1, amount=1825.60))
        assert isinstance(exc_info.value.orig, TypeError)
