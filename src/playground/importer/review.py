"""The review queue (plan 5.3.5): which items are checked again, and how you settle one.

The **review queue** lists documents, rows and conflicts the app could not handle with certainty.
Nothing in it is guessed or dropped. Every item keeps the extracted text or the raw row, the
fields read so far, and the transaction it concerns, if any.

Who raises what:

- the parsers and the classifier: unknown and ambiguous layouts, unknown CSV headers, rows they
  could not read, missing fields, amounts that do not add up, corporate actions, invalid ISINs;
- the pipeline, while matching and at every check: `missing_field` (a purchase, sale or
  transfer without its quantity or ISIN, for example one known only from a statement line),
  `field_conflict`, `possible_duplicate` and `missing_cost_basis` (a transfer in without the cost
  you enter with `pg transfers set-cost`);
- the ledger's lot book: `oversell` and `split_unclear`.

A transaction is **held back** (stored, but kept out of lots, holdings and value) while an item
that concerns it holds it: a `missing_field` or `possible_duplicate` of the pipeline, or a
parser's item about the transaction it came with. Dismissing such an item leaves the transaction
out; `resolve --use-parsed` (or `--merge` and `--keep-both` for a possible duplicate) releases it.

**Checked again.** `evaluate` runs on every stage, accept and resolution. An item whose
condition no longer holds is resolved as superseded by the file whose data removed the problem:

| Kind | Stays open while |
|---|---|
| `missing_field` | the merged transaction still lacks the field |
| `amounts_do_not_add_up` | the fields in use still come from that document (a manual CSV row supersedes it) |
| `field_conflict` | the sources still disagree on the field and no manual CSV row sets it |
| `possible_duplicate` | the held transaction still has a near-date match (rule c) and no exact one |
| `missing_cost_basis` | the transfer in still has no cost input (then it is resolved as "cost entered") |
| `oversell`, `split_unclear` | the rebuilt lot book still reports it |
| unknown and ambiguous layouts, unknown CSV headers | no parser recognises the file |

A held `possible_duplicate` whose check finds an exact match is merged into it; one whose near
match is gone becomes a transaction of its own. The other kinds stay open until you resolve or
dismiss them. A dismissed item stays dismissed; its `dedupe_key` stops it from coming back. An
item superseded automatically opens again if its problem comes back.
"""

from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Literal

from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.dates import format_ts_utc
from playground.core.errors import PlaygroundError
from playground.core.types import TxnType
from playground.importer import persist
from playground.importer.keys import quantity_text
from playground.importer.merge import Report, conflicts, missing_fields
from playground.importer.model import ReviewKind, SourceKind
from playground.importer.workspace import (
    UNRECOGNISED_KINDS,
    Cause,
    Closure,
    Item,
    Plan,
    Txn,
    Workspace,
    touching,
)
from playground.ledger.fifo import LedgerTxn, build_lots
from playground.storage import repos
from playground.storage.schema import review_items

Decision = Literal["merge", "keep-both", "use-parsed"]

TYPE_LABELS = {
    TxnType.BUY: "purchase",
    TxnType.SELL: "sale",
    TxnType.DIVIDEND: "dividend",
    TxnType.INTEREST: "interest payment",
    TxnType.FEE: "fee",
    TxnType.TAX: "tax booking",
    TxnType.DEPOSIT: "deposit",
    TxnType.WITHDRAWAL: "withdrawal",
    TxnType.SPLIT: "split",
    TxnType.TRANSFER_IN: "transfer in",
    TxnType.TRANSFER_OUT: "transfer out",
}
FIELD_NAMES = {"quantity": "quantity", "isin": "ISIN"}


class ReviewError(PlaygroundError):
    """A review item does not exist, is already settled, or does not take that resolution."""


# --- Checking every item again ----------------------------------------------------------------


