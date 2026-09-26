"""Writing a workspace back to the registry (plan 5.3.4 to 5.3.6).

Three ways, one per moment:

- `store_stage`: a stage writes only what is new, the new transactions (staged or held), every
  new source and every new review item. Accepted data is not touched until accept, so closures,
  releases and merges are only listed in the diff.
- `apply_plan`: accept, a resolution and a refresh write everything the check decided: merges,
  the fields in use, key and state of every transaction, the review items, and the lot book,
  which is replaced as a whole.
- `apply_item_decision`: a resolution of an item of the staged batch changes that item and its
  transaction only; accepting the batch checks everything else.

A transaction row always holds the fields in use of its sources (`merge.py`). Its content hash
is unique per portfolio, and a new key can take an occurrence that another row gives up in the
same write, so changed hashes are first set to a placeholder and then to their final value.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from playground.core.dates import format_ts_utc
from playground.importer.keys import content_hash
from playground.importer.serialise import transaction_to_dict
from playground.importer.workspace import Item, Plan, Txn, Workspace
from playground.storage import repos


class _Instruments:
    """Instrument ids by ISIN, created from the documents' names when missing (never mapped)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._ids: dict[str, int] = {}

    def id_for(self, isin: str, name: str | None) -> int:
        if isin not in self._ids:
            self._ids[isin] = repos.upsert_instrument_from_document(self._conn, isin=isin, name=name)
        return self._ids[isin]


def row_values(ws: Workspace, txn: Txn, *, state: str, batch_id: int, instruments: _Instruments) -> dict[str, Any]:
    """The `transactions` row of `txn`: its fields in use, key, occurrence, state and batch."""
    fields = txn.fields
    first = min(txn.sources, key=lambda source: source.order)
    return {
        "portfolio_id": ws.portfolio_id,
        "batch_id": batch_id,
        "import_id": first.import_id,
        "state": state,
        "ts_utc": format_ts_utc(fields.time.ts_utc),
        "ts_local": fields.time.ts_local.isoformat(),
        "source_tz": fields.time.source_tz,
        "ts_precision": fields.time.precision,
        "value_date": fields.value_date,
        "type": fields.type.value,
        "instrument_id": instruments.id_for(fields.isin, txn.view.name) if fields.isin else None,
        "quantity": fields.quantity,
        "price": fields.price,
        "currency": fields.currency,
        "amount": fields.amount,
        "amount_eur": fields.amount_eur,
        "fx_rate": fields.fx_rate,
        "fx_source": fields.fx_source,
        "fees_eur": fields.fees_eur,
        "tax_eur": fields.tax_eur,
        "tax_detail_json": {key: format(value, "f") for key, value in sorted(fields.tax_detail.items())},
        "split_new_quantity": fields.split_new_quantity,
        "origin": fields.origin,
        "source_ref": fields.source_ref,
        "semantic_key": txn.semantic_key,
        "occurrence": txn.occurrence,
        "content_hash": content_hash(txn.semantic_key, txn.occurrence),
        "precedence": txn.view.precedence,
    }


def store_stage(conn: Connection, ws: Workspace, plan: Plan) -> None:
    """Write what a stage found: new transactions, new sources and new review items."""
    instruments = _Instruments(conn)
    placeholders: dict[str, str] = {}
    for txn in ws.alive():
        if not txn.is_new:
            continue
        state = "held" if txn.key in plan.held else "staged"
        placeholder = txn.ref
        values = row_values(ws, txn, state=state, batch_id=txn.batch_id, instruments=instruments)
        txn.id = repos.insert_transaction(conn, **values)
        txn.stored_state = state
        txn.stored = values
        placeholders[placeholder] = str(txn.id)
    _store_new_sources(conn, ws)
    for item in _new_open_items(ws):
        _insert_item(conn, ws, item, placeholders)


def apply_plan(conn: Connection, ws: Workspace, plan: Plan, *, now: str, accepting: int | None = None) -> None:
    """Write everything `plan` decided; `accepting` is the batch being accepted, if any."""
    repos.delete_lots_and_disposals(conn, ws.portfolio_id)
    for merged_key, target_key in plan.merges:
        merged = ws.txns[merged_key]
        target = ws.final(target_key)
        if merged.id is not None and target.id is not None and merged.id != target.id:
            repos.merge_transaction_rows(conn, source_id=merged.id, target_id=target.id)

    instruments = _Instruments(conn)
    placeholders: dict[str, str] = {}
    updates: list[tuple[Txn, dict[str, Any]]] = []
    for txn in ws.alive():
        state = _state(ws, txn, plan, accepting)
        released = txn.stored_state == "held" and state != "held"
        batch_id = accepting if released and accepting is not None else txn.batch_id
        values = row_values(ws, txn, state=state, batch_id=batch_id, instruments=instruments)
        if txn.id is None:
            placeholder = txn.ref
            txn.id = repos.insert_transaction(conn, **values)
            placeholders[placeholder] = str(txn.id)
            txn.stored, txn.stored_state = values, state
        elif txn.stored is None or _differs(txn.stored, values):
            updates.append((txn, values))
    for txn, values in updates:
        if txn.stored is not None and txn.stored.get("content_hash") != values["content_hash"]:
            repos.update_transaction(conn, _stored_id(txn), content_hash=f"pending-{txn.id}")
    for txn, values in updates:
        repos.update_transaction(conn, _stored_id(txn), **values)
        txn.stored, txn.stored_state, txn.batch_id = values, values["state"], values["batch_id"]

    _store_new_sources(conn, ws)
    for item in _new_open_items(ws):
        _insert_item(conn, ws, item, placeholders)
    for closure in plan.closures:
        repos.close_review_item(conn, _item_id(closure.item), resolution=closure.resolution, resolved_at=now)
    for item in plan.reopened:
        repos.reopen_review_item(conn, _item_id(item))
    for item in plan.updated_items:
        repos.update_review_item(
            conn, _item_id(item), message=item.message, fields_json=item.fields, import_id=item.import_id
        )
    _store_changed_items(conn, plan)

    isins = {lot.isin for lot in plan.book.lots}
    repos.replace_lots_and_disposals(
        conn, ws.portfolio_id, plan.book, {isin: instruments.id_for(isin, None) for isin in isins}
    )


