"""Tests for the instrument master (plan 5.6, AC10): mapping, its log, and the mapping file.

"Mapping log written on every change" and "upsert_from_documents never sets a symbol" (7.2) are
both checked here, even though `upsert_instrument_from_document` itself is WP2's
`storage/repos.py`: AC10 is this package's acceptance criterion, so its full guarantee ("never set
without an explicit command or file") is what this file is written against.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from playground.core.clock import FixedClock
from playground.marketdata.instruments import (
    InstrumentMappingError,
    get_instrument,
    list_instruments,
    load_mapping_file,
    mapping_history,
    parse_mapping_file,
    set_mapping,
)
from playground.storage.db import open_registry
from playground.storage.repos import upsert_instrument_from_document
from playground.storage.schema import ensure_schema, instrument_mapping_log, instruments

SAP = "DE0007164600"
ALV = "DE0008404005"
CLOCK = FixedClock(date(2024, 12, 31))


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = open_registry(tmp_path / "data")
    ensure_schema(engine)
    return engine


# --- set_mapping ---------------------------------------------------------------------------------


def test_set_mapping_creates_the_instrument_when_no_document_has_named_it(engine: Engine) -> None:
    with engine.begin() as conn:
        instrument_id = set_mapping(
            conn, isin=SAP, source="stooq", symbol="sap.de", currency="EUR", changed_by="file", clock=CLOCK
        )

    with engine.connect() as conn:
        row = conn.execute(sa.select(instruments).where(instruments.c.id == instrument_id)).one()
    assert row.isin == SAP
    assert row.name == SAP  # no document has named it yet, so it is named by its own ISIN
    assert row.data_source == "stooq"
    assert row.data_symbol == "sap.de"
    assert row.currency == "EUR"
    assert row.mapping_status == "confirmed"
    assert row.mapping_updated_at is not None


def test_set_mapping_keeps_a_name_a_document_already_gave_it(engine: Engine) -> None:
    with engine.begin() as conn:
        upsert_instrument_from_document(conn, isin=SAP, name="SAP SE")
        set_mapping(conn, isin=SAP, source="stooq", symbol="sap.de", currency="EUR", changed_by="cli", clock=CLOCK)

    with engine.connect() as conn:
        row = conn.execute(sa.select(instruments.c.name).where(instruments.c.isin == SAP)).scalar_one()
    assert row == "SAP SE"


def test_set_mapping_uppercases_the_currency(engine: Engine) -> None:
    with engine.begin() as conn:
        set_mapping(conn, isin=SAP, source="stooq", symbol="sap.de", currency="eur", changed_by="cli", clock=CLOCK)
    with engine.connect() as conn:
        assert conn.execute(sa.select(instruments.c.currency).where(instruments.c.isin == SAP)).scalar_one() == "EUR"


def test_set_mapping_writes_a_log_row_every_time(engine: Engine) -> None:
    with engine.begin() as conn:
        instrument_id = set_mapping(
            conn, isin=SAP, source="stooq", symbol="sap.de", currency="EUR", changed_by="cli", clock=CLOCK
        )
        set_mapping(
            conn,
            isin=SAP,
            source="manual",
            symbol="SAP.OLD",
            currency="EUR",
            changed_by="cli",
            clock=CLOCK,
            note="correction",
        )

    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(instrument_mapping_log)
            .where(instrument_mapping_log.c.instrument_id == instrument_id)
            .order_by(instrument_mapping_log.c.id)
        ).all()
    assert len(rows) == 2
    assert rows[0].old_source is None
    assert rows[0].new_source == "stooq"
    assert rows[1].old_source == "stooq"
    assert rows[1].old_symbol == "sap.de"
    assert rows[1].new_source == "manual"
    assert rows[1].new_symbol == "SAP.OLD"
    assert rows[1].note == "correction"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"source": "yahoo"}, "source"),
        ({"currency": "EURO"}, "currency"),
        ({"currency": "12"}, "currency"),
        ({"changed_by": "someone"}, "changed_by"),
        ({"symbol": ""}, "symbol"),
    ],
)
def test_set_mapping_rejects_bad_input(engine: Engine, kwargs: dict[str, str], match: str) -> None:
    base: dict[str, Any] = {
        "isin": SAP,
        "source": "stooq",
        "symbol": "sap.de",
        "currency": "EUR",
        "changed_by": "cli",
        "clock": CLOCK,
    }
    base.update(kwargs)
    with engine.begin() as conn, pytest.raises(InstrumentMappingError, match=match):
        set_mapping(conn, **base)


def test_upsert_from_documents_never_sets_a_price_symbol(engine: Engine) -> None:
    """AC10: "never set without an explicit command or file"."""
    with engine.begin() as conn:
        instrument_id = upsert_instrument_from_document(conn, isin=SAP, name="SAP SE")
    with engine.connect() as conn:
        row = conn.execute(sa.select(instruments).where(instruments.c.id == instrument_id)).one()
    assert row.data_source is None
    assert row.data_symbol is None
    assert row.mapping_status == "unmapped"

    with engine.connect() as conn:
        log_count = conn.execute(sa.select(sa.func.count()).select_from(instrument_mapping_log)).scalar_one()
    assert log_count == 0


# --- list_instruments / get_instrument / mapping_history -----------------------------------------


def test_list_instruments_is_sorted_by_isin_and_shows_unmapped_ones_too(engine: Engine) -> None:
    with engine.begin() as conn:
        upsert_instrument_from_document(conn, isin=ALV, name="Allianz SE")
        set_mapping(conn, isin=SAP, source="stooq", symbol="sap.de", currency="EUR", changed_by="cli", clock=CLOCK)

    with engine.connect() as conn:
        records = list_instruments(conn)

    assert [record["isin"] for record in records] == [SAP, ALV]
    assert records[0]["mapping_status"] == "confirmed"
    assert records[1]["mapping_status"] == "unmapped"
    assert records[1]["data_source"] is None


def test_get_instrument_returns_none_for_an_unknown_isin(engine: Engine) -> None:
    with engine.connect() as conn:
        assert get_instrument(conn, SAP) is None


def test_mapping_history_is_oldest_first_and_raises_for_an_unknown_isin(engine: Engine) -> None:
    with engine.begin() as conn:
        set_mapping(conn, isin=SAP, source="stooq", symbol="sap.de", currency="EUR", changed_by="cli", clock=CLOCK)
        set_mapping(conn, isin=SAP, source="manual", symbol="SAP.OLD", currency="EUR", changed_by="api", clock=CLOCK)

    with engine.connect() as conn:
        history = mapping_history(conn, SAP)
    assert [entry["changed_by"] for entry in history] == ["cli", "api"]
    assert history[1]["old"]["symbol"] == "sap.de"
    assert history[1]["new"]["symbol"] == "SAP.OLD"

    with engine.connect() as conn, pytest.raises(InstrumentMappingError):
        mapping_history(conn, "US0000000000")


# --- parse_mapping_file / load_mapping_file -------------------------------------------------------


def test_parse_mapping_file_reads_every_row() -> None:
    data = b"isin;data_source;data_symbol;currency;note\nDE0007164600;stooq;sap.de;EUR;\nDE0008404005;manual;ALV.DE;EUR;typed in\n"
    rows = parse_mapping_file(data)
    assert [row.isin for row in rows] == [SAP, ALV]
    assert rows[0].note is None
    assert rows[1].note == "typed in"


def test_parse_mapping_file_accepts_no_header() -> None:
    data = f"{SAP};stooq;sap.de;EUR\n".encode()
    rows = parse_mapping_file(data)
    assert rows[0].symbol == "sap.de"
    assert rows[0].note is None


def test_parse_mapping_file_normalises_the_isin() -> None:
    data = f"ISIN: {SAP};stooq;sap.de;EUR\n".encode()
    assert parse_mapping_file(data)[0].isin == SAP


def test_parse_mapping_file_rejects_an_invalid_isin_naming_the_line() -> None:
    with pytest.raises(InstrumentMappingError, match="Line 2"):
        parse_mapping_file(b"isin;data_source;data_symbol;currency\nXX0000000000;stooq;x;EUR\n")


def test_parse_mapping_file_rejects_the_wrong_number_of_fields() -> None:
    with pytest.raises(InstrumentMappingError, match="Line 1"):
        parse_mapping_file(f"{SAP};stooq;sap.de\n".encode())


def test_load_mapping_file_applies_every_row_and_creates_missing_instruments(engine: Engine) -> None:
    data = f"isin;data_source;data_symbol;currency;note\n{SAP};stooq;sap.de;EUR;\n{ALV};manual;ALV.DE;EUR;\n".encode()

    with engine.begin() as conn:
        ids = load_mapping_file(conn, data, clock=CLOCK)
    assert len(ids) == 2

    with engine.connect() as conn:
        records = {record["isin"]: record for record in list_instruments(conn)}
    assert records[SAP]["data_source"] == "stooq"
    assert records[ALV]["data_source"] == "manual"


def test_load_mapping_file_defaults_changed_by_to_file(engine: Engine) -> None:
    data = f"{SAP};stooq;sap.de;EUR\n".encode()
    with engine.begin() as conn:
        load_mapping_file(conn, data, clock=CLOCK)
    with engine.connect() as conn:
        changed_by = conn.execute(sa.select(instrument_mapping_log.c.changed_by)).scalar_one()
    assert changed_by == "file"
