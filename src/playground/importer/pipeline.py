"""The import pipeline (plan 5.3.4, 5.3.6): stage one batch of files, then accept or discard it.

`stage_files` (and `stage_inputs`, for files already in memory) takes one batch:

1. Each file's kind is found: a PDF by its magic bytes, a CSV by its extension. Anything else,
   and a CSV file that is not text in UTF-8 or Windows-1252, is refused before anything is
   stored. Once the stage is written, each file is copied to `uploads/<sha256>.<ext>`
   (`_store_uploads`).
2. **File level.** A file already imported in a batch that was not discarded is recorded as
   `duplicate_file` and not parsed again. The exception is a file whose earlier import failed or
   found no parser: it is classified again, so a parser added since then takes effect. If a
   parser now reads it, it is parsed in this batch, and accepting the batch marks the earlier
   import `reparsed` and closes its review item as superseded.
3. The files are read (`pdf_text.extract_text`, `classify`, the layout parsers; the CSV
   profiles, the manual format) and every transaction read is a **candidate**.
4. **Transaction level.** The files are taken in a fixed order (precedence from high to low,
   then file name, then file hash) and their candidates in line order, so one batch gives the
   same result whatever order you name the files in. Each candidate is matched (rules a to d,
   `workspace.Workspace.match`) and becomes a new transaction or a new source of a known one.
5. The review items are checked again (`review.evaluate`): the pipeline raises what the data
   calls for (`missing_field`, `field_conflict`, `possible_duplicate`, `missing_cost_basis`),
   the lot book raises `oversell` and `split_unclear`, and the diff lists what accept will
   close. New transactions are stored as `staged`, or as `held` when an item holds them back.

Only one batch can be staged at a time, and nothing touches accepted data until `accept_batch`,
apart from the instruments: a stage stores the ones its files name, and names one known so far
only by its ISIN (`persist.store_stage`). Accept marks the batch accepted, applies the closures
and merges, releases what is no longer held, rebuilds the lot book from every accepted
transaction, then the value series and its holdings snapshots (plan 5.7, when it is given the
market data), and sets the time of the last import.
`discard_batch` removes the batch's transactions and sources and closes the items it raised as
"batch discarded". `pg status` shows a reminder when the last import is more than 30 days old,
and the latest value.
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import Connection

from playground.core.clock import Clock
from playground.core.dates import BERLIN, format_ts_utc
from playground.core.errors import PlaygroundError
from playground.importer import persist
from playground.importer.csv_common import decode_csv_bytes
from playground.importer.keys import PRECEDENCE, booking_day, report_keys, sha256_text
from playground.importer.manual_csv import HEADER as MANUAL_HEADER
from playground.importer.manual_csv import parse_manual_csv
from playground.importer.model import ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.review import brief, evaluate, item_record, possible_duplicate_item
from playground.importer.tr.classify import classify
from playground.importer.tr.csv_parser import parse_csv
from playground.importer.tr.csv_profiles import CsvProfile
from playground.importer.tr.layouts.common import DocumentParser
from playground.importer.tr.pdf_text import PdfTextError, extract_text
from playground.importer.workspace import Cause, ImportInfo, Item, Plan, Source, Txn, Workspace
from playground.storage import repos
from playground.storage.schema import (
    cost_basis_inputs,
    imports,
    review_items,
    transaction_sources,
    transactions,
)
from playground.valuation.portfolio import MarketData, day_record, latest_value, rebuild_values

REMINDER_AFTER_DAYS = 30
"""Spec 3.10: the app reminds you when the last import is more than this many days old."""

_PDF_MAGIC = b"%PDF-"
_MAGIC_WINDOW = 1024


# --- Errors -----------------------------------------------------------------------------------


class ImportRefusedError(PlaygroundError):
    """An import, accept or discard that cannot go ahead. The message says why and what to do."""


class NoFilesError(ImportRefusedError):
    """An import was started without any file."""


class UnsupportedFileError(ImportRefusedError):
    """A file is neither a PDF nor a CSV file, or cannot be read."""


class StagedBatchExistsError(ImportRefusedError):
    """A batch is already staged; it must be accepted or discarded first."""

    def __init__(self, batch_id: int) -> None:
        super().__init__(
            f"Import batch {batch_id} is staged and not accepted yet. Only one import can wait at a time. "
            f'Run "pg accept {batch_id}" or "pg discard {batch_id}" first.'
        )
        self.batch_id = batch_id


class BatchNotFoundError(ImportRefusedError):
    """No import batch has that number."""


class BatchNotStagedError(ImportRefusedError):
    """The batch was already accepted or discarded."""


# --- Inputs and results -----------------------------------------------------------------------


@dataclass(frozen=True)
class InputFile:
    """One file to import: its name (without the folder) and its bytes."""

    name: str
    data: bytes


@dataclass(frozen=True)
class BatchSummary:
    """What a stage found (the diff without the holdings), as stored with the batch."""

    batch_id: int
    data: dict[str, Any]

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.data["counts"])


@dataclass(frozen=True)
class AcceptResult:
    """What accepting a batch did."""

    batch_id: int
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class DiscardResult:
    """What discarding a batch did."""

    batch_id: int
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass(frozen=True)
class Reminder:
    """The reminder to import again (spec 3.10)."""

    due: bool
    days_since_last_import: int | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"due": self.due, "days_since_last_import": self.days_since_last_import, "message": self.message}


@dataclass(frozen=True)
class _File:
    name: str
    data: bytes
    sha256: str
    kind: Literal["pdf", "csv"]


@dataclass
class _Reading:
    """What reading one file gave: a parse result, or the reason no parser read it."""

    recognised: bool
    failed: bool
    text: str | None
    result: ParseResult | None
    review: list[ReviewNeeded]
    doc_type: str
    parser_id: str | None
    parser_version: int | None

    @property
    def status(self) -> str:
        if self.failed:
            return "failed"
        if not self.recognised or self.result is None:
            return "needs_review"
        if not self.result.review:
            return "parsed"
        return "partial" if self.result.transactions else "needs_review"


@dataclass
class _FileResult:
    file: _File
    import_id: int
    status: str
    stored_path: str
    reading: _Reading | None
    duplicate_of: str | None
    doc_type: str
    parser_id: str | None
    parser_version: int | None
    candidates: int = 0

    @property
    def precedence(self) -> int:
        if self.reading is None or self.reading.result is None or not self.reading.result.transactions:
            return 0
        return PRECEDENCE[self.reading.result.transactions[0].source_kind]


@dataclass
class _Outcome:
    """What happened to one candidate."""

    result: _FileResult
    source: Source
    txn_key: int
    rule: str | None
    matched_date: date | None
    items: list[Item] = field(default_factory=list)


# --- Staging ----------------------------------------------------------------------------------


def stage_files(
    conn: Connection,
    portfolio_id: int,
    paths: Sequence[Path],
    *,
    clock: Clock,
    uploads_dir: Path,
    extract: Callable[[bytes], str] = extract_text,
    parsers: Sequence[DocumentParser] | None = None,
    csv_profiles: Sequence[CsvProfile] | None = None,
) -> BatchSummary:
    """Stage the files at `paths` as one batch (see the module docstring)."""
    inputs = []
    for path in paths:
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError as exc:
            raise UnsupportedFileError(f"There is no file {path}.") from exc
        except OSError as exc:
            raise UnsupportedFileError(f"The file {path} could not be read: {exc.strerror or exc}.") from exc
        inputs.append(InputFile(name=Path(path).name, data=data))
    return stage_inputs(
        conn,
        portfolio_id,
        inputs,
        clock=clock,
        uploads_dir=uploads_dir,
        extract=extract,
        parsers=parsers,
        csv_profiles=csv_profiles,
    )


def stage_inputs(
    conn: Connection,
    portfolio_id: int,
    inputs: Sequence[InputFile],
    *,
    clock: Clock,
    uploads_dir: Path,
    extract: Callable[[bytes], str] = extract_text,
    parsers: Sequence[DocumentParser] | None = None,
    csv_profiles: Sequence[CsvProfile] | None = None,
) -> BatchSummary:
    """Stage `inputs` as one batch. `extract` turns PDF bytes into text (the tests cache it)."""
    if not inputs:
        raise NoFilesError("Name at least one Trade Republic PDF document or CSV file to import.")
    staged = repos.staged_batch_id(conn, portfolio_id)
    if staged is not None:
        raise StagedBatchExistsError(staged)
    files = sorted((_prepare(item) for item in inputs), key=lambda file: (file.name, file.sha256))

    now = format_ts_utc(clock.now_utc())
    ws = Workspace.load(conn, portfolio_id, staging=True)
    batch_id = repos.insert_batch(conn, portfolio_id=portfolio_id, created_at=now)
    ws.batches[batch_id] = "staged"
    results, reparsed = _read_files(conn, ws, files, batch_id, now, uploads_dir, extract, parsers, csv_profiles)
    cause = Cause(
        batch_id=batch_id, text=_batch_text([r.file.name for r in results if r.reading is not None], batch_id)
    )

    outcomes = _match(ws, results, batch_id, cause, now)
    plan = evaluate(ws, cause, now=now, reparsed=reparsed)
    created = [item for item in ws.items.values() if item.is_new and item.status == "open"]
    persist.store_stage(conn, ws, plan)
    summary = _summary(conn, ws, plan, results, outcomes, created, batch_id)
    repos.set_batch_summary(conn, batch_id, summary)
    _store_uploads(uploads_dir, files)
    return BatchSummary(batch_id=batch_id, data=summary)


def _prepare(item: InputFile) -> _File:
    head = item.data[:_MAGIC_WINDOW]
    if _PDF_MAGIC in head:
        kind: Literal["pdf", "csv"] = "pdf"
    elif item.name.lower().endswith(".csv"):
        kind = "csv"
        try:
            decode_csv_bytes(item.data)
        except UnicodeDecodeError as exc:
            raise UnsupportedFileError(
                f"{item.name} is not a readable CSV file. It is not text in UTF-8 or Windows-1252, the two "
                "character encodings the importer reads. Nothing was imported."
            ) from exc
    else:
        raise UnsupportedFileError(
            f"{item.name} is neither a PDF nor a CSV file. Only Trade Republic PDF documents and CSV files, "
            "and the manual CSV format, can be imported. Nothing was imported."
        )
    return _File(name=item.name, data=item.data, sha256=hashlib.sha256(item.data).hexdigest(), kind=kind)


def _upload_path(uploads_dir: Path, file: _File) -> str:
    """Where the copy of `file` is kept, relative to the data directory (`uploads/<sha256>.<ext>`),
    so the data directory can move."""
    return f"{uploads_dir.name}/{file.sha256}.{file.kind}"


def _store_uploads(uploads_dir: Path, files: Sequence[_File]) -> None:
    """Keep a copy of every file in `uploads/`, so review items can be reopened (plan 4.3).

    Called last, once the stage is written, so a refused import or a file that cannot be read
    leaves no copy behind. The caller commits after this and can still fail first (the CLI builds
    the diff before it commits). The copies then stay, which does no harm: a copy is named by its
    content, so the next import of the same file finds it and keeps it.
    """
    uploads_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        target = uploads_dir / f"{file.sha256}.{file.kind}"
        if not target.exists():
            target.write_bytes(file.data)


def _read(
    file: _File,
    extract: Callable[[bytes], str],
    parsers: Sequence[DocumentParser] | None,
    csv_profiles: Sequence[CsvProfile] | None,
) -> _Reading:
    """Read one file: which parser, if any, and what it read."""
    if file.kind == "pdf":
        try:
            text = extract(file.data)
        except PdfTextError as exc:
            review = ReviewNeeded(
                kind=ReviewKind.UNKNOWN_LAYOUT,
                message=f"{exc} Nothing was imported from it.",
                extracted_text="",
                fields={},
            )
            return _Reading(False, True, None, None, [review], "unreadable", None, None)
        found = classify(text, parsers)
        if isinstance(found, ReviewNeeded):
            return _Reading(False, False, text, None, [found], "unknown", None, None)
        result = found.parse(text)
        return _Reading(
            True, False, text, result, list(result.review), result.doc_type, result.parser_id, result.parser_version
        )

    text = decode_csv_bytes(file.data)
    first = next((line for line in text.splitlines() if line.strip()), "")
    if tuple(first.split(";")) == MANUAL_HEADER:
        result = parse_manual_csv(file.data)
    else:
        result = parse_csv(file.data) if csv_profiles is None else parse_csv(file.data, profiles=csv_profiles)
        unknown = [item for item in result.review if item.kind is ReviewKind.UNKNOWN_CSV_HEADER]
        if unknown and not result.transactions:
            return _Reading(False, False, text, None, unknown, "unknown", None, None)
    return _Reading(
        True, False, text, result, list(result.review), result.doc_type, result.parser_id, result.parser_version
    )


def _read_files(
    conn: Connection,
    ws: Workspace,
    files: Sequence[_File],
    batch_id: int,
    now: str,
    uploads_dir: Path,
    extract: Callable[[bytes], str],
    parsers: Sequence[DocumentParser] | None,
    csv_profiles: Sequence[CsvProfile] | None,
) -> tuple[list[_FileResult], dict[int, str]]:
    """Record every file of the batch, and read the ones not imported before (plan 5.3.4, file level)."""
    results: list[_FileResult] = []
    in_batch: dict[str, _FileResult] = {}
    reparsed: dict[int, str] = {}
    for file in files:
        stored_path = _upload_path(uploads_dir, file)
        earlier = ws.active_import_for(file.sha256)
        reading: _Reading | None = None
        duplicate: _FileResult | ImportInfo | None = in_batch.get(file.sha256)
        if duplicate is None and earlier is not None and earlier.parser_id is not None:
            duplicate = earlier
        if duplicate is None:
            reading = _read(file, extract, parsers, csv_profiles)
            if earlier is not None:
                # An earlier import found no parser or failed: classified again, it is a duplicate
                # like any other known file unless a parser reads it now.
                if reading.recognised:
                    reparsed[earlier.id] = file.name
                else:
                    duplicate, reading = earlier, None

        if duplicate is not None:
            doc_type = duplicate.doc_type
            parser_id = duplicate.parser_id
            parser_version = duplicate.parser_version
            duplicate_of = duplicate.file.name if isinstance(duplicate, _FileResult) else duplicate.file_name
            status = "duplicate_file"
        elif reading is not None:
            doc_type, parser_id, parser_version = reading.doc_type, reading.parser_id, reading.parser_version
            duplicate_of = None
            status = reading.status
        else:
            raise RuntimeError(f"{file.name} was neither read nor found as a duplicate.")
        import_id = repos.insert_import(
            conn,
            batch_id=batch_id,
            portfolio_id=ws.portfolio_id,
            file_hash=file.sha256,
            file_name=file.name,
            stored_path=stored_path,
            doc_type=doc_type,
            parser_id=parser_id,
            parser_version=parser_version,
            parsed_at=now,
            status=status,
            review_notes=None,
        )
        ws.imports[import_id] = ImportInfo(
            id=import_id,
            batch_id=batch_id,
            file_name=file.name,
            file_hash=file.sha256,
            status=status,
            parser_id=parser_id,
            doc_type=doc_type,
            parser_version=parser_version,
        )
        result = _FileResult(
            file=file,
            import_id=import_id,
            status=status,
            stored_path=stored_path,
            reading=reading,
            duplicate_of=duplicate_of,
            doc_type=doc_type,
            parser_id=parser_id,
            parser_version=parser_version,
        )
        results.append(result)
        if duplicate is None:
            in_batch[file.sha256] = result
    return results, reparsed


def _batch_text(names: Sequence[str], batch_id: int) -> str:
    return ", ".join(sorted(set(names))) if names else f"import batch {batch_id}"


def _match(ws: Workspace, results: Sequence[_FileResult], batch_id: int, cause: Cause, now: str) -> list[_Outcome]:
    """Match every candidate of the batch, in the fixed order of plan 5.3.4, and add the parsers' items."""
    outcomes: list[_Outcome] = []
    readable = [(result, result.reading) for result in results if result.reading is not None]
    for result, reading in sorted(
        readable, key=lambda pair: (-pair[0].precedence, pair[0].file.name, pair[0].file.sha256)
    ):
        parsed = reading.result
        attach_to: Txn | None = None
        keys: list[str] = []
        if parsed is not None:
            keys = report_keys(parsed.transactions, text=reading.text)
            result.candidates = len(parsed.transactions)
            for candidate, report_key in zip(parsed.transactions, keys, strict=True):
                source = Source(
                    import_id=result.import_id,
                    batch_id=batch_id,
                    file_name=result.file.name,
                    kind=candidate.source_kind,
                    report_key=report_key,
                    txn=candidate,
                )
                match = ws.match(candidate, report_key)
                if match.target is not None:
                    matched_date = match.target.day
                    ws.attach(match.target, source)
                    txn = match.target
                else:
                    matched_date = None
                    txn = ws.create(source, batch_id=batch_id)
                    if match.possible_duplicate:
                        ws.add_item(possible_duplicate_item(ws, txn, match.near, cause=cause, now=now))
                outcomes.append(_Outcome(result, source, txn.key, match.rule, matched_date))
                attach_to = txn
            one_document = (
                len(parsed.transactions) == 1 and parsed.transactions[0].source_kind is SourceKind.PDF_DOCUMENT
            )
            if not one_document:
                attach_to = None
        _add_parser_items(ws, result, reading, attach_to, keys, cause, now)
    return outcomes


