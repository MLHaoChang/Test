"""Small, table-grouped query functions used by the CLI and API (plan 5.2).

Every function here takes a `Connection` (never opens its own engine) so
the caller controls the transaction and can compose several calls into
one. WP2 added what `pg init` needs (`get_or_create_portfolio`); WP7 adds
the functions of the import pipeline: instruments named by documents,
batches, imports, transactions and their sources, review items, the lot
book and the time of the last import. Later work packages add theirs.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Row

from playground.core.clock import Clock
from playground.core.dates import format_ts_utc
from playground.storage.schema import (
    confirmed_holdings,
    cost_basis_inputs,
    disposals,
    import_batches,
    imports,
    instrument_mapping_log,
    instruments,
    lots,
    portfolios,
    review_items,
    transaction_sources,
    transactions,
)

#: P0 uses exactly one portfolio, named exactly this (spec 1.5).
DEFAULT_PORTFOLIO_NAME = "Trade Republic"


def get_or_create_portfolio(
    conn: Connection,
    *,
    clock: Clock,
    name: str = DEFAULT_PORTFOLIO_NAME,
    kind: str = "real",
    base_currency: str = "EUR",
    source: str = "trade_republic",
) -> int:
    """Return the id of the portfolio named `name`, creating it if it does not exist yet.

    `pg init` calls this with the defaults to create P0's one portfolio
    (spec 1.5). `created_at` comes from `clock`, never the system clock
    (3.3), so the same `PG_TODAY` always gives the same stored timestamp.
    Calling this again for the same `name` is a no-op that returns the
    existing id.
    """
    existing = conn.execute(sa.select(portfolios.c.id).where(portfolios.c.name == name)).first()
    if existing is not None:
        return existing.id

    result = conn.execute(
        sa.insert(portfolios).values(
            name=name,
            kind=kind,
            base_currency=base_currency,
            source=source,
            last_import_at=None,
            created_at=format_ts_utc(clock.now_utc()),
        )
    )
    inserted_id = result.inserted_primary_key
    if inserted_id is None:
        raise RuntimeError("get_or_create_portfolio: insert did not return a primary key.")
    return int(inserted_id[0])


# --- Instruments ------------------------------------------------------------------------------


def upsert_instrument_from_document(conn: Connection, *, isin: str, name: str | None) -> int:
    """Return the id of the instrument `isin`, creating it as `unmapped` if it does not exist yet.

    The name comes from the document that reported it; an instrument known so far only from a
    manual row (which names no instrument) is named by its ISIN until a document names it. This
    never sets a price symbol or a mapping (plan 5.6): mapping takes an explicit command.
    """
    row = conn.execute(sa.select(instruments.c.id, instruments.c.name).where(instruments.c.isin == isin)).first()
    if row is not None:
        if name and row.name == isin:
            conn.execute(sa.update(instruments).where(instruments.c.id == row.id).values(name=name))
        return int(row.id)
    result = conn.execute(
        sa.insert(instruments).values(isin=isin, name=name or isin, type="unknown", mapping_status="unmapped")
    )
    return _inserted_id(result)


def instrument_names(conn: Connection) -> dict[str, str]:
    """The name of every known instrument, by ISIN."""
    return {row.isin: row.name for row in conn.execute(sa.select(instruments.c.isin, instruments.c.name))}


def delete_unused_instruments(conn: Connection) -> int:
    """Delete unmapped instruments that no transaction, lot or mapping log row refers to (after a discard)."""
    used = (
        sa.select(transactions.c.instrument_id)
        .where(transactions.c.instrument_id.is_not(None))
        .union(sa.select(lots.c.instrument_id), sa.select(instrument_mapping_log.c.instrument_id))
    )
    result = conn.execute(
        sa.delete(instruments).where(instruments.c.mapping_status == "unmapped", instruments.c.id.not_in(used))
    )
    return int(result.rowcount or 0)


# --- Import batches and imports ---------------------------------------------------------------


def insert_batch(conn: Connection, *, portfolio_id: int, created_at: str) -> int:
    """Create a staged import batch and return its id."""
    result = conn.execute(
        sa.insert(import_batches).values(
            portfolio_id=portfolio_id, created_at=created_at, status="staged", accepted_at=None, summary_json=None
        )
    )
    return _inserted_id(result)


def get_batch(conn: Connection, batch_id: int) -> Row[Any] | None:
    return conn.execute(sa.select(import_batches).where(import_batches.c.id == batch_id)).first()


def staged_batch_id(conn: Connection, portfolio_id: int) -> int | None:
    """The id of the portfolio's staged batch, or `None` (only one can be staged at a time)."""
    value = conn.execute(
        sa.select(import_batches.c.id).where(
            import_batches.c.portfolio_id == portfolio_id, import_batches.c.status == "staged"
        )
    ).scalar()
    return int(value) if value is not None else None


