"""Tests for the value step of accept, a resolution and set-cost, and for the latest value (plan 5.3.6, 5.7).

Accept, a resolution of accepted data and `pg transfers set-cost` rebuild the lot book, and with it
the stored value series: one value per weekday from the booking date of the first accepted
transaction to the last weekday on or before today (the clock of plan 3.3), in `portfolio_values`,
with the price evidence of every holding in `holdings_snapshots`. `pg status` shows the value on the
last day of that range, worked out when you ask for it, so newer prices show at once.

The market data is given to these calls (`MarketData`): the CLI and the API read it from the data
directory. Without it the value step is left out, which is what the other pipeline tests rely on.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from playground.core.clock import FixedClock
from playground.importer.pipeline import accept_batch, portfolio_status
from playground.importer.reconcile import stored_ledger
from playground.importer.review import resolve_item
from playground.importer.transfers import set_cost
from playground.ledger.fifo import build_lots
from playground.marketdata.instruments import set_mapping
from playground.marketdata.lake import PricePoint, PriceSeries
from playground.storage.schema import holdings_snapshots, instruments, portfolio_values
from playground.valuation.portfolio import MarketData, compute_values, load_inputs, rebuild_values
from playground.valuation.series import InMemoryFx, InMemoryPrices, weekdays

SAP = "DE0007164600"
ALV = "DE0008404005"

TRANSFER_IN_ROW = "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;t-1"
DEPOSIT_ROW = "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;d-1"


def market(sap_close: str = "150.00", alv_close: str = "250.00") -> MarketData:
    """A close for SAP (Stooq, split and dividend adjusted) and ALV (a manual raw file) on every weekday of 2024."""

    def closes(value: str) -> tuple[PricePoint, ...]:
        return tuple(
            PricePoint(date=day, open=None, high=None, low=None, close=Decimal(value), volume=None)
            for day in weekdays(date(2024, 1, 1), date(2024, 12, 31))
        )

    return MarketData(
        prices=InMemoryPrices(
            [
                PriceSeries("stooq", "sap.de", "EUR", "split_dividend", closes(sap_close)),
                PriceSeries("manual", "ALV.DE", "EUR", "raw", closes(alv_close)),
            ]
        ),
        fx=InMemoryFx([]),
    )


def map_instruments(harness) -> None:
    with harness.engine.begin() as conn:
        for isin, source, symbol in ((SAP, "stooq", "sap.de"), (ALV, "manual", "ALV.DE")):
            set_mapping(
                conn, isin=isin, source=source, symbol=symbol, currency="EUR", changed_by="cli", clock=harness.clock
            )


def accept(harness, data: MarketData | None) -> dict[str, Any]:
    with harness.engine.begin() as conn:
        return accept_batch(conn, harness.staged_batch_id(), clock=harness.clock, market=data).to_dict()


def stored_values(harness) -> list[Any]:
    with harness.engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(portfolio_values)
                .where(portfolio_values.c.portfolio_id == harness.portfolio_id)
                .order_by(portfolio_values.c.date)
            ).all()
        )


def stored_snapshots(harness, day: date) -> dict[str, Any]:
    with harness.engine.connect() as conn:
        rows = conn.execute(
            sa.select(holdings_snapshots, instruments.c.isin)
            .join(instruments, instruments.c.id == holdings_snapshots.c.instrument_id)
            .where(holdings_snapshots.c.portfolio_id == harness.portfolio_id, holdings_snapshots.c.as_of == day)
        ).all()
    return {row.isin: row for row in rows}


def flag_kinds(row: Any) -> set[tuple[str, str | None]]:
    return {(flag["kind"], flag["isin"]) for flag in row.flags_json or []}


def test_accept_stores_the_value_series_over_the_default_range(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))  # 10 SAP at 140.00 on 2024-01-15
    map_instruments(harness)

    result = accept(harness, market())

    rows = stored_values(harness)
    assert [row.date for row in rows] == weekdays(date(2024, 1, 15), date(2024, 12, 31))
    last = rows[-1]
    assert (last.value_eur, last.cost_basis_eur, last.complete) == (Decimal("1500.00"), Decimal("1401.00"), 1)
    assert ("dividend_adjusted_prices", SAP) in flag_kinds(last)
    assert result["values"]["from"] == "2024-01-15"
    assert result["values"]["to"] == "2024-12-31"
    assert result["values"]["days"] == len(rows)
    assert result["values"]["latest"]["date"] == "2024-12-31"
    assert result["values"]["latest"]["value_eur"] == "1500.00"


def test_accept_stores_the_price_evidence_of_every_holding(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))
    map_instruments(harness)
    accept(harness, market())

    snapshot = stored_snapshots(harness, date(2024, 12, 31))[SAP]

    assert (snapshot.quantity, snapshot.pricing_quantity) == (Decimal("10"), Decimal("10"))
    assert (snapshot.price, snapshot.price_date, snapshot.price_currency) == (
        Decimal("150.00"),
        date(2024, 12, 31),
        "EUR",
    )
    assert (snapshot.fx_rate, snapshot.fx_date) == (None, None)
    assert (snapshot.value_eur, snapshot.cost_basis_eur) == (Decimal("1500.00"), Decimal("1401.00"))
    assert snapshot.sleeve == "core"
    assert ("dividend_adjusted_prices", SAP) in flag_kinds(snapshot)


def test_accept_without_market_data_leaves_the_value_step_out(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))

    result = accept(harness, None)

    assert result["values"] is None
    assert stored_values(harness) == []


def test_a_later_accept_replaces_the_whole_stored_series(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))
    map_instruments(harness)
    accept(harness, market())
    assert stored_values(harness)[0].date == date(2024, 1, 15)

    harness.stage(docs.csv("einzahlung.csv", [DEPOSIT_ROW]))
    accept(harness, market("160.00"))

    rows = stored_values(harness)
    assert [row.date for row in rows] == weekdays(date(2024, 1, 2), date(2024, 12, 31))
    assert rows[0].value_eur == Decimal(0)  # a deposit only: nothing held yet
    assert rows[-1].value_eur == Decimal("1600.00")


def test_set_cost_rebuilds_the_value_series_with_the_cost_you_entered(harness, docs) -> None:
    harness.stage(docs.csv("depotuebertrag.csv", [TRANSFER_IN_ROW]))
    map_instruments(harness)
    accept(harness, market())
    before = stored_values(harness)[-1]
    assert before.cost_basis_eur is None
    assert ("cost_basis_missing", ALV) in flag_kinds(before)
    assert before.value_eur == Decimal("1000.00")  # 4 x 250.00: the value does not need the cost

    with harness.engine.begin() as conn:
        result = set_cost(
            conn,
            harness.portfolio_id,
            isin=ALV,
            acquired_on=date(2020, 3, 2),
            cost_eur=Decimal("800.00"),
            clock=harness.clock,
            market=market(),
        )

    after = stored_values(harness)[-1]
    assert after.cost_basis_eur == Decimal("800.00")
    assert ("cost_basis_missing", ALV) not in flag_kinds(after)
    assert result["values"]["latest"]["cost_basis_eur"] == "800.00"


def test_a_resolution_of_accepted_data_rebuilds_the_value_series(harness, docs) -> None:
    # The document's booking does not add up, so the purchase is held back and nothing is valued.
    harness.stage(docs.trade("kauf_sap.pdf", booking="-1500.00"))
    map_instruments(harness)
    accept(harness, market())
    assert stored_values(harness) == []
    item = next(item for item in harness.items(status="open") if item.kind == "amounts_do_not_add_up")

    with harness.engine.begin() as conn:
        resolve_item(conn, item.id, how="use-parsed", clock=harness.clock, market=market())

    rows = stored_values(harness)
    assert rows[0].date == date(2024, 1, 15)
    assert rows[-1].value_eur == Decimal("1500.00")


def test_status_shows_the_value_on_the_last_weekday_on_or_before_today(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))
    map_instruments(harness)
    accept(harness, market())

    with harness.engine.connect() as conn:
        tuesday = portfolio_status(conn, harness.portfolio_id, clock=FixedClock(date(2024, 12, 31)), market=market())
        sunday = portfolio_status(conn, harness.portfolio_id, clock=FixedClock(date(2024, 12, 29)), market=market())

    assert tuesday["latest_value"]["date"] == "2024-12-31"
    assert tuesday["latest_value"]["value_eur"] == "1500.00"
    assert tuesday["latest_value"]["cost_basis_eur"] == "1401.00"
    assert tuesday["latest_value"]["complete"] is True
    assert tuesday["latest_value"]["flags"] == [f"dividend_adjusted_prices {SAP}"]
    assert sunday["latest_value"]["date"] == "2024-12-27"


def test_status_works_out_the_latest_value_from_the_prices_stored_now(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))
    map_instruments(harness)
    accept(harness, market("150.00"))

    with harness.engine.connect() as conn:
        status = portfolio_status(conn, harness.portfolio_id, clock=harness.clock, market=market("160.00"))

    assert status["latest_value"]["value_eur"] == "1600.00"


def test_status_has_no_latest_value_before_an_accept_or_without_market_data(harness, docs) -> None:
    with harness.engine.connect() as conn:
        empty = portfolio_status(conn, harness.portfolio_id, clock=harness.clock, market=market())
    assert empty["latest_value"] is None

    harness.run(docs.trade("kauf_sap.pdf"))
    with harness.engine.connect() as conn:
        without = portfolio_status(conn, harness.portfolio_id, clock=harness.clock)
    assert without["latest_value"] is None


def test_the_valuation_reads_the_same_lot_book_as_the_ledger_views(harness, docs) -> None:
    harness.run(*docs.golden_round_one())
    with harness.engine.begin() as conn:
        set_cost(
            conn,
            harness.portfolio_id,
            isin=ALV,
            acquired_on=date(2020, 3, 2),
            cost_eur=Decimal("800.00"),
            clock=harness.clock,
        )

    with harness.engine.connect() as conn:
        inputs = load_inputs(conn, harness.portfolio_id)
        ledger = build_lots(stored_ledger(conn, harness.portfolio_id))

    assert inputs.book.lots == ledger.lots
    assert inputs.book.positions == ledger.positions
    assert inputs.book.splits == ledger.splits
    assert inputs.first_booking == date(2024, 1, 2)
    assert {(trade.isin, trade.price_eur) for trade in inputs.trades} == {
        (SAP, Decimal("140.00")),
        ("IE00B4L5Y983", Decimal("80.00")),
        ("IE00B4L5Y983", Decimal("84.00")),
        ("US0378331005", Decimal("160.00")),
        ("US67066G1040", Decimal("850.00")),
        (SAP, Decimal("160.00")),
        (SAP, Decimal("175.00")),
    }
    assert inputs.instruments[SAP].name == "SAP SE"
    assert inputs.instruments[SAP].mapping_status == "unmapped"


def test_an_explicit_range_replaces_only_its_own_days(harness, docs) -> None:
    harness.stage(docs.trade("kauf_sap.pdf"))
    map_instruments(harness)
    accept(harness, market("150.00"))
    count = len(stored_values(harness))

    with harness.engine.begin() as conn:
        report = compute_values(
            conn,
            harness.portfolio_id,
            market=market("160.00"),
            clock=harness.clock,
            start=date(2024, 6, 3),
            end=date(2024, 6, 7),
        )

    assert [day.date for day in report.days] == weekdays(date(2024, 6, 3), date(2024, 6, 7))
    rows = {row.date: row.value_eur for row in stored_values(harness)}
    assert len(rows) == count
    assert rows[date(2024, 6, 5)] == Decimal("1600.00")
    assert rows[date(2024, 5, 31)] == Decimal("1500.00")
    assert rows[date(2024, 6, 10)] == Decimal("1500.00")


def test_rebuilding_with_nothing_accepted_stores_nothing(harness) -> None:
    with harness.engine.begin() as conn:
        report = rebuild_values(conn, harness.portfolio_id, market=market(), clock=harness.clock)

    assert report.days == []
    assert (report.start, report.end) == (None, None)
    assert stored_values(harness) == []