def _add_parser_items(
    ws: Workspace,
    result: _FileResult,
    reading: _Reading,
    attach_to: Txn | None,
    keys: Sequence[str],
    cause: Cause,
    now: str,
) -> None:
    """Turn what a parser could not read into review items, each at most once (its dedupe key)."""
    rows: dict[tuple[str, str], int] = {}
    for index, needed in enumerate(reading.review):
        kind = needed.kind.value
        if reading.failed:
            dedupe_key = f"{kind}|file|{result.file.sha256}"
        elif not reading.recognised:
            dedupe_key = f"{kind}|content|{sha256_text(reading.text or '')}"
        elif attach_to is not None:
            dedupe_key = f"{kind}|report|{keys[0]}|{index}"
        elif "line_text" in needed.fields:
            row = sha256_text(needed.fields["line_text"])
            rows[(kind, row)] = rows.get((kind, row), 0) + 1
            dedupe_key = f"{kind}|row|{row}|{rows[(kind, row)]}"
        else:
            dedupe_key = f"{kind}|content|{sha256_text(reading.text or '')}|{index}"
        if dedupe_key in ws.dedupe_keys:
            continue
        ws.add_item(
            Item(
                key=ws.new_item_key(),
                kind=needed.kind,
                status="open",
                dedupe_key=dedupe_key,
                message=needed.message,
                fields=dict(needed.fields),
                txn_key=attach_to.key if attach_to is not None else None,
                import_id=result.import_id,
                batch_id=cause.batch_id,
                extracted_text=needed.extracted_text,
                created_at=now,
            )
        )


