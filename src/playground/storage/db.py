"""Opening the SQLite registry, and the DecimalText column type (plan 5.2).

`schema.py` defines the tables (and imports `DecimalText` from here for
their DEC columns); this module only knows how to open the database file
itself. Table creation lives in `schema.ensure_schema`, not here, so
that this module never has to import `schema` back -- `schema.py` needs
`DecimalText` at import time (to define its columns), so the dependency
can only run in this one direction.
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine, create_engine, event


class DecimalText(sa.types.TypeDecorator[Decimal]):
    """Stores a `Decimal` as its exact text form; refuses a `float` outright.

    SQLite has no fixed-point decimal type, and its native floating-point
    REAL would silently reintroduce the binary-rounding errors `Decimal`
    exists to avoid (3.3: "Decimal everywhere... Floats are never used
    for money or quantity"). Writing a `Decimal` stores `str(value)`;
    writing a string that itself parses as a `Decimal` is also accepted,
    unchanged, so a value already formatted upstream keeps its exact
    digits. Reading a row always gives back a `Decimal`, and `NULL`
    round-trips as `None`.
    """

    impl = sa.Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(
                f"DecimalText refuses float values (got {value!r}); pass a Decimal or a decimal-format string."
            )
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, str):
            try:
                Decimal(value)
            except InvalidOperation as exc:
                raise TypeError(f"DecimalText cannot store {value!r}: not a valid decimal string.") from exc
            return value
        raise TypeError(f"DecimalText cannot store a value of type {type(value).__name__}.")

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)


def open_registry(data_dir: Path) -> Engine:
    """Open the SQLite registry at `<data_dir>/registry/runs.sqlite`, creating the file if needed.

    Also creates the sibling `lake/` and `uploads/` directories (5.2), so
    `pg init` can build the whole data-directory tree from this one call.
    Every new connection gets `PRAGMA foreign_keys=ON` (off by default in
    SQLite) and `PRAGMA journal_mode=WAL`.

    This does not create any table -- call `schema.ensure_schema(engine)`
    for that -- and it is always safe to call again on an existing
    directory.
    """
    registry_dir = data_dir / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "lake").mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{registry_dir / 'runs.sqlite'}")

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    # SQLite only creates the file on disk on the first real connection; connect once here
    # so the file exists as soon as this function returns, even before any table is created.
    with engine.connect():
        pass

    return engine
