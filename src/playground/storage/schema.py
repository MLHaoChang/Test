"""The SQLite registry schema: every P0 table (plan 4.1).

Column types follow the plan's own naming: `TEXT` for strings, `DEC`
(`DecimalText`, see `db.py`) for a decimal stored as exact text, `TS` for
an ISO-8601 UTC timestamp string (`core.dates.format_ts_utc`), `DATE`
for a plain calendar date (SQLAlchemy's `Date`, which SQLite stores as
`YYYY-MM-DD` text), and `JSON` for a JSON column (SQLAlchemy's `JSON`
type, which SQLite also stores as text and (de)serialises automatically).

Nullability follows the "NULL" annotations in plan 4.1: a column marked
NULL there is nullable here, and a foreign key, TEXT, DEC or DATE column
without that marker is not. The one deliberate exception is every
`*_json` column: plan 4.1 does not mark most of them NULL, but this
schema makes all of them nullable regardless, since P0 does not yet
write to most of these tables (that starts in WP7 and later) and an
empty object or array is exactly as easy to write as a stored `NULL`
once that insert code exists.

`schema_version` holds one row; P0 starts it at 1 (`ensure_schema`).
"""

import sqlalchemy as sa
from sqlalchemy import Engine

from playground.storage.db import DecimalText

metadata = sa.MetaData()

schema_version = sa.Table(
    "schema_version",
    metadata,
    sa.Column("version", sa.Integer, primary_key=True),
)

portfolios = sa.Table(
    "portfolios",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),  # real | virtual
    sa.Column("base_currency", sa.Text, nullable=False),  # "EUR" in P0
    sa.Column("source", sa.Text, nullable=False),  # "trade_republic" in P0
    sa.Column("last_import_at", sa.Text, nullable=True),  # TS
    sa.Column("created_at", sa.Text, nullable=False),  # TS
)

instruments = sa.Table(
    "instruments",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("isin", sa.Text, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),  # stock|etf|fund|bond|certificate|other|unknown
    sa.Column("country", sa.Text, nullable=True),
    sa.Column("sector", sa.Text, nullable=True),
    sa.Column("industry", sa.Text, nullable=True),
    sa.Column("currency", sa.Text, nullable=True),  # listing currency of data_symbol
    sa.Column("exchange", sa.Text, nullable=True),
    sa.Column("data_source", sa.Text, nullable=True),  # stooq|manual
    sa.Column("data_symbol", sa.Text, nullable=True),
    sa.Column("mapping_status", sa.Text, nullable=False),  # unmapped|confirmed
    sa.Column("mapping_updated_at", sa.Text, nullable=True),  # TS
    sa.Column("mapping_note", sa.Text, nullable=True),
)

instrument_mapping_log = sa.Table(
    "instrument_mapping_log",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
    sa.Column("changed_at", sa.Text, nullable=False),  # TS
    sa.Column("changed_by", sa.Text, nullable=False),  # cli|api|file
    sa.Column("old_source", sa.Text, nullable=True),
    sa.Column("old_symbol", sa.Text, nullable=True),
    sa.Column("old_currency", sa.Text, nullable=True),
    sa.Column("new_source", sa.Text, nullable=True),
    sa.Column("new_symbol", sa.Text, nullable=True),
    sa.Column("new_currency", sa.Text, nullable=True),
    sa.Column("note", sa.Text, nullable=True),
)

import_batches = sa.Table(
    "import_batches",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),  # TS
    sa.Column("status", sa.Text, nullable=False),  # staged|accepted|discarded
    sa.Column("accepted_at", sa.Text, nullable=True),  # TS
    sa.Column("summary_json", sa.JSON, nullable=True),
)