def evaluate(ws: Workspace, cause: Cause, *, now: str, reparsed: Mapping[int, str] | None = None) -> Plan:
    """Check every open item again, raise the items the data now calls for, and build the lot book.

    Changes `ws` in memory (merges, item statuses, new items) and returns what it decided.
    `reparsed` maps the id of an earlier import that no parser recognised to the name of the
    file that a parser now reads in the batch of `cause`.
    """
    plan = Plan()
    _settle_possible_duplicates(ws, cause, plan, now)

    desired = _desired_items(ws, cause, now)
    for item in _evaluated(ws):
        if item.origin == "ledger":
            continue
        if item.status == "open":
            resolution = _resolution(ws, item, desired, cause, reparsed or {})
            if resolution is not None:
                _close(item, resolution, plan, now)
            elif item.dedupe_key in desired:
                _refresh_text(item, desired[item.dedupe_key], plan)
        elif _may_reopen(item) and item.dedupe_key in desired:
            _reopen(item, plan)
            _refresh_text(item, desired[item.dedupe_key], plan)
    for key, item in desired.items():
        if key not in ws.dedupe_keys:
            ws.add_item(item)
            plan.new_items.append(item)

    plan.held = {item.txn_key for item in _evaluated(ws) if item.holds and item.txn_key is not None}
    plan.book = build_lots([ledger_txn(txn) for txn in ws.alive() if txn.key not in plan.held])
    _check_ledger_items(ws, cause, plan, now)
    return plan


def ledger_txn(txn: Txn) -> LedgerTxn:
    """A transaction as the ledger sees it (plan 5.5); its id is the workspace key."""
    fields = txn.fields
    return LedgerTxn(
        id=txn.key,
        type=fields.type,
        isin=fields.isin,
        ts_utc=fields.time.ts_utc,
        quantity=fields.quantity,
        amount_eur=fields.amount_eur,
        fees_eur=fields.fees_eur,
        tax_eur=fields.tax_eur,
        split_new_quantity=fields.split_new_quantity,
        cost_input=txn.cost_input if fields.type is TxnType.TRANSFER_IN else None,
    )


def _evaluated(ws: Workspace) -> Iterator[Item]:
    """The items a check looks at, stored ones by id, then new ones in the order they came."""
    for item in sorted(ws.items.values(), key=lambda item: (item.is_new, abs(item.key))):
        if item.evaluated:
            yield item


def _close(item: Item, resolution: dict[str, Any], plan: Plan, now: str) -> None:
    item.status = "resolved"
    item.resolution = resolution
    item.resolved_at = now
    if not item.is_new:
        plan.closures.append(Closure(item=item, resolution=resolution))


def _reopen(item: Item, plan: Plan) -> None:
    item.status = "open"
    item.resolution = None
    item.resolved_at = None
    plan.reopened.append(item)


def _refresh_text(item: Item, current: Item, plan: Plan) -> None:
    """An item that stays open describes the problem as it is now: the figures, and the file whose
    fields are in use, may have changed since it was raised."""
    if item.message == current.message and item.fields == current.fields and item.import_id == current.import_id:
        return
    item.message = current.message
    item.fields = dict(current.fields)
    item.import_id = current.import_id
    if not item.is_new and item not in plan.updated_items:
        plan.updated_items.append(item)


def _may_reopen(item: Item) -> bool:
    """An item the app closed on its own; one you settled stays settled."""
    return (
        item.status == "resolved"
        and (item.resolution or {}).get("how") == "superseded"
        and item.origin in ("pipeline", "ledger")
        and item.kind is not ReviewKind.POSSIBLE_DUPLICATE
    )


def _by(ws: Workspace, cause: Cause, txns: Iterable[Txn | None], prefer: Report | None = None) -> str:
    """The file whose data removed a problem: `prefer` if it came in the batch of `cause`, else
    the files of that batch that report any of `txns`, else what `cause` says."""
    if cause.batch_id is None:
        return cause.text
    if prefer is not None and prefer.batch_id == cause.batch_id:
        return prefer.file_name
    names = touching([txn for txn in txns if txn is not None], cause.batch_id)
    return ", ".join(names) if names else cause.text