def latest_batch_id(conn: Connection, portfolio_id: int) -> int | None:
    value = conn.execute(
        sa.select(sa.func.max(import_batches.c.id)).where(import_batches.c.portfolio_id == portfolio_id)
    ).scalar()
    return int(value) if value is not None else None


def list_batches(conn: Connection, portfolio_id: int) -> list[Row[Any]]:
    return list(
        conn.execute(
            sa.select(import_batches).where(import_batches.c.portfolio_id == portfolio_id).order_by(import_batches.c.id)
        ).all()
    )


def set_batch_status(conn: Connection, batch_id: int, status: str, *, accepted_at: str | None = None) -> None:
    values: dict[str, Any] = {"status": status}
    if accepted_at is not None:
        values["accepted_at"] = accepted_at
    conn.execute(sa.update(import_batches).where(import_batches.c.id == batch_id).values(**values))


def set_batch_summary(conn: Connection, batch_id: int, summary: Mapping[str, Any]) -> None:
    conn.execute(sa.update(import_batches).where(import_batches.c.id == batch_id).values(summary_json=dict(summary)))


def insert_import(conn: Connection, **values: Any) -> int:
    """Record one file of a batch (the `imports` table, plan 4.1) and return its id."""
    return _inserted_id(conn.execute(sa.insert(imports), values))


def set_import_status(conn: Connection, import_id: int, status: str) -> None:
    conn.execute(sa.update(imports).where(imports.c.id == import_id).values(status=status))


# --- Transactions and their sources -----------------------------------------------------------


def insert_transaction(conn: Connection, **values: Any) -> int:
    # The values go in as parameters, so one compiled statement serves every row.
    return _inserted_id(conn.execute(sa.insert(transactions), values))


def update_transaction(conn: Connection, txn_id: int, **values: Any) -> None:
    statement = sa.update(transactions).where(transactions.c.id == sa.bindparam("row_id"))
    conn.execute(statement, {"row_id": txn_id, **values})


def add_source(conn: Connection, **values: Any) -> int:
    """Record one report of a transaction (the `transaction_sources` table) and return its id."""
    return _inserted_id(conn.execute(sa.insert(transaction_sources), values))


def add_sources(conn: Connection, rows: Sequence[Mapping[str, Any]]) -> None:
    """Record many reports at once (one statement); their ids are not needed back."""
    if rows:
        conn.execute(sa.insert(transaction_sources), [dict(row) for row in rows])


def merge_transaction_rows(conn: Connection, *, source_id: int, target_id: int) -> None:
    """Move every source, review item and cost input of transaction `source_id` to `target_id`, then delete it."""
    conn.execute(
        sa.update(transaction_sources)
        .where(transaction_sources.c.transaction_id == source_id)
        .values(transaction_id=target_id)
    )
    conn.execute(
        sa.update(review_items).where(review_items.c.transaction_id == source_id).values(transaction_id=target_id)
    )
    conn.execute(
        sa.update(cost_basis_inputs)
        .where(cost_basis_inputs.c.transaction_id == source_id)
        .values(transaction_id=target_id)
    )
    conn.execute(sa.delete(transactions).where(transactions.c.id == source_id))


# --- Review items -----------------------------------------------------------------------------


def open_review_item(conn: Connection, *, dedupe_key: str, **values: Any) -> int:
    """Store a new review item; `dedupe_key` stops the same item from being stored twice (plan 4.1)."""
    return _inserted_id(conn.execute(sa.insert(review_items), {"dedupe_key": dedupe_key, "status": "open", **values}))


def close_review_item(
    conn: Connection, item_id: int, *, resolution: Mapping[str, Any], resolved_at: str, status: str = "resolved"
) -> None:
    """Mark an item resolved (or dismissed) with how it was settled, for example `{"how": "superseded", "by": ...}`."""
    update_review_item(conn, item_id, status=status, resolution_json=dict(resolution), resolved_at=resolved_at)