# --- The stored summary of a stage ------------------------------------------------------------


def _summary(
    conn: Connection,
    ws: Workspace,
    plan: Plan,
    results: Sequence[_FileResult],
    outcomes: Sequence[_Outcome],
    created: Sequence[Item],
    batch_id: int,
) -> dict[str, Any]:
    names = repos.instrument_names(conn)
    seen: set[int] = set()
    merged: list[dict[str, Any]] = []
    known: list[dict[str, Any]] = []
    new_count = 0
    for outcome in outcomes:
        final = ws.final(outcome.txn_key)
        if final.key < 0:
            if final.key not in seen:
                seen.add(final.key)
                new_count += 1
                continue
            merged.append(_match_record(outcome, final, names))
        else:
            known.append(_match_record(outcome, final, names))

    alive = sorted(ws.alive(), key=_display_order)
    new_txns = [txn for txn in alive if txn.key < 0]
    held_back = [
        txn
        for txn in alive
        if txn.key in plan.held and (txn.key < 0 or any(source.batch_id == batch_id for source in txn.sources))
    ]
    completed = [txn for txn in alive if txn.key > 0 and txn.stored_state == "held" and txn.key not in plan.held]
    review_new = sorted(created, key=lambda item: item.id or 0) + sorted(plan.reopened, key=lambda item: item.id or 0)
    review_closed = sorted((closure.item for closure in plan.closures), key=lambda item: item.id or 0)

    files = []
    for result in results:
        files.append(
            {
                "file_name": result.file.name,
                "file_hash": result.file.sha256,
                "status": result.status,
                "doc_type": result.doc_type,
                "parser_id": result.parser_id,
                "parser_version": result.parser_version,
                "source_kind": _file_kind(result),
                "candidates": result.candidates,
                "review_new": sum(1 for item in created if item.import_id == result.import_id),
                "duplicate_of": result.duplicate_of,
                "import_id": result.import_id,
                "stored_path": result.stored_path,
            }
        )

    return {
        "counts": {
            "files": len(results),
            "duplicate_files": sum(1 for result in results if result.status == "duplicate_file"),
            "candidates": len(outcomes),
            "new": new_count,
            "merged": len(merged),
            "already_known": len(known),
            "held_back": len(held_back),
            "completed": len(completed),
            "review_new": len(review_new),
            "review_closed": len(review_closed),
        },
        "files": files,
        "new": [transaction_record(ws, txn, names, state=_after_state(txn, plan)) for txn in new_txns],
        "merged": merged,
        "already_known": known,
        "held_back": [
            {**transaction_record(ws, txn, names, state="held"), "reasons": _hold_reasons(ws, txn)} for txn in held_back
        ],
        "completed": [transaction_record(ws, txn, names, state="accepted") for txn in completed],
        "review_new": [item_record(ws, item, names) for item in review_new],
        "review_closed": [item_record(ws, item, names) for item in review_closed],
    }