def _settle_possible_duplicates(ws: Workspace, cause: Cause, plan: Plan, now: str) -> None:
    """Check every open `possible_duplicate` again until nothing changes (a merge can settle another)."""
    while True:
        for item in _evaluated(ws):
            if item.status != "open" or item.kind is not ReviewKind.POSSIBLE_DUPLICATE or item.origin != "pipeline":
                continue
            txn = ws.txns.get(item.txn_key) if item.txn_key is not None else None
            if txn is None or not txn.alive or item.dedupe_key != f"possible_duplicate|txn|{txn.ref}":
                # Its transaction was merged into another one: the question is settled.
                _close(item, {"how": "superseded", "by": _by(ws, cause, [txn])}, plan, now)
                break
            match = ws.rematch(txn)
            if match.possible_duplicate:
                continue
            nearby = [txn, *ws.near(txn.parts, exclude=txn.key)]
            if match.target is not None:
                nearby.append(match.target)
            by = _by(ws, cause, nearby)
            if match.target is not None:
                ws.merge_into(txn, match.target)
                plan.merges.append((txn.key, match.target.key))
            _close(item, {"how": "superseded", "by": by}, plan, now)
            break
        else:
            return


def _new_item(
    ws: Workspace,
    dedupe_key: str,
    kind: ReviewKind,
    txn: Txn,
    message: str,
    fields: dict[str, Any],
    cause: Cause,
    now: str,
) -> Item:
    top = max(txn.sources, key=lambda source: (source.precedence, source.order))
    return Item(
        key=ws.new_item_key(),
        kind=kind,
        status="open",
        dedupe_key=dedupe_key,
        message=message,
        fields=fields,
        txn_key=txn.key,
        import_id=top.import_id,
        batch_id=cause.batch_id,
        created_at=now,
        first_owner=txn.key,
    )


def _desired_items(ws: Workspace, cause: Cause, now: str) -> dict[str, Item]:
    """The pipeline's items the data calls for now, by dedupe key (plan 5.3.4, 5.3.5)."""
    desired: dict[str, Item] = {}
    for txn in ws.alive():
        fields = txn.fields
        missing = missing_fields(fields)
        if missing:
            key = f"missing_field|txn|{txn.ref}"
            desired[key] = _new_item(
                ws,
                key,
                ReviewKind.MISSING_FIELD,
                txn,
                _missing_message(txn, missing),
                {"missing": ",".join(missing)},
                cause,
                now,
            )
        for conflict in conflicts([source.report() for source in txn.sources]):
            key = f"field_conflict|txn|{txn.ref}|{conflict.field}"
            values = {kind.value: format(value, "f") for kind, value in conflict.values.items()}
            desired[key] = _new_item(
                ws,
                key,
                ReviewKind.FIELD_CONFLICT,
                txn,
                conflict.message(subject(txn)),
                {"field": conflict.field, **values, "used": format(conflict.used, "f")},
                cause,
                now,
            )
        if fields.type is TxnType.TRANSFER_IN and txn.cost_input is None:
            key = f"missing_cost_basis|txn|{txn.ref}"
            quantity = quantity_text(fields.quantity) if fields.quantity is not None else "?"
            desired[key] = _new_item(
                ws,
                key,
                ReviewKind.MISSING_COST_BASIS,
                txn,
                _cost_message(txn, quantity),
                {"isin": fields.isin or "", "quantity": quantity, "booked_on": txn.day.isoformat()},
                cause,
                now,
            )
    return desired


def _resolution(
    ws: Workspace, item: Item, desired: Mapping[str, Item], cause: Cause, reparsed: Mapping[int, str]
) -> dict[str, Any] | None:
    """How an open item is settled now, or `None` while its problem still holds."""
    if item.origin == "pipeline":
        if item.kind is ReviewKind.POSSIBLE_DUPLICATE or item.dedupe_key in desired:
            return None
        txn = ws.txns.get(item.txn_key) if item.txn_key is not None else None
        if item.kind is ReviewKind.MISSING_COST_BASIS and txn is not None and txn.cost_input is not None:
            return {"how": "cost entered"}
        prefer = None
        if txn is not None and item.kind is ReviewKind.MISSING_FIELD:
            wanted = [name for name in str(item.fields.get("missing", "")).split(",") if name]
            prefer = next((txn.view.providers[name] for name in wanted if name in txn.view.providers), None)
        elif txn is not None and item.kind is ReviewKind.FIELD_CONFLICT:
            prefer = txn.view.providers.get(str(item.fields.get("field")))
        return {"how": "superseded", "by": _by(ws, cause, [txn], prefer)}
    if item.kind in UNRECOGNISED_KINDS:
        if item.import_id is not None and item.import_id in reparsed:
            return {"how": "superseded", "by": reparsed[item.import_id]}
        return None
    if item.kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP and item.txn_key is not None:
        txn = ws.txns[item.txn_key]
        if txn.view.top.kind is SourceKind.MANUAL_CSV:
            return {"how": "superseded", "by": _by(ws, cause, [txn], txn.view.top)}
    return None


