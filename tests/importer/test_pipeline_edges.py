"""Edge cases of the import pipeline beyond the plan's list (plan 5.3.4 to 5.3.6).

- A resolution while a batch is staged: an item of the staged batch changes only that item and
  its transaction (accept checks the rest); an item of the accepted data applies at once and
  leaves the staged batch alone.
- A damaged PDF is recorded as failed with an item, and is classified again on the next import.
- The same bytes twice in one batch: the second copy is a duplicate file.
- An instrument first known from a manual row (which names no instrument) is named by its ISIN
  until a document names it.
- Discarding a batch removes the instruments only it had brought.
- The files are kept in `uploads/` once the stage is stored, so a failed import leaves no copy.
- No row takes a key and occurrence another row still holds: not a new row of a stage while a
  stored transaction moves in memory, and not a merge while a batch is staged.
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from playground.importer.keys import content_hash
from playground.importer.pipeline import InputFile
from playground.storage.schema import instruments

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEXT = FIXTURES / "tr" / "text"
SAP = "DE0007164600"
VWRL = "IE00BK5BQT80"
T2_ROW = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;csv-0002"


def only(items):
    assert len(items) == 1, items
    return items[0]


def amounts_off(docs):
    path = TEXT / "tr.wertpapierabrechnung.de.2023" / "amounts_off_by_one_euro.txt"
    return docs.pdf("amounts_off_by_one_euro.pdf", path.read_text(encoding="utf-8"))


def statement_buy(docs, day: date):
    return docs.statement("kontoauszug.pdf", [docs.statement_row(day, "Kauf", "-1401.00", isin=SAP)])


def assert_keys_hold(harness) -> None:
    """Every row's content hash is its key and occurrence, and no two rows share one."""
    txns = harness.transactions()
    assert all(txn.content_hash == content_hash(txn.semantic_key, txn.occurrence) for txn in txns)
    assert len({txn.content_hash for txn in txns}) == len(txns)


def instrument_names(harness) -> dict[str, str]:
    with harness.engine.connect() as conn:
        return {row.isin: row.name for row in conn.execute(sa.select(instruments.c.isin, instruments.c.name))}


def test_using_a_held_transaction_of_the_staged_batch_as_parsed_lets_accept_take_it(harness, docs) -> None:
    harness.stage(amounts_off(docs))
    item = only(harness.items())

    harness.resolve(item.id, "use-parsed")

    assert only(harness.transactions()).state == "staged"
    assert harness.lot_rows() == []  # nothing accepted yet
    harness.accept()
    assert only(harness.transactions()).state == "accepted"
    assert harness.holdings() == {VWRL: Decimal("5")}


def test_merging_a_possible_duplicate_of_the_staged_batch_then_discarding_restores_the_ledger(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [T2_ROW]))
    before = harness.canonical()
    harness.stage(docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16)))
    item = only(harness.items(kind="possible_duplicate"))

    harness.resolve(item.id, "merge")
    assert len(harness.transactions()) == 1  # the held document now reports the CSV's transaction

    harness.discard()

    assert harness.canonical() == before
    assert only(harness.transactions()).kinds == ("csv_export",)


def test_merging_a_possible_duplicate_of_the_staged_batch_then_accepting_gives_one_transaction(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [T2_ROW]))
    harness.stage(docs.trade("kauf_16_januar.pdf", day=date(2024, 1, 16)))
    harness.resolve(only(harness.items(kind="possible_duplicate")).id, "merge")

    harness.accept()

    txn = only(harness.transactions())
    assert txn.kinds == ("csv_export", "pdf_document")
    assert txn.day == "2024-01-16"  # the document has the higher precedence
    assert txn.state == "accepted"
    assert harness.holdings() == {SAP: Decimal("10")}
    assert harness.items(status="open") == []


def test_a_resolution_of_accepted_data_while_a_batch_is_staged_leaves_the_staged_batch_alone(harness, docs) -> None:
    harness.run(amounts_off(docs))
    held_item = only(harness.items())
    staged = harness.stage(docs.csv("export.csv", [T2_ROW]))

    harness.resolve(held_item.id, "use-parsed")

    # The accepted transaction is released at once; the staged one waits for accept.
    states = {txn.isin: txn.state for txn in harness.transactions()}
    assert states == {VWRL: "accepted", SAP: "staged"}
    assert harness.holdings() == {VWRL: Decimal("5")}
    assert harness.batch_status(staged.batch_id) == "staged"
    harness.accept()
    assert harness.holdings() == {VWRL: Decimal("5"), SAP: Decimal("10")}


def test_a_damaged_pdf_is_failed_with_an_item_and_is_classified_again_later(harness) -> None:
    damaged = InputFile(name="kaputt.pdf", data=b"%PDF-1.4\n" + b"\x00" * 64)

    summary = harness.run(damaged)

    assert only(summary.data["files"])["status"] == "failed"
    item = only(harness.items())
    assert item.kind == "unknown_layout"
    assert "could not be read" in item.message
    again = harness.run(damaged)
    assert only(again.data["files"])["status"] == "duplicate_file"
    assert only(harness.items()).status == "open"