def _file_kind(result: _FileResult) -> str | None:
    if result.reading is None or result.reading.result is None or not result.reading.result.transactions:
        return None
    return result.reading.result.transactions[0].source_kind.value


def _after_state(txn: Txn, plan: Plan) -> str:
    return "held" if txn.key in plan.held else "staged"


def _hold_reasons(ws: Workspace, txn: Txn) -> list[str]:
    return sorted({item.kind.value for item in ws.items_of(txn) if ws.holds(item)})


def _display_order(txn: Txn) -> tuple[str, str, int]:
    return (format_ts_utc(txn.fields.time.ts_utc), txn.semantic_key, txn.occurrence)


def _match_record(outcome: _Outcome, final: Txn, names: Mapping[str, str]) -> dict[str, Any]:
    evidence = outcome.source.txn.evidence
    matched = outcome.matched_date if outcome.matched_date is not None else final.day
    return {
        "file_name": outcome.result.file.name,
        "source_kind": outcome.source.kind.value,
        "line_from": evidence[0],
        "line_to": evidence[1],
        "rule": outcome.rule or "near_date",
        "candidate_date": booking_day(outcome.source.txn).isoformat(),
        "matched_date": matched.isoformat(),
        "transaction": brief(final, names),
    }


def transaction_record(
    ws: Workspace, txn: Txn, names: Mapping[str, str], *, state: str | None = None, stored: bool = False
) -> dict[str, Any]:
    """One transaction as JSON-ready data: the fields in use (or, with `stored`, as stored) and its sources."""
    fields = txn.fields
    values = txn.stored if stored and txn.stored is not None else None

    def dec(value: Any) -> str | None:
        return format(value, "f") if value is not None else None

    sources = sorted(txn.sources, key=lambda source: (-source.precedence, source.order))
    record = {
        "id": txn.id,
        "state": state or txn.stored_state,
        "type": fields.type.value,
        "isin": fields.isin,
        "name": names.get(fields.isin, txn.view.name) if fields.isin else None,
        "date": txn.day.isoformat(),
        "ts_utc": format_ts_utc(fields.time.ts_utc),
        "ts_local": fields.time.ts_local.isoformat(),
        "ts_precision": fields.time.precision,
        "value_date": fields.value_date.isoformat() if fields.value_date is not None else None,
        "quantity": dec(fields.quantity),
        "price": dec(fields.price),
        "amount_eur": dec(fields.amount_eur),
        "fees_eur": dec(fields.fees_eur),
        "tax_eur": dec(fields.tax_eur),
        "tax_detail": {key: format(value, "f") for key, value in sorted(fields.tax_detail.items())},
        "fx_rate": dec(fields.fx_rate),
        "fx_source": fields.fx_source,
        "split_new_quantity": dec(fields.split_new_quantity),
        "origin": fields.origin,
        "source_ref": fields.source_ref,
        "semantic_key": txn.semantic_key,
        "sources": [
            {
                "kind": source.kind.value,
                "file_name": source.file_name,
                "report_key": source.report_key,
                "line_from": source.txn.evidence[0],
                "line_to": source.txn.evidence[1],
            }
            for source in sources
        ],
    }
    if values is not None:
        record.update(_stored_fields(values))
    return record