def apply_item_decision(conn: Connection, ws: Workspace, plan: Plan, *, now: str) -> None:
    """Write a resolution of an item of the staged batch: the item, a merge, and its transaction's state."""
    for merged_key, target_key in plan.merges:
        merged = ws.txns[merged_key]
        target = ws.final(target_key)
        if merged.id is not None and target.id is not None and merged.id != target.id:
            repos.merge_transaction_rows(conn, source_id=merged.id, target_id=target.id)
    _store_changed_items(conn, plan)
    for item in plan.changed_items:
        if item.txn_key is None:
            continue
        txn = ws.final(item.txn_key)
        if txn.id is None or txn.stored_state != "held":
            continue
        if not any(other.holds for other in ws.items_of(txn)):
            state = "accepted" if ws.batches.get(txn.batch_id) == "accepted" else "staged"
            repos.update_transaction(conn, txn.id, state=state)
            txn.stored_state = state


def _state(ws: Workspace, txn: Txn, plan: Plan, accepting: int | None) -> str:
    if txn.key in plan.held:
        return "held"
    if txn.batch_id == accepting or ws.batches.get(txn.batch_id) == "accepted":
        return "accepted"
    if accepting is not None and txn.stored_state == "held":
        return "accepted"  # released into the batch being accepted
    return "staged"


def _differs(stored: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    return any(_same_form(stored.get(key)) != _same_form(value) for key, value in values.items())


def _same_form(value: Any) -> Any:
    """Decimals compared by their exact digits ("1.00" is not "1.0"), dates as text."""
    if isinstance(value, Decimal):
        return ("decimal", str(value))
    if isinstance(value, date):
        return ("date", value.isoformat())
    return value


def _stored_id(txn: Txn) -> int:
    if txn.id is None:
        raise RuntimeError("A transaction to update has no id.")
    return txn.id


def _item_id(item: Item) -> int:
    if item.id is None:
        raise RuntimeError("A review item to update has no id.")
    return item.id


def _store_new_sources(conn: Connection, ws: Workspace) -> None:
    """Store every source not stored yet, in one statement, under the transaction that holds it."""
    rows = []
    for txn in list(ws.txns.values()):
        for source in txn.sources:
            if source.stored:
                continue
            owner = ws.storage_owner(source.first_owner)
            rows.append(
                {
                    "transaction_id": _stored_id(owner),
                    "import_id": source.import_id,
                    "source_kind": source.kind.value,
                    "precedence": source.precedence,
                    "report_key": source.report_key,
                    "line_from": source.txn.evidence[0],
                    "line_to": source.txn.evidence[1],
                    "fields_json": transaction_to_dict(source.txn),
                }
            )
            source.stored = True
    repos.add_sources(conn, rows)


def _new_open_items(ws: Workspace) -> list[Item]:
    return sorted(
        (item for item in ws.items.values() if item.is_new and item.status == "open"), key=lambda item: -item.key
    )


def _insert_item(conn: Connection, ws: Workspace, item: Item, placeholders: Mapping[str, str]) -> None:
    txn_id = None
    anchor = item.first_owner if item.first_owner is not None else item.txn_key
    if anchor is not None:
        txn_id = _stored_id(ws.storage_owner(anchor))
    dedupe_key = _resolve_placeholder(ws, item.dedupe_key, placeholders)
    item.id = repos.open_review_item(
        conn,
        dedupe_key=dedupe_key,
        portfolio_id=ws.portfolio_id,
        batch_id=item.batch_id,
        import_id=item.import_id,
        transaction_id=txn_id,
        kind=item.kind.value,
        message=item.message,
        extracted_text=item.extracted_text,
        fields_json=item.fields,
        created_at=item.created_at,
        resolved_at=None,
        resolution_json=None,
    )
    ws.dedupe_keys.discard(item.dedupe_key)
    item.dedupe_key = dedupe_key
    ws.dedupe_keys.add(dedupe_key)


def _resolve_placeholder(ws: Workspace, dedupe_key: str, placeholders: Mapping[str, str]) -> str:
    """Put the stored id of a new transaction into a dedupe key that names it as "new<n>"."""
    parts = dedupe_key.split("|")
    if len(parts) < 3 or parts[1] != "txn" or not parts[2].startswith("new"):
        return dedupe_key
    placeholder = parts[2]
    stored = placeholders.get(placeholder)
    if stored is None:
        stored = str(_stored_id(ws.storage_owner(-int(placeholder.removeprefix("new")))))
    parts[2] = stored
    return "|".join(parts)


def _store_changed_items(conn: Connection, plan: Plan) -> None:
    for item in plan.changed_items:
        if item.id is None:
            continue
        repos.update_review_item(
            conn,
            item.id,
            status=item.status,
            resolution_json=item.resolution,
            resolved_at=item.resolved_at,
        )
