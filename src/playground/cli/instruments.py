"""The instrument master commands (plan 5.9): `list`, `map`, `suggest`, `history`.

Nothing here ever sets a mapping without you asking: `map` is the only command that calls
`marketdata.instruments.set_mapping`, and `suggest` only prints what OpenFIGI answers (plan 5.6,
6.7, AC10). `history` reads back the `instrument_mapping_log` table every mapping change writes.
"""

import os
from pathlib import Path

import typer

from playground.cli.context import AppContext, fail, portfolio_transaction, print_json
from playground.core.isin import InvalidIsinError, normalise_isin
from playground.core.text import plural
from playground.marketdata.instruments import (
    InstrumentMappingError,
    get_instrument,
    list_instruments,
    load_mapping_file,
    mapping_history,
    set_mapping,
)
from playground.marketdata.openfigi import API_KEY_ENV_VAR, OpenFigiClient, OpenFigiError

instruments_app = typer.Typer(help="The instrument master: ISIN to price-source mapping.", no_args_is_help=True)

_JSON = typer.Option(False, "--json", help="Print JSON instead of text.")


def _normalised_isin(value: str) -> str:
    try:
        return normalise_isin(value)
    except InvalidIsinError as exc:
        fail(str(exc))


@instruments_app.command(name="list")
def list_command(ctx: typer.Context, json_output: bool = _JSON) -> None:
    """List every known instrument, and its mapping when it has one."""
    app_ctx: AppContext = ctx.obj
    with portfolio_transaction(app_ctx) as (conn, _):
        records = list_instruments(conn)
    if json_output:
        print_json({"instruments": records})
        return
    if not records:
        typer.echo("No instruments yet. They appear once you import a document, or map one.")
    for record in records:
        if record["mapping_status"] == "confirmed":
            mapping = f"{record['data_source']} {record['data_symbol']} ({record['currency']})"
        else:
            mapping = "unmapped"
        typer.echo(f"{record['isin']} {record['name']}: {mapping}")


@instruments_app.command(name="map")
def map_command(
    ctx: typer.Context,
    isin: str | None = typer.Argument(None, help="The ISIN to map (omit this and use --file instead)."),
    source: str | None = typer.Option(None, "--source", help="stooq or manual."),
    symbol: str | None = typer.Option(None, "--symbol", help="The data source's symbol for this instrument."),
    currency: str | None = typer.Option(None, "--currency", help="The listing currency of --symbol."),
    note: str | None = typer.Option(None, "--note", help="An optional note."),
    file: Path | None = typer.Option(  # noqa: B008
        None, "--file", help="Apply isin;data_source;data_symbol;currency;note rows from FILE."
    ),
    json_output: bool = _JSON,
) -> None:
    """Map one instrument (an ISIN with --source, --symbol and --currency), or many with --file.

    Creates the instrument if no document has named it yet, so you can map an ISIN before your
    first import. Every change is logged (pg instruments history); nothing here is ever guessed.
    """
    app_ctx: AppContext = ctx.obj
    if file is not None:
        if isin is not None or source or symbol or currency:
            fail("Use --file on its own, or an ISIN with --source, --symbol and --currency, not both.")
        try:
            data = file.read_bytes()
        except FileNotFoundError:
            fail(f"There is no file {file}.")
        with portfolio_transaction(app_ctx) as (conn, _):
            try:
                mapped_ids = load_mapping_file(conn, data, clock=app_ctx.clock)
            except InstrumentMappingError as exc:
                fail(str(exc))
        if json_output:
            print_json({"file": str(file), "mapped": len(mapped_ids)})
            return
        typer.echo(f"Mapped {plural(len(mapped_ids), 'instrument')} from {file}.")
        return

    if isin is None or not source or not symbol or not currency:
        fail("Give an ISIN with --source, --symbol and --currency, or use --file FILE.")
    normalised = _normalised_isin(isin)
    with portfolio_transaction(app_ctx) as (conn, _):
        try:
            set_mapping(
                conn,
                isin=normalised,
                source=source,
                symbol=symbol,
                currency=currency,
                note=note,
                changed_by="cli",
                clock=app_ctx.clock,
            )
        except InstrumentMappingError as exc:
            fail(str(exc))
        record = get_instrument(conn, normalised)
    if json_output:
        print_json(record)
        return
    typer.echo(f"Mapped {normalised} to {source} {symbol} ({currency.upper()}).")


@instruments_app.command(name="suggest")
def suggest_command(
    ctx: typer.Context,
    isin: str = typer.Argument(..., help="The ISIN to look up."),
    json_output: bool = _JSON,
) -> None:
    """Ask OpenFIGI for this ISIN's listings. Prints only: nothing is stored."""
    # Plan 5.6 and 6.7: a suggestion is never applied for you.
    app_ctx: AppContext = ctx.obj
    normalised = _normalised_isin(isin)
    client = OpenFigiClient(http=app_ctx.http_client, api_key=os.environ.get(API_KEY_ENV_VAR) or None)
    try:
        suggestions = client.suggest(normalised)
    except OpenFigiError as exc:
        fail(str(exc))
    records = [
        {
            "ticker": suggestion.ticker,
            "exchange_code": suggestion.exchange_code,
            "name": suggestion.name,
            "security_type": suggestion.security_type,
            "stooq_symbol": suggestion.stooq_symbol,
        }
        for suggestion in suggestions
    ]
    if json_output:
        print_json({"isin": normalised, "suggestions": records})
        return
    if not records:
        typer.echo(f"OpenFIGI has no listings for {normalised}.")
    for record in records:
        hint = f", possible Stooq symbol {record['stooq_symbol']}" if record["stooq_symbol"] else ""
        typer.echo(
            f"{record['ticker']} on {record['exchange_code']}: {record['name']} ({record['security_type']}){hint}"
        )
    typer.echo(
        "This is a suggestion, not applied. Map it yourself: pg instruments map ISIN --source ... --symbol ... --currency ..."
    )


@instruments_app.command(name="history")
def history_command(
    ctx: typer.Context,
    isin: str = typer.Argument(..., help="The ISIN."),
    json_output: bool = _JSON,
) -> None:
    """Show every mapping change for one instrument, oldest first."""
    app_ctx: AppContext = ctx.obj
    normalised = _normalised_isin(isin)
    with portfolio_transaction(app_ctx) as (conn, _):
        try:
            entries = mapping_history(conn, normalised)
        except InstrumentMappingError as exc:
            fail(str(exc))
    if json_output:
        print_json({"isin": normalised, "history": entries})
        return
    if not entries:
        typer.echo(f"No mapping history for {normalised} yet.")
    for entry in entries:
        typer.echo(f"{entry['changed_at']} ({entry['changed_by']}): {entry['old']} -> {entry['new']}")


def register(app: typer.Typer) -> None:
    """Add the instrument master commands to `app`."""
    app.add_typer(instruments_app, name="instruments")
