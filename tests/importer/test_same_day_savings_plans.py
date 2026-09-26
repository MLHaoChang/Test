"""Two savings plan executions for the same ETF, day and amount stay two transactions (plan 5.3.4, 6.2, AC5).

`tests/fixtures/idempotency/same_day_savings_plans/` holds documents A and B, identical except for
their execution numbers, a copy of A with other bytes but the same text, and `transactions.csv`,
a CSV export that lists both executions. Their semantic keys are equal. Rule b (one report of
each kind per transaction) keeps them apart: B cannot merge into the transaction that A already
reports, so it takes occurrence 2, and the CSV then fills both, in whichever order the files come.
"""

from itertools import permutations
from pathlib import Path

import pytest

SAME_DAY = Path(__file__).resolve().parents[1] / "fixtures" / "idempotency" / "same_day_savings_plans"
KEY = "buy|IE00BK5BQT80|2024-09-02|-5000|EUR"

A = "same_day_a.pdf"
B = "same_day_b.pdf"
CSV = "transactions.csv"

ORDERS = [list(order) for order in permutations([A, B])] + [list(order) for order in permutations([A, B, CSV])]


def order_id(order: list[str]) -> str:
    return "+".join(name.removesuffix(".pdf").removesuffix(".csv") for name in order)


def check_two_transactions(harness, with_csv: bool) -> None:
    txns = harness.transactions()
    assert len(txns) == 2
    assert {txn.semantic_key for txn in txns} == {KEY}
    assert sorted(txn.occurrence for txn in txns) == [1, 2]
    assert {txn.state for txn in txns} == {"accepted"}
    # Each transaction has exactly one document, and A and B are not in the same one.
    pdf_files = sorted(source.file_name for txn in txns for source in txn.sources if source.kind == "pdf_document")
    assert pdf_files == [A, B]
    for txn in txns:
        kinds = sorted(source.kind for source in txn.sources)
        assert kinds == (["csv_export", "pdf_document"] if with_csv else ["pdf_document"])
    assert sorted(txn.source_ref for txn in txns) == ["c9d4-86b3", "e1f0-2a7c"]
    assert harness.items() == []


def test_the_fixture_csv_lists_both_executions() -> None:
    lines = (SAME_DAY / CSV).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Datum;Uhrzeit;Typ;ISIN;")
    assert len(lines) == 3
    assert all(";Sparplan;IE00BK5BQT80;" in line and ";-50,00;" in line for line in lines[1:])


@pytest.mark.parametrize("order", ORDERS, ids=[order_id(order) for order in ORDERS])
def test_as_one_batch(harness, docs, order: list[str]) -> None:
    summary = harness.run(*(docs.file(SAME_DAY / name) for name in order))

    assert summary.counts["new"] == 2
    assert summary.counts["merged"] == (2 if CSV in order else 0)
    assert summary.counts["review_new"] == 0
    check_two_transactions(harness, with_csv=CSV in order)


@pytest.mark.parametrize("order", ORDERS, ids=[order_id(order) for order in ORDERS])
def test_as_one_batch_per_file(harness, docs, order: list[str]) -> None:
    for name in order:
        summary = harness.run(docs.file(SAME_DAY / name))
        assert summary.counts["review_new"] == 0

    check_two_transactions(harness, with_csv=CSV in order)


def test_importing_them_all_again_adds_nothing(harness, docs) -> None:
    files = [docs.file(SAME_DAY / name) for name in (A, B, CSV)]
    harness.run(*files)
    summary = harness.run(*files)

    assert summary.counts["duplicate_files"] == 3
    assert summary.counts["new"] == 0
    check_two_transactions(harness, with_csv=True)


def test_the_copy_of_a_with_other_bytes_but_the_same_text_merges_into_a(harness, docs) -> None:
    copy = docs.file(SAME_DAY / "same_day_a_copy.pdf")
    original = docs.file(SAME_DAY / A)
    assert copy.data != original.data

    harness.run(original, docs.file(SAME_DAY / B))
    summary = harness.run(copy)

    assert summary.counts["new"] == 0
    assert summary.counts["already_known"] == 1
    txns = harness.transactions()
    assert len(txns) == 2
    txn_a = next(txn for txn in txns if txn.source_ref == "e1f0-2a7c")
    assert txn_a.files == ("same_day_a.pdf", "same_day_a_copy.pdf")
    assert harness.items() == []