def _stored_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a stored row, for a transaction whose sources include a batch not accepted yet."""

    def dec(value: Any) -> str | None:
        return format(value, "f") if value is not None else None

    return {
        "state": values["state"],
        "date": str(values["ts_local"])[:10],
        "ts_utc": values["ts_utc"],
        "ts_local": values["ts_local"],
        "ts_precision": values["ts_precision"],
        "value_date": values["value_date"].isoformat() if values["value_date"] is not None else None,
        "quantity": dec(values["quantity"]),
        "price": dec(values["price"]),
        "amount_eur": dec(values["amount_eur"]),
        "fees_eur": dec(values["fees_eur"]),
        "tax_eur": dec(values["tax_eur"]),
        "tax_detail": dict(values["tax_detail_json"] or {}),
        "fx_rate": dec(values["fx_rate"]),
        "fx_source": values["fx_source"],
        "split_new_quantity": dec(values["split_new_quantity"]),
        "origin": values["origin"],
        "source_ref": values["source_ref"],
        "semantic_key": values["semantic_key"],
    }


# --- Accept, discard and refresh --------------------------------------------------------------


def _staged_batch(conn: Connection, batch_id: int) -> Any:
    batch = repos.get_batch(conn, batch_id)
    if batch is None:
        raise BatchNotFoundError(f"There is no import batch {batch_id}.")
    if batch.status != "staged":
        raise BatchNotStagedError(f"Import batch {batch_id} is already {batch.status}. Only a staged batch can change.")
    return batch


def reparsed_by(ws: Workspace, batch_id: int) -> dict[int, str]:
    """Earlier imports no parser recognised whose file a parser reads in `batch_id`, with that file's name."""
    found: dict[int, str] = {}
    for info in ws.imports.values():
        if info.batch_id != batch_id or info.parser_id is None or info.status == "duplicate_file":
            continue
        for earlier in ws.imports.values():
            if (
                earlier.file_hash == info.file_hash
                and earlier.batch_id != batch_id
                and ws.batches.get(earlier.batch_id) == "accepted"
                and earlier.parser_id is None
                and earlier.status in ("needs_review", "failed")
            ):
                found[earlier.id] = info.file_name
    return found


