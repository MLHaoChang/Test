"""Tests for matching candidates to transactions (importer/pipeline.py, plan 5.3.4).

A **candidate** is one transaction as read from one file. Candidates are matched one at a time
against the accepted, staged and held transactions of the portfolio:

- a. **Same report**: a transaction already has a source with the candidate's report key.
- b. **Same key**: a transaction with the same semantic key and no source of the candidate's
  kind yet (one report of each kind per transaction). Several: the lowest occurrence among those
  that agree on every detail both sides carry (time of day, quantity, fees), else the lowest.
- c. **Near date**: keys that differ only in the date, by up to 3 days. Merged automatically when
  they all share one date and either the candidate is a statement line or the transactions are
  known only from statement lines; any other near-date match is held back as `possible_duplicate`.
- d. **New**: a new transaction with the next free occurrence for its key.

Then each field is taken from the source with the highest precedence that has it (manual CSV 4,
PDF document 3, CSV export 2, account statement 1).
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from playground.importer.review import ReviewError

SAP = "DE0007164600"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAME_DAY = FIXTURES / "idempotency" / "same_day_savings_plans"

T2_ROW = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;csv-0002"


def only(items):
    assert len(items) == 1, items
    return items[0]


# --- Rule a: the same report ------------------------------------------------------------------


def test_rule_a_the_same_document_in_another_file_merges_into_its_transaction(harness, docs) -> None:
    # The copy has other bytes (another PDF title) but the same text, so the same execution number.
    harness.run(docs.file(SAME_DAY / "same_day_a.pdf"))
    summary = harness.run(docs.file(SAME_DAY / "same_day_a_copy.pdf"))

    assert summary.counts["already_known"] == 1
    assert summary.counts["new"] == 0
    assert only(summary.data["already_known"])["rule"] == "same_report"
    txn = only(harness.transactions())
    assert txn.kinds == ("pdf_document", "pdf_document")
    assert harness.items() == []


# --- Rule b: the same key ---------------------------------------------------------------------


def test_rule_b_merges_the_csv_row_into_the_trade_confirmation(harness, docs) -> None:
    summary = harness.run(docs.trade("kauf_sap.pdf"), docs.csv("export.csv", [T2_ROW]))

    assert summary.counts["candidates"] == 2
    assert summary.counts["new"] == 1
    assert summary.counts["merged"] == 1
    merged = only(summary.data["merged"])
    assert merged["rule"] == "same_key"
    assert merged["file_name"] == "export.csv"
    txn = only(harness.transactions())
    assert txn.kinds == ("csv_export", "pdf_document")
    assert txn.state == "accepted"
    assert txn.semantic_key == "buy|DE0007164600|2024-01-15|-140100|EUR"
    assert txn.occurrence == 1


def test_rule_b_is_one_to_one_per_source_kind(harness, docs) -> None:
    # Two notes of identical trades: the second cannot merge into the one the first already reports.
    summary = harness.run(
        docs.trade("first.pdf", execution="aaaa-0001"),
        docs.trade("second.pdf", execution="aaaa-0002"),
    )

    assert summary.counts["new"] == 2
    txns = harness.transactions()
    assert sorted(txn.occurrence for txn in txns) == [1, 2]
    assert {txn.semantic_key for txn in txns} == {"buy|DE0007164600|2024-01-15|-140100|EUR"}
    assert all(txn.kinds == ("pdf_document",) for txn in txns)

    # A CSV that lists the trade once fills the lowest occurrence; a second identical row fills the other.
    harness.run(docs.csv("export.csv", [T2_ROW, T2_ROW.replace("csv-0002", "csv-0003")]))
    assert sorted(txn.kinds for txn in harness.transactions()) == [
        ("csv_export", "pdf_document"),
        ("csv_export", "pdf_document"),
    ]


def test_rule_b_prefers_the_occurrence_that_agrees_on_the_details(harness, docs) -> None:
    harness.run(
        docs.csv(
            "export.csv",
            [T2_ROW, "15.01.2024;14:00;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;csv-0003"],
        )
    )
    by_time = {txn.ts_local: txn for txn in harness.transactions()}
    assert by_time["2024-01-15T10:05:00"].occurrence == 1
    assert by_time["2024-01-15T14:00:00"].occurrence == 2

    harness.run(docs.trade("kauf_14_uhr.pdf", at="14:00"))

    by_time = {txn.ts_local: txn for txn in harness.transactions()}
    assert by_time["2024-01-15T14:00:00"].kinds == ("csv_export", "pdf_document")
    assert by_time["2024-01-15T10:05:00"].kinds == ("csv_export",)


def test_rule_b_takes_the_lowest_occurrence_when_none_agrees(harness, docs) -> None:
    harness.run(
        docs.csv(
            "export.csv",
            [T2_ROW, "15.01.2024;11:00;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;csv-0003"],
        )
    )
    harness.run(docs.trade("kauf_15_uhr.pdf", at="15:00"))

    merged = only([txn for txn in harness.transactions() if "pdf_document" in txn.kinds])
    assert merged.occurrence == 1
    # The document has the higher precedence, so its time of day is used.
    assert merged.ts_local == "2024-01-15T15:00:00"


# --- Rule c: near dates -----------------------------------------------------------------------


def statement_buy(docs, day: date, name: str = "kontoauszug.pdf"):
    return docs.statement(name, [docs.statement_row(day, "Kauf", "-1401.00", isin=SAP)])


def test_rule_c_a_statement_line_two_days_after_the_trade_merges_when_the_statement_comes_first(harness, docs) -> None:
    harness.run(statement_buy(docs, date(2024, 1, 17)))
    held = only(harness.transactions())
    assert held.state == "held"
    assert held.day == "2024-01-17"

    summary = harness.run(docs.csv("export.csv", [T2_ROW]))

    match = only(summary.data["already_known"])
    assert match["rule"] == "near_date"
    # The diff shows both dates.
    assert match["candidate_date"] == "2024-01-15"
    assert match["matched_date"] == "2024-01-17"
    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.day == "2024-01-15"
    # The key follows the merged fields, so a later document matches exactly (rule b).
    assert txn.semantic_key == "buy|DE0007164600|2024-01-15|-140100|EUR"

    later = harness.run(docs.trade("kauf_sap.pdf"))
    assert only(later.data["already_known"])["rule"] == "same_key"
    assert only(harness.transactions()).kinds == ("csv_export", "pdf_document", "pdf_statement")
    assert harness.items(status="open") == []
    assert harness.items(kind="possible_duplicate") == []


def test_rule_c_a_statement_line_two_days_after_the_trade_merges_when_the_trade_comes_first(harness, docs) -> None:
    harness.run(docs.trade("kauf_sap.pdf"))
    summary = harness.run(statement_buy(docs, date(2024, 1, 17)))

    match = only(summary.data["already_known"])
    assert match["rule"] == "near_date"
    assert match["candidate_date"] == "2024-01-17"
    assert match["matched_date"] == "2024-01-15"
    txn = only(harness.transactions())
    assert txn.kinds == ("pdf_document", "pdf_statement")
    assert txn.day == "2024-01-15"
    assert txn.state == "accepted"
    assert harness.items() == []


def test_rule_c_both_orders_of_trade_and_late_statement_line_give_the_same_ledger(harness_factory, docs) -> None:
    first = harness_factory()
    first.run(docs.trade("kauf_sap.pdf"))
    first.run(statement_buy(docs, date(2024, 1, 17)))
    second = harness_factory()
    second.run(statement_buy(docs, date(2024, 1, 17)))
    second.run(docs.trade("kauf_sap.pdf"))

    assert first.canonical() == second.canonical()


@pytest.mark.parametrize(("days", "merges"), [(3, True), (4, False)])
def test_rule_c_reaches_three_days_and_no_further(harness, docs, days: int, merges: bool) -> None:
    harness.run(docs.trade("kauf_sap.pdf"))
    harness.run(statement_buy(docs, date(2024, 1, 15) + timedelta(days=days)))

    txns = harness.transactions()
    if merges:
        assert only(txns).kinds == ("pdf_document", "pdf_statement")
        assert harness.items() == []
    else:
        assert sorted(txn.kinds for txn in txns) == [("pdf_document",), ("pdf_statement",)]
        assert only(harness.items(kind="missing_field")).status == "open"
    assert harness.holdings() == {SAP: Decimal("10")}


def test_rule_c_any_other_near_date_match_is_held_back_as_a_possible_duplicate(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [T2_ROW]))
    summary = harness.stage(docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16)))

    assert summary.counts["new"] == 1
    assert summary.counts["held_back"] == 1
    item = only(harness.items(kind="possible_duplicate"))
    assert item.status == "open"
    held = only([txn for txn in harness.transactions() if txn.state == "held"])
    assert held.kinds == ("pdf_document",)
    assert held.day == "2024-01-16"
    assert item.transaction_id == held.id

    harness.accept()
    assert harness.holdings() == {SAP: Decimal("10")}


def test_rule_c_near_matches_on_two_different_dates_are_not_merged(harness, docs) -> None:
    harness.run(
        docs.statement(
            "kontoauszug.pdf",
            [
                docs.statement_row(date(2024, 1, 14), "Kauf", "-1401.00", isin=SAP),
                docs.statement_row(date(2024, 1, 18), "Kauf", "-1401.00", isin=SAP),
            ],
        )
    )
    harness.run(docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16)))

    assert len(harness.transactions()) == 3
    assert only(harness.items(kind="possible_duplicate")).status == "open"


@pytest.mark.parametrize("decision", ["merge", "keep-both"])
def test_a_possible_duplicate_reaches_the_same_ledger_in_both_orders_once_you_decide(
    harness_factory, docs, decision: str
) -> None:
    csv = docs.csv("export.csv", [T2_ROW])
    pdf = docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16))
    ledgers = []
    for files in ([csv, pdf], [pdf, csv]):
        harness = harness_factory()
        for file in files:
            harness.run(file)
        item = only(harness.items(kind="possible_duplicate", status="open"))
        harness.resolve(item.id, decision)
        assert harness.items(status="open") == []
        ledgers.append(harness.canonical())
        expected = {SAP: Decimal("10")} if decision == "merge" else {SAP: Decimal("20")}
        assert harness.holdings() == expected

    assert ledgers[0] == ledgers[1]


def near_trade_and_kept_csv_row(harness, docs):
    """A trade of 16 January, a CSV row of 14 January kept as a second purchase, then a trade of
    15 January near both. The trade of 16 January already has a PDF document, so the new trade
    can only be the CSV row's transaction (one report of each kind per transaction)."""
    harness.run(docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16), execution="aaaa-0016"))
    harness.run(docs.csv("export.csv", [T2_ROW.replace("15.01.2024", "14.01.2024")]))
    harness.resolve(only(harness.items(kind="possible_duplicate", status="open")).id, "keep-both")
    harness.run(docs.trade("kauf_15_januar.pdf", execution="aaaa-0015"))
    return only(harness.items(kind="possible_duplicate", status="open"))


