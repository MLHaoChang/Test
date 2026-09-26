"""Main CLI entry point."""

from pathlib import Path

import typer

import playground

app = typer.Typer()

DEFAULT_DATA_DIR = Path("./data")


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        typer.echo(f"pg {playground.__version__}")
        typer.echo("No real orders. Your portfolio is read-only.")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(  # noqa: B008
        None, "--version", callback=version_callback, is_eager=True, help="Show version"
    ),
    data_dir: Path = typer.Option(  # noqa: B008
        DEFAULT_DATA_DIR, "--data-dir", help="Data directory", envvar="PG_DATA_DIR"
    ),
) -> None:
    """Playground: import, reconcile and value your Trade Republic portfolio."""


@app.command()
def init() -> None:
    """Initialize the portfolio."""
    typer.echo("Not yet implemented.")


if __name__ == "__main__":
    app()
