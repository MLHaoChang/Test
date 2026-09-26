"""Shared fixtures for the import pipeline tests (plan 5.3.4 to 5.3.6, WP7).

Test modules must not import from this file (pytest may load another `conftest` module under
the same name). They ask for these fixtures instead:

- `harness`: a fresh registry with the default portfolio, a clock fixed on 2024-12-31, an
  uploads directory and helpers to stage, accept, discard and inspect imports;
- `harness_factory`: makes more harnesses in one test (module scope, so a Hypothesis test can
  make a fresh one per example); `harness_factory(in_memory=True)` gives an in-memory registry;
- `docs`: builds synthetic input files: Trade Republic documents in the text form of
  `pdf_text.py`, account statements, CSV exports and manual CSV files.

The pipeline modules are imported inside the helpers, so the other tests in this directory still
load when one of them is broken.

A document built by `docs` is a "text PDF": the PDF magic bytes, a marker line and the text.
The harness passes the pipeline an extractor that returns that text directly and runs the real
pdfplumber extraction (cached by file hash) for real PDF files, such as the golden inputs. So
the tests exercise the whole pipeline, including the classifier and every layout parser, without
writing PDFs. The PDF text layer itself is tested in `tests/importer/tr/test_pdf_text.py`.
"""

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool

from playground.core.clock import FixedClock
from playground.importer.tr.pdf_text import extract_text
from playground.storage.db import open_registry
from playground.storage.repos import get_or_create_portfolio
from playground.storage.schema import (
    ensure_schema,
    import_batches,
    imports,
    instruments,
    lots,
    metadata,
    review_items,
    transaction_sources,
    transactions,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN_INPUTS = FIXTURES_DIR / "golden" / "inputs"
SAME_DAY_DIR = FIXTURES_DIR / "idempotency" / "same_day_savings_plans"

TEXT_PDF_MAGIC = b"%PDF-1.4\n%text-pdf-for-tests\n"

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"
VWRL = "IE00BK5BQT80"
NAMES = {
    SAP: "SAP SE",
    MSCIW: "iShsIII-Core MSCI World U.ETF",
    AAPL: "Apple Inc.",
    NVDA: "NVIDIA Corp.",
    ALV: "Allianz SE",
    VWRL: "Vanguard FTSE All-World U.ETF",
}

_REAL_TEXT_CACHE: dict[str, str] = {}


def extract_for_tests(data: bytes) -> str:
    """The text of a text PDF, or pdfplumber's extraction of a real PDF (cached by file hash)."""
    if data.startswith(TEXT_PDF_MAGIC):
        return data[len(TEXT_PDF_MAGIC) :].decode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    if digest not in _REAL_TEXT_CACHE:
        _REAL_TEXT_CACHE[digest] = extract_text(data)
    return _REAL_TEXT_CACHE[digest]


# --- Number formatting for the documents ------------------------------------------------------


def de_money(value: Decimal | str) -> str:
    """A German amount with two decimals and a thousands dot: -1401 gives "-1.401,00"."""
    amount = Decimal(value).quantize(Decimal("0.01"))
    sign = "-" if amount < 0 else ""
    whole, _, cents = f"{abs(amount):f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{'.'.join(groups)},{cents}"


def de_quantity(value: Decimal | str) -> str:
    """A German quantity as printed: "2.5" gives "2,5", "1000" gives "1.000"."""
    text = f"{Decimal(value):f}"
    whole, _, fraction = text.partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    printed = ".".join(groups)
    return f"{printed},{fraction}" if fraction else printed


def dotted(day: date) -> str:
    return day.strftime("%d.%m.%Y")


_MONTHS = ("Jan.", "Feb.", "Mär.", "Apr.", "Mai", "Jun.", "Jul.", "Aug.", "Sep.", "Okt.", "Nov.", "Dez.")


def statement_date(day: date) -> str:
    """The date form of the 2024 account statement: "15 Jan. 2024"."""
    return f"{day.day:02d} {_MONTHS[day.month - 1]} {day.year}"


HEADER_LINES = (
    "TRADE REPUBLIC BANK GMBH BRUNNENSTRASSE 19-21 10119 BERLIN",
    "Jana Beispiel SEITE 1 von 1",
)
FOOTER_LINES = (
    "Diese Abrechnung wird maschinell erstellt und trägt keine Unterschrift.",
    "Trade Republic Bank GmbH www.traderepublic.com",
)


def _text(lines: Iterable[str]) -> str:
    return "\n".join(lines) + "\n"


def _input_file(name: str, data: bytes) -> Any:
    from playground.importer.pipeline import InputFile

    return InputFile(name=name, data=data)


@dataclass(frozen=True)
class StatementRow:
    """One row of the 2024 account statement: booking day, TYP word, description and signed amount."""

    day: date
    typ: str
    description: str
    amount: Decimal


class Docs:
    """Builds synthetic input files. Every amount is a `Decimal` or a decimal string."""

    def pdf(self, name: str, text: str) -> Any:
        return _input_file(name, TEXT_PDF_MAGIC + text.encode("utf-8"))

    def trade(
        self,
        name: str,
        *,
        side: str = "Kauf",
        day: date = date(2024, 1, 15),
        at: str = "10:05",
        isin: str = SAP,
        title: str | None = None,
        quantity: str = "10",
        price: str = "140.00",
        fee: str = "1.00",
        taxes: Mapping[str, str] | None = None,
        value_day: date | None = None,
        execution: str = "5d0a-93f1",
        booking: str | None = None,
    ) -> Any:
        """A trade confirmation in the 2023 layout (like golden T2). Amounts add up unless `booking` is given."""
        return self.pdf(
            name,
            self.trade_text(
                side=side,
                day=day,
                at=at,
                isin=isin,
                title=title,
                quantity=quantity,
                price=price,
                fee=fee,
                taxes=taxes,
                value_day=value_day,
                execution=execution,
                booking=booking,
            ),
        )

    def trade_text(
        self,
        *,
        side: str = "Kauf",
        day: date = date(2024, 1, 15),
        at: str = "10:05",
        isin: str = SAP,
        title: str | None = None,
        quantity: str = "10",
        price: str = "140.00",
        fee: str = "1.00",
        taxes: Mapping[str, str] | None = None,
        value_day: date | None = None,
        execution: str = "5d0a-93f1",
        booking: str | None = None,
    ) -> str:
        instrument = title if title is not None else NAMES.get(isin, "Beispiel AG")
        position = (Decimal(quantity) * Decimal(price)).quantize(Decimal("0.01"))
        tax_lines = dict(taxes or {})
        tax_total = sum((Decimal(amount) for amount in tax_lines.values()), start=Decimal("0"))
        buying = side == "Kauf"
        total = -(position + Decimal(fee) + tax_total) if buying else position - Decimal(fee) - tax_total
        booked = Decimal(booking) if booking is not None else total
        settlement = [f"Fremdkostenzuschlag {de_money(-Decimal(fee))} EUR"]
        settlement += [f"{label} {de_money(-Decimal(amount))} EUR" for label, amount in tax_lines.items()]
        return _text(
            [
                *HEADER_LINES,
                f"Ahornweg 7 DATUM {dotted(day)}",
                "12345 Musterstadt ORDER 0000-0001",
                f"AUSFÜHRUNG {execution}",
                "DEPOT 0000001111",
                "WERTPAPIERABRECHNUNG",
                "ÜBERSICHT",
                f"Market-Order {side} am {dotted(day)}, um {at} Uhr (Europe/Berlin) an der Lang & Schwarz Exchange.",
                "Handelspartner der Transaktion ist die Lang & Schwarz TradeCenter AG & Co. KG.",
                "POSITION ANZAHL PREIS BETRAG",
                f"{instrument} {de_quantity(quantity)} Stk. {de_money(price)} EUR {de_money(position)} EUR",
                "Inhaber-Aktien o.N.",
                f"ISIN: {isin}",
                f"GESAMT {de_money(position)} EUR",
                "ABRECHNUNG",
                "POSITION BETRAG",
                *settlement,
                f"GESAMT {de_money(total)} EUR",
                "BUCHUNG",
                "VERRECHNUNGSKONTO WERTSTELLUNG BETRAG",
                f"DE00000000000000000000 {dotted(value_day or day)} {de_money(booked)} EUR",
                *FOOTER_LINES,
            ]
        )

    def savings_plan(
        self,
        name: str,
        *,
        day: date = date(2024, 9, 2),
        isin: str = VWRL,
        quantity: str = "0.4798",
        price: str = "104.20",
        amount: str = "50.00",
        value_day: date | None = None,
        execution: str = "e1f0-2a7c",
    ) -> Any:
        """A savings plan execution (like the same-day pair of plan 6.2)."""
        instrument = NAMES.get(isin, "Beispiel ETF")
        text = _text(
            [
                *HEADER_LINES,
                f"Ahornweg 7 DATUM {dotted(day)}",
                f"12345 Musterstadt AUSFÜHRUNG {execution}",
                "SPARPLAN 4b2d-a0e8",
                "DEPOT 0000001111",
                "WERTPAPIERABRECHNUNG SPARPLAN",
                "ÜBERSICHT",
                f"Sparplanausführung am {dotted(day)} an der Lang & Schwarz Exchange.",
                "Handelspartner der Transaktion ist die Lang & Schwarz TradeCenter AG & Co. KG.",
                "POSITION ANZAHL DURCHSCHNITTSKURS BETRAG",
                f"{instrument} {de_quantity(quantity)} Stk. {de_money(price)} EUR {de_money(amount)} EUR",
                "Registered Shares USD Acc. o.N.",
                f"ISIN: {isin}",
                f"GESAMT {de_money(amount)} EUR",
                "BUCHUNG",
                "VERRECHNUNGSKONTO WERTSTELLUNG BETRAG",
                f"DE00000000000000000000 {dotted(value_day or day)} {de_money(-Decimal(amount))} EUR",
                *FOOTER_LINES,
            ]
        )
        return self.pdf(name, text)

    def statement(self, name: str, rows: Sequence[StatementRow]) -> Any:
        """An account statement in the 2024 layout (UMSATZÜBERSICHT) with the given rows."""
        lines = [
            *HEADER_LINES,
            "Ahornweg 7 DATUM 31.12.2024",
            "12345 Musterstadt DEPOT 0000001111",
            "UMSATZÜBERSICHT",
            "ZEITRAUM 01 Jan. 2024 - 31 Dez. 2024",
            "DATUM TYP BESCHREIBUNG EINGANG AUSGANG SALDO",
        ]
        for row in rows:
            incoming = f"{de_money(row.amount)} €" if row.amount > 0 else "-"
            outgoing = f"{de_money(-row.amount)} €" if row.amount < 0 else "-"
            lines.append(f"{statement_date(row.day)} {row.typ} {row.description} {incoming} {outgoing} 0,00 €")
        lines += ["Diese Aufstellung wird maschinell erstellt und trägt keine Unterschrift.", FOOTER_LINES[1]]
        return self.pdf(name, _text(lines))

    def statement_row(self, day: date, typ: str, amount: str, isin: str | None = None) -> StatementRow:
        description = f"{isin} {NAMES.get(isin, 'Beispiel AG')}" if isin is not None else "Bareinzahlung"
        return StatementRow(day=day, typ=typ, description=description, amount=Decimal(amount))

    def csv(self, name: str, rows: Sequence[str]) -> Any:
        """A Trade Republic CSV export (profile tr_csv_synthetic_v1) with the given data rows."""
        header = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"
        return _input_file(name, ("\n".join([header, *rows]) + "\n").encode("utf-8"))

    def manual(self, name: str, rows: Sequence[str]) -> Any:
        """A manual transaction file (plan 5.3.3) with the given data rows."""
        header = "date;time;type;isin;quantity;price;currency;amount_eur;fees_eur;tax_eur;note"
        return _input_file(name, ("\n".join([header, *rows]) + "\n").encode("utf-8"))

    def file(self, path: Path) -> Any:
        return _input_file(path.name, path.read_bytes())

    def golden(self, relative: str) -> Any:
        """A golden portfolio input, for example "pdf/2024-01-15_kauf_sap.pdf" or "tr_transactions_2024.csv"."""
        return self.file(GOLDEN_INPUTS / relative)

    def golden_round_one(self) -> list[Any]:
        """The CSV export and the nine PDF documents of import round 1 (plan 1.2)."""
        pdfs = sorted((GOLDEN_INPUTS / "pdf").glob("*.pdf"))
        return [self.golden("tr_transactions_2024.csv"), *(self.file(path) for path in pdfs)]

    def golden_statement(self) -> Any:
        return self.golden("statement/kontoauszug_2024_h1.pdf")


# --- Rows read back from the registry ---------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    kind: str
    file_name: str
    report_key: str
    batch_id: int


@dataclass(frozen=True)
class TxnRow:
    id: int
    batch_id: int
    state: str
    type: str
    isin: str | None
    ts_utc: str
    ts_local: str
    ts_precision: str
    value_date: date | None
    quantity: Decimal | None
    price: Decimal | None
    amount_eur: Decimal | None
    fees_eur: Decimal | None
    tax_eur: Decimal | None
    tax_detail: Mapping[str, str]
    fx_rate: Decimal | None
    split_new_quantity: Decimal | None
    origin: str | None
    source_ref: str | None
    semantic_key: str
    occurrence: int
    content_hash: str
    precedence: int
    sources: tuple[SourceRow, ...]

    @property
    def day(self) -> str:
        return self.ts_local[:10]

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(source.kind for source in self.sources))

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted(source.file_name for source in self.sources))