def test_merge_takes_the_near_transaction_the_item_names_and_keeps_one_report_of_each_kind(harness, docs) -> None:
    item = near_trade_and_kept_csv_row(harness, docs)
    csv_row = only([txn for txn in harness.transactions() if txn.kinds == ("csv_export",)])
    assert item.fields["near_dates"] == "2024-01-14"
    assert "the one on 2024-01-14" in item.message

    resolved = harness.resolve(item.id, "merge")

    assert resolved["resolution"] == {"how": "merge", "into": csv_row.id}
    by_day = {txn.day: txn for txn in harness.transactions()}
    assert set(by_day) == {"2024-01-15", "2024-01-16"}
    assert by_day["2024-01-15"].id == csv_row.id
    assert by_day["2024-01-15"].kinds == ("csv_export", "pdf_document")
    assert by_day["2024-01-15"].source_ref == "aaaa-0015"
    assert by_day["2024-01-16"].kinds == ("pdf_document",)
    assert by_day["2024-01-16"].source_ref == "aaaa-0016"
    assert {txn.state for txn in harness.transactions()} == {"accepted"}
    assert harness.holdings() == {SAP: Decimal("20")}


def test_merge_into_a_transaction_that_is_no_candidate_is_refused(harness, docs) -> None:
    item = near_trade_and_kept_csv_row(harness, docs)
    harness.run(docs.csv("einzahlung.csv", ["02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;r-1"]))
    txns = harness.transactions()
    trade_16 = only([txn for txn in txns if txn.source_ref == "aaaa-0016"])
    csv_row = only([txn for txn in txns if txn.kinds == ("csv_export",) and txn.type == "buy"])
    deposit = only([txn for txn in txns if txn.type == "deposit"])
    before = harness.canonical()

    with pytest.raises(ReviewError, match="PDF document"):
        harness.resolve(item.id, "merge", into=trade_16.id)
    with pytest.raises(ReviewError, match="1 to 3 days apart"):
        harness.resolve(item.id, "merge", into=deposit.id)
    with pytest.raises(ReviewError, match="no transaction 10000"):
        harness.resolve(item.id, "merge", into=10_000)
    with pytest.raises(ReviewError, match="holds back"):
        harness.resolve(item.id, "merge", into=item.transaction_id)

    assert harness.canonical() == before
    harness.resolve(item.id, "merge", into=csv_row.id)
    assert only([txn for txn in harness.transactions() if txn.id == csv_row.id]).kinds == (
        "csv_export",
        "pdf_document",
    )