def reopen_review_item(conn: Connection, item_id: int) -> None:
    """Open an item again whose problem came back after a later file had removed it."""
    update_review_item(conn, item_id, status="open", resolution_json=None, resolved_at=None)


def update_review_item(conn: Connection, item_id: int, **values: Any) -> None:
    statement = sa.update(review_items).where(review_items.c.id == sa.bindparam("row_id"))
    conn.execute(statement, {"row_id": item_id, **values})


# --- Lots and disposals -----------------------------------------------------------------------


def replace_lots_and_disposals(
    conn: Connection, portfolio_id: int, book: Any, instrument_ids: Mapping[str, int]
) -> None:
    """Replace the portfolio's stored lot book with `book` (a `ledger.fifo.LotBook`, plan 5.5).

    The ids in `book` are transaction ids. The caller runs this inside one database transaction,
    so the old and the new lot book are never mixed.
    """
    delete_lots_and_disposals(conn, portfolio_id)
    lot_ids: dict[int, int] = {}
    insert_lot = sa.insert(lots)
    for lot in book.lots:
        values = {
            "portfolio_id": portfolio_id,
            "instrument_id": instrument_ids[lot.isin],
            "open_txn_id": lot.open_txn_id,
            "origin": lot.origin,
            "booked_ts": format_ts_utc(lot.booked_ts),
            "opened_ts": format_ts_utc(lot.opened_ts),
            "quantity_initial": lot.quantity_initial,
            "quantity_open": lot.quantity_open,
            "cost_eur_initial": lot.cost_eur_initial,
            "cost_eur_open": lot.cost_eur_open,
            "cost_missing": 1 if lot.cost_missing else 0,
        }
        lot_ids[lot.open_txn_id] = _inserted_id(conn.execute(insert_lot, values))
    rows = [
        {
            "portfolio_id": portfolio_id,
            "lot_id": lot_ids[disposal.lot_open_txn_id],
            "txn_id": disposal.txn_id,
            "kind": disposal.kind,
            "ts_utc": format_ts_utc(disposal.ts_utc),
            "quantity": disposal.quantity,
            "cost_eur": disposal.cost_eur,
            "proceeds_eur": disposal.proceeds_eur,
            "fees_eur": disposal.fees_eur,
            "realised_eur": disposal.realised_eur,
        }
        for disposal in book.disposals
    ]
    if rows:
        conn.execute(sa.insert(disposals), rows)


def delete_lots_and_disposals(conn: Connection, portfolio_id: int) -> None:
    conn.execute(sa.delete(disposals).where(disposals.c.portfolio_id == portfolio_id))
    conn.execute(sa.delete(lots).where(lots.c.portfolio_id == portfolio_id))


# --- Portfolios -------------------------------------------------------------------------------


def get_portfolio(conn: Connection, portfolio_id: int) -> Row[Any]:
    return conn.execute(sa.select(portfolios).where(portfolios.c.id == portfolio_id)).one()


def default_portfolio_id(conn: Connection) -> int | None:
    """The id of the one P0 portfolio, or `None` before `pg init`."""
    value = conn.execute(sa.select(portfolios.c.id).where(portfolios.c.name == DEFAULT_PORTFOLIO_NAME)).scalar()
    return int(value) if value is not None else None


def set_last_import_at(conn: Connection, portfolio_id: int, when: str) -> None:
    conn.execute(sa.update(portfolios).where(portfolios.c.id == portfolio_id).values(last_import_at=when))


# --- Confirmed holdings -----------------------------------------------------------------------


def save_confirmed_holdings(
    conn: Connection, portfolio_id: int, rows: Sequence[tuple[date, str, Decimal]], *, source: str, entered_at: str
) -> None:
    """Store the holdings you confirmed, replacing earlier ones for the same days and ISINs."""
    for as_of, isin, quantity in rows:
        conn.execute(
            sa.delete(confirmed_holdings).where(
                confirmed_holdings.c.portfolio_id == portfolio_id,
                confirmed_holdings.c.as_of == as_of,
                confirmed_holdings.c.isin == isin,
            )
        )
        conn.execute(
            sa.insert(confirmed_holdings).values(
                portfolio_id=portfolio_id,
                as_of=as_of,
                isin=isin,
                quantity=quantity,
                source=source,
                entered_at=entered_at,
            )
        )


def _inserted_id(result: Any) -> int:
    inserted = result.inserted_primary_key
    if inserted is None:
        raise RuntimeError("An insert did not return a primary key.")
    return int(inserted[0])
