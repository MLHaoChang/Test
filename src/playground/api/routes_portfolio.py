"""GET and POST routes under `/portfolio` (plan 5.8): the import pipeline, the ledger views and the
value series, over the same services `pg import`, `pg accept`, `pg holdings`, `pg lots`, `pg value`,
`pg transactions`, `pg review` and `pg status` call (AC12). No path here places, routes or automates
an order (spec 1.5): staging, accepting and discarding a batch, and setting an instrument's mapping
(`routes_instruments.py`), are the only writes the whole API can make.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import Connection

from playground.api import schemas
from playground.api.app import BadRequestError, get_state, plain_validation_message, portfolio_conn
from playground.core.dates import format_ts_utc
from playground.core.errors import InvalidIsinError
from playground.core.isin import normalise_isin
from playground.core.types import TxnType
from playground.importer.confirmed_csv import ConfirmedCsvError, ConfirmedHolding, parse_confirmed_csv
from playground.importer.pipeline import (
    InputFile,
    NoFilesError,
    accept_batch,
    discard_batch,
    portfolio_status,
    stage_inputs,
)
from playground.importer.reconcile import build_diff, transaction_records
from playground.importer.review import ReviewError, list_items
from playground.storage import repos
from playground.valuation.portfolio import ValueRangeError, compute_values, holdings_report

router = APIRouter(tags=["portfolio"])

_TXN_TYPES = {member.value for member in TxnType}


def _parse_date(text: str | None, name: str) -> date | None:
    """A `YYYY-MM-DD` value from a multipart form field (query and path dates are parsed by FastAPI itself)."""
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise BadRequestError(f"{name} must be a date in YYYY-MM-DD format. Got: {text!r}.") from exc


def _normalised_isin(value: str) -> str:
    try:
        return normalise_isin(value)
    except InvalidIsinError as exc:
        raise BadRequestError(str(exc)) from exc


# --- Imports: stage, diff, confirmed holdings, accept, discard ----------------------------------


@router.post("/portfolio/imports", status_code=201, response_model=schemas.ImportDiffResponse)
async def create_import(
    request: Request,
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
    files: list[UploadFile] | None = File(None),  # noqa: B008
) -> dict[str, Any]:
    """Stage one batch from the uploaded `files` and return its diff (plan 5.3.6), 201 on success."""
    conn, portfolio_id = conn_and_portfolio
    if not files:
        raise NoFilesError("Name at least one Trade Republic PDF document or CSV file to import.")
    inputs = [InputFile(name=upload.filename or "upload", data=await upload.read()) for upload in files]
    state = get_state(request)
    summary = stage_inputs(
        conn, portfolio_id, inputs, clock=state.clock, uploads_dir=state.settings.data_dir / "uploads"
    )
    diff = build_diff(conn, summary.batch_id, clock=state.clock)
    return diff.to_dict()


@router.get("/portfolio/imports/{batch_id}", response_model=schemas.ImportDiffResponse)
def get_import(
    batch_id: int,
    request: Request,
    as_of: date | None = Query(None),  # noqa: B008
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
) -> dict[str, Any]:
    """The batch and its diff (plan 5.3.6): what a stage found, or, once accepted, before and after it."""
    conn, _ = conn_and_portfolio
    state = get_state(request)
    diff = build_diff(conn, batch_id, clock=state.clock, as_of=as_of)
    return diff.to_dict()


def _rows_from_json(payload: list[schemas.ConfirmedHoldingRow]) -> list[ConfirmedHolding]:
    rows = []
    for row in payload:
        try:
            quantity = Decimal(row.quantity)
        except InvalidOperation as exc:
            raise BadRequestError(f"{row.quantity!r} is not a valid quantity.") from exc
        as_of = _parse_date(row.as_of, "as_of")
        if as_of is None:
            raise BadRequestError("Every confirmed-holdings row needs its own as_of (YYYY-MM-DD).")
        rows.append(ConfirmedHolding(isin=_normalised_isin(row.isin), quantity=quantity, as_of=as_of))
    return rows


@router.post("/portfolio/imports/{batch_id}/confirmed-holdings", response_model=schemas.ImportDiffResponse)
async def upload_confirmed_holdings(
    batch_id: int,
    request: Request,
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
) -> dict[str, Any]:
    """Save the holdings you confirm (a CSV file, or JSON rows) and return the diff with the comparison.

    The request is multipart (a `file` field in the format of `importer.confirmed_csv`, an optional
    `as_of` field for the diff's own day) or JSON (`{"rows": [...], "as_of": ...}`, plan 5.8).
    """
    conn, portfolio_id = conn_and_portfolio
    state = get_state(request)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise BadRequestError("Send the confirmed-holdings file as a multipart field named 'file'.")
        try:
            rows = parse_confirmed_csv(await upload.read())
        except ConfirmedCsvError as exc:
            raise BadRequestError(str(exc)) from exc
        as_of_field = form.get("as_of")
        top_as_of = _parse_date(as_of_field if isinstance(as_of_field, str) else None, "as_of")
        source = "file"
    elif content_type.startswith("application/json"):
        try:
            body = schemas.ConfirmedHoldingsRequest.model_validate(await request.json())
        except ValidationError as exc:
            raise BadRequestError(plain_validation_message(exc.errors())) from exc
        rows = _rows_from_json(body.rows)
        top_as_of = _parse_date(body.as_of, "as_of")
        source = "typed"
    else:
        raise BadRequestError(
            "Send the confirmed holdings as multipart/form-data (a 'file' field) or as application/json."
        )
    if rows:
        repos.save_confirmed_holdings(
            conn,
            portfolio_id,
            [(row.as_of, row.isin, row.quantity) for row in rows],
            source=source,
            entered_at=format_ts_utc(state.clock.now_utc()),
        )
    diff = build_diff(conn, batch_id, clock=state.clock, confirmed=rows, as_of=top_as_of)
    return diff.to_dict()


@router.post("/portfolio/imports/{batch_id}/accept", response_model=schemas.AcceptResponse)
def accept(
    batch_id: int, request: Request, conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn)
) -> dict[str, Any]:
    """Accept a staged batch: rebuild the lots, then the value series (plan 5.3.6). 409 if not staged."""
    conn, _ = conn_and_portfolio
    state = get_state(request)
    return accept_batch(conn, batch_id, clock=state.clock, market=state.market).to_dict()


@router.post("/portfolio/imports/{batch_id}/discard", response_model=schemas.DiscardResponse)
def discard(
    batch_id: int, request: Request, conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn)
) -> dict[str, Any]:
    """Discard a staged batch: its transactions go, the review items it raised close. 409 if not staged."""
    conn, _ = conn_and_portfolio
    state = get_state(request)
    return discard_batch(conn, batch_id, clock=state.clock).to_dict()


# --- Portfolio summary, holdings, transactions, lots, value, review ------------------------------


@router.get("/portfolio", response_model=schemas.PortfolioResponse)
def get_portfolio(
    request: Request, conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn)
) -> dict[str, Any]:
    """The portfolio, the last import, the 30-day reminder (spec 3.10, AC16), the review queue and
    the latest value: the same summary `pg status` shows."""
    conn, portfolio_id = conn_and_portfolio
    state = get_state(request)
    return portfolio_status(conn, portfolio_id, clock=state.clock, market=state.market)


@router.get("/portfolio/holdings", response_model=schemas.HoldingsResponse)
def get_holdings(
    request: Request,
    as_of: date | None = Query(None),  # noqa: B008
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
) -> dict[str, Any]:
    """Every holding as of `as_of` (today by default): quantity, cost, value and its evidence (plan 5.7)."""
    conn, portfolio_id = conn_and_portfolio
    state = get_state(request)
    day = as_of if as_of is not None else state.clock.today()
    return holdings_report(conn, portfolio_id, day=day, market=state.market)


@router.get("/portfolio/transactions", response_model=schemas.TransactionsResponse)
def get_transactions(
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
    date_from: date | None = Query(None, alias="from"),  # noqa: B008
    date_to: date | None = Query(None, alias="to"),  # noqa: B008
    type: str | None = Query(None),  # noqa: A002 - "type" is the query parameter's documented name (plan 5.8)
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """The stored transactions with their sources, paginated (plan 5.8)."""
    conn, portfolio_id = conn_and_portfolio
    if type is not None and type not in _TXN_TYPES:
        raise BadRequestError(f"Unknown type {type!r}. Use one of: {', '.join(sorted(_TXN_TYPES))}.")
    records = transaction_records(
        conn, portfolio_id, date_from=date_from, date_to=date_to, txn_type=type, limit=limit, offset=offset
    )
    return {"count": len(records), "transactions": records}


def _dec(value: Any) -> str | None:
    return format(value, "f") if value is not None else None


def _lot_record(row: Any, names: dict[str, str]) -> dict[str, Any]:
    """The same shape `cli.portfolio._lot_record` builds, so `pg lots` and this endpoint agree (AC12)."""
    return {
        "id": row.id,
        "isin": row.isin,
        "name": names.get(row.isin, row.isin),
        "origin": row.origin,
        "booked_ts": row.booked_ts,
        "opened_ts": row.opened_ts,
        "quantity_initial": _dec(row.quantity_initial),
        "quantity_open": _dec(row.quantity_open),
        "cost_eur_initial": _dec(row.cost_eur_initial),
        "cost_eur_open": _dec(row.cost_eur_open),
        "cost_missing": bool(row.cost_missing),
        "open_transaction": {"id": row.open_txn_id},
    }


def _disposal_record(row: Any, names: dict[str, str]) -> dict[str, Any]:
    """The same shape `cli.portfolio._disposal_record` builds, so `pg lots` and this endpoint agree."""
    return {
        "id": row.id,
        "isin": row.isin,
        "name": names.get(row.isin, row.isin),
        "kind": row.kind,
        "ts_utc": row.ts_utc,
        "quantity": _dec(row.quantity),
        "cost_eur": _dec(row.cost_eur),
        "proceeds_eur": _dec(row.proceeds_eur),
        "fees_eur": _dec(row.fees_eur),
        "realised_eur": _dec(row.realised_eur),
        "lot": {"id": row.lot_id},
        "transaction": {"id": row.txn_id},
    }


@router.get("/portfolio/lots", response_model=schemas.LotsResponse)
def get_lots(
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn), isin: str | None = Query(None)
) -> dict[str, Any]:
    """The stored lot book: every open lot and disposal, exactly as `pg accept` (or a resolution) last built them."""
    conn, portfolio_id = conn_and_portfolio
    wanted = _normalised_isin(isin) if isin is not None else None
    names = repos.instrument_names(conn)
    lot_rows = repos.list_lots(conn, portfolio_id, isin=wanted)
    disposal_rows = repos.list_disposals(conn, portfolio_id, isin=wanted)
    return {
        "isin": wanted,
        "lots": [_lot_record(row, names) for row in lot_rows],
        "disposals": [_disposal_record(row, names) for row in disposal_rows],
    }


@router.get("/portfolio/value", response_model=schemas.ValueResponse)
def get_value(
    request: Request,
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn),
    date_from: date | None = Query(None, alias="from"),  # noqa: B008
    date_to: date | None = Query(None, alias="to"),  # noqa: B008
) -> dict[str, Any]:
    """The daily value in EUR over a range (the same default range as `pg value`, plan 5.7), stored
    as a side effect exactly as `pg value` stores it, so both read back the same series afterwards."""
    conn, portfolio_id = conn_and_portfolio
    state = get_state(request)
    try:
        report = compute_values(
            conn, portfolio_id, market=state.market, clock=state.clock, start=date_from, end=date_to
        )
    except ValueRangeError as exc:
        raise BadRequestError(str(exc)) from exc
    return report.to_dict()


@router.get("/portfolio/review", response_model=schemas.ReviewListResponse)
def get_review(
    conn_and_portfolio: tuple[Connection, int] = Depends(portfolio_conn), status: str = Query("open")
) -> dict[str, Any]:
    """The review queue (plan 5.3.5): the open items unless `status` asks for another one, or "all"."""
    conn, portfolio_id = conn_and_portfolio
    wanted = None if status == "all" else status
    try:
        items = list_items(conn, portfolio_id, status=wanted)
    except ReviewError as exc:
        raise BadRequestError(str(exc)) from exc
    return {"status": status, "count": len(items), "items": items}
