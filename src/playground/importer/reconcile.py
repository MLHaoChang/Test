"""The reconciliation diff of an import batch (plan 5.3.6, AC6).

`build_diff` returns what a stage found, as stored with the batch when it was staged, and adds:

- the **holdings** of every ISIN before (the accepted transactions only) and after (the accepted
  ones plus the staged ones, held-back ones left out) as of a day, computed with the ledger's
  lot book, so splits and transfers in count;
- when you give your **confirmed holdings** (the quantities the Trade Republic app shows), a
  comparison row for each of your rows and for each ISIN held that your list does not name: the
  computed quantity after the import, yours, the difference (computed minus yours), and `match`,
  `mismatch`, `missing_in_import` (you hold it, the import does not show it) or
  `missing_in_confirmed` (the import shows it, your list does not). Each of your rows is compared
  on its own day, so an ISIN you list on two days gives a row for each.

The stored lists (the files, the transactions, the review items and their counts) are not
computed again: a review item settled while the batch is staged can make them out of date. The
holdings are computed on every call, so for a staged batch they show what accept will do, and
the text says so.

For a batch accepted earlier, before and after are the portfolio as it is now, without and with
that batch's transactions: the registry keeps no history, so later imports and resolutions count
on both sides. The text says so.

The day defaults to today, from the clock (plan 3.3). Nothing here writes to the registry.

`render_diff_text` writes the same diff in plain English for `pg import` and `pg reconcile`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.dates import format_ts_utc
from playground.core.text import plural
from playground.core.types import TxnType
from playground.importer.confirmed_csv import ConfirmedHolding
from playground.importer.keys import quantity_text
from playground.importer.pipeline import BatchNotFoundError, reparsed_by, transaction_record
from playground.importer.review import TYPE_LABELS, evaluate, ledger_txn
from playground.importer.workspace import Cause, Workspace
from playground.ledger.fifo import LedgerTxn, LotBook, build_lots
from playground.storage import repos

_ZERO = Decimal(0)
SUMMARY_KEYS = (
    "counts",
    "files",
    "new",
    "merged",
    "already_known",
    "held_back",
    "completed",
    "review_new",
    "review_closed",
)


@dataclass(frozen=True)
class HoldingChange:
    """One ISIN's quantity before and after the import."""

    isin: str
    name: str | None
    before: Decimal
    after: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "isin": self.isin,
            "name": self.name,
            "before": quantity_text(self.before),
            "after": quantity_text(self.after),
            "change": quantity_text(self.after - self.before),
        }


@dataclass(frozen=True)
class ConfirmedRow:
    """One ISIN compared with the holdings you confirmed."""

    isin: str
    name: str | None
    as_of: date
    computed: Decimal
    confirmed: Decimal
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "isin": self.isin,
            "name": self.name,
            "as_of": self.as_of.isoformat(),
            "computed": quantity_text(self.computed),
            "confirmed": quantity_text(self.confirmed),
            "difference": quantity_text(self.computed - self.confirmed),
            "status": self.status,
        }


@dataclass(frozen=True)
class ConfirmedComparison:
    """The computed holdings against the ones you confirmed, sorted by ISIN and day: a row for each
    of your rows, and one for each ISIN held that your list does not name."""

    rows: tuple[ConfirmedRow, ...]

    @property
    def all_match(self) -> bool:
        return all(row.status == "match" for row in self.rows)

    @property
    def message(self) -> str:
        matched = sum(1 for row in self.rows if row.status == "match")
        return f"{matched} of {len(self.rows)} match"

    def to_dict(self) -> dict[str, Any]:
        counts = {status: 0 for status in ("match", "mismatch", "missing_in_import", "missing_in_confirmed")}
        for row in self.rows:
            counts[row.status] += 1
        return {
            "rows": [row.to_dict() for row in self.rows],
            "counts": counts,
            "all_match": self.all_match,
            "message": self.message,
        }


@dataclass(frozen=True)
class ReconciliationDiff:
    """The diff of one batch (plan 5.3.6)."""

    batch: dict[str, Any]
    as_of: date
    summary: dict[str, Any]
    holdings: tuple[HoldingChange, ...]
    confirmed: ConfirmedComparison | None

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.summary.get("counts") or {})

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"batch": self.batch, "as_of": self.as_of.isoformat()}
        for key in SUMMARY_KEYS:
            data[key] = self.summary.get(key, [] if key != "counts" else {})
        data["holdings"] = [change.to_dict() for change in self.holdings]
        data["confirmed"] = self.confirmed.to_dict() if self.confirmed is not None else None
        return data