def test_merge_needs_into_when_the_item_is_near_more_than_one_transaction(harness, docs) -> None:
    harness.run(
        docs.statement(
            "kontoauszug.pdf",
            [
                docs.statement_row(date(2024, 1, 14), "Kauf", "-1401.00", isin=SAP),
                docs.statement_row(date(2024, 1, 18), "Kauf", "-1401.00", isin=SAP),
            ],
        )
    )
    harness.run(docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16)))
    item = only(harness.items(kind="possible_duplicate"))
    by_day = {txn.day: txn for txn in harness.transactions()}

    with pytest.raises(ReviewError, match="--into") as refused:
        harness.resolve(item.id, "merge")

    assert f"transaction {by_day['2024-01-14'].id} on 2024-01-14" in str(refused.value)
    assert f"transaction {by_day['2024-01-18'].id} on 2024-01-18" in str(refused.value)
    assert only(harness.items(kind="possible_duplicate")).status == "open"

    harness.resolve(item.id, "merge", into=by_day["2024-01-18"].id)

    txns = {txn.id: txn for txn in harness.transactions()}
    assert len(txns) == 2
    merged = txns[by_day["2024-01-18"].id]
    assert merged.kinds == ("pdf_document", "pdf_statement")
    assert merged.day == "2024-01-16"
    assert merged.state == "accepted"
    assert txns[by_day["2024-01-14"].id].state == "held"
    assert harness.holdings() == {SAP: Decimal("10")}


