"""GET and PUT routes under `/instruments` (plan 5.8, 5.6): the instrument master the CLI reads and
writes with `pg instruments`. `PUT` is the one write in this file, and, like `pg instruments map`,
it always names an explicit source, symbol and currency: nothing here is ever mapped by guessing
(AC10). A mapping change is logged the same way whichever side made it (`changed_by="api"` here,
`"cli"` for the command, `"file"` for a loaded mapping file).
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection

from playground.api import schemas
from playground.api.app import BadRequestError, NotFoundError, get_state, portfolio_conn
from playground.core.errors import InvalidIsinError
from playground.core.isin import normalise_isin
from playground.marketdata.instruments import (
    InstrumentMappingError,
    get_instrument,
    list_instruments,
    mapping_history,
    set_mapping,
)

router = APIRouter(tags=["instruments"])


def _normalised_isin(value: str) -> str:
    try:
        return normalise_isin(value)
    except InvalidIsinError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/instruments", response_model=schemas.InstrumentsResponse)
def list_instruments_route(conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn)) -> dict[str, Any]:
    """Every known instrument, and its mapping when it has one (`pg instruments list`)."""
    conn, _ = conn_and_portfolio
    return {"instruments": list_instruments(conn)}


@router.put("/instruments/{isin}/mapping", response_model=schemas.InstrumentRecord)
def put_mapping(
    isin: str,
    body: schemas.MappingRequest,
    request: Request,
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
) -> dict[str, Any]:
    """Set `isin`'s data source, symbol and currency; log the change (`pg instruments map`).

    Creates the instrument, named by its own ISIN, if no document has named it yet, so an ISIN can
    be mapped before its first import (WP9's e2e subset needs exactly this).
    """
    conn, _ = conn_and_portfolio
    normalised = _normalised_isin(isin)
    state = get_state(request)
    try:
        set_mapping(
            conn,
            isin=normalised,
            source=body.source,
            symbol=body.symbol,
            currency=body.currency,
            note=body.note,
            changed_by="api",
            clock=state.clock,
        )
    except InstrumentMappingError as exc:
        raise BadRequestError(str(exc)) from exc
    record = get_instrument(conn, normalised)
    if record is None:  # set_mapping above just created or updated this exact row
        raise NotFoundError(f"No instrument known for ISIN {normalised}.")
    return record


@router.get("/instruments/{isin}/mapping-history", response_model=schemas.MappingHistoryResponse)
def get_mapping_history(
    isin: str, conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn)
) -> dict[str, Any]:
    """Every mapping change for `isin`, oldest first (`pg instruments history`); 404 for an unknown ISIN."""
    conn, _ = conn_and_portfolio
    normalised = _normalised_isin(isin)
    try:
        entries = mapping_history(conn, normalised)
    except InstrumentMappingError as exc:
        raise NotFoundError(str(exc)) from exc
    return {"isin": normalised, "history": entries}