def accept_batch(conn: Connection, batch_id: int, *, clock: Clock, market: MarketData | None = None) -> AcceptResult:
    """Accept a staged batch: apply what its diff showed, rebuild the lot book, then the values (plan 5.3.6).

    With `market`, the stored value series is rebuilt over its default range (plan 5.7) and the
    result says so under `values`. Without it the value step is left out and `values` is `None`:
    the CLI and the API always give the market data of their data directory.
    """
    batch = _staged_batch(conn, batch_id)
    portfolio_id = int(batch.portfolio_id)
    now = format_ts_utc(clock.now_utc())
    ws = Workspace.load(conn, portfolio_id)
    held_before = {txn.key for txn in ws.alive() if txn.stored_state == "held"}
    reparsed = reparsed_by(ws, batch_id)
    names = [
        info.file_name for info in ws.imports.values() if info.batch_id == batch_id and info.status != "duplicate_file"
    ]
    plan = evaluate(ws, Cause(batch_id=batch_id, text=_batch_text(names, batch_id)), now=now, reparsed=reparsed)
    persist.apply_plan(conn, ws, plan, now=now, accepting=batch_id)
    for import_id in sorted(reparsed):
        repos.set_import_status(conn, import_id, "reparsed")
    repos.set_batch_status(conn, batch_id, "accepted", accepted_at=now)
    repos.set_last_import_at(conn, portfolio_id, now)
    values = rebuild_values(conn, portfolio_id, market=market, clock=clock) if market is not None else None

    instrument_names = repos.instrument_names(conn)
    in_batch = [txn for txn in ws.alive() if txn.batch_id == batch_id]
    released = [txn for txn in ws.alive() if txn.key in held_before and txn.key not in plan.held]
    opened = [*plan.new_items, *plan.reopened]
    data = {
        "batch": _batch_record(repos.get_batch(conn, batch_id)),
        "accepted": sum(1 for txn in in_batch if txn.key not in plan.held),
        "held_back": sum(1 for txn in in_batch if txn.key in plan.held),
        "released": len(released),
        "review_closed": [item_record(ws, closure.item, instrument_names) for closure in plan.closures],
        "review_opened": [item_record(ws, item, instrument_names) for item in opened],
        "lots": len(plan.book.lots),
        "disposals": len(plan.book.disposals),
        "open_review_items": sum(1 for item in ws.items.values() if item.status == "open"),
        "last_import_at": now,
        "values": values.summary() if values is not None else None,
    }
    return AcceptResult(batch_id=batch_id, data=data)


