"""Main CLI entry point."""

import os
from pathlib import Path
from typing import Literal

import typer

import playground
from playground.cli import imports as import_commands
from playground.cli import instruments as instruments_commands
from playground.cli import marketdata as marketdata_commands
from playground.cli import portfolio as portfolio_commands
from playground.cli import review as review_commands
from playground.cli import serve as serve_commands
from playground.cli.context import AppContext
from playground.config import Settings
from playground.core.clock import InvalidClockSettingError, clock_from_settings, today_from_env
from playground.http.client import make_http_client
from playground.storage.db import open_registry
from playground.storage.repos import get_or_create_portfolio
from playground.storage.schema import ensure_schema

app = typer.Typer()

DEFAULT_DATA_DIR = Path("./data")

__all__ = ["AppContext", "app"]


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        typer.echo(f"pg {playground.__version__}")
        typer.echo("No real orders. Your portfolio is read-only.")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(  # noqa: B008
        None, "--version", callback=version_callback, is_eager=True, help="Show version"
    ),
    data_dir: Path = typer.Option(  # noqa: B008
        DEFAULT_DATA_DIR, "--data-dir", help="Data directory", envvar="PG_DATA_DIR"
    ),
    http_replay: Path | None = typer.Option(  # noqa: B008
        None,
        "--http-replay",
        help="Serve market-data responses recorded under DIR instead of the network.",
        envvar="PG_HTTP_REPLAY",
    ),
    http_record: Path | None = typer.Option(  # noqa: B008
        None,
        "--http-record",
        help="Fetch over the network and also record the responses under DIR.",
        envvar="PG_HTTP_RECORD",
    ),
) -> None:
    """Playground: import, reconcile and value your Trade Republic portfolio."""
    # PG_TODAY (3.3) is read directly from the environment, not through a Typer option: it
    # fixes "today" for tests, the e2e scenario and the Playwright server, and is not part of
    # the documented CLI surface (plan 5.9 lists it as an environment variable, not a flag).
    try:
        today = today_from_env(os.environ.get("PG_TODAY"))
    except InvalidClockSettingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if http_replay is not None and http_record is not None:
        typer.echo("Use either --http-replay or --http-record, not both.", err=True)
        raise typer.Exit(code=1)
    http_mode: Literal["network", "replay", "record"] = "network"
    if http_replay is not None:
        http_mode = "replay"
    elif http_record is not None:
        http_mode = "record"

    settings = Settings(
        data_dir=data_dir, http_mode=http_mode, http_replay_dir=http_replay, http_record_dir=http_record, today=today
    )
    ctx.obj = AppContext(settings=settings, clock=clock_from_settings(settings), http_client=make_http_client(settings))


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the data directory, the registry and the default portfolio."""
    app_ctx: AppContext = ctx.obj
    engine = open_registry(app_ctx.settings.data_dir)
    ensure_schema(engine)
    with engine.begin() as conn:
        get_or_create_portfolio(conn, clock=app_ctx.clock)
    typer.echo(f"Portfolio ready at {app_ctx.settings.data_dir}")


import_commands.register(app)
review_commands.register(app)
portfolio_commands.register(app)
instruments_commands.register(app)
marketdata_commands.register(app)
serve_commands.register(app)


if __name__ == "__main__":
    app()
