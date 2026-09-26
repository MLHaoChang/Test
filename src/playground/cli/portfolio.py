"""The ledger view and transfer commands (plan 5.9): `holdings`, `lots` and `transfers`.

`pg holdings` and `pg lots` read the accepted portfolio: what `pg accept`, a review resolution or
`pg transfers set-cost` last built (plan 5.3.6, 5.5). `pg holdings` computes the quantity and open
cost basis of every ISIN as of a day, the ledger's own as-of rule (5.5, `ledger.fifo.LotBook`);
`pg lots` lists the stored lot book exactly as it stands now (every lot and disposal). `pg
transfers list` shows every transfer in and whether you have entered its cost basis; `pg transfers
set-cost` enters it, which resolves the `missing_cost_basis` review item and rebuilds the lots at
once (`importer.transfers`).
"""

from collections.abc import Mapping
from typing import Any

import typer

from playground.cli.context import AppContext, fail, parse_day, portfolio_transaction, print_json
from playground.core.errors import NumberFormatError
from playground.core.isin import InvalidIsinError, normalise_isin
from playground.core.numbers import parse_en_decimal
from playground.importer.keys import quantity_text
from playground.importer.reconcile import stored_ledger
from playground.importer.transfers import TransferError, list_transfers, set_cost
from playground.ledger.fifo import build_lots
from playground.storage import repos

transfers_app = typer.Typer(help="Cost basis for transfers in.", no_args_is_help=True)

_JSON = typer.Option(False, "--json", help="Print JSON instead of text.")


def _dec(value: Any) -> str | None:
    return format(value, "f") if value is not None else None


def _normalised_isin(value: str) -> str:
    try:
        return normalise_isin(value)
    except InvalidIsinError as exc:
        fail(str(exc))


# --- holdings -----------------------------------------------------------------------------------


def holdings(
    ctx: typer.Context,
    as_of: str | None = typer.Option(None, "--as-of", help="Show holdings on this day (YYYY-MM-DD). Today by default."),
    json_output: bool = _JSON,
) -> None:
    """Quantity and cost basis of every ISIN held, as of a day (today by default)."""
    app_ctx: AppContext = ctx.obj
    day = parse_day(as_of, "--as-of") or app_ctx.clock.today()
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        book = build_lots(stored_ledger(conn, portfolio_id))
        names = repos.instrument_names(conn)
    quantities = book.holdings(day)
    costs = book.open_cost(day)
    records = [
        {
            "isin": isin,
            "name": names.get(isin, isin),
            "quantity": quantity_text(quantities[isin]),
            "cost_eur": _dec(costs[isin]),
        }
        for isin in sorted(quantities)
    ]
    if json_output:
        print_json({"as_of": day.isoformat(), "holdings": records})
        return
    typer.echo(f"Holdings on {day.isoformat()}:")
    if not records:
        typer.echo("  none")
    for record in records:
        cost = f"{record['cost_eur']} EUR" if record["cost_eur"] is not None else "cost unknown (see pg transfers list)"
        typer.echo(f"  {record['isin']} {record['name']}: {record['quantity']} shares, cost {cost}")


# --- lots ---------------------------------------------------------------------------------------


