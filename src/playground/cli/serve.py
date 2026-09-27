"""`pg serve` (plan 5.9): the API of plan 5.8 on `127.0.0.1` only (AC12).

Once WP12 builds `web/dist`, `create_app` will serve it at `/` too; this package only wires up the
API. The server shares the `AppContext` the top-level `pg` callback already built, so `pg serve
--http-replay DIR` and `PG_TODAY` affect it exactly as they affect every other command.
"""

import typer

from playground.cli.context import AppContext

#: Never anything else (plan 5.8: "Binds to 127.0.0.1 only"): the API is reachable only from this
#: machine, never from the network, whatever host or firewall rule surrounds it.
BIND_HOST = "127.0.0.1"

_PORT_OPTION = typer.Option(8765, "--port", help="The TCP port to serve on (127.0.0.1 only).")


def serve(ctx: typer.Context, port: int = _PORT_OPTION) -> None:
    """Run the API (and, once built, the import page) on 127.0.0.1. Never reachable from the network."""
    import uvicorn

    from playground.api.app import create_app

    app_ctx: AppContext = ctx.obj
    app = create_app(app_ctx.settings, clock=app_ctx.clock, http_client=app_ctx.http_client)
    typer.echo("No real orders. Your portfolio is read-only.")
    typer.echo(f"Serving on http://{BIND_HOST}:{port}")
    uvicorn.run(app, host=BIND_HOST, port=port, log_level="warning")


def register(app: typer.Typer) -> None:
    """Add `pg serve` to `app`."""
    app.command()(serve)
