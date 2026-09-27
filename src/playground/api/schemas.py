"""Pydantic response and request models for the API of plan 5.8 (AC12, 7.4).

Every model mirrors, field for field, the JSON-ready dictionaries the CLI already prints with
`--json` (`importer.reconcile.ReconciliationDiff.to_dict`, `importer.pipeline.AcceptResult.to_dict`,
`importer.review.item_record`, `valuation.portfolio.holdings_report` and `ValueReport.to_dict`,
`marketdata.instruments.list_instruments`, `marketdata.benchmarks.benchmark_series`, and so on), so
the API and the CLI show the same figures from the same services. Money, quantities and prices are
always `str` here, never `float` (plan 3.3): FastAPI validates every response against these models
before it is sent, so a stray float would fail the response instead of reaching a client.

A handful of fields whose JSON name is a Python keyword (`from`, imported into a range) are
declared under a trailing-underscore name with `Field(alias="from")`; FastAPI serialises responses
by alias by default, so the JSON still reads `"from"`, exactly as `pg value --json` prints it.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Plain building blocks ------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """One machine-readable code and one plain-English sentence (plan 5.8)."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """The shape of every non-2xx response: `{"error": {"code": ..., "message": ...}}`."""

    error: ErrorDetail


class HealthResponse(BaseModel):
    """`GET /health` (plan 5.8, 7.4): confirms this is not a trading system before anything else."""

    status: Literal["ok"]
    version: str
    real_orders: Literal[False]
    portfolio_read_only: Literal[True]


class BatchRef(BaseModel):
    """One import batch, as `importer.pipeline._batch_record` writes it."""

    id: int
    status: str
    created_at: str
    accepted_at: str | None


class TransactionBrief(BaseModel):
    """A transaction in a few fields (`importer.review.brief`): which one an item or a match is about."""

    id: int
    type: str
    isin: str | None
    name: str | None
    date: str
    amount_eur: str | None
    quantity: str | None


class ReviewItem(BaseModel):
    """One review item (`importer.review.item_record`, without its extracted text: plan 5.8 has no
    per-item read endpoint, only the list)."""

    id: int
    kind: str
    status: str
    message: str
    file_name: str | None
    transaction: TransactionBrief | None
    fields: dict[str, Any]
    created_at: str
    resolved_at: str | None
    resolution: dict[str, Any] | None


class HoldingChange(BaseModel):
    """One ISIN's quantity before and after an import (`importer.reconcile.HoldingChange`)."""

    isin: str
    name: str | None
    before: str
    after: str
    change: str


class ConfirmedRow(BaseModel):
    """One ISIN compared with the holdings you confirmed (`importer.reconcile.ConfirmedRow`)."""

    isin: str
    name: str | None
    as_of: str
    computed: str
    confirmed: str
    difference: str
    status: str


class ConfirmedComparison(BaseModel):
    """The computed holdings against the ones you confirmed (`importer.reconcile.ConfirmedComparison`)."""

    rows: list[ConfirmedRow]
    counts: dict[str, int]
    all_match: bool
    message: str


class DayValueRecord(BaseModel):
    """One day of the value series (`valuation.portfolio.day_record`); also the latest value shown
    on `GET /portfolio` and the summary after an accept."""

    date: str
    value_eur: str | None
    cost_basis_eur: str | None
    complete: bool
    flags: list[str]


# --- Imports: stage, diff, confirmed holdings, accept, discard ------------------------------


class ImportDiffResponse(BaseModel):
    """The diff of one batch (`importer.reconcile.ReconciliationDiff.to_dict`): `POST` and `GET
    /portfolio/imports/{id}`, and the confirmed-holdings upload."""

    batch: BatchRef
    as_of: str
    counts: dict[str, int]
    files: list[dict[str, Any]]
    new: list[dict[str, Any]]
    merged: list[dict[str, Any]]
    already_known: list[dict[str, Any]]
    held_back: list[dict[str, Any]]
    completed: list[dict[str, Any]]
    review_new: list[ReviewItem]
    review_closed: list[ReviewItem]
    holdings: list[HoldingChange]
    confirmed: ConfirmedComparison | None


