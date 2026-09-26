"""Tests for the review queue (importer/review.py, plan 5.3.5, AC4).

The **review queue** lists documents, rows and conflicts the app could not handle with certainty.
Nothing in it is guessed or dropped: an unknown layout, an unknown CSV header, an unknown row
type, a missing required field and amounts that do not add up each create an item that shows the
extracted text or the raw row. A transaction an item concerns is **held back**: stored, but kept
out of lots, holdings and value until the item is settled. Every item has a `dedupe_key`, so
importing the same thing again never repeats it.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from playground.importer.pipeline import InputFile, NoFilesError, StagedBatchExistsError, UnsupportedFileError
from playground.importer.review import ReviewError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEXT = FIXTURES / "tr" / "text"
CSV_DIR = FIXTURES / "tr" / "csv"
VWRL = "IE00BK5BQT80"
ALV = "DE0008404005"
SAP = "DE0007164600"
# T13 of the golden portfolio on its own: a transfer in without a cost basis.
TRANSFER_IN_ROW = "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;csv-2024-0010"
T2_ROW = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;csv-0002"


def only(items):
    assert len(items) == 1, items
    return items[0]


def text_pdf(docs, relative: str, name: str | None = None) -> InputFile:
    path = TEXT / relative
    return docs.pdf(name or path.with_suffix(".pdf").name, path.read_text(encoding="utf-8"))


def import_status(harness, file_name: str) -> str:
    return only([row for row in harness.imports() if row.file_name == file_name]).status


# --- Every problem becomes an item with the text or the row -----------------------------------


def test_an_unknown_pdf_layout_is_an_item_with_the_extracted_text(harness, docs) -> None:
    summary = harness.stage(docs.golden("pdf/unbekannt_kosteninformation.pdf"))

    item = only(harness.items())
    assert item.kind == "unknown_layout"
    assert item.status == "open"
    assert item.file_name == "unbekannt_kosteninformation.pdf"
    assert "KOSTENINFORMATION" in item.extracted_text
    assert harness.transactions() == []
    assert import_status(harness, "unbekannt_kosteninformation.pdf") == "needs_review"
    assert summary.counts["review_new"] == 1
    assert only(summary.data["review_new"])["kind"] == "unknown_layout"


def test_an_unknown_csv_header_is_one_item_showing_the_header_and_the_first_rows(harness, docs) -> None:
    harness.stage(docs.file(CSV_DIR / "unknown_header.csv"))

    item = only(harness.items())
    assert item.kind == "unknown_csv_header"
    assert item.fields["header"].startswith("Date;Time;Type;ISIN")
    assert "2024-01-15;10:05;BUY;DE0007164600" in item.extracted_text
    assert harness.transactions() == []
    assert import_status(harness, "unknown_header.csv") == "needs_review"


def test_an_unknown_row_type_is_an_item_for_that_row_only(harness, docs) -> None:
    harness.stage(docs.file(CSV_DIR / "tr_csv_synthetic_v1" / "unknown_row_type.csv"))

    item = only(harness.items())
    assert item.kind == "unparsed_row"
    assert "Bonuszahlung" in item.fields["line_text"]
    assert item.transaction_id is None
    assert [txn.type for txn in harness.transactions()] == ["buy", "interest"]
    assert {txn.state for txn in harness.transactions()} == {"staged"}
    assert import_status(harness, "unknown_row_type.csv") == "partial"


def test_bad_manual_rows_are_items_while_the_good_rows_import(harness, docs) -> None:
    harness.stage(docs.file(FIXTURES / "manual_csv" / "bad_rows.csv"))

    kinds = sorted(item.kind for item in harness.items())
    assert kinds == ["invalid_isin", "unparsed_row", "unparsed_row", "unparsed_row"]
    assert [txn.type for txn in harness.transactions()] == ["buy", "interest"]


def test_a_missing_required_field_is_an_item_and_nothing_is_guessed(harness, docs) -> None:
    harness.stage(text_pdf(docs, "tr.wertpapierabrechnung.de.2023/truncated_trade.txt"))

    item = only(harness.items())
    assert item.kind == "missing_field"
    assert item.fields["missing"] == "settlement_total"
    assert "GESAMT" in item.extracted_text
    assert harness.transactions() == []
    assert import_status(harness, "truncated_trade.pdf") == "needs_review"


def test_a_corporate_action_is_an_item_and_no_transaction(harness, docs) -> None:
    harness.stage(text_pdf(docs, "tr.corporate_action.de/umtausch.txt"))

    item = only(harness.items())
    assert item.kind == "corporate_action"
    assert harness.transactions() == []


def test_amounts_that_do_not_add_up_hold_the_transaction_back(harness, docs) -> None:
    summary = harness.stage(text_pdf(docs, "tr.wertpapierabrechnung.de.2023/amounts_off_by_one_euro.txt"))

    txn = only(harness.transactions())
    assert txn.state == "held"
    item = only(harness.items())
    assert item.kind == "amounts_do_not_add_up"
    assert item.transaction_id == txn.id
    assert "-523,00 EUR" in item.extracted_text
    assert summary.counts["held_back"] == 1
    assert summary.counts["new"] == 1

    harness.accept()
    assert harness.holdings() == {}
    assert import_status(harness, "amounts_off_by_one_euro.pdf") == "partial"


def test_use_parsed_releases_a_held_transaction_at_once_and_rebuilds_the_lots(harness, docs) -> None:
    harness.run(text_pdf(docs, "tr.wertpapierabrechnung.de.2023/amounts_off_by_one_euro.txt"))
    item = only(harness.items())

    harness.resolve(item.id, "use-parsed")

    resolved = only(harness.items())
    assert resolved.status == "resolved"
    assert resolved.resolution == {"how": "use-parsed"}
    assert only(harness.transactions()).state == "accepted"
    assert harness.holdings() == {VWRL: Decimal("5")}
    lot = only(harness.lot_rows())
    assert lot.cost_eur_initial == Decimal("523.00")


def test_dismissing_an_item_that_holds_a_transaction_keeps_the_transaction_out(harness, docs) -> None:
    harness.run(text_pdf(docs, "tr.wertpapierabrechnung.de.2023/amounts_off_by_one_euro.txt"))
    item = only(harness.items())

    harness.dismiss(item.id, reason="I do not trust this document")

    dismissed = only(harness.items())
    assert dismissed.status == "dismissed"
    assert dismissed.resolution == {"how": "dismissed", "reason": "I do not trust this document"}
    assert only(harness.transactions()).state == "held"
    assert harness.holdings() == {}


def test_a_dismissed_missing_field_item_stops_holding_the_buy_once_a_file_gives_the_field(harness, docs) -> None:
    harness.run(
        docs.statement("kontoauszug.pdf", [docs.statement_row(date(2024, 1, 15), "Kauf", "-1401.00", isin=SAP)])
    )
    item = only(harness.items(kind="missing_field"))
    harness.dismiss(item.id, reason="I will import the CSV export later")
    assert only(harness.transactions()).state == "held"

    # The CSV row gives the quantity: nothing is missing any more, so nothing holds the buy back.
    summary = harness.stage(docs.csv("export.csv", [T2_ROW]))

    assert summary.counts["held_back"] == 0
    assert summary.data["held_back"] == []
    assert only(summary.data["completed"])["isin"] == SAP
    holdings = {row["isin"]: row["after"] for row in harness.diff().to_dict()["holdings"]}
    assert holdings == {SAP: "10"}

    harness.accept()

    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.quantity == Decimal("10")
    assert txn.fees_eur == Decimal("1.00")
    assert txn.batch_id == summary.batch_id  # released into the batch of the file that completed it
    assert harness.holdings() == {SAP: Decimal("10")}
    # You dismissed the item, so it stays dismissed.
    assert only(harness.items(kind="missing_field")).status == "dismissed"


def test_a_dismissed_amounts_item_stops_holding_once_a_manual_row_sets_the_figures(harness, docs) -> None:
    harness.run(text_pdf(docs, "tr.wertpapierabrechnung.de.2023/amounts_off_by_one_euro.txt"))
    harness.dismiss(only(harness.items()).id, reason="I do not trust this document")

    # Your manual row now gives the fields in use (plan 5.3.5), so the document's numbers no longer count.
    harness.run(
        docs.manual("korrektur.csv", ["2024-05-08;14:02;buy;IE00BK5BQT80;5;104.20;EUR;-523.00;2.00;;the fee was 2.00"])
    )

    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.fees_eur == Decimal("2.00")
    assert harness.holdings() == {VWRL: Decimal("5")}
    assert only(harness.items(kind="amounts_do_not_add_up")).status == "dismissed"


def test_missing_cost_basis_is_raised_at_staging_for_a_transfer_in(harness, docs) -> None:
    summary = harness.stage(docs.csv("depotuebertrag.csv", [TRANSFER_IN_ROW]))

    item = only(harness.items())
    assert item.kind == "missing_cost_basis"
    assert item.status == "open"
    assert item.batch_id == summary.batch_id
    transfer = only([txn for txn in harness.transactions() if txn.type == "transfer_in"])
    assert item.transaction_id == transfer.id
    assert ALV in item.message
    assert "pg transfers set-cost" in item.message
    # It does not hold the transfer back: quantities and value stay right, only the cost is unknown.
    assert transfer.state == "staged"
    holdings = {row["isin"]: row["after"] for row in harness.diff().to_dict()["holdings"]}
    assert holdings[ALV] == "4"

    harness.accept()
    lot = only([row for row in harness.lot_rows() if row.origin == "transfer_in"])
    assert lot.cost_missing == 1


# --- Nothing is repeated ----------------------------------------------------------------------


def test_importing_the_same_files_again_raises_no_item_a_second_time(harness, docs) -> None:
    files = [
        docs.golden("pdf/unbekannt_kosteninformation.pdf"),
        docs.csv("depotuebertrag.csv", [TRANSFER_IN_ROW]),
        docs.file(CSV_DIR / "tr_csv_synthetic_v1" / "unknown_row_type.csv"),
    ]
    harness.run(*files)
    before = [(item.id, item.kind, item.status) for item in harness.items()]
    assert len(before) == 3

    summary = harness.run(*files)

    assert summary.counts["review_new"] == 0
    assert summary.counts["duplicate_files"] == 3
    assert [(item.id, item.kind, item.status) for item in harness.items()] == before


def test_the_same_bad_row_in_another_file_is_not_raised_again(harness, docs) -> None:
    bad_row = "02.05.2024;;Bonuszahlung;;;;;50,00;;;EUR;;urt-0002"
    harness.run(docs.csv("export_1.csv", [bad_row]))
    summary = harness.run(docs.csv("export_2.csv", ["01.07.2024;;Zinsen;;;;;3,12;;;EUR;;urt-0003", bad_row]))

    assert summary.counts["review_new"] == 0
    assert len(harness.items()) == 1


def test_a_dismissed_item_stays_dismissed_and_does_not_come_back(harness, docs) -> None:
    unknown = docs.golden("pdf/unbekannt_kosteninformation.pdf")
    harness.run(unknown)
    item = only(harness.items())
    harness.dismiss(item.id, reason="Only a cost information sheet, nothing to import.")

    # The same file again, and the same document as a file with other bytes.
    text = (TEXT / "unclassified" / "unknown_cost_information.txt").read_text(encoding="utf-8")
    summary = harness.run(unknown, docs.pdf("kosteninformation_kopie.pdf", text))

    assert summary.counts["review_new"] == 0
    assert only(harness.items()).status == "dismissed"


# --- Discarding a batch -----------------------------------------------------------------------


def test_items_raised_by_a_discarded_batch_are_closed_as_batch_discarded(harness, docs) -> None:
    files = [
        text_pdf(docs, "tr.wertpapierabrechnung.de.2023/amounts_off_by_one_euro.txt"),
        docs.csv("depotuebertrag.csv", [TRANSFER_IN_ROW]),
    ]
    staged = harness.stage(*files)
    assert len(harness.transactions()) == 2

    harness.discard(staged.batch_id)

    assert harness.batch_status(staged.batch_id) == "discarded"
    assert harness.transactions() == []
    items = harness.items()
    assert sorted(item.kind for item in items) == ["amounts_do_not_add_up", "missing_cost_basis"]
    for item in items:
        assert item.status == "resolved"
        assert item.resolution == {"how": "batch discarded"}

    # The discarded batch counts for nothing: the same files import again, items included.
    again = harness.stage(*files)
    assert again.counts["duplicate_files"] == 0
    assert again.counts["review_new"] == 2
    assert len(harness.items(status="open")) == 2
    assert len(harness.transactions()) == 2


# --- Refusals ---------------------------------------------------------------------------------


def test_only_one_batch_can_be_staged_at_a_time(harness, docs) -> None:
    harness.stage(docs.csv("depotuebertrag.csv", [TRANSFER_IN_ROW]))

    with pytest.raises(StagedBatchExistsError) as refused:
        harness.stage(docs.golden("pdf/2024-01-15_kauf_sap.pdf"))

    message = str(refused.value)
    assert "accept" in message
    assert "discard" in message


def test_an_import_needs_at_least_one_file(harness) -> None:
    with pytest.raises(NoFilesError):
        harness.stage()


def test_a_file_that_is_neither_pdf_nor_csv_is_refused_before_anything_is_staged(harness, docs) -> None:
    with pytest.raises(UnsupportedFileError) as refused:
        harness.stage(docs.golden("tr_transactions_2024.csv"), InputFile(name="notes.docx", data=b"PK\x03\x04"))

    assert "notes.docx" in str(refused.value)
    assert harness.imports() == []


def test_a_csv_file_that_is_not_readable_text_is_refused_before_anything_is_stored(harness, docs) -> None:
    # 0x81 and 0x8D are neither UTF-8 nor characters of Windows-1252.
    unreadable = InputFile(name="export.csv", data=b"Datum;Uhrzeit;Typ\n\x81\x8d\n")

    with pytest.raises(UnsupportedFileError, match="export.csv is not a readable CSV file") as refused:
        harness.stage(docs.trade("kauf_sap.pdf"), unreadable)

    assert "Nothing was imported" in str(refused.value)
    assert harness.imports() == []
    assert harness.transactions() == []
    assert list(harness.uploads_dir.iterdir()) == []


def test_resolutions_that_do_not_fit_the_item_are_refused(harness, docs) -> None:
    harness.run(
        docs.statement(
            "kontoauszug.pdf", [docs.statement_row(date(2024, 1, 15), "Kauf", "-1401.00", isin="DE0007164600")]
        )
    )
    missing = only(harness.items(kind="missing_field"))

    with pytest.raises(ReviewError):
        harness.resolve(missing.id, "use-parsed")  # the quantity is still unknown
    with pytest.raises(ReviewError):
        harness.resolve(missing.id, "merge")  # only for a possible duplicate
    with pytest.raises(ReviewError):
        harness.resolve(missing.id, "keep-both")

    harness.dismiss(missing.id)
    with pytest.raises(ReviewError):
        harness.dismiss(missing.id)
    with pytest.raises(ReviewError):
        harness.resolve(10_000, "use-parsed")