@dataclass(frozen=True)
class ItemRow:
    id: int
    kind: str
    status: str
    message: str
    extracted_text: str | None
    fields: Mapping[str, Any]
    resolution: Mapping[str, Any] | None
    transaction_id: int | None
    batch_id: int | None
    import_id: int | None
    file_name: str | None
    dedupe_key: str


def _registry_engine_in_memory() -> Engine:
    engine = sa.create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    return engine


@dataclass
class Harness:
    """A registry with the default portfolio and the helpers the import tests share."""

    engine: Engine
    portfolio_id: int
    uploads_dir: Path
    clock: FixedClock
    extract: Callable[[bytes], str] = extract_for_tests
    staged: list[int] = field(default_factory=list)

    # --- Actions ---

    def stage(self, *files: Any, **options: Any) -> Any:
        from playground.importer.pipeline import stage_inputs

        with self.engine.begin() as conn:
            summary = stage_inputs(
                conn,
                self.portfolio_id,
                list(files),
                clock=self.clock,
                uploads_dir=self.uploads_dir,
                extract=self.extract,
                **options,
            )
        return summary

    def accept(self, batch_id: int | None = None) -> Any:
        from playground.importer.pipeline import accept_batch

        with self.engine.begin() as conn:
            return accept_batch(conn, batch_id if batch_id is not None else self.staged_batch_id(), clock=self.clock)

    def discard(self, batch_id: int | None = None) -> Any:
        from playground.importer.pipeline import discard_batch

        with self.engine.begin() as conn:
            return discard_batch(conn, batch_id if batch_id is not None else self.staged_batch_id(), clock=self.clock)

    def run(self, *files: Any, **options: Any) -> Any:
        """Stage the files as one batch and accept it; return the staging summary."""
        summary = self.stage(*files, **options)
        self.accept(summary.batch_id)
        return summary

    def diff(self, batch_id: int | None = None, **options: Any) -> Any:
        from playground.importer.reconcile import build_diff

        with self.engine.begin() as conn:
            return build_diff(
                conn, batch_id if batch_id is not None else self.latest_batch_id(), clock=self.clock, **options
            )

    def dismiss(self, item_id: int, reason: str = "checked by hand") -> Any:
        from playground.importer.review import dismiss_item

        with self.engine.begin() as conn:
            return dismiss_item(conn, item_id, reason=reason, clock=self.clock)

    def resolve(self, item_id: int, how: str, **options: Any) -> Any:
        from playground.importer.review import resolve_item

        with self.engine.begin() as conn:
            return resolve_item(conn, item_id, how=how, clock=self.clock, **options)

    # --- Reading back ---

    def staged_batch_id(self) -> int:
        with self.engine.connect() as conn:
            batch_id = conn.execute(
                sa.select(import_batches.c.id).where(
                    import_batches.c.portfolio_id == self.portfolio_id, import_batches.c.status == "staged"
                )
            ).scalar_one()
        return int(batch_id)

    def latest_batch_id(self) -> int:
        with self.engine.connect() as conn:
            batch_id = conn.execute(
                sa.select(sa.func.max(import_batches.c.id)).where(import_batches.c.portfolio_id == self.portfolio_id)
            ).scalar_one()
        return int(batch_id)

    def batch_status(self, batch_id: int) -> str:
        with self.engine.connect() as conn:
            return str(
                conn.execute(sa.select(import_batches.c.status).where(import_batches.c.id == batch_id)).scalar_one()
            )

    def transactions(self, *, state: str | None = None) -> list[TxnRow]:
        with self.engine.connect() as conn:
            isin_of = {row.id: row.isin for row in conn.execute(sa.select(instruments.c.id, instruments.c.isin))}
            source_rows = conn.execute(
                sa.select(
                    transaction_sources.c.transaction_id,
                    transaction_sources.c.source_kind,
                    transaction_sources.c.report_key,
                    imports.c.file_name,
                    imports.c.batch_id,
                )
                .join(imports, imports.c.id == transaction_sources.c.import_id)
                .order_by(transaction_sources.c.id)
            ).all()
            txn_rows = conn.execute(
                sa.select(transactions)
                .where(transactions.c.portfolio_id == self.portfolio_id)
                .order_by(transactions.c.ts_utc, transactions.c.id)
            ).all()
        sources: dict[int, list[SourceRow]] = {}
        for row in source_rows:
            sources.setdefault(row.transaction_id, []).append(
                SourceRow(
                    kind=row.source_kind, file_name=row.file_name, report_key=row.report_key, batch_id=row.batch_id
                )
            )
        result = [
            TxnRow(
                id=row.id,
                batch_id=row.batch_id,
                state=row.state,
                type=row.type,
                isin=isin_of.get(row.instrument_id) if row.instrument_id is not None else None,
                ts_utc=row.ts_utc,
                ts_local=row.ts_local,
                ts_precision=row.ts_precision,
                value_date=row.value_date,
                quantity=row.quantity,
                price=row.price,
                amount_eur=row.amount_eur,
                fees_eur=row.fees_eur,
                tax_eur=row.tax_eur,
                tax_detail=row.tax_detail_json or {},
                fx_rate=row.fx_rate,
                split_new_quantity=row.split_new_quantity,
                origin=row.origin,
                source_ref=row.source_ref,
                semantic_key=row.semantic_key,
                occurrence=row.occurrence,
                content_hash=row.content_hash,
                precedence=row.precedence,
                sources=tuple(sources.get(row.id, [])),
            )
            for row in txn_rows
        ]
        return [txn for txn in result if state is None or txn.state == state]

    def items(self, *, status: str | None = None, kind: str | None = None) -> list[ItemRow]:
        with self.engine.connect() as conn:
            file_of = {row.id: row.file_name for row in conn.execute(sa.select(imports.c.id, imports.c.file_name))}
            rows = conn.execute(
                sa.select(review_items)
                .where(review_items.c.portfolio_id == self.portfolio_id)
                .order_by(review_items.c.id)
            ).all()
        items = [
            ItemRow(
                id=row.id,
                kind=row.kind,
                status=row.status,
                message=row.message,
                extracted_text=row.extracted_text,
                fields=row.fields_json or {},
                resolution=row.resolution_json,
                transaction_id=row.transaction_id,
                batch_id=row.batch_id,
                import_id=row.import_id,
                file_name=file_of.get(row.import_id) if row.import_id is not None else None,
                dedupe_key=row.dedupe_key,
            )
            for row in rows
        ]
        return [
            item for item in items if (status is None or item.status == status) and (kind is None or item.kind == kind)
        ]

    def imports(self) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(sa.select(imports).order_by(imports.c.id)).all())

    def lot_rows(self) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(sa.select(lots).order_by(lots.c.id)).all())

    def holdings(self) -> dict[str, Decimal]:
        """Open quantity per ISIN in the stored lot book (the accepted ledger), zero positions left out."""
        with self.engine.connect() as conn:
            isin_of = {row.id: row.isin for row in conn.execute(sa.select(instruments.c.id, instruments.c.isin))}
            rows = conn.execute(sa.select(lots.c.instrument_id, lots.c.quantity_open)).all()
        held: dict[str, Decimal] = {}
        for row in rows:
            isin = isin_of[row.instrument_id]
            held[isin] = held.get(isin, Decimal(0)) + row.quantity_open
        return {isin: quantity for isin, quantity in sorted(held.items()) if quantity != 0}

    def canonical(self) -> tuple[Any, ...]:
        """The accepted ledger, the held transactions and the open review items, without ids,
        occurrence numbers, content hashes and timestamps (plan 7.2), so two registries can be compared."""
        txns = self.transactions()
        key_of = {txn.id: _canonical_txn_key(txn) for txn in txns}
        ledger = sorted(_canonical_txn(txn) for txn in txns if txn.state in ("accepted", "held"))
        with self.engine.connect() as conn:
            isin_of = {row.id: row.isin for row in conn.execute(sa.select(instruments.c.id, instruments.c.isin))}
            lot_rows = conn.execute(sa.select(lots)).all()
        book = sorted(
            (
                isin_of[row.instrument_id],
                row.origin,
                row.booked_ts,
                row.opened_ts,
                str(row.quantity_initial),
                str(row.quantity_open),
                str(row.cost_eur_initial),
                str(row.cost_eur_open),
                row.cost_missing,
                key_of[row.open_txn_id],
            )
            for row in lot_rows
        )
        open_items = sorted(
            (
                item.kind,
                item.message,
                item.file_name or "",
                key_of.get(item.transaction_id, ()) if item.transaction_id is not None else (),
            )
            for item in self.items(status="open")
        )
        return (tuple(ledger), tuple(book), tuple(open_items))