def discard_batch(conn: Connection, batch_id: int, *, clock: Clock) -> DiscardResult:
    """Discard a staged batch: its transactions and sources go, the items it raised close (plan 5.3.5)."""
    batch = _staged_batch(conn, batch_id)
    now = format_ts_utc(clock.now_utc())
    txn_ids = [row.id for row in conn.execute(sa.select(transactions.c.id).where(transactions.c.batch_id == batch_id))]
    import_ids = [row.id for row in conn.execute(sa.select(imports.c.id).where(imports.c.batch_id == batch_id))]

    closed = 0
    for row in conn.execute(sa.select(review_items).where(review_items.c.batch_id == batch_id)).all():
        values: dict[str, Any] = {"dedupe_key": f"{row.dedupe_key}#discarded-{batch_id}"}
        if row.status == "open":
            values.update(status="resolved", resolution_json={"how": "batch discarded"}, resolved_at=now)
            closed += 1
        repos.update_review_item(conn, row.id, **values)
    if txn_ids:
        conn.execute(
            sa.update(review_items).where(review_items.c.transaction_id.in_(txn_ids)).values(transaction_id=None)
        )
        conn.execute(sa.delete(cost_basis_inputs).where(cost_basis_inputs.c.transaction_id.in_(txn_ids)))
        conn.execute(sa.delete(transaction_sources).where(transaction_sources.c.transaction_id.in_(txn_ids)))
    if import_ids:
        conn.execute(sa.delete(transaction_sources).where(transaction_sources.c.import_id.in_(import_ids)))
    conn.execute(sa.delete(transactions).where(transactions.c.batch_id == batch_id))
    repos.set_batch_status(conn, batch_id, "discarded")
    repos.delete_unused_instruments(conn)
    return DiscardResult(
        batch_id=batch_id,
        data={
            "batch": _batch_record(repos.get_batch(conn, batch_id)),
            "removed_transactions": len(txn_ids),
            "review_closed": closed,
            "portfolio_id": int(batch.portfolio_id),
        },
    )


@dataclass(frozen=True)
class RefreshResult:
    """What checking the portfolio again did after a change that is not an import.

    `values` summarises the rebuilt value series (`ValueReport.summary`), or is `None` when the
    refresh was not given the market data.
    """

    review_closed: list[dict[str, Any]]
    review_opened: list[dict[str, Any]]
    lots: int
    disposals: int
    values: dict[str, Any] | None = None


def refresh_portfolio(
    conn: Connection, portfolio_id: int, *, clock: Clock, cause: str, market: MarketData | None = None
) -> RefreshResult:
    """Check every review item again and rebuild the lot book from the accepted transactions.

    For a change outside an import, such as a cost basis entered with `pg transfers set-cost`
    (plan 5.3.5): `cause` says what changed and is named as what superseded an item. A batch
    that is staged is left out: its data is checked when it is accepted. With `market`, the
    stored value series is rebuilt too (plan 5.7).
    """
    now = format_ts_utc(clock.now_utc())
    ws = Workspace.load(conn, portfolio_id, exclude_batch=repos.staged_batch_id(conn, portfolio_id))
    plan = evaluate(ws, Cause(batch_id=None, text=cause), now=now)
    persist.apply_plan(conn, ws, plan, now=now)
    values = rebuild_values(conn, portfolio_id, market=market, clock=clock) if market is not None else None
    names = repos.instrument_names(conn)
    return RefreshResult(
        review_closed=[item_record(ws, closure.item, names) for closure in plan.closures],
        review_opened=[item_record(ws, item, names) for item in [*plan.new_items, *plan.reopened]],
        lots=len(plan.book.lots),
        disposals=len(plan.book.disposals),
        values=values.summary() if values is not None else None,
    )


