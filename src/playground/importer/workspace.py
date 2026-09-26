"""The portfolio's transactions, their sources and the review items, in memory (plan 5.3.4, 5.3.5).

Staging, accept, the reconciliation diff and every resolution work on a `Workspace`: a copy of
what the registry holds for one portfolio, loaded in one go, changed in memory, and written back
by the caller (`pipeline.py`, `review.py`). Holding everything in memory keeps matching simple and
fast, and lets a stage compute exactly what accept will do without writing it.

The pieces:

- `Source`: one report of a transaction, the candidate as one file gave it (`fields_json`).
- `Txn`: a transaction: its sources, the fields in use (merged by precedence, `merge.py`), its
  semantic key and its occurrence. A transaction always follows its merged fields: when a new
  source changes them, its key is computed again and, if it changed, it takes the next
  occurrence of the new key (the next number after the highest one in use, counting the rows
  the next write leaves as they are).
- `Item`: a review item.

Matching (`match`) is rules a to d of plan 5.3.4. A transaction that is merged into another one
keeps a pointer to it (`merged_into`) until it is deleted.

Transactions and items not yet stored have negative keys; stored ones use their row id.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import Connection

from playground.importer.keys import NEAR_DATE_DAYS, PRECEDENCE, KeyParts, key_parts, parse_key
from playground.importer.merge import MergedView, Report, merge, missing_fields
from playground.importer.model import ParsedTransaction, ReviewKind, SourceKind
from playground.importer.serialise import transaction_from_dict
from playground.ledger.fifo import CostInput
from playground.storage.schema import (
    cost_basis_inputs,
    import_batches,
    imports,
    review_items,
    transaction_sources,
    transactions,
)

Rule = Literal["same_report", "same_key", "near_date"]

LEDGER_KINDS = frozenset({ReviewKind.OVERSELL, ReviewKind.SPLIT_UNCLEAR})
UNRECOGNISED_KINDS = frozenset({ReviewKind.UNKNOWN_LAYOUT, ReviewKind.AMBIGUOUS_LAYOUT, ReviewKind.UNKNOWN_CSV_HEADER})
_ORDER_STEP = 100_000


@dataclass(frozen=True)
class ImportInfo:
    """One row of `imports`: a file of a batch."""

    id: int
    batch_id: int
    file_name: str
    file_hash: str
    status: str
    parser_id: str | None
    doc_type: str
    parser_version: int | None = None


@dataclass
class Source:
    """One report of a transaction: the candidate as one file gave it."""

    import_id: int
    batch_id: int
    file_name: str
    kind: SourceKind
    report_key: str
    txn: ParsedTransaction
    id: int | None = None
    first_owner: int = 0
    stored: bool = False

    @property
    def precedence(self) -> int:
        return PRECEDENCE[self.kind]

    @property
    def order(self) -> int:
        """Later imports come later; inside one file, the line order."""
        return self.import_id * _ORDER_STEP + self.txn.evidence[0]

    def report(self) -> Report:
        return Report(
            kind=self.kind,
            precedence=self.precedence,
            order=self.order,
            txn=self.txn,
            file_name=self.file_name,
            report_key=self.report_key,
            batch_id=self.batch_id,
        )


@dataclass
class Txn:
    """A transaction with all its sources and the fields in use."""

    key: int
    batch_id: int
    sources: list[Source]
    view: MergedView
    parts: KeyParts
    occurrence: int
    id: int | None = None
    stored_state: str | None = None
    stored: dict[str, Any] | None = None
    cost_input: CostInput | None = None
    merged_into: int | None = None

    @property
    def fields(self) -> ParsedTransaction:
        return self.view.txn

    @property
    def semantic_key(self) -> str:
        return self.parts.key

    @property
    def kinds(self) -> frozenset[SourceKind]:
        return frozenset(source.kind for source in self.sources)

    @property
    def day(self) -> date:
        return self.parts.day

    @property
    def alive(self) -> bool:
        return self.merged_into is None

    @property
    def is_new(self) -> bool:
        return self.id is None

    @property
    def ref(self) -> str:
        """How dedupe keys name this transaction: its id, or a placeholder until it is stored."""
        return str(self.id) if self.id is not None else f"new{-self.key}"


@dataclass
class Item:
    """A review item (the `review_items` table)."""

    key: int
    kind: ReviewKind
    status: str
    dedupe_key: str
    message: str
    fields: dict[str, Any]
    txn_key: int | None = None
    import_id: int | None = None
    batch_id: int | None = None
    extracted_text: str | None = None
    resolution: dict[str, Any] | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    id: int | None = None
    evaluated: bool = True
    first_owner: int | None = None

    @property
    def is_new(self) -> bool:
        return self.id is None

    @property
    def origin(self) -> Literal["pipeline", "ledger", "parser"]:
        """Who raised it: the pipeline while matching, the ledger's lot book, or a parser."""
        if self.dedupe_key.split("|")[1:2] == ["txn"]:
            return "ledger" if self.kind in LEDGER_KINDS else "pipeline"
        return "parser"

    @property
    def may_hold(self) -> bool:
        """True when this item is of a kind that keeps its transaction out of lots, holdings and value.

        A pipeline `missing_field` or `possible_duplicate`, or a parser's item about the transaction
        it came with (amounts that do not add up, a line it could not read), may hold the
        transaction back while the item is open, and also once you dismiss it: dismissing means
        "leave it out as it is". Whether it holds it now is `Workspace.holds`. A resolution
        (`use-parsed`, `merge`, `keep-both`) or a later file releases it.
        """
        if self.txn_key is None or self.status not in ("open", "dismissed"):
            return False
        if self.origin == "pipeline":
            return self.kind in (ReviewKind.MISSING_FIELD, ReviewKind.POSSIBLE_DUPLICATE)
        return self.origin == "parser"


