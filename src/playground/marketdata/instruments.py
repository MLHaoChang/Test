"""The instrument master: the editable, logged ISIN-to-symbol mapping (plan 5.6).

`upsert_instrument_from_document` (`storage/repos.py`, WP2) is the only other code that creates an
`instruments` row, and it never sets a price mapping. `set_mapping` is the one path that does, and
it always writes an `instrument_mapping_log` row first, so `pg instruments history` can show every
change (AC10: "logged on every change ... never set without an explicit command or file").
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.dates import format_ts_utc
from playground.core.errors import InvalidIsinError, PlaygroundError
from playground.core.isin import normalise_isin
from playground.storage import repos
from playground.storage.schema import instrument_mapping_log, instruments

#: `instruments.data_source` (plan 4.1): Stooq, or a manual price file (6.5).
VALID_SOURCES = frozenset({"stooq", "manual"})
#: `instrument_mapping_log.changed_by` (plan 4.1): who ran the command that changed the mapping.
CHANGED_BY_VALUES = frozenset({"cli", "api", "file"})

MAPPING_FILE_HEADER = ("isin", "data_source", "data_symbol", "currency", "note")


class InstrumentMappingError(PlaygroundError):
    """A mapping command or a mapping file row is invalid (plan 5.6)."""


def set_mapping(
    conn: Connection,
    *,
    isin: str,
    source: str,
    symbol: str,
    currency: str,
    changed_by: str,
    clock: Clock,
    note: str | None = None,
) -> int:
    """Set `isin`'s data source, symbol and currency; log the change; mark it confirmed (plan 5.6).

    Creates the instrument, named by its own ISIN, if no document has reported it yet -- so a
    mapping file can map an ISIN before any import (WP9's e2e step 18 runs on a data directory
    with none). Returns the instrument's id. Raises `InstrumentMappingError` for an unknown
    `source`, a `currency` that is not a 3-letter code, or an unknown `changed_by`.
    """
    if source not in VALID_SOURCES:
        raise InstrumentMappingError(f"Unknown data source {source!r}. Use one of: {', '.join(sorted(VALID_SOURCES))}.")
    if changed_by not in CHANGED_BY_VALUES:
        raise InstrumentMappingError(f"Unknown changed_by {changed_by!r}.")
    if len(currency) != 3 or not currency.isalpha():
        raise InstrumentMappingError(f"Not a 3-letter currency code: {currency!r}.")
    currency = currency.upper()
    if not symbol:
        raise InstrumentMappingError("A data symbol is required.")

    instrument_id = repos.upsert_instrument_from_document(conn, isin=isin, name=None)
    before = conn.execute(
        sa.select(instruments.c.data_source, instruments.c.data_symbol, instruments.c.currency).where(
            instruments.c.id == instrument_id
        )
    ).one()

    now = format_ts_utc(clock.now_utc())
    conn.execute(
        sa.insert(instrument_mapping_log).values(
            instrument_id=instrument_id,
            changed_at=now,
            changed_by=changed_by,
            old_source=before.data_source,
            old_symbol=before.data_symbol,
            old_currency=before.currency,
            new_source=source,
            new_symbol=symbol,
            new_currency=currency,
            note=note,
        )
    )
    conn.execute(
        sa.update(instruments)
        .where(instruments.c.id == instrument_id)
        .values(
            data_source=source,
            data_symbol=symbol,
            currency=currency,
            mapping_status="confirmed",
            mapping_updated_at=now,
            mapping_note=note,
        )
    )
    return instrument_id


@dataclass(frozen=True)
class MappingRow:
    """One row of a mapping file (plan 5.6): `isin;data_source;data_symbol;currency;note`."""

    isin: str
    source: str
    symbol: str
    currency: str
    note: str | None


def parse_mapping_file(data: bytes) -> list[MappingRow]:
    """Parse `isin;data_source;data_symbol;currency[;note]` rows (plan 5.6, `pg instruments map --file`).

    A header line (`isin;data_source;...`, case-insensitive) is accepted and skipped if present.
    Raises `InstrumentMappingError`, naming the 1-based line, for a wrong number of fields or an
    invalid ISIN; `set_mapping` raises for the rest (an unknown source or currency).
    """
    text = data.decode("utf-8-sig")
    rows: list[MappingRow] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = next(csv.reader([line], delimiter=";"))
        if fields[0].strip().lower() == "isin":
            continue
        if len(fields) not in (4, 5):
            raise InstrumentMappingError(
                f"Line {line_no}: expected {';'.join(MAPPING_FILE_HEADER)}, got {len(fields)} fields: {line!r}."
            )
        isin_text, source, symbol, currency, *rest = (field.strip() for field in fields)
        try:
            isin = normalise_isin(isin_text)
        except InvalidIsinError as exc:
            raise InstrumentMappingError(f"Line {line_no}: {exc}") from exc
        note = rest[0] if rest and rest[0] else None
        rows.append(MappingRow(isin=isin, source=source, symbol=symbol, currency=currency, note=note))
    return rows


def load_mapping_file(conn: Connection, data: bytes, *, clock: Clock, changed_by: str = "file") -> list[int]:
    """Apply every row of a mapping file through `set_mapping` (plan 5.6). Returns the instrument ids touched."""
    return [
        set_mapping(
            conn,
            isin=row.isin,
            source=row.source,
            symbol=row.symbol,
            currency=row.currency,
            note=row.note,
            changed_by=changed_by,
            clock=clock,
        )
        for row in parse_mapping_file(data)
    ]


def list_instruments(conn: Connection) -> list[dict[str, Any]]:
    """Every known instrument with its mapping, ISIN order (`pg instruments list`, AC10)."""
    rows = conn.execute(sa.select(instruments).order_by(instruments.c.isin)).all()
    return [
        {
            "isin": row.isin,
            "name": row.name,
            "type": row.type,
            "currency": row.currency,
            "data_source": row.data_source,
            "data_symbol": row.data_symbol,
            "mapping_status": row.mapping_status,
            "mapping_updated_at": row.mapping_updated_at,
            "mapping_note": row.mapping_note,
        }
        for row in rows
    ]


def get_instrument(conn: Connection, isin: str) -> dict[str, Any] | None:
    row = conn.execute(sa.select(instruments).where(instruments.c.isin == isin)).first()
    if row is None:
        return None
    return {
        "isin": row.isin,
        "name": row.name,
        "type": row.type,
        "currency": row.currency,
        "data_source": row.data_source,
        "data_symbol": row.data_symbol,
        "mapping_status": row.mapping_status,
        "mapping_updated_at": row.mapping_updated_at,
        "mapping_note": row.mapping_note,
    }


def mapping_history(conn: Connection, isin: str) -> list[dict[str, Any]]:
    """Every mapping change for `isin`, oldest first (`pg instruments history`). Raises if `isin` is unknown."""
    instrument_id = conn.execute(sa.select(instruments.c.id).where(instruments.c.isin == isin)).scalar()
    if instrument_id is None:
        raise InstrumentMappingError(f"No instrument known for ISIN {isin}.")
    rows: Sequence[Any] = conn.execute(
        sa.select(instrument_mapping_log)
        .where(instrument_mapping_log.c.instrument_id == instrument_id)
        .order_by(instrument_mapping_log.c.id)
    ).all()
    return [
        {
            "changed_at": row.changed_at,
            "changed_by": row.changed_by,
            "old": {"source": row.old_source, "symbol": row.old_symbol, "currency": row.old_currency},
            "new": {"source": row.new_source, "symbol": row.new_symbol, "currency": row.new_currency},
            "note": row.note,
        }
        for row in rows
    ]
