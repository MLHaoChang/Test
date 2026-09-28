"""The import commands (plan 5.9): `import`, `imports`, `reconcile`, `accept`, `discard`, `transactions`, `status`.

`pg import FILE...` stages one batch and prints the reconciliation diff; nothing changes until
`pg accept`, which rebuilds the lots and then the value series. `pg status` shows the latest value.
Every command that reads takes `--json`. Exit status: 0 on success, 1 on a usage or input error, 3
when `reconcile --strict` finds a difference with your confirmed holdings.
"""

from pathlib import Path
from typing import Any

import typer

from playground.cli.context import (
    EXIT_MISMATCH,
    AppContext,
    fail,
    parse_day,
    portfolio_transaction,
    print_json,
    values_line,
)
from playground.core.dates import format_ts_utc
from playground.core.text import plural, shares
from playground.core.types import TxnType
from playground.importer.confirmed_csv import ConfirmedCsvError, parse_confirmed_csv
from playground.importer.pipeline import (
    accept_batch,
    batch_record,
    discard_batch,
    list_batches,
    portfolio_status,
    resolve_batch_ref,
    stage_files,
)
from playground.importer.reconcile import SOURCE_KIND_NAMES, build_diff, render_diff_text, transaction_records
from playground.importer.review import TYPE_LABELS
from playground.storage import repos

imports_app = typer.Typer(help="Import batches and their diffs.", no_args_is_help=True)

_JSON = typer.Option(False, "--json", help="Print JSON instead of text.")


def import_files(
    ctx: typer.Context,
    files: list[Path] | None = typer.Argument(  # noqa: B008
        None, help="Trade Republic PDF documents and CSV exports, or manual CSV files.", show_default=False
    ),
    json_output: bool = _JSON,
) -> None:
    """Stage one batch of files and print the diff. Nothing changes until you accept it."""
    app_ctx: AppContext = ctx.obj
    if not files:
        fail("Name at least one Trade Republic PDF document or CSV file to import.")
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        summary = stage_files(conn, portfolio_id, files, clock=app_ctx.clock, uploads_dir=app_ctx.uploads_dir)
        diff = build_diff(conn, summary.batch_id, clock=app_ctx.clock)
    if json_output:
        print_json(diff.to_dict())
    else:
        typer.echo(render_diff_text(diff))