# --- Rule d: new transactions -----------------------------------------------------------------


def test_rule_d_a_new_transaction_takes_the_next_free_occurrence(harness, docs) -> None:
    deposit = "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;r-{}"
    harness.run(docs.csv("one.csv", [deposit.format(1)]))
    harness.run(docs.csv("two.csv", [deposit.format(1), deposit.format(2)]))

    txns = harness.transactions()
    assert [txn.occurrence for txn in txns] == [1, 2]
    assert len({txn.content_hash for txn in txns}) == 2


# --- Precedence -------------------------------------------------------------------------------


def test_each_field_comes_from_the_highest_precedence_source_that_has_it(harness, docs) -> None:
    harness.run(
        docs.csv("export.csv", [T2_ROW]),
        docs.trade("kauf_sap.pdf", value_day=date(2024, 1, 17)),
        statement_buy(docs, date(2024, 1, 15)),
    )
    txn = only(harness.transactions())
    assert txn.kinds == ("csv_export", "pdf_document", "pdf_statement")
    assert txn.precedence == 3
    assert txn.ts_local == "2024-01-15T10:05:00"
    assert txn.ts_precision == "minute"
    assert txn.value_date == date(2024, 1, 17)  # only the document has it
    assert txn.quantity == Decimal("10")
    assert txn.price == Decimal("140.00")
    assert txn.fees_eur == Decimal("1.00")
    assert txn.tax_eur == Decimal("0.00")  # the statement line has no tax figure; it does not blank the others
    assert txn.source_ref == "5d0a-93f1"  # the document's execution number, not the CSV reference
    assert txn.origin == "order"

    # A manual CSV row has the highest precedence: you typed it on purpose. It raises no conflict.
    harness.run(
        docs.manual("korrektur.csv", ["2024-01-15;10:05;buy;DE0007164600;10;140.00;EUR;-1401.00;1.50;;fee checked"])
    )
    txn = only(harness.transactions())
    assert txn.precedence == 4
    assert txn.fees_eur == Decimal("1.50")
    assert txn.value_date == date(2024, 1, 17)
    assert harness.items(kind="field_conflict") == []


def test_sources_of_two_kinds_that_disagree_raise_a_field_conflict_naming_both_values(harness, docs) -> None:
    harness.run(
        docs.trade("kauf_sap.pdf"),
        docs.csv("export.csv", ["15.01.2024;10:05;Kauf;DE0007164600;SAP SE;11;127,27;-1401,00;1,00;;EUR;;csv-0002"]),
    )

    txn = only(harness.transactions())
    assert txn.quantity == Decimal("10")  # the higher-precedence value is kept
    assert txn.state == "accepted"
    item = only(harness.items(kind="field_conflict"))
    assert item.status == "open"
    assert item.transaction_id == txn.id
    assert item.fields["field"] == "quantity"
    assert item.fields["pdf_document"] == "10"
    assert item.fields["csv_export"] == "11"
    assert "10" in item.message
    assert "11" in item.message


