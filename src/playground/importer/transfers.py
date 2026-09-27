"""Cost basis for a transfer in (plan 5.3.4, 5.3.5, 5.5): `pg transfers list` and `set-cost`.

A transfer in books a lot without a cost, so the FIFO ledger has quantity and value for it but not
a cost basis or a realised gain when it is sold (its lot is `cost_missing`), and the pipeline
raises `missing_cost_basis` at staging. `set_cost` stores what you enter (the `cost_basis_inputs`
table) and rebuilds the portfolio (`pipeline.refresh_portfolio`): the review item is resolved as
"cost entered", and the lot's cost and acquisition date follow what you gave (5.5). A transaction
can be given a cost more than once, which corrects an earlier entry; only the most recent one is
the one the ledger uses (`Workspace.load` orders them by `entered_at`, then `id`). Given the
market data, the refresh rebuilds the stored value series as well (plan 5.7).

`list_transfers` and `set_cost` see the portfolio as it is now: while a batch is staged, its data
is left out, exactly as `refresh_portfolio` leaves it out until you accept it (plan 5.3.5).
"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.dates import format_ts_utc
from playground.core.errors import PlaygroundError
from playground.core.types import TxnType
from playground.importer.keys import quantity_text
from playground.importer.pipeline import RefreshResult, refresh_portfolio
from playground.importer.workspace import Txn, Workspace
from playground.storage import repos
from playground.valuation.portfolio import MarketData


class TransferError(PlaygroundError):
    """`pg transfers set-cost` was asked for a transfer in that does not exist, or is ambiguous."""


def _workspace(conn: Connection, portfolio_id: int) -> Workspace:
    """The portfolio as `set_cost` and `list_transfers` see it: any staged batch left out (module docstring)."""
    return Workspace.load(conn, portfolio_id, exclude_batch=repos.staged_batch_id(conn, portfolio_id))


def _transfers_in(ws: Workspace, isin: str | None = None) -> list[Txn]:
    """Every transfer in transaction, oldest booking day first; only `isin`'s when given."""
    found = [txn for txn in ws.alive() if txn.fields.type is TxnType.TRANSFER_IN]
    if isin is not None:
        found = [txn for txn in found if txn.fields.isin == isin]
    return sorted(found, key=lambda txn: (txn.day, txn.id or 0))


def transfer_record(txn: Txn, names: Mapping[str, str]) -> dict[str, Any]:
    """One transfer in as JSON-ready data, with its cost basis if you have entered one."""
    fields = txn.fields
    isin = fields.isin
    cost_basis = None
    if txn.cost_input is not None:
        cost_basis = {
            "acquired_on": txn.cost_input.acquired_on.isoformat(),
            "cost_eur": format(txn.cost_input.cost_eur, "f"),
        }
    return {
        "id": txn.id,
        "isin": isin,
        "name": names.get(isin, txn.view.name) if isin else None,
        "quantity": quantity_text(fields.quantity) if fields.quantity is not None else None,
        "booked_on": txn.day.isoformat(),
        "state": txn.stored_state,
        "cost_basis": cost_basis,
    }


def list_transfers(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """Every transfer in, oldest first, with its cost basis if you have entered one (`pg transfers list`)."""
    ws = _workspace(conn, portfolio_id)
    names = repos.instrument_names(conn)
    return [transfer_record(txn, names) for txn in _transfers_in(ws)]


def _candidates_message(isin: str, candidates: Sequence[Txn]) -> str:
    named = [f"transaction {txn.id} on {txn.day.isoformat()}" for txn in candidates]
    listed = named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"
    return f"There are {len(candidates)} transfers in of {isin}: {listed}. Name the one to set the cost for with --txn."


def _find_transfer(ws: Workspace, *, isin: str, txn_id: int | None) -> Txn:
    """The transfer in `set_cost` should act on: the one you named, or the only one there is."""
    if txn_id is not None:
        raw = ws.txns.get(txn_id)
        if raw is None:
            raise TransferError(f"There is no transaction {txn_id}.")
        txn = ws.final(raw.key)
        if txn.fields.type is not TxnType.TRANSFER_IN or txn.fields.isin != isin:
            raise TransferError(f"Transaction {txn.id} is not a transfer in of {isin}.")
        return txn
    candidates = _transfers_in(ws, isin)
    if not candidates:
        raise TransferError(
            f"There is no transfer in of {isin} to set a cost for. Run pg transfers list to see what is there."
        )
    if len(candidates) > 1:
        raise TransferError(_candidates_message(isin, candidates))
    return candidates[0]


def _refresh_dict(result: RefreshResult) -> dict[str, Any]:
    return {
        "review_closed": result.review_closed,
        "review_opened": result.review_opened,
        "lots": result.lots,
        "disposals": result.disposals,
        "values": result.values,
    }


def set_cost(
    conn: Connection,
    portfolio_id: int,
    *,
    isin: str,
    acquired_on: date,
    cost_eur: Decimal,
    clock: Clock,
    txn_id: int | None = None,
    note: str | None = None,
    market: MarketData | None = None,
) -> dict[str, Any]:
    """Enter the cost basis of a transfer in and rebuild the portfolio (`pg transfers set-cost`).

    `txn_id` names the transaction when `isin` has more than one transfer in; without it, there
    must be exactly one. Raises `TransferError` when there is none, when `isin` has more than one
    and `txn_id` does not say which, or when `txn_id` does not name a transfer in of `isin`. With
    `market`, the value series is rebuilt too and `values` summarises it; otherwise it is `None`.
    """
    if cost_eur < 0:
        raise TransferError(f"--cost-eur must not be negative, got {cost_eur}.")
    ws = _workspace(conn, portfolio_id)
    target = _find_transfer(ws, isin=isin, txn_id=txn_id)
    if target.id is None:
        raise RuntimeError("A transfer in read from the registry has no stored id.")

    now = format_ts_utc(clock.now_utc())
    repos.insert_cost_basis_input(
        conn, transaction_id=target.id, acquired_on=acquired_on, cost_eur=cost_eur, entered_at=now, note=note
    )
    refreshed = refresh_portfolio(
        conn, portfolio_id, clock=clock, cause=f"cost entered for the transfer in of {isin}", market=market
    )
    return {
        "isin": isin,
        "transaction_id": target.id,
        "acquired_on": acquired_on.isoformat(),
        "cost_eur": format(cost_eur, "f"),
        **_refresh_dict(refreshed),
    }