def list_command(ctx: typer.Context, json_output: bool = _JSON) -> None:
    """List every import batch with its status and counts."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        batches = list_batches(conn, portfolio_id)
    if json_output:
        print_json({"batches": batches})
        return
    if not batches:
        typer.echo("No imports yet. Import files with: pg import FILE...")
    for batch in batches:
        counts = batch["counts"] or {}
        typer.echo(
            f"Batch {batch['id']}: {batch['status']}, {plural(counts.get('files', 0), 'file')}, "
            f"{plural(counts.get('new', 0), 'new transaction')}, {plural(counts.get('review_new', 0), 'new review item')}"
        )


def show_command(
    ctx: typer.Context,
    batch: str = typer.Argument(..., help='The batch number, or "latest".'),
    as_of: str | None = typer.Option(None, "--as-of", help="Show holdings on this day (YYYY-MM-DD)."),
    json_output: bool = _JSON,
) -> None:
    """Show one batch and its diff."""
    app_ctx: AppContext = ctx.obj
    day = parse_day(as_of, "--as-of")
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        batch_id = resolve_batch_ref(conn, portfolio_id, batch)
        diff = build_diff(conn, batch_id, clock=app_ctx.clock, as_of=day)
    if json_output:
        print_json(diff.to_dict())
    else:
        typer.echo(render_diff_text(diff))


def reconcile(
    ctx: typer.Context,
    batch: str = typer.Argument(..., help='The batch number, or "latest".'),
    confirmed: Path | None = typer.Option(  # noqa: B008
        None,
        "--confirmed",
        help="A CSV file of your holdings: the header isin;quantity;as_of, then one line per position.",
    ),
    as_of: str | None = typer.Option(None, "--as-of", help="Compare holdings on this day (YYYY-MM-DD)."),
    strict: bool = typer.Option(False, "--strict", help="Exit with status 3 if any holding differs."),
    json_output: bool = _JSON,
) -> None:
    """Show the diff of a batch with the comparison against your confirmed holdings."""
    app_ctx: AppContext = ctx.obj
    day = parse_day(as_of, "--as-of")
    rows = None
    if confirmed is not None:
        try:
            rows = parse_confirmed_csv(confirmed.read_bytes())
        except FileNotFoundError:
            fail(f"There is no file {confirmed}.")
        except ConfirmedCsvError as exc:
            fail(f"{confirmed}: {exc}")
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        batch_id = resolve_batch_ref(conn, portfolio_id, batch)
        if rows is not None:
            repos.save_confirmed_holdings(
                conn,
                portfolio_id,
                [(row.as_of, row.isin, row.quantity) for row in rows],
                source="file",
                entered_at=format_ts_utc(app_ctx.clock.now_utc()),
            )
        diff = build_diff(conn, batch_id, clock=app_ctx.clock, confirmed=rows, as_of=day)
    if json_output:
        print_json(diff.to_dict())
    else:
        typer.echo(render_diff_text(diff))
    if strict and diff.confirmed is not None and not diff.confirmed.all_match:
        raise typer.Exit(code=EXIT_MISMATCH)


def accept(
    ctx: typer.Context,
    batch: str = typer.Argument(..., help='The batch number, or "latest".'),
    json_output: bool = _JSON,
) -> None:
    """Accept these transactions into your portfolio copy, and rebuild the lots and the value series."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        batch_id = resolve_batch_ref(conn, portfolio_id, batch)
        result = accept_batch(conn, batch_id, clock=app_ctx.clock, market=app_ctx.market).to_dict()
    if json_output:
        print_json(result)
        return
    typer.echo(
        f"Accepted import batch {batch_id}: {plural(result['accepted'], 'transaction')}, "
        f"{result['held_back']} held back, {result['released']} released."
    )
    typer.echo(f"Lots rebuilt: {plural(result['lots'], 'lot')}, {plural(result['disposals'], 'disposal')}.")
    line = values_line(result["values"])
    if line is not None:
        typer.echo(line)
    for item in result["review_closed"]:
        resolution = item.get("resolution") or {}
        typer.echo(f"Closed review item {item['id']} ({item['kind']}): {resolution.get('how')}.")
    typer.echo(f"Open review items: {result['open_review_items']}.")