# --- Batches, status and the reminder ---------------------------------------------------------


def _batch_record(batch: Any) -> dict[str, Any]:
    return {
        "id": batch.id,
        "status": batch.status,
        "created_at": batch.created_at,
        "accepted_at": batch.accepted_at,
    }


def batch_record(conn: Connection, batch_id: int) -> dict[str, Any]:
    batch = repos.get_batch(conn, batch_id)
    if batch is None:
        raise BatchNotFoundError(f"There is no import batch {batch_id}.")
    return _batch_record(batch)


def resolve_batch_ref(conn: Connection, portfolio_id: int, ref: str) -> int:
    """The batch number `ref` names: a number, or "latest" for the most recent batch."""
    if ref == "latest":
        latest = repos.latest_batch_id(conn, portfolio_id)
        if latest is None:
            raise BatchNotFoundError("There is no import yet. Import files with: pg import FILE...")
        return latest
    if not ref.isdigit():
        raise BatchNotFoundError(f'"{ref}" is not an import batch. Give its number, or "latest".')
    batch = repos.get_batch(conn, int(ref))
    if batch is None or batch.portfolio_id != portfolio_id:
        raise BatchNotFoundError(f"There is no import batch {ref}.")
    return int(ref)


def list_batches(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """Every batch with its status and counts, oldest first."""
    rows = []
    for batch in repos.list_batches(conn, portfolio_id):
        summary = batch.summary_json or {}
        rows.append({**_batch_record(batch), "counts": summary.get("counts")})
    return rows


def import_reminder(last_import_at: str | None, today: date) -> Reminder:
    """The reminder to import again: due when the last import is more than 30 days before `today`.

    The day of the last import is its Europe/Berlin calendar date. Before any import the reminder
    is due, with a message that says how to start.
    """
    if last_import_at is None:
        return Reminder(
            due=True,
            days_since_last_import=None,
            message="Nothing has been accepted yet. Import your Trade Republic exports with: pg import FILE...",
        )
    imported = datetime.fromisoformat(last_import_at.replace("Z", "+00:00")).astimezone(BERLIN).date()
    days = (today - imported).days
    if days > REMINDER_AFTER_DAYS:
        return Reminder(
            due=True,
            days_since_last_import=days,
            message=(
                f"Your last import was {days} days ago, more than {REMINDER_AFTER_DAYS}. "
                "Import your latest Trade Republic exports to keep your portfolio up to date."
            ),
        )
    when = "today" if days == 0 else ("1 day ago" if days == 1 else f"{days} days ago")
    return Reminder(due=False, days_since_last_import=days, message=f"Your last import was {when}.")


def portfolio_status(
    conn: Connection, portfolio_id: int, *, clock: Clock, market: MarketData | None = None
) -> dict[str, Any]:
    """The portfolio summary of `pg status` (and `GET /portfolio`): the last import, the reminder and the queue.

    With `market`, `latest_value` is the value on the last weekday on or before today (plan 5.7),
    worked out from the prices and rates stored now: `valuation.portfolio.day_record`. It is
    `None` before anything is accepted, and without the market data.
    """
    portfolio = repos.get_portfolio(conn, portfolio_id)
    today = clock.today()
    counts: dict[str, int] = {
        str(state): int(number)
        for state, number in conn.execute(
            sa.select(transactions.c.state, sa.func.count())
            .where(transactions.c.portfolio_id == portfolio_id)
            .group_by(transactions.c.state)
        ).all()
    }
    open_items = conn.execute(
        sa.select(sa.func.count())
        .select_from(review_items)
        .where(review_items.c.portfolio_id == portfolio_id, review_items.c.status == "open")
    ).scalar_one()
    latest = latest_value(conn, portfolio_id, market=market, clock=clock) if market is not None else None
    return {
        "portfolio": {"name": portfolio.name, "base_currency": portfolio.base_currency, "source": portfolio.source},
        "today": today.isoformat(),
        "last_import_at": portfolio.last_import_at,
        "reminder": import_reminder(portfolio.last_import_at, today).to_dict(),
        "staged_batch": repos.staged_batch_id(conn, portfolio_id),
        "open_review_items": int(open_items),
        "transactions": {state: int(counts.get(state, 0)) for state in ("accepted", "held", "staged")},
        "latest_value": day_record(latest) if latest is not None else None,
    }