class ConfirmedHoldingRow(BaseModel):
    """One row of the JSON form of the confirmed-holdings upload (the CSV form is `importer.confirmed_csv`)."""

    isin: str
    quantity: str
    as_of: str


class ConfirmedHoldingsRequest(BaseModel):
    """The JSON body of `POST /portfolio/imports/{id}/confirmed-holdings` (the alternative to a CSV file)."""

    as_of: str | None = None
    rows: list[ConfirmedHoldingRow] = Field(default_factory=list)


class ValueSummary(BaseModel):
    """A rebuilt value series, summarised (`valuation.portfolio.ValueReport.summary`): part of accept."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    days: int
    complete_days: int
    latest: DayValueRecord | None


class AcceptResponse(BaseModel):
    """What accepting a batch did (`importer.pipeline.AcceptResult.to_dict`)."""

    batch: BatchRef
    accepted: int
    held_back: int
    released: int
    review_closed: list[ReviewItem]
    review_opened: list[ReviewItem]
    lots: int
    disposals: int
    open_review_items: int
    last_import_at: str
    values: ValueSummary | None


class DiscardResponse(BaseModel):
    """What discarding a batch did (`importer.pipeline.DiscardResult.to_dict`)."""

    batch: BatchRef
    removed_transactions: int
    review_closed: int
    portfolio_id: int


# --- Portfolio summary, holdings, transactions, lots, value, review -------------------------


class PortfolioSummary(BaseModel):
    name: str
    base_currency: str
    source: str


class Reminder(BaseModel):
    """The 30-day import reminder (spec 3.10, AC16)."""

    due: bool
    days_since_last_import: int | None
    message: str


class TransactionCounts(BaseModel):
    accepted: int
    held: int
    staged: int


class PortfolioResponse(BaseModel):
    """`GET /portfolio` (`importer.pipeline.portfolio_status`): the same summary `pg status` shows,
    including the reminder (AC16)."""

    portfolio: PortfolioSummary
    today: str
    last_import_at: str | None
    reminder: Reminder
    staged_batch: int | None
    open_review_items: int
    transactions: TransactionCounts
    latest_value: DayValueRecord | None


class PriceEvidence(BaseModel):
    close: str
    date: str | None
    currency: str | None
    source: str | None
    symbol: str | None
    adjustment: str | None


class FxEvidence(BaseModel):
    currency: str | None
    rate: str
    date: str | None


class MappingInfo(BaseModel):
    status: str
    source: str | None
    symbol: str | None
    currency: str | None


class HoldingFlag(BaseModel):
    kind: str
    detail: str


class HoldingRecord(BaseModel):
    """One holding (`valuation.portfolio.holding_record`): quantity, cost, value and its evidence."""

    isin: str
    name: str
    quantity: str
    cost_eur: str | None
    value_eur: str | None
    pricing_quantity: str
    price: PriceEvidence | None
    fx: FxEvidence | None
    mapping: MappingInfo
    flags: list[HoldingFlag]


class HoldingsResponse(BaseModel):
    """`GET /portfolio/holdings?as_of=` (`valuation.portfolio.holdings_report`)."""

    as_of: str
    currency: str
    value_eur: str | None
    cost_basis_eur: str | None
    complete: bool
    holdings: list[HoldingRecord]


class SourceRecord(BaseModel):
    kind: str
    file_name: str
    report_key: str
    line_from: int
    line_to: int


class TransactionRecord(BaseModel):
    """One transaction with its sources (`importer.pipeline.transaction_record`, stored form)."""

    id: int
    state: str
    type: str
    isin: str | None
    name: str | None
    date: str
    ts_utc: str
    ts_local: str
    ts_precision: str
    value_date: str | None
    quantity: str | None
    price: str | None
    amount_eur: str | None
    fees_eur: str | None
    tax_eur: str | None
    tax_detail: dict[str, str]
    fx_rate: str | None
    fx_source: str
    split_new_quantity: str | None
    origin: str | None
    source_ref: str | None
    semantic_key: str
    sources: list[SourceRecord]


class TransactionsResponse(BaseModel):
    """`GET /portfolio/transactions?from=&to=&type=&limit=&offset=`."""

    count: int
    transactions: list[TransactionRecord]


class LotTxnRef(BaseModel):
    id: int


class LotRecord(BaseModel):
    """One stored lot, exactly as `pg lots --json` prints it."""

    id: int
    isin: str
    name: str
    origin: str
    booked_ts: str
    opened_ts: str
    quantity_initial: str
    quantity_open: str
    cost_eur_initial: str | None
    cost_eur_open: str | None
    cost_missing: bool
    open_transaction: LotTxnRef


class DisposalRecord(BaseModel):
    """One stored disposal, exactly as `pg lots --json` prints it."""

    id: int
    isin: str
    name: str
    kind: str
    ts_utc: str
    quantity: str
    cost_eur: str | None
    proceeds_eur: str | None
    fees_eur: str | None
    realised_eur: str | None
    lot: LotTxnRef
    transaction: LotTxnRef


class LotsResponse(BaseModel):
    """`GET /portfolio/lots`: the open lots and disposals of the stored lot book."""

    isin: str | None
    lots: list[LotRecord]
    disposals: list[DisposalRecord]


class FlagSummary(BaseModel):
    """One flag of a value series, once, with its meaning and the days it is on (`flag_summaries`)."""

    kind: str
    isin: str | None
    name: str | None
    detail: str
    first: str
    last: str
    days: int


class ValueResponse(BaseModel):
    """`GET /portfolio/value?from=&to=` (`valuation.portfolio.ValueReport.to_dict`)."""

    model_config = ConfigDict(populate_by_name=True)

    currency: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    stale_after_days: int
    days: int
    complete_days: int
    latest: DayValueRecord | None
    flags: list[FlagSummary]
    series: list[DayValueRecord]


class ReviewListResponse(BaseModel):
    """`GET /portfolio/review?status=`."""

    status: str
    count: int
    items: list[ReviewItem]


# --- Instruments -----------------------------------------------------------------------------


class InstrumentRecord(BaseModel):
    """One instrument with its mapping (`marketdata.instruments.list_instruments`, `get_instrument`)."""

    isin: str
    name: str | None
    type: str
    currency: str | None
    data_source: str | None
    data_symbol: str | None
    mapping_status: str
    mapping_updated_at: str | None
    mapping_note: str | None


class InstrumentsResponse(BaseModel):
    """`GET /instruments`."""

    instruments: list[InstrumentRecord]


class MappingRequest(BaseModel):
    """The body of `PUT /instruments/{isin}/mapping`: an explicit source, symbol and currency.

    Nothing here is ever guessed (AC10): every field but `note` is required.
    """

    source: str
    symbol: str
    currency: str
    note: str | None = None


class MappingEndpoint(BaseModel):
    source: str | None
    symbol: str | None
    currency: str | None


class MappingHistoryEntry(BaseModel):
    changed_at: str
    changed_by: str
    old: MappingEndpoint
    new: MappingEndpoint
    note: str | None = None


class MappingHistoryResponse(BaseModel):
    """`GET /instruments/{isin}/mapping-history`."""

    isin: str
    history: list[MappingHistoryEntry]


# --- Benchmarks ------------------------------------------------------------------------------


class BenchmarkRecord(BaseModel):
    id: str
    name: str
    isin: str | None
    currency: str
    data_source: str
    data_symbol: str
    also_in: list[str]


class BenchmarksResponse(BaseModel):
    """`GET /benchmarks`."""

    benchmarks: list[BenchmarkRecord]


class SeriesPointRecord(BaseModel):
    date: str
    value: str


class BenchmarkSeriesResponse(BaseModel):
    """`GET /benchmarks/{id}/series?from=&to=&currency=`."""

    model_config = ConfigDict(populate_by_name=True)

    benchmark: str
    currency: str
    from_: str = Field(alias="from")
    to: str
    points: list[SeriesPointRecord]
