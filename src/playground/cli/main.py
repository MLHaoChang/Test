"""Main CLI entry point."""

import typer

import playground

app = typer.Typer()


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        typer.echo(f"pg {playground.__version__}")
        typer.echo("No real orders. Your portfolio is read-only.")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None, "--version", callback=version_callback, is_eager=True, help="Show version"
    ),
) -> None:
    """Playground: import, reconcile and value your Trade Republic portfolio."""


@app.command()
def init() -> None:
    """Initialize the portfolio."""
    typer.echo("Not yet implemented.")


if __name__ == "__main__":
    app()