imports = sa.Table(
    "imports",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("batch_id", sa.Integer, sa.ForeignKey("import_batches.id"), nullable=False),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("file_hash", sa.Text, nullable=False),  # SHA-256 of the file bytes
    sa.Column("file_name", sa.Text, nullable=False),
    sa.Column("stored_path", sa.Text, nullable=False),
    sa.Column("doc_type", sa.Text, nullable=False),
    sa.Column("parser_id", sa.Text, nullable=True),
    sa.Column("parser_version", sa.Integer, nullable=True),
    sa.Column("parsed_at", sa.Text, nullable=False),  # TS
    sa.Column("status", sa.Text, nullable=False),  # parsed|partial|needs_review|duplicate_file|failed|reparsed
    sa.Column("review_notes", sa.Text, nullable=True),
)

transactions = sa.Table(
    "transactions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("batch_id", sa.Integer, sa.ForeignKey("import_batches.id"), nullable=False),
    sa.Column("import_id", sa.Integer, sa.ForeignKey("imports.id"), nullable=False),  # first source
    sa.Column("state", sa.Text, nullable=False),  # staged|held|accepted
    sa.Column("ts_utc", sa.Text, nullable=False),  # TS
    sa.Column("ts_local", sa.Text, nullable=False),
    sa.Column("source_tz", sa.Text, nullable=False),
    sa.Column("ts_precision", sa.Text, nullable=False),  # minute|day
    sa.Column("value_date", sa.Date, nullable=True),
    sa.Column("type", sa.Text, nullable=False),  # TxnType value
    sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=True),
    sa.Column("quantity", DecimalText, nullable=True),
    sa.Column("price", DecimalText, nullable=True),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("amount", DecimalText, nullable=True),  # signed booking amount, in `currency`
    sa.Column("amount_eur", DecimalText, nullable=True),
    sa.Column("fx_rate", DecimalText, nullable=True),
    sa.Column("fx_source", sa.Text, nullable=False),  # document|ecb|none
    sa.Column("fees_eur", DecimalText, nullable=True),
    sa.Column("tax_eur", DecimalText, nullable=True),  # NULL when no source says (a statement-only line)
    sa.Column("tax_detail_json", sa.JSON, nullable=True),
    sa.Column("split_new_quantity", DecimalText, nullable=True),
    sa.Column("origin", sa.Text, nullable=True),  # order|savings_plan|transfer
    sa.Column("source_ref", sa.Text, nullable=True),
    sa.Column("semantic_key", sa.Text, nullable=False),
    sa.Column("occurrence", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("precedence", sa.Integer, nullable=False),  # of the highest-precedence source
    sa.UniqueConstraint("portfolio_id", "content_hash"),
)