def _canonical_txn_key(txn: TxnRow) -> tuple[Any, ...]:
    return (txn.type, txn.isin or "", txn.ts_utc, str(txn.amount_eur), str(txn.quantity), str(txn.split_new_quantity))


def _canonical_txn(txn: TxnRow) -> tuple[Any, ...]:
    return (
        txn.state,
        txn.type,
        txn.isin or "",
        txn.ts_utc,
        txn.ts_local,
        txn.ts_precision,
        str(txn.value_date),
        str(txn.quantity),
        str(txn.price),
        str(txn.amount_eur),
        str(txn.fees_eur),
        str(txn.tax_eur),
        tuple(sorted(txn.tax_detail.items())),
        str(txn.fx_rate),
        str(txn.split_new_quantity),
        txn.origin or "",
        txn.source_ref or "",
        txn.precedence,
        tuple(sorted((source.kind, source.file_name, source.report_key) for source in txn.sources)),
    )


def _make_harness(base: Path, *, in_memory: bool) -> Harness:
    base.mkdir(parents=True, exist_ok=True)
    engine = _registry_engine_in_memory() if in_memory else open_registry(base / "data")
    if not in_memory:
        ensure_schema(engine)
    clock = FixedClock(date(2024, 12, 31))
    with engine.begin() as conn:
        portfolio_id = get_or_create_portfolio(conn, clock=clock)
    uploads = base / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    return Harness(engine=engine, portfolio_id=portfolio_id, uploads_dir=uploads, clock=clock)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A fresh registry on disk with the default portfolio and a clock fixed on 2024-12-31."""
    return _make_harness(tmp_path / "harness", in_memory=False)


@pytest.fixture(scope="module")
def harness_factory(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., Harness]:
    """Makes a fresh harness each call; `in_memory=True` keeps the registry in memory (fast)."""
    counter = iter(range(1_000_000))

    def make(*, in_memory: bool = False) -> Harness:
        base = tmp_path_factory.mktemp("harness") / str(next(counter))
        return _make_harness(base, in_memory=in_memory)

    return make


@pytest.fixture(scope="session")
def docs() -> Docs:
    """Builds synthetic input files (see `Docs`)."""
    return Docs()
