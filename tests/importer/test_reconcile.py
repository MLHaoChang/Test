"""Tests for the reconciliation diff and accept (importer/reconcile.py, pipeline.py, plan 5.3.6, AC6).

The diff lists the new, merged and already-known transactions, the review items, the holdings
before (accepted only) and after (accepted plus staged, held ones left out), computed with the
ledger's quantity timeline so splits and transfers in count, and, when you give your confirmed
holdings, the comparison per ISIN. Nothing changes until accept. Accept marks the batch accepted,
applies the closures, rebuilds the lot book from all accepted transactions and sets the time of
the last import.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from playground.importer.confirmed_csv import ConfirmedHolding, parse_confirmed_csv
from playground.importer.pipeline import BatchNotFoundError, BatchNotStagedError
from playground.importer.reconcile import render_diff_text
from playground.storage.schema import disposals, portfolios

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"


def only(items):
    assert len(items) == 1, items
    return items[0]


def holdings_table(diff) -> dict[str, tuple[str, str]]:
    return {row["isin"]: (row["before"], row["after"]) for row in diff.to_dict()["holdings"]}


def golden_confirmed() -> list[ConfirmedHolding]:
    return parse_confirmed_csv((GOLDEN / "inputs" / "confirmed_holdings_2024-12-31.csv").read_bytes())


def last_import_at(harness) -> str | None:
    with harness.engine.connect() as conn:
        return conn.execute(
            sa.select(portfolios.c.last_import_at).where(portfolios.c.id == harness.portfolio_id)
        ).scalar_one()


# --- The golden portfolio, round 1 ------------------------------------------------------------


def test_the_golden_round_one_diff_shows_five_of_five_match_before_accept(harness, docs) -> None:
    summary = harness.stage(*docs.golden_round_one())

    diff = harness.diff(summary.batch_id, confirmed=golden_confirmed(), as_of=date(2024, 12, 31))
    data = diff.to_dict()

    assert data["as_of"] == "2024-12-31"
    assert data["counts"] == summary.counts
    # Before accept nothing is held; after it, NVDA counts 20 shares (the split) and ALV 4 (the transfer in).
    assert holdings_table(diff) == {
        SAP: ("0", "3"),
        MSCIW: ("0", "5"),
        AAPL: ("0", "5"),
        NVDA: ("0", "20"),
        ALV: ("0", "4"),
    }
    confirmed = data["confirmed"]
    assert confirmed["message"] == "5 of 5 match"
    assert confirmed["all_match"] is True
    # Rows are sorted by ISIN.
    assert [
        (row["isin"], row["computed"], row["confirmed"], row["difference"], row["status"]) for row in confirmed["rows"]
    ] == [
        (SAP, "3", "3", "0", "match"),
        (ALV, "4", "4", "0", "match"),
        (MSCIW, "5", "5", "0", "match"),
        (AAPL, "5", "5", "0", "match"),
        (NVDA, "20", "20", "0", "match"),
    ]
    assert {row["isin"]: row["name"] for row in confirmed["rows"]}[NVDA] == "NVIDIA Corp."


def test_nothing_changes_until_accept(harness, docs) -> None:
    harness.stage(*docs.golden_round_one())

    assert {txn.state for txn in harness.transactions()} == {"staged"}
    assert harness.lot_rows() == []
    assert last_import_at(harness) is None

    result = harness.accept()

    assert result.batch_id == harness.latest_batch_id()
    assert {txn.state for txn in harness.transactions()} == {"accepted"}
    assert len(harness.lot_rows()) == 7
    with harness.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(disposals)).scalar_one() == 2
    assert harness.holdings() == {
        SAP: Decimal("3"),
        MSCIW: Decimal("5.0"),
        AAPL: Decimal("5"),
        NVDA: Decimal("20"),
        ALV: Decimal("4"),
    }
    assert last_import_at(harness) == "2024-12-31T11:00:00Z"


def test_holdings_before_are_the_accepted_ones_and_after_leave_held_transactions_out(harness, docs) -> None:
    harness.run(docs.trade("kauf_sap.pdf"))
    summary = harness.stage(
        docs.trade(
            "kauf_nvda.pdf", isin=NVDA, day=date(2024, 3, 20), at="15:45", quantity="2", price="850.00", execution="n-1"
        ),
        docs.statement(
            "kontoauszug.pdf",
            [docs.statement_row(date(2024, 4, 10), "Kauf", "-801.00", isin=SAP)],  # held: no quantity
        ),
    )

    diff = harness.diff(summary.batch_id)

    assert summary.counts["held_back"] == 1
    assert holdings_table(diff) == {SAP: ("10", "10"), NVDA: ("0", "2")}
    assert diff.to_dict()["confirmed"] is None


def test_the_diff_of_an_earlier_accepted_batch_compares_the_portfolio_now_without_and_with_it(harness, docs) -> None:
    first = harness.run(docs.trade("kauf_sap.pdf"))
    harness.run(
        docs.trade(
            "kauf_nvda.pdf", isin=NVDA, day=date(2024, 3, 20), at="15:45", quantity="2", price="850.00", execution="n-1"
        )
    )

    diff = harness.diff(first.batch_id)

    # The registry keeps no history, so the later batch counts on both sides, and the text says so.
    assert holdings_table(diff) == {SAP: ("0", "10"), NVDA: ("2", "2")}
    assert "(without this batch -> with it, as your portfolio is now)" in render_diff_text(diff)


def test_the_diff_uses_the_ledger_as_of_the_given_day(harness, docs) -> None:
    summary = harness.stage(*docs.golden_round_one())

    may = holdings_table(harness.diff(summary.batch_id, as_of=date(2024, 5, 31)))

    # Before the split NVDA is 2 shares; ALV was booked in on 2024-06-20.
    assert may == {SAP: ("0", "15"), MSCIW: ("0", "5"), AAPL: ("0", "5"), NVDA: ("0", "2")}


def test_as_of_defaults_to_today_from_the_clock(harness, docs) -> None:
    summary = harness.stage(docs.trade("kauf_sap.pdf"))
    assert harness.diff(summary.batch_id).to_dict()["as_of"] == "2024-12-31"


def test_the_stored_diff_lists_what_staging_found(harness, docs) -> None:
    summary = harness.stage(*docs.golden_round_one())
    data = harness.diff(summary.batch_id).to_dict()

    for key in (
        "counts",
        "files",
        "new",
        "merged",
        "already_known",
        "held_back",
        "completed",
        "review_new",
        "review_closed",
    ):
        assert data[key] == summary.data[key], key
    assert len(data["files"]) == 10
    assert len(data["new"]) == 14
    assert len(data["merged"]) == 5
    assert data["batch"]["status"] == "staged"


# --- Comparing with your confirmed holdings ---------------------------------------------------


def test_the_comparison_names_mismatches_and_positions_missing_on_either_side(harness, docs) -> None:
    summary = harness.stage(*docs.golden_round_one())
    confirmed = [
        ConfirmedHolding(isin=SAP, quantity=Decimal("4"), as_of=date(2024, 12, 31)),
        ConfirmedHolding(isin=MSCIW, quantity=Decimal("5.0"), as_of=date(2024, 12, 31)),
        ConfirmedHolding(isin="US5949181045", quantity=Decimal("1"), as_of=date(2024, 12, 31)),
    ]

    comparison = harness.diff(summary.batch_id, confirmed=confirmed, as_of=date(2024, 12, 31)).to_dict()["confirmed"]

    statuses = {
        row["isin"]: (row["computed"], row["confirmed"], row["difference"], row["status"]) for row in comparison["rows"]
    }
    assert statuses == {
        SAP: ("3", "4", "-1", "mismatch"),
        MSCIW: ("5", "5", "0", "match"),
        "US5949181045": ("0", "1", "-1", "missing_in_import"),
        AAPL: ("5", "0", "5", "missing_in_confirmed"),
        NVDA: ("20", "0", "20", "missing_in_confirmed"),
        ALV: ("4", "0", "4", "missing_in_confirmed"),
    }
    assert comparison["all_match"] is False
    assert comparison["message"] == "1 of 6 match"


def test_each_confirmed_row_is_compared_on_its_own_day(harness, docs) -> None:
    summary = harness.stage(*docs.golden_round_one())
    confirmed = [ConfirmedHolding(isin=NVDA, quantity=Decimal("2"), as_of=date(2024, 5, 31))]

    comparison = harness.diff(summary.batch_id, confirmed=confirmed, as_of=date(2024, 5, 31)).to_dict()["confirmed"]

    nvda = only([row for row in comparison["rows"] if row["isin"] == NVDA])
    assert (nvda["as_of"], nvda["computed"], nvda["status"]) == ("2024-05-31", "2", "match")


# --- Accept and discard -----------------------------------------------------------------------


def test_accepting_twice_or_discarding_an_accepted_batch_is_refused(harness, docs) -> None:
    summary = harness.run(docs.trade("kauf_sap.pdf"))

    with pytest.raises(BatchNotStagedError):
        harness.accept(summary.batch_id)
    with pytest.raises(BatchNotStagedError):
        harness.discard(summary.batch_id)
    with pytest.raises(BatchNotFoundError):
        harness.accept(9_999)


def test_accepting_a_batch_with_nothing_new_is_allowed_and_counts_as_an_import(harness, docs) -> None:
    trade = docs.trade("kauf_sap.pdf")
    harness.run(trade)
    harness.clock = type(harness.clock)(date(2025, 1, 20))

    summary = harness.run(trade)

    assert summary.counts["new"] == 0
    assert summary.counts["duplicate_files"] == 1
    assert harness.batch_status(summary.batch_id) == "accepted"
    assert last_import_at(harness) == "2025-01-20T11:00:00Z"


def test_accept_stores_the_lots_and_the_disposals_of_the_whole_ledger(harness, docs) -> None:
    harness.run(*docs.golden_round_one())

    by_isin: dict[str, list] = {}
    for row in harness.lot_rows():
        by_isin.setdefault(row.origin, []).append(row)
    assert len(by_isin["buy"]) == 6
    transfer = only(by_isin["transfer_in"])
    assert transfer.quantity_open == Decimal("4")
    assert transfer.cost_missing == 1
    with harness.engine.connect() as conn:
        rows = sorted(conn.execute(sa.select(disposals)).all(), key=lambda row: row.quantity, reverse=True)
    assert [(row.quantity, row.cost_eur, row.proceeds_eur) for row in rows] == [
        (Decimal("10"), Decimal("1401.00"), Decimal("1749.16666667")),
        (Decimal("2"), Decimal("320.40"), Decimal("349.83333333")),
    ]