def build_diff(
    conn: Connection,
    batch_id: int,
    *,
    clock: Clock,
    confirmed: Sequence[ConfirmedHolding] | None = None,
    as_of: date | None = None,
) -> ReconciliationDiff:
    """The diff of `batch_id` with the holdings as of `as_of` (today by default) and, if given, your holdings."""
    batch = repos.get_batch(conn, batch_id)
    if batch is None:
        raise BatchNotFoundError(f"There is no import batch {batch_id}.")
    day = as_of or clock.today()
    before, after = _books(conn, batch, clock)
    names = repos.instrument_names(conn)
    held_before = before.holdings(day)
    held_after = after.holdings(day)
    holdings = tuple(
        HoldingChange(
            isin=isin, name=names.get(isin), before=held_before.get(isin, _ZERO), after=held_after.get(isin, _ZERO)
        )
        for isin in sorted(set(held_before) | set(held_after))
    )
    comparison = compare_confirmed(after, confirmed, day, names) if confirmed is not None else None
    return ReconciliationDiff(
        batch={
            "id": batch.id,
            "status": batch.status,
            "created_at": batch.created_at,
            "accepted_at": batch.accepted_at,
        },
        as_of=day,
        summary=dict(batch.summary_json or {}),
        holdings=holdings,
        confirmed=comparison,
    )


def compare_confirmed(
    book: LotBook, confirmed: Sequence[ConfirmedHolding], as_of: date, names: Mapping[str, str]
) -> ConfirmedComparison:
    """Compare your holdings with `book`, each of your rows on its own day; the rest on `as_of`.

    Every row you give is compared, so an ISIN you list on two days gives two rows.
    """
    rows: list[ConfirmedRow] = []
    for holding in confirmed:
        computed = book.holdings(holding.as_of).get(holding.isin, _ZERO)
        if computed == holding.quantity:
            status = "match"
        elif computed == 0:
            status = "missing_in_import"
        else:
            status = "mismatch"
        rows.append(
            ConfirmedRow(
                isin=holding.isin,
                name=names.get(holding.isin),
                as_of=holding.as_of,
                computed=computed,
                confirmed=holding.quantity,
                status=status,
            )
        )
    listed = {holding.isin for holding in confirmed}
    for isin, quantity in book.holdings(as_of).items():
        if isin not in listed:
            rows.append(
                ConfirmedRow(
                    isin=isin,
                    name=names.get(isin),
                    as_of=as_of,
                    computed=quantity,
                    confirmed=_ZERO,
                    status="missing_in_confirmed",
                )
            )
    return ConfirmedComparison(rows=tuple(sorted(rows, key=lambda row: (row.isin, row.as_of))))


def _books(conn: Connection, batch: Any, clock: Clock) -> tuple[LotBook, LotBook]:
    """The lot book before and after the batch.

    For a staged batch: the accepted transactions, then those plus the batch as accept would take
    them. For an accepted batch: every accepted transaction but the batch's, then all of them, so
    for a batch accepted earlier both books include what later batches brought.
    """
    portfolio_id = int(batch.portfolio_id)
    if batch.status == "staged":
        before = build_lots(stored_ledger(conn, portfolio_id, exclude_batch=int(batch.id)))
        ws = Workspace.load(conn, portfolio_id)
        now = format_ts_utc(clock.now_utc())
        cause = Cause(batch_id=int(batch.id), text=f"import batch {batch.id}")
        plan = evaluate(ws, cause, now=now, reparsed=reparsed_by(ws, int(batch.id)))
        return before, plan.book
    current = build_lots(stored_ledger(conn, portfolio_id, exclude_batch=repos.staged_batch_id(conn, portfolio_id)))
    if batch.status == "accepted":
        return build_lots(stored_ledger(conn, portfolio_id, exclude_batch=int(batch.id))), current
    return current, current


def stored_ledger(conn: Connection, portfolio_id: int, *, exclude_batch: int | None = None) -> list[LedgerTxn]:
    """The accepted transactions as the ledger sees them, leaving one batch and its sources out."""
    ws = Workspace.load(conn, portfolio_id, exclude_batch=exclude_batch)
    return [ledger_txn(txn) for txn in ws.alive() if txn.stored_state == "accepted"]