def _check_ledger_items(ws: Workspace, cause: Cause, plan: Plan, now: str) -> None:
    """Turn the lot book's issues into items, and close the ones it no longer reports (plan 5.3.5)."""
    desired: dict[str, Item] = {}
    for issue in plan.book.issues:
        txn = ws.txns[issue.txn_id]
        key = f"{issue.kind}|txn|{txn.ref}"
        if key not in desired:
            desired[key] = _new_item(
                ws, key, ReviewKind(issue.kind), txn, issue.message, {"isin": issue.isin}, cause, now
            )
    for item in _evaluated(ws):
        if item.origin != "ledger":
            continue
        if item.status == "open" and item.dedupe_key not in desired:
            isin = item.fields.get("isin")
            same_isin = [txn for txn in ws.alive() if txn.fields.isin == isin]
            _close(item, {"how": "superseded", "by": _by(ws, cause, same_isin)}, plan, now)
        elif item.status == "open":
            _refresh_text(item, desired[item.dedupe_key], plan)
        elif _may_reopen(item) and item.dedupe_key in desired:
            _reopen(item, plan)
            _refresh_text(item, desired[item.dedupe_key], plan)
    for key, item in desired.items():
        if key not in ws.dedupe_keys:
            ws.add_item(item)
            plan.new_items.append(item)


# --- Messages ---------------------------------------------------------------------------------


def subject(txn: Txn) -> str:
    """How a message names a transaction: "the purchase of SAP SE (DE0007164600) on 2024-01-15"."""
    fields = txn.fields
    label = TYPE_LABELS[fields.type]
    if fields.isin:
        name = txn.view.name or fields.isin
        return f"the {label} of {name} ({fields.isin}) on {txn.day.isoformat()}"
    return f"the {label} on {txn.day.isoformat()}"


def _missing_message(txn: Txn, missing: list[str]) -> str:
    names = " and the ".join(FIELD_NAMES.get(name, name) for name in missing)
    what = subject(txn)
    if txn.kinds == {SourceKind.PDF_STATEMENT}:
        return (
            f"{what[0].upper()}{what[1:]} is known only from an account statement line, which does not give "
            f"the {names}. It is held back from your holdings until a trade confirmation, the CSV export "
            f"or a manual CSV row gives the {names}."
        )
    return (
        f"{what[0].upper()}{what[1:]} has no {names}. It is held back from your holdings until a file "
        f"gives the {names}. You can add it with a manual CSV row."
    )


def _cost_message(txn: Txn, quantity: str) -> str:
    fields = txn.fields
    name = txn.view.name or fields.isin or "a security"
    isin = fields.isin or "ISIN"
    return (
        f"{quantity} shares of {name} ({isin}) were transferred in on {txn.day.isoformat()} without a cost basis. "
        "They count in your holdings and value, but their cost and any gain stay unknown until you enter "
        f"the cost with: pg transfers set-cost --isin {isin} --acquired YYYY-MM-DD --cost-eur AMOUNT"
    )


def _duplicate_message(txn: Txn, near: Iterable[Txn]) -> str:
    dates = sorted({other.day.isoformat() for other in near})
    amount = txn.fields.amount_eur
    booked = f" for {format(amount, 'f')} EUR" if amount is not None else ""
    return (
        f"{subject(txn)[0].upper()}{subject(txn)[1:]}{booked} may be the same transaction as the one "
        f"on {' and '.join(dates)}: the dates differ by up to 3 days. It is held back until you decide. "
        "Run pg review resolve with --merge if it is the same transaction, or --keep-both if there are two."
    )


