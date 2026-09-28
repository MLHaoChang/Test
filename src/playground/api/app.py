"""The FastAPI application (plan 5.8): one instance per data directory, over the same services
`pg` uses.

`create_app` opens the registry (creating its tables if they are missing, exactly as every `pg`
command does) and builds one `ApiState`, shared by every request; `pg serve` (`cli/serve.py`) is
the only place that binds it to a socket, and it always binds to `127.0.0.1`. A request that needs
the one P0 portfolio goes through `portfolio_conn`, which opens one database transaction (committed
when the request finishes without error, rolled back otherwise, exactly like the CLI's own
`cli.context.portfolio_transaction`) and fails with a plain-English message if `pg init` was never
run.

Every error, from a bad request to a batch that does not exist, comes back as
`{"error": {"code": ..., "message": ...}}` (plan 5.8): each domain error defined here or raised by
the services below is translated by a small handler registered in `_register_exception_handlers`,
never as a bare traceback or FastAPI's own `{"detail": ...}` shape.

`create_app` also serves the built import page (plan 5.10, WP12): when `web/dist` exists, it is
mounted at `/` last, after every API route, so a request that matches one of those (`/health`,
`/portfolio/...`, and so on) is always answered by that route first (Starlette tries routes in the
order they were added and stops at the first match) and only a path none of them own falls through
to the static files (the page itself at `/`, and its built JS and CSS under `/assets/...`). When
`web/dist` does not exist (every test run, and a fresh clone before `npm run build`), nothing is
mounted and `/` behaves as it always did: not found, like any other unknown path.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Connection, Engine
from starlette.exceptions import HTTPException as StarletteHTTPException

import playground
from playground.api import schemas
from playground.config import Settings
from playground.core.clock import Clock, clock_from_settings
from playground.core.errors import PlaygroundError
from playground.http.client import HttpClient, make_http_client
from playground.importer.pipeline import (
    BatchNotFoundError,
    BatchNotStagedError,
    NoFilesError,
    StagedBatchExistsError,
    UnsupportedFileError,
)
from playground.storage import repos
from playground.storage.db import open_registry
from playground.storage.schema import ensure_schema
from playground.valuation.portfolio import MarketData

__all__ = [
    "ApiState",
    "BadRequestError",
    "DEFAULT_WEB_DIST_DIR",
    "NotFoundError",
    "PortfolioNotInitialisedError",
    "create_app",
    "get_state",
    "portfolio_conn",
]

#: The built import page (plan 5.10): `web/dist` at the repository root, exactly where `(cd web &&
#: npm run build)` writes it. `app.py` lives at src/playground/api/app.py, so parents[3] is the
#: repository root (the same reckoning `marketdata/benchmarks.py` uses for configs/benchmarks.yaml).
DEFAULT_WEB_DIST_DIR = Path(__file__).resolve().parents[3] / "web" / "dist"


# --- Errors this layer owns (a service's own errors are mapped where they are raised) -----------


class PortfolioNotInitialisedError(PlaygroundError):
    """The data directory has no portfolio yet: `pg init` (or an equivalent setup call) was never run."""


class NotFoundError(PlaygroundError):
    """Something named in the path (a batch, an instrument) does not exist."""


class BadRequestError(PlaygroundError):
    """The request body or a query value could not be read."""


# --- Shared state -------------------------------------------------------------------------------


@dataclass
class ApiState:
    """Built once by `create_app`; every route reads it through `get_state` (plan 5.8)."""

    settings: Settings
    clock: Clock
    http_client: HttpClient
    engine: Engine

    @property
    def market(self) -> MarketData:
        """The prices and ECB rates stored under the data directory (plan 4.2), for every valuation."""
        return MarketData.from_data_dir(self.settings.data_dir)


def get_state(request: Request) -> ApiState:
    """The `ApiState` `create_app` attached to this application."""
    state: ApiState = request.app.state.ctx
    return state


def portfolio_conn(request: Request) -> Iterator[tuple[Connection, int]]:
    """One database transaction on the default portfolio (mirrors `cli.context.portfolio_transaction`).

    Commits when the request handler returns normally, rolls back if it raises. Raises
    `PortfolioNotInitialisedError` when no `pg init` (or `POST /portfolio/imports`'s own setup) has
    created the one P0 portfolio yet.
    """
    state = get_state(request)
    with state.engine.begin() as conn:
        portfolio_id = repos.default_portfolio_id(conn)
        if portfolio_id is None:
            raise PortfolioNotInitialisedError(
                f"There is no portfolio in {state.settings.data_dir} yet. Run pg init first."
            )
        yield conn, portfolio_id


# --- Errors to HTTP responses --------------------------------------------------------------------


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BatchNotFoundError)
    async def _batch_not_found(request: Request, exc: BatchNotFoundError) -> JSONResponse:
        return _error_response(404, "not_found", str(exc))

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return _error_response(404, "not_found", str(exc))

    @app.exception_handler(StagedBatchExistsError)
    async def _batch_staged(request: Request, exc: StagedBatchExistsError) -> JSONResponse:
        return _error_response(409, "batch_staged", str(exc))

    @app.exception_handler(BatchNotStagedError)
    async def _batch_not_staged(request: Request, exc: BatchNotStagedError) -> JSONResponse:
        return _error_response(409, "batch_not_staged", str(exc))

    @app.exception_handler(NoFilesError)
    async def _no_files(request: Request, exc: NoFilesError) -> JSONResponse:
        return _error_response(422, "no_files", str(exc))

    @app.exception_handler(UnsupportedFileError)
    async def _unsupported_file(request: Request, exc: UnsupportedFileError) -> JSONResponse:
        return _error_response(422, "unsupported_file", str(exc))

    @app.exception_handler(PortfolioNotInitialisedError)
    async def _not_initialised(request: Request, exc: PortfolioNotInitialisedError) -> JSONResponse:
        return _error_response(400, "not_initialised", str(exc))

    @app.exception_handler(BadRequestError)
    async def _bad_request(request: Request, exc: BadRequestError) -> JSONResponse:
        return _error_response(400, "invalid_request", str(exc))

    # A fallback for every other domain error this app raises on purpose (plan core/errors.py):
    # an invalid ISIN, a bad mapping field, an unreadable confirmed-holdings file, and so on.
    # Registered last so the more specific handlers above (subclasses of PlaygroundError) win.
    @app.exception_handler(PlaygroundError)
    async def _domain_error(request: Request, exc: PlaygroundError) -> JSONResponse:
        return _error_response(400, "invalid_request", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, "invalid_request", f"The request could not be read: {exc}")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(exc.status_code, "http_error", str(exc.detail))


# --- The app --------------------------------------------------------------------------------------


def create_app(
    settings: Settings,
    *,
    clock: Clock | None = None,
    http_client: HttpClient | None = None,
    web_dist_dir: Path | None = None,
) -> FastAPI:
    """Build the API of plan 5.8 for `settings.data_dir`, and serve the built import page if there is one.

    Binds to nothing by itself (`pg serve` does that, on `127.0.0.1` only): this only wires the
    routes to the registry and the market data of one data directory. `clock` and `http_client`
    default to what `settings` says (3.3, 5.4); `pg serve` passes its own so the API shares the
    same clock and HTTP mode as the rest of that `pg` invocation. `web_dist_dir` defaults to
    `DEFAULT_WEB_DIST_DIR`; tests pass their own so they never depend on whether `web/dist` happens
    to exist in the working tree.
    """
    engine = open_registry(settings.data_dir)
    ensure_schema(engine)
    state = ApiState(
        settings=settings,
        clock=clock if clock is not None else clock_from_settings(settings),
        http_client=http_client if http_client is not None else make_http_client(settings),
        engine=engine,
    )

    app = FastAPI(
        title="playground",
        version=playground.__version__,
        description="No real orders. Your portfolio is read-only.",
    )
    app.state.ctx = state

    @app.get("/health", tags=["health"], response_model=schemas.HealthResponse)
    def health() -> dict[str, Any]:
        """`{"status": "ok", ...}`: confirms the app never places, routes or automates an order."""
        return {
            "status": "ok",
            "version": playground.__version__,
            "real_orders": False,
            "portfolio_read_only": True,
        }

    # Imported here, not at module level: the route modules import `portfolio_conn` and the error
    # classes from this module, so importing them at module level here would be circular.
    from playground.api.routes_benchmarks import router as benchmarks_router
    from playground.api.routes_instruments import router as instruments_router
    from playground.api.routes_portfolio import router as portfolio_router

    app.include_router(portfolio_router)
    app.include_router(instruments_router)
    app.include_router(benchmarks_router)

    _register_exception_handlers(app)

    # Mounted last (see the module docstring): every API route above is tried first, and only a
    # path none of them own falls through to the page's own files. A `Mount` is plain Starlette
    # routing, not a `fastapi.routing.APIRoute`, so it never adds an entry to `/openapi.json`.
    dist_dir = web_dist_dir if web_dist_dir is not None else DEFAULT_WEB_DIST_DIR
    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="web")

    return app