@dataclass(frozen=True)
class Match:
    """What rules a to d say about one candidate."""

    target: Txn | None
    rule: Rule | None
    near: tuple[Txn, ...] = ()

    @property
    def possible_duplicate(self) -> bool:
        return self.target is None and bool(self.near)


@dataclass
class Workspace:
    """Everything the registry holds for one portfolio, in memory."""

    portfolio_id: int
    batches: dict[int, str] = field(default_factory=dict)
    imports: dict[int, ImportInfo] = field(default_factory=dict)
    txns: dict[int, Txn] = field(default_factory=dict)
    items: dict[int, Item] = field(default_factory=dict)
    dedupe_keys: set[str] = field(default_factory=set)
    excluded_batch: int | None = None
    _next_key: int = -1
    _by_report: dict[str, int] = field(default_factory=dict)
    _by_key: dict[str, set[int]] = field(default_factory=dict)
    _by_near: dict[tuple[str, str], set[int]] = field(default_factory=dict)
    _kept: dict[str, set[int]] = field(default_factory=dict)

    # --- Loading ---

    @classmethod
    def load(
        cls, conn: Connection, portfolio_id: int, *, exclude_batch: int | None = None, staging: bool = False
    ) -> "Workspace":
        """Load the portfolio: every transaction and source of a batch that is not discarded.

        `exclude_batch` leaves one batch out, with its sources and the items it raised: a
        resolution while a batch is staged works on the accepted data only. It makes no merge,
        because the reports left out still count for the one-to-one rule (`review.py`).

        Two writes leave some rows as they are, and a row keeps its key and occurrence until it
        is written again, so no other row may take them (`next_occurrence`): the rows of
        `exclude_batch`, and, with `staging`, every stored row. A stage adds rows only: a stored
        transaction whose key moves in memory keeps its old key in the registry until accept.
        """
        ws = cls(portfolio_id=portfolio_id, excluded_batch=exclude_batch)
        for row in conn.execute(
            sa.select(import_batches.c.id, import_batches.c.status).where(import_batches.c.portfolio_id == portfolio_id)
        ):
            ws.batches[row.id] = row.status
        active = {batch for batch, status in ws.batches.items() if status != "discarded" and batch != exclude_batch}

        for row in conn.execute(sa.select(imports).where(imports.c.portfolio_id == portfolio_id)):
            ws.imports[row.id] = ImportInfo(
                id=row.id,
                batch_id=row.batch_id,
                file_name=row.file_name,
                file_hash=row.file_hash,
                status=row.status,
                parser_id=row.parser_id,
                doc_type=row.doc_type,
                parser_version=row.parser_version,
            )

        stored: dict[int, Any] = {
            row.id: row
            for row in conn.execute(
                sa.select(transactions).where(
                    transactions.c.portfolio_id == portfolio_id, transactions.c.batch_id.in_(active)
                )
            )
        }
        kept = list(stored.values()) if staging else []
        if exclude_batch is not None:
            kept += conn.execute(
                sa.select(transactions.c.semantic_key, transactions.c.occurrence).where(
                    transactions.c.portfolio_id == portfolio_id, transactions.c.batch_id == exclude_batch
                )
            ).all()
        for row in kept:
            ws._kept.setdefault(row.semantic_key, set()).add(row.occurrence)
        sources: dict[int, list[Source]] = {txn_id: [] for txn_id in stored}
        for row in conn.execute(
            sa.select(transaction_sources, imports.c.batch_id, imports.c.file_name)
            .join(imports, imports.c.id == transaction_sources.c.import_id)
            .where(imports.c.portfolio_id == portfolio_id, imports.c.batch_id.in_(active))
            .order_by(transaction_sources.c.id)
        ):
            if row.transaction_id not in sources:
                continue
            sources[row.transaction_id].append(
                Source(
                    import_id=row.import_id,
                    batch_id=row.batch_id,
                    file_name=row.file_name,
                    kind=SourceKind(row.source_kind),
                    report_key=row.report_key,
                    txn=transaction_from_dict(row.fields_json),
                    id=row.id,
                    first_owner=row.transaction_id,
                    stored=True,
                )
            )
        costs: dict[int, CostInput] = {}
        for row in conn.execute(
            sa.select(cost_basis_inputs)
            .where(cost_basis_inputs.c.transaction_id.in_(list(stored)))
            .order_by(cost_basis_inputs.c.entered_at, cost_basis_inputs.c.id)
        ):
            costs[row.transaction_id] = CostInput(acquired_on=row.acquired_on, cost_eur=row.cost_eur)

        for txn_id, row in sorted(stored.items()):
            own = sources[txn_id]
            if not own:
                raise RuntimeError(f"Transaction {txn_id} has no source. The registry is inconsistent.")
            txn = Txn(
                key=txn_id,
                id=txn_id,
                batch_id=row.batch_id,
                sources=own,
                view=merge([source.report() for source in own]),
                parts=parse_key(row.semantic_key),
                occurrence=row.occurrence,
                stored_state=row.state,
                stored=dict(row._mapping),
                cost_input=costs.get(txn_id),
            )
            ws.txns[txn_id] = txn
            ws._index(txn)
        # A transaction always follows its merged fields (plan 5.3.4): sources of the batch being
        # accepted may have moved its booking date, and with it its key.
        for txn_id in sorted(stored):
            ws.refresh(ws.txns[txn_id])

        for row in conn.execute(
            sa.select(review_items).where(review_items.c.portfolio_id == portfolio_id).order_by(review_items.c.id)
        ):
            ws.dedupe_keys.add(row.dedupe_key)
            txn_key = row.transaction_id
            evaluated = (
                (exclude_batch is None or row.batch_id != exclude_batch)
                and (txn_key is None or txn_key in ws.txns)
                and (row.batch_id is None or ws.batches.get(row.batch_id) != "discarded")
            )
            ws.items[row.id] = Item(
                key=row.id,
                id=row.id,
                kind=ReviewKind(row.kind),
                status=row.status,
                dedupe_key=row.dedupe_key,
                message=row.message,
                fields=dict(row.fields_json or {}),
                txn_key=txn_key if txn_key in ws.txns else None,
                import_id=row.import_id,
                batch_id=row.batch_id,
                extracted_text=row.extracted_text,
                resolution=row.resolution_json,
                created_at=row.created_at,
                resolved_at=row.resolved_at,
                evaluated=evaluated,
                first_owner=txn_key if txn_key in ws.txns else None,
            )
        return ws

    # --- Reading ---

    def alive(self) -> Iterator[Txn]:
        """Transactions that were not merged away, stored ones first by id, then new ones in order."""
        for txn in sorted(self.txns.values(), key=_txn_order):
            if txn.alive:
                yield txn

    def final(self, key: int) -> Txn:
        """The transaction `key` ended in, following merges."""
        txn = self.txns[key]
        while txn.merged_into is not None:
            txn = self.txns[txn.merged_into]
        return txn

    def storage_owner(self, key: int) -> Txn:
        """The transaction a new source or item first attached to `key` is stored under.

        A stored transaction still exists in the registry until accept applies a merge, so it
        keeps what was attached to it; a new one that was merged away hands it on.
        """
        txn = self.txns[key]
        while txn.id is None and txn.merged_into is not None:
            txn = self.txns[txn.merged_into]
        return txn

    def items_of(self, txn: Txn) -> list[Item]:
        return [item for item in self.items.values() if item.txn_key == txn.key]

    def holds(self, item: Item) -> bool:
        """True when `item` holds its transaction back now (see `Item.may_hold`).

        It holds while its problem remains. For two kinds a later file can remove the problem,
        and then the item holds no more, even once you dismissed it: a pipeline `missing_field`
        holds only while the transaction still lacks the field (plan 5.3.4), and a parser's
        `amounts_do_not_add_up` only while the fields in use still come from that document, not
        from a manual CSV row (plan 5.3.5).
        """
        if not item.may_hold or item.txn_key is None or item.txn_key not in self.txns:
            return False
        txn = self.final(item.txn_key)
        if item.origin == "pipeline" and item.kind is ReviewKind.MISSING_FIELD:
            return bool(missing_fields(txn.fields))
        if item.origin == "parser" and item.kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP:
            return txn.view.top.kind is not SourceKind.MANUAL_CSV
        return True

    def active_import_for(self, file_hash: str) -> ImportInfo | None:
        """The import that holds this file's data: in a batch that is not discarded, not a duplicate."""
        found = [
            info
            for info in self.imports.values()
            if info.file_hash == file_hash
            and self.batches.get(info.batch_id) not in (None, "discarded")
            and info.batch_id != self.excluded_batch
            and info.status in ("parsed", "partial", "needs_review", "failed")
        ]
        return max(found, key=lambda info: info.id) if found else None

    def near(self, parts: KeyParts, *, exclude: int | None = None) -> list[Txn]:
        """Transactions whose key differs from `parts` only in the date, by 1 to 3 days (rule c)."""
        found = []
        for key in self._by_near.get(parts.without_day, set()):
            other = self.txns[key]
            if other.key != exclude and other.alive and 0 < abs((other.day - parts.day).days) <= NEAR_DATE_DAYS:
                found.append(other)
        return sorted(found, key=_occurrence_order)

    def with_key(self, key: str) -> list[Txn]:
        return sorted((self.txns[k] for k in self._by_key.get(key, set()) if self.txns[k].alive), key=_occurrence_order)

    def owner_of_report(self, report_key: str) -> Txn | None:
        key = self._by_report.get(report_key)
        return self.txns[key] if key is not None else None

    # --- Matching (plan 5.3.4) ---

    def match(self, candidate: ParsedTransaction, report_key: str) -> Match:
        """Rules a to c for one candidate; no target and no near-date match means rule d (new)."""
        kind = candidate.source_kind
        same_report = self.owner_of_report(report_key)
        if same_report is not None:
            return Match(target=same_report, rule="same_report")

        parts = key_parts(candidate)
        qualifying = [txn for txn in self.with_key(parts.key) if kind not in txn.kinds]
        if qualifying:
            agreeing = [txn for txn in qualifying if agrees(candidate, txn.fields)]
            return Match(target=(agreeing or qualifying)[0], rule="same_key")

        near = [txn for txn in self.near(parts) if kind not in txn.kinds]
        if near:
            statement_case = kind is SourceKind.PDF_STATEMENT or all(
                txn.kinds == {SourceKind.PDF_STATEMENT} for txn in near
            )
            if len({txn.day for txn in near}) == 1 and statement_case:
                return Match(target=near[0], rule="near_date")
            return Match(target=None, rule=None, near=tuple(near))
        return Match(target=None, rule=None)

    def rematch(self, txn: Txn) -> Match:
        """What matching `txn`'s reports would find now, among the other transactions.

        Used to check a held `possible_duplicate` again: an exact match (same key, or one of its
        reports, with no source of a kind it has) means it is that transaction; a near-date match
        that rule c merges on its own means the same; any other near-date match keeps the question
        open; none means it is a transaction of its own.
        """
        kinds = txn.kinds
        for source in txn.sources:
            owner = self.owner_of_report(source.report_key)
            if owner is not None and owner.key != txn.key:
                return Match(target=owner, rule="same_report")
        exact = [
            other for other in self.with_key(txn.semantic_key) if other.key != txn.key and not (other.kinds & kinds)
        ]
        if exact:
            return Match(target=exact[0], rule="same_key")
        near = [other for other in self.near(txn.parts, exclude=txn.key) if not (other.kinds & kinds)]
        if not near:
            return Match(target=None, rule=None)
        statement_case = kinds == {SourceKind.PDF_STATEMENT} or all(
            other.kinds == {SourceKind.PDF_STATEMENT} for other in near
        )
        if len({other.day for other in near}) == 1 and statement_case:
            return Match(target=near[0], rule="near_date")
        return Match(target=None, rule=None, near=tuple(near))

    # --- Changing ---

    def create(self, source: Source, *, batch_id: int) -> Txn:
        """A new transaction from one candidate (rule d), with the next occurrence of its key."""
        key = self._next_key
        self._next_key -= 1
        view = merge([source.report()])
        parts = key_parts(view.txn)
        txn = Txn(
            key=key,
            batch_id=batch_id,
            sources=[source],
            view=view,
            parts=parts,
            occurrence=self.next_occurrence(parts.key),
        )
        source.first_owner = key
        self.txns[key] = txn
        self._index(txn)
        return txn

    def attach(self, txn: Txn, source: Source) -> None:
        """Add a report to `txn` and follow its merged fields."""
        source.first_owner = txn.key
        txn.sources.append(source)
        self._by_report[source.report_key] = txn.key
        self.refresh(txn)

    def merge_into(self, merged: Txn, target: Txn) -> None:
        """Merge `merged` into `target`: every source, item and cost input moves to `target`."""
        self._unindex(merged)
        for source in merged.sources:
            target.sources.append(source)
            self._by_report[source.report_key] = target.key
        merged.sources = []
        merged.merged_into = target.key
        for item in self.items.values():
            if item.txn_key == merged.key:
                item.txn_key = target.key
        if target.cost_input is None and merged.cost_input is not None:
            target.cost_input = merged.cost_input
        self.refresh(target)

    def refresh(self, txn: Txn) -> None:
        """Merge the sources again; a changed key takes the next occurrence of the new key."""
        txn.view = merge([source.report() for source in txn.sources])
        parts = key_parts(txn.view.txn)
        if parts.key == txn.semantic_key:
            return
        self._unindex(txn)
        txn.parts = parts
        txn.occurrence = self.next_occurrence(parts.key)
        self._index(txn)

    def next_occurrence(self, key: str) -> int:
        """The next number after the highest occurrence of `key` in use (1 for the first).

        In use are the occurrences of the transactions here, and those of the rows the next
        write leaves as they are (see `load`).
        """
        used = [self.txns[k].occurrence for k in self._by_key.get(key, set()) if self.txns[k].alive]
        used += self._kept.get(key, set())
        return max(used, default=0) + 1

    def add_item(self, item: Item) -> Item:
        if item.first_owner is None:
            item.first_owner = item.txn_key
        self.items[item.key] = item
        self.dedupe_keys.add(item.dedupe_key)
        return item

    def new_item_key(self) -> int:
        key = self._next_key
        self._next_key -= 1
        return key

    # --- Indexes ---

    def _index(self, txn: Txn) -> None:
        self._by_key.setdefault(txn.semantic_key, set()).add(txn.key)
        self._by_near.setdefault(txn.parts.without_day, set()).add(txn.key)
        for source in txn.sources:
            self._by_report[source.report_key] = txn.key

    def _unindex(self, txn: Txn) -> None:
        self._by_key.get(txn.semantic_key, set()).discard(txn.key)
        self._by_near.get(txn.parts.without_day, set()).discard(txn.key)