transaction_sources = sa.Table(
    "transaction_sources",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("transaction_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=False),
    sa.Column("import_id", sa.Integer, sa.ForeignKey("imports.id"), nullable=False),
    sa.Column("source_kind", sa.Text, nullable=False),  # pdf_document|csv_export|pdf_statement|manual_csv
    sa.Column("precedence", sa.Integer, nullable=False),
    sa.Column("report_key", sa.Text, nullable=False),
    sa.Column("line_from", sa.Integer, nullable=True),
    sa.Column("line_to", sa.Integer, nullable=True),
    sa.Column("fields_json", sa.JSON, nullable=True),
    sa.UniqueConstraint("import_id", "report_key"),
)

review_items = sa.Table(
    "review_items",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("batch_id", sa.Integer, sa.ForeignKey("import_batches.id"), nullable=True),
    sa.Column("import_id", sa.Integer, sa.ForeignKey("imports.id"), nullable=True),
    sa.Column("transaction_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=True),
    sa.Column("kind", sa.Text, nullable=False),  # a ReviewKind value
    sa.Column("status", sa.Text, nullable=False),  # open|resolved|dismissed
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("extracted_text", sa.Text, nullable=True),
    sa.Column("fields_json", sa.JSON, nullable=True),
    sa.Column("created_at", sa.Text, nullable=False),  # TS
    sa.Column("resolved_at", sa.Text, nullable=True),  # TS
    sa.Column("resolution_json", sa.JSON, nullable=True),
    sa.Column("dedupe_key", sa.Text, nullable=False, unique=True),
)

cost_basis_inputs = sa.Table(
    "cost_basis_inputs",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("transaction_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=False),
    sa.Column("acquired_on", sa.Date, nullable=False),
    sa.Column("cost_eur", DecimalText, nullable=False),
    sa.Column("entered_at", sa.Text, nullable=False),  # TS
    sa.Column("note", sa.Text, nullable=True),
)

lots = sa.Table(
    "lots",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
    sa.Column("open_txn_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=False),
    sa.Column("origin", sa.Text, nullable=False),  # buy|transfer_in
    sa.Column("booked_ts", sa.Text, nullable=False),  # TS: when the lot entered the account
    sa.Column("opened_ts", sa.Text, nullable=False),  # TS: acquisition time, for FIFO order
    sa.Column("quantity_initial", DecimalText, nullable=False),
    sa.Column("quantity_open", DecimalText, nullable=False),
    sa.Column("cost_eur_initial", DecimalText, nullable=True),
    sa.Column("cost_eur_open", DecimalText, nullable=True),
    sa.Column("cost_missing", sa.Integer, nullable=False),  # 0 or 1
)

disposals = sa.Table(
    "disposals",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("lot_id", sa.Integer, sa.ForeignKey("lots.id"), nullable=False),
    sa.Column("txn_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),  # sell|transfer_out
    sa.Column("ts_utc", sa.Text, nullable=False),  # TS
    sa.Column("quantity", DecimalText, nullable=False),
    sa.Column("cost_eur", DecimalText, nullable=True),
    sa.Column("proceeds_eur", DecimalText, nullable=True),
    sa.Column("fees_eur", DecimalText, nullable=False),
    sa.Column("realised_eur", DecimalText, nullable=True),
)

confirmed_holdings = sa.Table(
    "confirmed_holdings",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("as_of", sa.Date, nullable=False),
    sa.Column("isin", sa.Text, nullable=False),
    sa.Column("quantity", DecimalText, nullable=False),
    sa.Column("source", sa.Text, nullable=False),  # typed|file|statement
    sa.Column("entered_at", sa.Text, nullable=False),  # TS
)

holdings_snapshots = sa.Table(
    "holdings_snapshots",
    metadata,
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("as_of", sa.Date, nullable=False),
    sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
    sa.Column("quantity", DecimalText, nullable=False),
    sa.Column("pricing_quantity", DecimalText, nullable=False),
    sa.Column("price", DecimalText, nullable=True),
    sa.Column("price_date", sa.Date, nullable=True),
    sa.Column("price_currency", sa.Text, nullable=True),
    sa.Column("fx_rate", DecimalText, nullable=True),
    sa.Column("fx_date", sa.Date, nullable=True),
    sa.Column("value_eur", DecimalText, nullable=True),
    sa.Column("cost_basis_eur", DecimalText, nullable=True),
    sa.Column("sleeve", sa.Text, nullable=False),  # "core" in P0
    sa.Column("flags_json", sa.JSON, nullable=True),
    sa.PrimaryKeyConstraint("portfolio_id", "as_of", "instrument_id"),
)

portfolio_values = sa.Table(
    "portfolio_values",
    metadata,
    sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False),
    sa.Column("date", sa.Date, nullable=False),
    sa.Column("value_eur", DecimalText, nullable=False),
    sa.Column("cost_basis_eur", DecimalText, nullable=True),
    sa.Column("complete", sa.Integer, nullable=False),  # 0 or 1
    sa.Column("flags_json", sa.JSON, nullable=True),
    sa.PrimaryKeyConstraint("portfolio_id", "date"),
)

benchmarks = sa.Table(
    "benchmarks",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),  # slug, e.g. "msci_world_eur"
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("isin", sa.Text, nullable=True),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("data_source", sa.Text, nullable=False),
    sa.Column("data_symbol", sa.Text, nullable=False),
)


def ensure_schema(engine: Engine) -> None:
    """Create every table above that does not exist yet, and seed `schema_version` to 1.

    Idempotent: safe to call every time the registry is opened, not just
    the first time (4.1: "P0 starts at 1 and creates tables with
    metadata.create_all").
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(sa.select(schema_version.c.version)).first()
        if existing is None:
            conn.execute(sa.insert(schema_version).values(version=1))
