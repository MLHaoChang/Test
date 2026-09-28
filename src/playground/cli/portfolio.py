"""The ledger view, value and transfer commands (plan 5.9): `holdings`, `lots`, `value` and `transfers`.

`pg holdings`, `pg lots` and `pg value` read the accepted portfolio: what `pg accept`, a review
resolution or `pg transfers set-cost` last built (plan 5.3.6, 5.5). `pg holdings` computes the
quantity and open cost basis of every ISIN as of a day, the ledger's own as-of rule (5.5,
`ledger.fifo.LotBook`), and values each holding at the latest close and ECB rate on or before that
day, with the evidence and flags behind the value (5.7). `pg lots` lists the stored lot book exactly
as it stands now (every lot and disposal). `pg value` works out the daily value in EUR over a range,
stores it and prints a summary with every flag (a **flag** is a note on a value: a holding left
out, a price or rate that is old, or a check that failed). It never fails on missing data. `pg
transfers list` shows every transfer in and whether you have entered its cost basis; `pg transfers
set-cost` enters it, which resolves the `missing_cost_basis` review item and rebuilds the lots and
the value series at once (`importer.transfers`).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from playground.cli.context import AppContext, fail, parse_day, portfolio_transaction, print_json, values_line
from playground.core.dates import berlin_day
from playground.core.errors import NumberFormatError
from playground.core.isin import InvalidIsinError, normalise_isin
from playground.core.numbers import parse_en_decimal
from playground.core.text import plural, shares
from playground.importer.transfers import list_transfers, set_cost
from playground.storage import repos
from playground.valuation.portfolio import ValueReport, compute_values, flag_summaries, holdings_report, money

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


# Why a holding has no value, by the flag that left it out (plan 5.7).
_NO_VALUE = {
    "unmapped": "no price source mapped yet (pg instruments map)",
    "missing_price": "no close stored on or before this day (pg prices fetch or pg prices import-file)",
    "missing_fx": "no ECB rate stored on or before this day (pg fx fetch)",
}


def holdings(
    ctx: typer.Context,
    as_of: str | None = typer.Option(None, "--as-of", help="Show holdings on this day (YYYY-MM-DD). Today by default."),
    json_output: bool = _JSON,
) -> None:
    """Quantity, cost basis and value in EUR of every ISIN held, as of a day (today by default)."""
    app_ctx: AppContext = ctx.obj
    day = parse_day(as_of, "--as-of") or app_ctx.clock.today()
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        report = holdings_report(conn, portfolio_id, day=day, market=app_ctx.market)
    if json_output:
        print_json(report)
        return
    typer.echo(f"Holdings on {report['as_of']}, valued in EUR at the latest close on or before that day:")
    if not report["holdings"]:
        typer.echo("  none")
    for record in report["holdings"]:
        typer.echo("  " + _holding_line(record))
    if report["holdings"]:
        cost = (
            f"cost basis {report['cost_basis_eur']} EUR"
            if report["cost_basis_eur"] is not None
            else "cost basis unknown (see pg transfers list)"
        )
        state = "complete" if report["complete"] else "incomplete: some holdings have no value"
        typer.echo(f"Total value {report['value_eur']} EUR, {cost}, {state}.")


def _holding_line(record: Mapping[str, Any]) -> str:
    # A holding's cost is unknown only while a transfer in has no cost basis: the pipeline holds
    # back a purchase without its amount (plan 5.3.4), so no purchase lot lacks its cost.
    cost = (
        f"cost {record['cost_eur']} EUR" if record["cost_eur"] is not None else "cost unknown (see pg transfers list)"
    )
    line = f"{record['isin']} {record['name']}: {shares(record['quantity'])}, {cost}"
    kinds = [flag["kind"] for flag in record["flags"]]
    if record["value_eur"] is None:
        reason = next((_NO_VALUE[kind] for kind in kinds if kind in _NO_VALUE), "no value")
        line += f", no value: {reason}"
    else:
        price = record["price"]
        evidence = f"close {price['close']} {price['currency']} on {price['date']}"
        if record["pricing_quantity"] != record["quantity"]:
            # A split-adjusted series quotes today's share basis (plan 5.7, quantity for pricing).
            evidence = f"priced as {shares(record['pricing_quantity'])} after later splits, {evidence}"
        if record["fx"] is not None:
            fx = record["fx"]
            evidence += f", at {fx['rate']} {fx['currency']} per EUR on {fx['date']}"
        line += f", value {record['value_eur']} EUR ({evidence})"
    notes = [kind for kind in kinds if kind not in _NO_VALUE]
    if notes:
        line += f". Flags: {', '.join(notes)}"
    return line


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


def lot_line(record: Mapping[str, Any]) -> str:
    """One lot of `pg lots` in plain text. An unknown cost says how to enter it, where you can."""
    if not record["cost_missing"]:
        cost = f"cost {record['cost_eur_open']} EUR open of {record['cost_eur_initial']} EUR initial"
    elif record["origin"] == "transfer_in":
        cost = (
            f"cost unknown (enter it with pg transfers set-cost --isin {record['isin']} "
            "--acquired YYYY-MM-DD --cost-eur AMOUNT)"
        )
    else:
        cost = "cost unknown"
    origin = _ORIGIN_WORDS.get(record["origin"], str(record["origin"]).replace("_", " "))
    return (
        f"{record['isin']} {record['name']}: {origin}, booked {berlin_day(record['booked_ts']).isoformat()}, "
        f"{record['quantity_open']} of {record['quantity_initial']} open, {cost}"
    )


# A lot's origin and a disposal's kind in plain words (the stored values are ids).
_ORIGIN_WORDS = {"buy": "purchase", "transfer_in": "transfer in"}
_DISPOSAL_WORDS = {"sell": "sale", "transfer_out": "transfer out"}


def disposal_line(record: Mapping[str, Any]) -> str:
    """One disposal of `pg lots` in plain text, on its Berlin date."""
    kind = _DISPOSAL_WORDS.get(record["kind"], str(record["kind"]).replace("_", " "))
    realised = f", realised {record['realised_eur']} EUR" if record["realised_eur"] is not None else ""
    day = berlin_day(record["ts_utc"]).isoformat()
    return f"{record['isin']} {record['name']}: {kind} on {day}, {shares(record['quantity'])}{realised}"


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
        typer.echo(f"  {lot_line(record)}")
    typer.echo("Disposals:")
    if not disposal_records:
        typer.echo("  none")
    for record in disposal_records:
        typer.echo(f"  {disposal_line(record)}")


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
            f"[{record['id']}] {record['isin']} {name}: {shares(record['quantity'])} booked {record['booked_on']}: {cost}"
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
        result = set_cost(
            conn,
            portfolio_id,
            isin=normalised,
            acquired_on=acquired_on,
            cost_eur=amount,
            clock=app_ctx.clock,
            txn_id=txn,
            market=app_ctx.market,
        )
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
    typer.echo(f"Lots rebuilt: {plural(result['lots'], 'lot')}, {plural(result['disposals'], 'disposal')}.")
    line = values_line(result["values"])
    if line is not None:
        typer.echo(line)


# --- value ----------------------------------------------------------------------------------------


def value(
    ctx: typer.Context,
    date_from: str | None = typer.Option(
        None, "--from", help="First day (YYYY-MM-DD). The day of your first accepted transaction by default."
    ),
    date_to: str | None = typer.Option(
        None, "--to", help="Last day (YYYY-MM-DD), today at the latest. The last weekday up to today by default."
    ),
    csv_file: Path | None = typer.Option(  # noqa: B008
        None, "--csv", help="Also write one row per day to this CSV file (semicolons, dot decimals)."
    ),
    json_output: bool = _JSON,
) -> None:
    """Work out the daily value of your portfolio in EUR, store it and list every flag.

    One value per weekday, from the latest close and ECB rate on or before each day. A holding
    without a price source, a close or a rate is left out and flagged; nothing fails.
    """
    app_ctx: AppContext = ctx.obj
    start = parse_day(date_from, "--from")
    end = parse_day(date_to, "--to")
    today = app_ctx.clock.today()
    if start is not None and end is not None and start > end:
        fail(f"--from ({start.isoformat()}) is after --to ({end.isoformat()}). Give a --from on or before --to.")
    if end is not None and end > today:
        fail(
            f"--to ({end.isoformat()}) is after today ({today.isoformat()}). The value series ends today at the latest."
        )
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        report = compute_values(conn, portfolio_id, market=app_ctx.market, clock=app_ctx.clock, start=start, end=end)
    if csv_file is not None and report.start is not None:
        try:
            csv_file.write_text(report.csv_text(), encoding="utf-8")
        except OSError as exc:
            fail(f"Could not write {csv_file}: {exc.strerror or exc}.")
    if json_output:
        print_json(report.to_dict())
        return
    for line in _value_lines(report, csv_file):
        typer.echo(line)


def _value_lines(report: ValueReport, csv_file: Path | None) -> list[str]:
    if report.start is None or report.end is None:
        return ["Nothing to value yet: no transaction has been accepted. Import your files with pg import FILE..."]
    lines = [
        f"Value in EUR from {report.start.isoformat()} to {report.end.isoformat()}: "
        f"{plural(len(report.days), 'weekday')}, {report.complete_days} complete."
    ]
    latest = report.latest
    if latest is not None:
        cost = (
            f"cost basis {money(latest.cost_basis_eur)} EUR"
            if latest.cost_basis_eur is not None
            else "cost basis unknown (see pg transfers list)"
        )
        state = "complete" if latest.complete else "incomplete: some holdings have no value"
        lines.append(f"Latest value: {money(latest.value_eur)} EUR on {latest.date.isoformat()}, {cost}, {state}.")
    summaries = flag_summaries(report.days, report.names)
    if summaries:
        lines.append("Flags (notes on the values: a holding left out, an old price or rate, or a failed check):")
        for summary in summaries:
            who = f"{summary['isin']} {summary['name']}" if summary["isin"] is not None else "the portfolio"
            span = summary["first"] if summary["days"] == 1 else f"{summary['first']} to {summary['last']}"
            count = plural(summary["days"], "day")
            lines.append(f"  {summary['kind']}, {who}, {span} ({count}): {summary['detail']}")
    else:
        lines.append("No flags: every holding has a price source, a recent close and a recent rate.")
    stored = f"Stored {plural(len(report.days), 'day')}."
    lines.append(
        f"{stored} Wrote them to {csv_file}." if csv_file is not None else f"{stored} Add --csv FILE for a file."
    )
    return lines


def register(app: typer.Typer) -> None:
    """Add the ledger view, value and transfer commands to `app`."""
    app.command()(holdings)
    app.command()(lots)
    app.command()(value)
    app.add_typer(transfers_app, name="transfers")
