"""Small, table-grouped query functions used by the CLI and API (plan 5.2).

Every function here takes a `Connection` (never opens its own engine) so
the caller controls the transaction and can compose several calls into
one. WP2 provides only what `pg init` needs (`get_or_create_portfolio`);
later work packages add the rest of the list in plan 5.2 (staging,
matching, review items, lots, values and the read helpers used by the
CLI and API) to this same module.
"""

import sqlalchemy as sa
from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.dates import format_ts_utc
from playground.storage.schema import portfolios

#: P0 uses exactly one portfolio, named exactly this (spec 1.5).
DEFAULT_PORTFOLIO_NAME = "Trade Republic"


def get_or_create_portfolio(
    conn: Connection,
    *,
    clock: Clock,
    name: str = DEFAULT_PORTFOLIO_NAME,
    kind: str = "real",
    base_currency: str = "EUR",
    source: str = "trade_republic",
) -> int:
    """Return the id of the portfolio named `name`, creating it if it does not exist yet.

    `pg init` calls this with the defaults to create P0's one portfolio
    (spec 1.5). `created_at` comes from `clock`, never the system clock
    (3.3), so the same `PG_TODAY` always gives the same stored timestamp.
    Calling this again for the same `name` is a no-op that returns the
    existing id.
    """
    existing = conn.execute(sa.select(portfolios.c.id).where(portfolios.c.name == name)).first()
    if existing is not None:
        return existing.id

    result = conn.execute(
        sa.insert(portfolios).values(
            name=name,
            kind=kind,
            base_currency=base_currency,
            source=source,
            last_import_at=None,
            created_at=format_ts_utc(clock.now_utc()),
        )
    )
    inserted_id = result.inserted_primary_key
    if inserted_id is None:
        raise RuntimeError("get_or_create_portfolio: insert did not return a primary key.")
    return int(inserted_id[0])