def discard(
    ctx: typer.Context,
    batch: str = typer.Argument(..., help='The batch number, or "latest".'),
    json_output: bool = _JSON,
) -> None:
    """Discard a staged batch. Its transactions go, and the review items it raised close."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        batch_id = resolve_batch_ref(conn, portfolio_id, batch)
        result = discard_batch(conn, batch_id, clock=app_ctx.clock).to_dict()
        result["batch"] = batch_record(conn, batch_id)
    if json_output:
        print_json(result)
        return
    typer.echo(
        f"Discarded import batch {batch_id}: {plural(result['removed_transactions'], 'staged transaction')} "
        f"removed, {plural(result['review_closed'], 'review item')} closed."
    )


def transactions(
    ctx: typer.Context,
    date_from: str | None = typer.Option(None, "--from", help="First booking day (YYYY-MM-DD)."),
    date_to: str | None = typer.Option(None, "--to", help="Last booking day (YYYY-MM-DD)."),
    txn_type: str | None = typer.Option(None, "--type", help="Only this type, for example buy or dividend."),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Show at most this many."),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip this many first."),
    json_output: bool = _JSON,
) -> None:
    """List the transactions with the files that report them."""
    app_ctx: AppContext = ctx.obj
    start = parse_day(date_from, "--from")
    end = parse_day(date_to, "--to")
    if txn_type is not None and txn_type not in {member.value for member in TxnType}:
        fail(f"Unknown type {txn_type!r}. Use one of: {', '.join(member.value for member in TxnType)}.")
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        records = transaction_records(
            conn, portfolio_id, date_from=start, date_to=end, txn_type=txn_type, limit=limit, offset=offset
        )
    if json_output:
        print_json({"count": len(records), "transactions": records})
        return
    if not records:
        typer.echo("No transactions.")
    for record in records:
        typer.echo(_transaction_line(record))


# A transaction's state in plain words (the stored values are ids).
_STATE_WORDS = {"accepted": "accepted", "held": "held back", "staged": "staged"}


def _transaction_line(record: dict[str, Any]) -> str:
    """One line of `pg transactions`, in the words the diff and the page use (QA P0 round 2)."""
    label = TYPE_LABELS[TxnType(record["type"])]
    what = record["name"] or record["isin"]
    subject = f"{label} {what}" if what else label
    quantity = f", {shares(record['quantity'])}" if record["quantity"] is not None else ""
    amount = f"{record['amount_eur']} EUR" if record["amount_eur"] is not None else "no cash"
    state = _STATE_WORDS.get(record["state"], record["state"])
    kinds = ", ".join(SOURCE_KIND_NAMES.get(source["kind"], source["kind"]) for source in record["sources"])
    return f"{record['date']} {subject}{quantity}: {amount} [{state}] ref {record['source_ref'] or '-'} from {kinds}"


def status(ctx: typer.Context, json_output: bool = _JSON) -> None:
    """Show the portfolio, the last import, the reminder to import again, the review queue and the latest value."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        summary = portfolio_status(conn, portfolio_id, clock=app_ctx.clock, market=app_ctx.market)
    if json_output:
        print_json(summary)
        return
    portfolio = summary["portfolio"]
    typer.echo(
        f"Portfolio: {portfolio['name']} ({portfolio['base_currency']}). No real orders. Your portfolio is read-only."
    )
    last = summary["last_import_at"]
    typer.echo(f"Last import: {last if last is not None else 'none yet'}")
    reminder = summary["reminder"]
    typer.echo(("Reminder: " if reminder["due"] else "") + reminder["message"])
    if summary["staged_batch"] is not None:
        batch = summary["staged_batch"]
        typer.echo(f'Import batch {batch} is staged: run "pg accept {batch}" or "pg discard {batch}".')
    typer.echo(f"Open review items: {summary['open_review_items']} (see pg review list)")
    counts = summary["transactions"]
    typer.echo(f"Transactions: {counts['accepted']} accepted, {counts['held']} held back, {counts['staged']} staged")
    for line in _latest_value_lines(summary["latest_value"]):
        typer.echo(line)


def _latest_value_lines(latest: dict[str, Any] | None) -> list[str]:
    """The latest value in plain English: the value, the cost basis, and how many flags of each kind it has."""
    if latest is None:
        return ["Latest value: none yet. It appears once an import is accepted."]
    cost = (
        f"cost basis {latest['cost_basis_eur']} EUR"
        if latest["cost_basis_eur"] is not None
        else "cost basis unknown (see pg transfers list)"
    )
    state = "complete" if latest["complete"] else "incomplete: some holdings have no price or rate (see pg value)"
    lines = [f"Latest value: {latest['value_eur']} EUR on {latest['date']}, {cost}, {state}."]
    if latest["flags"]:
        kinds: dict[str, int] = {}
        for label in latest["flags"]:
            kind = label.split(" ", 1)[0]
            kinds[kind] = kinds.get(kind, 0) + 1
        listed = ", ".join(f"{kind} ({count})" for kind, count in kinds.items())
        lines.append(f"Flags on that day: {listed}. pg holdings says what each one means.")
    return lines


def register(app: typer.Typer) -> None:
    """Add the import commands to `app`."""
    app.command(name="import")(import_files)
    imports_app.command(name="list")(list_command)
    imports_app.command(name="show")(show_command)
    app.add_typer(imports_app, name="imports")
    app.command()(reconcile)
    app.command()(accept)
    app.command()(discard)
    app.command()(transactions)
    app.command()(status)
