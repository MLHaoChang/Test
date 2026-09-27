"""The review queue commands (plan 5.9): `pg review list`, `show`, `dismiss`, `resolve` and `export`.

The review queue lists documents, rows and conflicts the app could not handle with certainty.
A resolution applies at once, and the lots and the value series are rebuilt. `pg review export`
writes an item's extracted text to a file, optionally anonymised, so a new layout can be shared and
turned into a fixture without ever sending the file itself (plan 5.3.5).
"""

from pathlib import Path

import typer

from playground.cli.context import AppContext, fail, portfolio_transaction, print_json
from playground.importer.anonymise import anonymise_text
from playground.importer.review import dismiss_item, get_item, list_items, resolve_item

review_app = typer.Typer(help="The review queue: documents, rows and conflicts that need you.", no_args_is_help=True)

_JSON = typer.Option(False, "--json", help="Print JSON instead of text.")


@review_app.command(name="list")
def list_command(
    ctx: typer.Context,
    status: str = typer.Option("open", "--status", help="open, resolved, dismissed or all."),
    json_output: bool = _JSON,
) -> None:
    """List the review items (the open ones unless you ask for another status)."""
    app_ctx: AppContext = ctx.obj
    wanted = None if status == "all" else status
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        items = list_items(conn, portfolio_id, status=wanted)
    if json_output:
        print_json({"status": status, "count": len(items), "items": items})
        return
    if not items:
        typer.echo("No review items." if status == "all" else f"No {status} review items.")
    for item in items:
        where = f" {item['file_name']}" if item["file_name"] else ""
        typer.echo(f"[{item['id']}] {item['kind']} ({item['status']}){where}: {item['message']}")


@review_app.command(name="show")
def show_command(
    ctx: typer.Context,
    item_id: int = typer.Argument(..., help="The review item number."),
    json_output: bool = _JSON,
) -> None:
    """Show one review item with the text or row it came from."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        item = get_item(conn, portfolio_id, item_id)
    if json_output:
        print_json(item)
        return
    typer.echo(f"Review item {item['id']}: {item['kind']} ({item['status']})")
    if item["file_name"]:
        typer.echo(f"File: {item['file_name']}")
    typer.echo(item["message"])
    for key, value in item["fields"].items():
        typer.echo(f"  {key}: {value}")
    if item["resolution"]:
        typer.echo(f"Resolution: {item['resolution']}")
    if item["extracted_text"]:
        typer.echo("Text:")
        typer.echo(item["extracted_text"].rstrip("\n"))


@review_app.command(name="dismiss")
def dismiss_command(
    ctx: typer.Context,
    item_id: int = typer.Argument(..., help="The review item number."),
    reason: str | None = typer.Option(None, "--reason", help="Why you dismiss it (required)."),
    json_output: bool = _JSON,
) -> None:
    """Dismiss an item for good. You cannot undo it.

    A transaction it holds back stays out of your holdings.

    It is released only if a later file gives a missing field, or a manual CSV row replaces figures that did not add up.
    """
    app_ctx: AppContext = ctx.obj
    if not reason:
        fail("Say why you dismiss the item with --reason.")
    with portfolio_transaction(app_ctx) as (conn, _):
        item = dismiss_item(conn, item_id, reason=reason, clock=app_ctx.clock, market=app_ctx.market)
    if json_output:
        print_json(item)
        return
    typer.echo(f"Dismissed review item {item_id}. A dismissal cannot be undone.")


@review_app.command(name="resolve")
def resolve_command(
    ctx: typer.Context,
    item_id: int = typer.Argument(..., help="The review item number."),
    merge: bool = typer.Option(False, "--merge", help="A possible duplicate is the same transaction: merge them."),
    keep_both: bool = typer.Option(False, "--keep-both", help="A possible duplicate is a second transaction."),
    use_parsed: bool = typer.Option(False, "--use-parsed", help="Keep a held-back transaction as it was read."),
    into: int | None = typer.Option(
        None, "--into", help="With --merge: the transaction to merge into, when there is more than one."
    ),
    json_output: bool = _JSON,
) -> None:
    """Settle an item with one of --merge, --keep-both or --use-parsed. Lots are rebuilt at once."""
    app_ctx: AppContext = ctx.obj
    chosen = [name for name, flag in (("merge", merge), ("keep-both", keep_both), ("use-parsed", use_parsed)) if flag]
    if len(chosen) != 1:
        fail("Choose exactly one of --merge, --keep-both or --use-parsed.")
    if into is not None and chosen[0] != "merge":
        fail("--into goes with --merge only.")
    with portfolio_transaction(app_ctx) as (conn, _):
        item = resolve_item(conn, item_id, how=chosen[0], clock=app_ctx.clock, into=into, market=app_ctx.market)
    if json_output:
        print_json(item)
        return
    typer.echo(f"Resolved review item {item_id} with --{chosen[0]}.")


@review_app.command(name="export")
def export_command(
    ctx: typer.Context,
    item_id: int = typer.Argument(..., help="The review item number."),
    out: Path = typer.Option(..., "--out", help="Write the text to this file."),  # noqa: B008
    anonymise: bool = typer.Option(
        False, "--anonymise", help="Mask your name, address, IBAN, depot and order numbers first."
    ),
    json_output: bool = _JSON,
) -> None:
    """Write a review item's extracted text to a file, so a new layout can be shared without the file itself.

    Check the written file yourself before you share it: `anonymise_text` is a first pass, not a guarantee.
    """
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, portfolio_id):
        item = get_item(conn, portfolio_id, item_id)
    text = item["extracted_text"]
    if not text:
        fail(f"Review item {item_id} has no extracted text to export.")
    if anonymise:
        text = anonymise_text(text)
    out.write_text(text, encoding="utf-8")
    if json_output:
        print_json({"id": item_id, "out": str(out), "anonymised": anonymise})
        return
    what = "Anonymised text" if anonymise else "Text"
    typer.echo(f"{what} of review item {item_id} written to {out}.")


def register(app: typer.Typer) -> None:
    """Add the review commands to `app`."""
    app.add_typer(review_app, name="review")