def possible_duplicate_item(ws: Workspace, txn: Txn, near: Iterable[Txn], *, cause: Cause, now: str) -> Item:
    """The item for a candidate that rule c holds back as a possible duplicate (plan 5.3.4)."""
    near = list(near)
    fields = {
        "date": txn.day.isoformat(),
        "near_dates": ",".join(sorted({other.day.isoformat() for other in near})),
    }
    return _new_item(
        ws,
        f"possible_duplicate|txn|{txn.ref}",
        ReviewKind.POSSIBLE_DUPLICATE,
        txn,
        _duplicate_message(txn, near),
        fields,
        cause,
        now,
    )


# --- Records for the CLI, the diff and the API -------------------------------------------------


def brief(txn: Txn | None, names: Mapping[str, str]) -> dict[str, Any] | None:
    """A transaction in a few fields, to show which one an item or a match is about."""
    if txn is None:
        return None
    fields = txn.fields
    return {
        "id": txn.id,
        "type": fields.type.value,
        "isin": fields.isin,
        "name": names.get(fields.isin, txn.view.name) if fields.isin else None,
        "date": txn.day.isoformat(),
        "amount_eur": format(fields.amount_eur, "f") if fields.amount_eur is not None else None,
        "quantity": format(fields.quantity, "f") if fields.quantity is not None else None,
    }


def item_record(ws: Workspace, item: Item, names: Mapping[str, str], *, with_text: bool = False) -> dict[str, Any]:
    """One review item as JSON-ready data (the extracted text only when asked for)."""
    txn = ws.final(item.txn_key) if item.txn_key is not None and item.txn_key in ws.txns else None
    info = ws.imports.get(item.import_id) if item.import_id is not None else None
    record: dict[str, Any] = {
        "id": item.id,
        "kind": item.kind.value,
        "status": item.status,
        "message": item.message,
        "file_name": info.file_name if info is not None else None,
        "transaction": brief(txn, names),
        "fields": {key: item.fields[key] for key in sorted(item.fields)},
        "created_at": item.created_at,
        "resolved_at": item.resolved_at,
        "resolution": item.resolution,
    }
    if with_text:
        record["extracted_text"] = item.extracted_text
    return record


STATUSES = ("open", "resolved", "dismissed")


def list_items(conn: Connection, portfolio_id: int, *, status: str | None = "open") -> list[dict[str, Any]]:
    """The review items of the portfolio with `status` (every item for `None`), oldest first."""
    if status is not None and status not in STATUSES:
        raise ReviewError(f"Unknown status {status!r}. Use open, resolved, dismissed or all.")
    ws = Workspace.load(conn, portfolio_id)
    names = repos.instrument_names(conn)
    items = sorted((item for item in ws.items.values() if status is None or item.status == status), key=lambda i: i.key)
    return [item_record(ws, item, names) for item in items]


def get_item(conn: Connection, portfolio_id: int, item_id: int) -> dict[str, Any]:
    """One review item with its extracted text."""
    ws = Workspace.load(conn, portfolio_id)
    item = ws.items.get(item_id)
    if item is None:
        raise ReviewError(f"There is no review item {item_id}.")
    return item_record(ws, item, repos.instrument_names(conn), with_text=True)


# --- Settling an item -------------------------------------------------------------------------


def dismiss_item(conn: Connection, item_id: int, *, reason: str, clock: Clock) -> dict[str, Any]:
    """Dismiss an open item with your reason. A transaction it holds back stays out of your holdings."""
    return _settle(conn, item_id, clock=clock, how="dismiss", reason=reason)


def resolve_item(
    conn: Connection, item_id: int, *, how: Decision | str, clock: Clock, into: int | None = None
) -> dict[str, Any]:
    """Settle an open item: `merge` or `keep-both` for a possible duplicate, `use-parsed` to keep a held
    transaction as parsed. `into` names the transaction to merge into when there is more than one."""
    if how not in ("merge", "keep-both", "use-parsed"):
        raise ReviewError(f"Unknown resolution {how!r}. Use merge, keep-both or use-parsed.")
    return _settle(conn, item_id, clock=clock, how=how, into=into)