def agrees(candidate: ParsedTransaction, fields: ParsedTransaction) -> bool:
    """True when the two agree on every detail both carry: time of day, quantity and fees (rule b)."""
    both_timed = candidate.time.precision == "minute" and fields.time.precision == "minute"
    if both_timed and candidate.time.ts_local != fields.time.ts_local:
        return False
    if candidate.quantity is not None and fields.quantity is not None and candidate.quantity != fields.quantity:
        return False
    return not (
        candidate.fees_eur is not None and fields.fees_eur is not None and candidate.fees_eur != fields.fees_eur
    )


def _txn_order(txn: Txn) -> tuple[int, int]:
    return (0, txn.key) if txn.key > 0 else (1, -txn.key)


def _occurrence_order(txn: Txn) -> tuple[int, int, int]:
    first, second = _txn_order(txn)
    return (txn.occurrence, first, second)


def touching(txns: Iterable[Txn], batch_id: int) -> list[str]:
    """The names of the files of `batch_id` that report any of `txns`, sorted."""
    return sorted({source.file_name for txn in txns for source in txn.sources if source.batch_id == batch_id})


# --- What a check of the review items decided -------------------------------------------------


@dataclass(frozen=True)
class Cause:
    """What prompted a check of the review items (plan 5.3.5).

    `batch_id` is the batch being staged or accepted: the files of that batch are named as the
    ones that superseded an item. `text` names the cause when no file does, for example
    "review item 7" after a resolution.
    """

    batch_id: int | None
    text: str


@dataclass(frozen=True)
class Closure:
    """An open item whose problem no longer holds, and how it was settled."""

    item: Item
    resolution: dict[str, Any]


@dataclass
class Plan:
    """Everything one check decided, applied to the workspace in memory.

    Accept and a resolution write all of it; a stage writes only the new transactions, sources
    and items, and lists the closures in the diff.
    """

    closures: list[Closure] = field(default_factory=list)
    reopened: list[Item] = field(default_factory=list)
    new_items: list[Item] = field(default_factory=list)
    merges: list[tuple[int, int]] = field(default_factory=list)
    held: set[int] = field(default_factory=set)
    book: Any = None
    changed_items: list[Item] = field(default_factory=list)
    updated_items: list[Item] = field(default_factory=list)
