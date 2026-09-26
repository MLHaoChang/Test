"""Main CLI entry point."""

import os
from dataclasses import dataclass
from pathlib import Path

import typer

import playground
from playground.config import Settings
from playground.core.clock import Clock, InvalidClockSettingError, clock_from_settings, today_from_env
from playground.storage.db import open_registry
from playground.storage.repos import get_or_create_portfolio
from playground.storage.schema import ensure_schema

app = typer.Typer()

DEFAULT_DATA_DIR = Path("./data")


@dataclass
class AppContext:
    """Shared state built once in the top-level callback, and used by every command."""

    settings: Settings
    clock: Clock


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

    settings = Settings(data_dir=data_dir, today=today)
    ctx.obj = AppContext(settings=settings, clock=clock_from_settings(settings))


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the data directory, the registry and the default portfolio."""
    app_ctx: AppContext = ctx.obj
    engine = open_registry(app_ctx.settings.data_dir)
    ensure_schema(engine)
    with engine.begin() as conn:
        get_or_create_portfolio(conn, clock=app_ctx.clock)
    typer.echo(f"Portfolio ready at {app_ctx.settings.data_dir}")


if __name__ == "__main__":
    app()