def test_between_two_files_of_the_same_kind_the_later_import_wins_without_a_review_item(harness, docs) -> None:
    harness.run(docs.csv("export_1.csv", [T2_ROW]))
    # A corrected re-export: the fee was 1.50 and the price 139.95. It still books 1,401.00, so its
    # amounts add up and it is the same report.
    corrected = T2_ROW.replace(";140,00;", ";139,95;").replace(";1,00;", ";1,50;").replace("csv-0002", "neu-0002")
    harness.run(docs.csv("export_2.csv", [corrected]))

    txn = only(harness.transactions())
    assert txn.fees_eur == Decimal("1.50")
    assert txn.price == Decimal("139.95")
    assert txn.source_ref == "neu-0002"
    assert harness.items() == []


# --- Held back: known only from a statement line ----------------------------------------------


def test_a_buy_known_only_from_a_statement_line_is_held_back_with_a_missing_field_item(harness, docs) -> None:
    summary = harness.stage(statement_buy(docs, date(2024, 1, 15)))

    assert summary.counts["held_back"] == 1
    held_entry = only(summary.data["held_back"])
    assert held_entry["isin"] == SAP
    txn = only(harness.transactions())
    assert txn.state == "held"
    assert txn.quantity is None
    assert txn.fees_eur is None
    assert txn.tax_eur is None
    item = only(harness.items(kind="missing_field"))
    assert item.status == "open"
    assert item.transaction_id == txn.id
    assert item.fields["missing"] == "quantity"
    assert "quantity" in item.message

    diff = harness.diff()
    assert diff.to_dict()["holdings"] == []
    harness.accept()
    assert harness.holdings() == {}
    assert only(harness.transactions()).state == "held"


def test_the_held_buy_is_released_when_the_csv_arrives(harness, docs) -> None:
    first = harness.run(statement_buy(docs, date(2024, 1, 15)))
    item = only(harness.items(kind="missing_field"))

    summary = harness.stage(docs.csv("export.csv", [T2_ROW]))

    # Staging only lists what accept will do.
    assert summary.counts["completed"] == 1
    assert summary.counts["review_closed"] == 1
    closed = only(summary.data["review_closed"])
    assert closed["kind"] == "missing_field"
    assert closed["resolution"] == {"how": "superseded", "by": "export.csv"}
    assert only(summary.data["completed"])["isin"] == SAP
    assert only(harness.transactions()).state == "held"
    assert only(harness.items(kind="missing_field")).status == "open"

    harness.accept()

    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.batch_id == summary.batch_id  # released into the batch of the file that completed it
    assert txn.batch_id != first.batch_id
    assert txn.quantity == Decimal("10")
    closed_item = only(harness.items(kind="missing_field"))
    assert closed_item.id == item.id
    assert closed_item.status == "resolved"
    assert closed_item.resolution == {"how": "superseded", "by": "export.csv"}
    assert harness.holdings() == {SAP: Decimal("10")}


def test_cash_lines_known_only_from_a_statement_are_kept_with_fees_and_taxes_unknown(harness, docs) -> None:
    harness.run(
        docs.statement(
            "kontoauszug.pdf",
            [
                docs.statement_row(date(2024, 1, 2), "Einzahlung", "5000.00"),
                docs.statement_row(date(2024, 5, 15), "Dividende", "24.30", isin=SAP),
            ],
        )
    )

    txns = harness.transactions()
    assert [txn.type for txn in txns] == ["deposit", "dividend"]
    for txn in txns:
        assert txn.state == "accepted"
        assert txn.fees_eur is None
        assert txn.tax_eur is None
    assert harness.items() == []


# --- One batch, any file order ----------------------------------------------------------------


def test_one_batch_gives_the_same_result_whatever_order_the_files_are_named_in(harness_factory, docs) -> None:
    files = [
        docs.csv("export.csv", [T2_ROW]),
        docs.trade("kauf_sap.pdf"),
        statement_buy(docs, date(2024, 1, 17)),
        docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16), execution="bbbb-0001"),
    ]
    results = []
    for order in (files, list(reversed(files)), [files[2], files[0], files[3], files[1]]):
        harness = harness_factory()
        harness.run(*order)
        results.append(harness.canonical())

    assert results[0] == results[1] == results[2]