def transaction_records(
    conn: Connection,
    portfolio_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    txn_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The stored transactions (accepted, held and staged) with their sources, by booking time."""
    ws = Workspace.load(conn, portfolio_id)
    names = repos.instrument_names(conn)
    records = []
    for txn in sorted(ws.alive(), key=lambda txn: (str((txn.stored or {}).get("ts_utc")), txn.key)):
        record = transaction_record(ws, txn, names, stored=True)
        day = date.fromisoformat(record["date"])
        if date_from is not None and day < date_from:
            continue
        if date_to is not None and day > date_to:
            continue
        if txn_type is not None and record["type"] != txn_type:
            continue
        records.append(record)
    selected = records[offset:]
    return selected[:limit] if limit is not None else selected


# --- Plain text -------------------------------------------------------------------------------

# What each kind of file is, in plain words, for the file lines of the diff (not the parser ids).
DOC_TYPE_NAMES = {
    "trade_confirmation": "trade confirmation",
    "savings_plan_confirmation": "savings plan execution",
    "dividend_note": "dividend note",
    "split_notice": "split notice",
    "tax_notice": "tax notice",
    "interest_statement": "interest statement",
    "account_statement": "account statement",
    "corporate_action_notice": "corporate action notice",
    "csv_export": "Trade Republic CSV export",
    "manual_transaction": "manual CSV file",
}

_STATUS_TEXT = {
    "parsed": "read",
    "partial": "read in part, see the review items",
    "needs_review": "not read, see the review items",
    "duplicate_file": "already imported, skipped",
    "failed": "could not be read, see the review items",
    "reparsed": "read again by a newer parser",
}


def render_diff_text(diff: ReconciliationDiff) -> str:
    """The diff in plain English, for `pg import`, `pg imports show` and `pg reconcile`."""
    data = diff.to_dict()
    batch = data["batch"]
    counts = data["counts"]
    lines = []
    state = {"staged": "staged, not accepted yet", "accepted": "accepted", "discarded": "discarded"}.get(
        batch["status"], batch["status"]
    )
    lines.append(f"Import batch {batch['id']}: {state}.")
    lines.append(f"Files: {counts.get('files', 0)} ({counts.get('duplicate_files', 0)} already imported, skipped)")
    for entry in data["files"]:
        kind = DOC_TYPE_NAMES.get(entry.get("doc_type") or "") if entry.get("parser_id") else None
        name = f"{entry['file_name']} ({kind})" if kind else entry["file_name"]
        found = f", {plural(entry['candidates'], 'transaction')}" if entry.get("candidates") else ""
        lines.append(f"  {name}: {_STATUS_TEXT.get(entry['status'], entry['status'])}{found}")
    lines.append(f"Transactions read: {counts.get('candidates', 0)}")
    lines.append(f"  {plural(counts.get('new', 0), 'new transaction')}")
    lines.append(f"  {counts.get('merged', 0)} merged with a report in another file of this import")
    lines.append(f"  {counts.get('already_known', 0)} already known from earlier imports")
    lines.append(
        f"  {counts.get('held_back', 0)} held back (kept out of your holdings until the review items are settled)"
    )
    if counts.get("completed"):
        lines.append(f"  {counts['completed']} held back before and completed by this import")
    for entry in data["already_known"] + data["merged"]:
        if entry["candidate_date"] != entry["matched_date"]:
            lines.append(
                f"  Note: {entry['file_name']} dates {_what(entry['transaction'])} on {entry['candidate_date']}, "
                f"matched to the one on {entry['matched_date']}."
            )
    review_new = counts.get("review_new", 0)
    needs = "needs" if review_new == 1 else "need"
    lines.append(
        f"Review: {plural(review_new, 'new item')} {needs} your review, {counts.get('review_closed', 0)} will close"
    )
    for item in data["review_new"]:
        where = f" ({item['file_name']})" if item.get("file_name") else ""
        lines.append(f"  - {item['kind']}{where}: {item['message']}")
    for item in data["review_closed"]:
        resolution = item.get("resolution") or {}
        lines.append(f"  - closes {item['kind']}: {resolution.get('how')} by {resolution.get('by')}")
    if batch["status"] == "staged":
        lines.append(
            "The lists above show what the stage found. A review item settled since then can make them out of date."
        )
        lines.append("The holdings below are computed now and show what accept will do.")
    if batch["status"] == "accepted":
        lines.append(f"Holdings on {data['as_of']} (without this batch -> with it, as your portfolio is now):")
    else:
        lines.append(f"Holdings on {data['as_of']} (before -> after):")
    if not data["holdings"]:
        lines.append("  none")
    for change in data["holdings"]:
        lines.append(
            f"  {change['isin']} {change['name'] or ''}: {change['before']} -> {change['after']}".replace("  :", ":")
        )
    confirmed = data["confirmed"]
    if confirmed is not None:
        lines.append(f"Your confirmed holdings: {confirmed['message']}")
        for row in confirmed["rows"]:
            lines.append(
                f"  {row['isin']} {row['name'] or ''}: computed {row['computed']}, yours {row['confirmed']} "
                f"on {row['as_of']}: {row['status']}"
            )
    if batch["status"] == "staged":
        lines.append(
            f'Next: check the diff, then run "pg accept {batch["id"]}" to accept these transactions '
            f'into your portfolio copy, or "pg discard {batch["id"]}" to drop them.'
        )
    return "\n".join(lines)


def _what(txn: Mapping[str, Any] | None) -> str:
    """How the near-date note names a transaction: "a purchase of SAP SE (DE0007164600)", "a deposit"."""
    if not txn:
        return "a transaction"
    label = TYPE_LABELS[TxnType(txn["type"])]
    article = "an" if label[0] in "aeiou" else "a"
    isin = txn.get("isin")
    if not isin:
        return f"{article} {label}"
    name = txn.get("name")
    return f"{article} {label} of {name} ({isin})" if name and name != isin else f"{article} {label} of {isin}"