def _settle(
    conn: Connection, item_id: int, *, clock: Clock, how: str, reason: str | None = None, into: int | None = None
) -> dict[str, Any]:
    portfolio_id = _portfolio_of_item(conn, item_id)
    staged = repos.staged_batch_id(conn, portfolio_id)
    now = format_ts_utc(clock.now_utc())
    full = Workspace.load(conn, portfolio_id)
    item = full.items[item_id]
    _check_allowed(full, item, how)
    txn = full.txns.get(item.txn_key) if item.txn_key is not None else None
    in_staged = staged is not None and (item.batch_id == staged or (txn is not None and txn.batch_id == staged))

    # An item of the staged batch: only the item and its transaction change now; accept checks the rest.
    ws = full if in_staged else Workspace.load(conn, portfolio_id, exclude_batch=staged)
    item = ws.items[item_id]
    txn = ws.txns.get(item.txn_key) if item.txn_key is not None else None
    target = _merge_target(ws, item, txn, into) if how == "merge" else None
    _apply_decision(item, how, reason, now, target)

    plan = Plan(changed_items=[item])
    if target is not None and txn is not None:
        ws.merge_into(txn, target)
        plan.merges.append((txn.key, target.key))
    if in_staged:
        persist.apply_item_decision(conn, ws, plan, now=now)
    else:
        checked = evaluate(ws, Cause(batch_id=None, text=f"review item {item_id}"), now=now)
        checked.changed_items.append(item)
        checked.merges[:0] = plan.merges
        persist.apply_plan(conn, ws, checked, now=now)
    return item_record(ws, item, repos.instrument_names(conn))


def _portfolio_of_item(conn: Connection, item_id: int) -> int:
    row = conn.execute(review_items.select().where(review_items.c.id == item_id)).first()
    if row is None:
        raise ReviewError(f"There is no review item {item_id}.")
    return int(row.portfolio_id)


def _check_allowed(ws: Workspace, item: Item, how: str) -> None:
    if item.status != "open":
        raise ReviewError(f"Review item {item.id} is already {item.status}. Only an open item can be settled.")
    if how == "dismiss":
        return
    if how in ("merge", "keep-both"):
        if item.kind is not ReviewKind.POSSIBLE_DUPLICATE:
            raise ReviewError(
                f"Review item {item.id} is not a possible duplicate, so --merge and --keep-both do not apply. "
                "Use --use-parsed, or dismiss it."
            )
        return
    if item.kind is ReviewKind.POSSIBLE_DUPLICATE:
        raise ReviewError(f"Review item {item.id} is a possible duplicate. Use --merge or --keep-both.")
    if item.kind is ReviewKind.MISSING_FIELD and item.origin == "pipeline":
        raise ReviewError(
            f"Review item {item.id}: the transaction still lacks the {item.fields.get('missing', 'field')}, "
            "so it cannot be used as parsed. Import a document or a manual CSV row that gives it."
        )
    if not item.holds or item.origin != "parser":
        raise ReviewError(
            f"Review item {item.id} does not hold a transaction back, so --use-parsed does not apply. "
            "Dismiss it once you have checked it."
        )


def _merge_target(ws: Workspace, item: Item, txn: Txn | None, into: int | None) -> Txn:
    if txn is None:
        raise ReviewError(f"Review item {item.id} has no transaction to merge.")
    candidates = ws.near(txn.parts, exclude=txn.key)
    if into is not None:
        chosen = ws.txns.get(into)
        if chosen is None or not chosen.alive or chosen.key == txn.key:
            raise ReviewError(f"There is no transaction {into} to merge into.")
        return chosen
    if not candidates:
        raise ReviewError(f"Review item {item.id}: no transaction near this one is left to merge into.")
    # The nearest date first; between two on one date, the lower occurrence (the earlier one).
    return min(candidates, key=lambda other: (abs((other.day - txn.day).days), other.occurrence, abs(other.key)))


def _apply_decision(item: Item, how: str, reason: str | None, now: str, target: Txn | None) -> None:
    if how == "dismiss":
        item.status = "dismissed"
        item.resolution = {"how": "dismissed", "reason": reason or ""}
    elif how == "merge" and target is not None:
        item.status = "resolved"
        item.resolution = {"how": "merge", "into": target.id}
    else:
        item.status = "resolved"
        item.resolution = {"how": how}
    item.resolved_at = now
