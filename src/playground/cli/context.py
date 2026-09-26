"""What every `pg` command shares: the settings, the clock, the registry and plain output (plan 5.9)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

import typer
from sqlalchemy import Connection, Engine

from playground.config import Settings
from playground.core.clock import Clock
from playground.core.errors import PlaygroundError
from playground.storage import repos
from playground.storage.db import open_registry
from playground.storage.schema import ensure_schema

EXIT_INPUT_ERROR = 1
EXIT_MISMATCH = 3


@dataclass
class AppContext:
    """Shared state built once in the top-level callback, and used by every command."""

    settings: Settings
    clock: Clock

    @property
    def data_dir(self) -> Path:
        return self.settings.data_dir

    @property
    def uploads_dir(self) -> Path:
        return self.settings.data_dir / "uploads"


def fail(message: str, code: int = EXIT_INPUT_ERROR) -> NoReturn:
    """Print `message` to standard error and stop with `code` (1: a usage or input error)."""
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def parse_day(text: str | None, option: str) -> date | None:
    """A `YYYY-MM-DD` option value, or `None` when the option is not given."""
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        fail(f"{option} must be a date in YYYY-MM-DD format. Got: {text!r}.")


def registry(app_ctx: AppContext) -> Engine:
    """The registry of the data directory; refuses when `pg init` has not been run there."""
    if not (app_ctx.data_dir / "registry" / "runs.sqlite").exists():
        fail(f"There is no portfolio in {app_ctx.data_dir} yet. Run pg init first.")
    engine = open_registry(app_ctx.data_dir)
    ensure_schema(engine)
    return engine


@contextmanager
def portfolio_transaction(app_ctx: AppContext) -> Iterator[tuple[Connection, int]]:
    """One database transaction on the default portfolio. A domain error stops the command with exit 1."""
    engine = registry(app_ctx)
    try:
        with engine.begin() as conn:
            portfolio_id = repos.default_portfolio_id(conn)
            if portfolio_id is None:
                fail(f"There is no portfolio in {app_ctx.data_dir} yet. Run pg init first.")
            yield conn, portfolio_id
    except PlaygroundError as exc:
        fail(str(exc))
    finally:
        engine.dispose()