def test_the_same_bytes_twice_in_one_batch_are_one_file_and_a_duplicate(harness, docs) -> None:
    trade = docs.trade("kauf_sap.pdf")
    copy = InputFile(name="kauf_sap_kopie.pdf", data=trade.data)

    summary = harness.run(trade, copy)

    statuses = {entry["file_name"]: (entry["status"], entry["duplicate_of"]) for entry in summary.data["files"]}
    assert statuses == {"kauf_sap.pdf": ("parsed", None), "kauf_sap_kopie.pdf": ("duplicate_file", "kauf_sap.pdf")}
    assert summary.counts["duplicate_files"] == 1
    assert only(harness.transactions()).kinds == ("pdf_document",)


def test_an_instrument_known_only_from_a_manual_row_is_named_by_its_isin_until_a_document_names_it(
    harness, docs
) -> None:
    harness.run(docs.manual("nachtrag.csv", ["2024-01-15;10:05;buy;DE0007164600;10;140.00;EUR;-1401.00;1.00;;my note"]))
    assert instrument_names(harness) == {SAP: SAP}

    harness.run(docs.csv("export.csv", [T2_ROW]))

    assert instrument_names(harness) == {SAP: "SAP SE"}


def test_discarding_a_batch_removes_the_instruments_only_it_brought(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [T2_ROW]))
    harness.stage(amounts_off(docs))
    assert set(instrument_names(harness)) == {SAP, VWRL}

    harness.discard()

    assert set(instrument_names(harness)) == {SAP}


def test_a_new_row_never_takes_the_key_and_occurrence_a_stored_row_still_holds(harness, docs) -> None:
    harness.run(statement_buy(docs, date(2024, 1, 17)))

    # The row of 15 January matches the statement line by rule c and moves its key to 15 January,
    # in memory only until accept. The identical row of 17 January is a purchase of its own. The
    # stored row still holds its key and occurrence 1, so the new one takes occurrence 2.
    row_17 = T2_ROW.replace("15.01.2024", "17.01.2024").replace("csv-0002", "csv-0003")
    summary = harness.stage(docs.csv("export.csv", [T2_ROW, row_17]))

    assert summary.counts["new"] == 1
    assert summary.counts["already_known"] == 1
    assert_keys_hold(harness)

    harness.accept()

    by_day = {txn.day: txn for txn in harness.transactions()}
    assert set(by_day) == {"2024-01-15", "2024-01-17"}
    assert by_day["2024-01-15"].kinds == ("csv_export", "pdf_statement")
    assert by_day["2024-01-17"].kinds == ("csv_export",)
    assert {txn.state for txn in by_day.values()} == {"accepted"}
    assert_keys_hold(harness)
    assert harness.holdings() == {SAP: Decimal("20")}


def test_a_merge_while_a_batch_is_staged_never_takes_the_occurrence_of_a_staged_row(harness, docs) -> None:
    # A CSV row of 14 January, then two identical trades of 16 January, each a possible duplicate of it.
    harness.run(docs.csv("export.csv", [T2_ROW.replace("15.01.2024", "14.01.2024")]))
    harness.run(docs.trade("kauf_16_januar_1.pdf", day=date(2024, 1, 16), execution="aaaa-0001"))
    first = only(harness.items(kind="possible_duplicate"))
    harness.run(docs.trade("kauf_16_januar_2.pdf", day=date(2024, 1, 16), execution="aaaa-0002"))
    # A third one is staged: it takes occurrence 3 of the key of 16 January.
    harness.stage(docs.trade("kauf_16_januar_3.pdf", day=date(2024, 1, 16), execution="aaaa-0003"))

    # The merge gives the CSV row's transaction the key of 16 January. The staged row keeps
    # occurrence 3 until accept, so the merged transaction cannot take it.
    harness.resolve(first.id, "merge")

    assert_keys_hold(harness)
    harness.accept()
    txns = harness.transactions()
    assert [txn.day for txn in txns] == ["2024-01-16"] * 3
    assert {txn.state for txn in txns} == {"accepted"}
    assert_keys_hold(harness)
    assert harness.holdings() == {SAP: Decimal("30")}


def test_the_files_are_kept_in_uploads_once_the_stage_is_stored_and_not_before(harness, docs) -> None:
    trade = docs.trade("kauf_sap.pdf")
    export = docs.csv("export.csv", [T2_ROW])
    working = harness.extract

    def broken(data: bytes) -> str:
        raise RuntimeError("the text extractor broke")

    harness.extract = broken
    with pytest.raises(RuntimeError):
        harness.stage(trade, export)
    assert list(harness.uploads_dir.iterdir()) == []
    assert harness.imports() == []

    harness.extract = working
    harness.stage(trade, export)

    trade_hash = hashlib.sha256(trade.data).hexdigest()
    export_hash = hashlib.sha256(export.data).hexdigest()
    assert sorted(path.name for path in harness.uploads_dir.iterdir()) == sorted(
        [f"{trade_hash}.pdf", f"{export_hash}.csv"]
    )
    assert (harness.uploads_dir / f"{trade_hash}.pdf").read_bytes() == trade.data
    stored = {row.file_name: row.stored_path for row in harness.imports()}
    assert stored == {"kauf_sap.pdf": f"uploads/{trade_hash}.pdf", "export.csv": f"uploads/{export_hash}.csv"}