def _lot_record(row: Any, names: Mapping[str, str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "isin": row.isin,
        "name": names.get(row.isin, row.isin),
        "origin": row.origin,
        "booked_ts": row.booked_ts,
        "opened_ts": row.opened_ts,
        "quantity_initial": _dec(row.quantity_initial),
        "quantity_open": _dec(row.quantity_open),
        "cost_eur_initial": _dec(row.cost_eur_initial),
        "cost_eur_open": _dec(row.cost_eur_open),
        "cost_missing": bool(row.cost_missing),
        "open_transaction": {"id": row.open_txn_id},
    }


def _disposal_record(row: Any, names: Mapping[str, str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "isin": row.isin,
        "name": names.get(row.isin, row.isin),
        "kind": row.kind,
        "ts_utc": row.ts_utc,
        "quantity": _dec(row.quantity),
        "cost_eur": _dec(row.cost_eur),
        "proceeds_eur": _dec(row.proceeds_eur),
        "fees_eur": _dec(row.fees_eur),
        "realised_eur": _dec(row.realised_eur),
        "lot": {"id": row.lot_id},
        "transaction": {"id": row.txn_id},
    }


def lots(
    ctx: typer.Context,
    isin: str | None = typer.Option(None, "--isin", help="Only this ISIN."),
    json_output: bool = _JSON,
) -> None:
    """The stored lot book: every lot and disposal, exactly as accept (or a resolution) last built them."""
    app_ctx: AppContext = ctx.obj
    wanted = _normalised_isin(isin) if isin is not None else None
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        lot_rows = repos.list_lots(conn, portfolio_id, isin=wanted)
        disposal_rows = repos.list_disposals(conn, portfolio_id, isin=wanted)
        names = repos.instrument_names(conn)
    lot_records = [_lot_record(row, names) for row in lot_rows]
    disposal_records = [_disposal_record(row, names) for row in disposal_rows]
    if json_output:
        print_json({"isin": wanted, "lots": lot_records, "disposals": disposal_records})
        return
    typer.echo("Lots:")
    if not lot_records:
        typer.echo("  none")
    for record in lot_records:
        if record["cost_missing"]:
            cost = "cost unknown (see pg transfers list)"
        else:
            cost = f"cost {record['cost_eur_open']} EUR open of {record['cost_eur_initial']} EUR initial"
        typer.echo(
            f"  {record['isin']} {record['name']}: {record['origin']}, booked {record['booked_ts'][:10]}, "
            f"{record['quantity_open']} of {record['quantity_initial']} open, {cost}"
        )
    typer.echo("Disposals:")
    if not disposal_records:
        typer.echo("  none")
    for record in disposal_records:
        realised = f", realised {record['realised_eur']} EUR" if record["realised_eur"] is not None else ""
        typer.echo(
            f"  {record['isin']} {record['name']}: {record['kind']} on {record['ts_utc']}, "
            f"{record['quantity']} shares{realised}"
        )


# --- transfers ------------------------------------------------------------------------------------


@transfers_app.command(name="list")
def transfers_list(ctx: typer.Context, json_output: bool = _JSON) -> None:
    """List every transfer in, and whether you have entered its cost basis."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        records = list_transfers(conn, portfolio_id)
    if json_output:
        print_json({"transfers": records})
        return
    if not records:
        typer.echo("No transfers in.")
    for record in records:
        name = record["name"] or record["isin"]
        if record["cost_basis"] is None:
            cost = (
                f"no cost basis yet: pg transfers set-cost --isin {record['isin']} "
                "--acquired YYYY-MM-DD --cost-eur AMOUNT"
            )
        else:
            cost = f"acquired {record['cost_basis']['acquired_on']}, cost {record['cost_basis']['cost_eur']} EUR"
        typer.echo(
            f"[{record['id']}] {record['isin']} {name}: {record['quantity']} shares "
            f"booked {record['booked_on']}: {cost}"
        )


@transfers_app.command(name="set-cost")
def transfers_set_cost(
    ctx: typer.Context,
    isin: str = typer.Option(..., "--isin", help="The ISIN of the transfer in."),
    acquired: str = typer.Option(..., "--acquired", help="The day you acquired the shares (YYYY-MM-DD)."),
    cost_eur: str = typer.Option(..., "--cost-eur", help="What the shares cost you, in EUR."),
    txn: int | None = typer.Option(None, "--txn", help="The transaction, when --isin names more than one."),
    json_output: bool = _JSON,
) -> None:
    """Enter the cost basis of a transfer in. Resolves missing_cost_basis and rebuilds the lots."""
    app_ctx: AppContext = ctx.obj
    normalised = _normalised_isin(isin)
    acquired_on = parse_day(acquired, "--acquired")
    if acquired_on is None:
        fail("Give the day you acquired the shares with --acquired YYYY-MM-DD.")
    try:
        amount = parse_en_decimal(cost_eur)
    except NumberFormatError as exc:
        fail(f"--cost-eur: {exc}")
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        try:
            result = set_cost(
                conn,
                portfolio_id,
                isin=normalised,
                acquired_on=acquired_on,
                cost_eur=amount,
                clock=app_ctx.clock,
                txn_id=txn,
            )
        except TransferError as exc:
            fail(str(exc))
    if json_output:
        print_json(result)
        return
    typer.echo(
        f"Cost basis entered for transaction {result['transaction_id']} ({normalised}): "
        f"{result['cost_eur']} EUR, acquired {result['acquired_on']}."
    )
    for item in result["review_closed"]:
        resolution = item.get("resolution") or {}
        typer.echo(f"Closed review item {item['id']} ({item['kind']}): {resolution.get('how')}.")
    typer.echo(f"Lots rebuilt: {result['lots']} lots, {result['disposals']} disposals.")


def register(app: typer.Typer) -> None:
    """Add the ledger view and transfer commands to `app`."""
    app.command()(holdings)
    app.command()(lots)
    app.add_typer(transfers_app, name="transfers")
