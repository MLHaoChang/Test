"""Tests for checking review items again (importer/review.py, plan 5.3.5, AC4).

Items are checked again on every stage, accept, discard and resolution. An item whose condition
no longer holds is resolved as superseded by the file whose data removed the problem, and a
transaction it held back is released into that file's batch. A stage only lists these closures
in the diff (`review_closed`); accept applies them, so a discarded batch closes nothing.

One test per row of the table in plan 5.3.5, plus the kinds that stay open until you act.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from playground.core.types import TxnType
from playground.importer.model import ParseResult
from playground.importer.pipeline import refresh_portfolio
from playground.importer.tr.classify import PARSERS
from playground.importer.tr.csv_profiles import PROFILES, CsvProfile, RowType
from playground.importer.tr.layouts.wertpapierabrechnung import WERTPAPIERABRECHNUNG_2019
from playground.storage.schema import cost_basis_inputs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEXT = FIXTURES / "tr" / "text"
CSV_DIR = FIXTURES / "tr" / "csv"
SAP = "DE0007164600"
NVDA = "US67066G1040"
ALV = "DE0008404005"
VWRL = "IE00BK5BQT80"

T2_ROW = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;csv-0002"


def only(items):
    assert len(items) == 1, items
    return items[0]


def text_pdf(docs, relative: str):
    path = TEXT / relative
    return docs.pdf(path.with_suffix(".pdf").name, path.read_text(encoding="utf-8"))


# --- missing_field ----------------------------------------------------------------------------


def test_missing_field_is_listed_at_staging_applied_at_accept_and_left_alone_by_a_discard(harness, docs) -> None:
    harness.run(
        docs.statement("kontoauszug.pdf", [docs.statement_row(date(2024, 1, 15), "Kauf", "-1401.00", isin=SAP)])
    )
    csv = docs.csv("export.csv", [T2_ROW])

    staged = harness.stage(csv)
    assert only(staged.data["review_closed"])["kind"] == "missing_field"
    harness.discard(staged.batch_id)

    # The discarded batch closed nothing and released nothing.
    assert only(harness.items(kind="missing_field")).status == "open"
    assert only(harness.transactions()).state == "held"
    assert harness.holdings() == {}

    again = harness.stage(csv)
    assert again.counts["review_closed"] == 1
    harness.accept()
    item = only(harness.items(kind="missing_field"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "superseded", "by": "export.csv"}
    assert only(harness.transactions()).state == "accepted"
    assert harness.holdings() == {SAP: Decimal("10")}


# --- amounts_do_not_add_up --------------------------------------------------------------------


def test_amounts_do_not_add_up_is_superseded_by_a_manual_row_for_the_same_transaction(harness, docs) -> None:
    harness.run(text_pdf(docs, "tr.wertpapierabrechnung.de.2023/amounts_off_by_one_euro.txt"))
    assert only(harness.transactions()).state == "held"

    # A CSV export does not supersede it: the fields in use still come from the document.
    harness.run(
        docs.csv(
            "export.csv",
            ["08.05.2024;14:02;Kauf;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;5;104,20;-523,00;1,00;;EUR;;x-1"],
        )
    )
    assert only(harness.items(kind="amounts_do_not_add_up")).status == "open"
    assert only(harness.transactions()).state == "held"

    manual = docs.manual(
        "korrektur.csv", ["2024-05-08;14:02;buy;IE00BK5BQT80;5;104.20;EUR;-523.00;2.00;;the fee was 2.00"]
    )
    summary = harness.run(manual)

    assert summary.counts["completed"] == 1
    item = only(harness.items(kind="amounts_do_not_add_up"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "superseded", "by": "korrektur.csv"}
    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.fees_eur == Decimal("2.00")
    assert txn.batch_id == summary.batch_id
    assert harness.holdings() == {VWRL: Decimal("5")}


# --- field_conflict ---------------------------------------------------------------------------


def test_field_conflict_is_superseded_by_a_manual_row_that_sets_the_field(harness, docs) -> None:
    harness.run(
        docs.trade("kauf_sap.pdf"),
        docs.csv("export.csv", ["15.01.2024;10:05;Kauf;DE0007164600;SAP SE;11;127,27;-1401,00;1,00;;EUR;;csv-0002"]),
    )
    assert only(harness.items(kind="field_conflict")).status == "open"

    harness.run(
        docs.manual("korrektur.csv", ["2024-01-15;10:05;buy;DE0007164600;10;140.00;EUR;-1401.00;1.00;;10 is right"])
    )

    item = only(harness.items(kind="field_conflict"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "superseded", "by": "korrektur.csv"}
    assert only(harness.transactions()).quantity == Decimal("10")


# --- possible_duplicate -----------------------------------------------------------------------


def test_possible_duplicate_is_superseded_when_its_near_date_match_gets_its_own_report(harness_factory, docs) -> None:
    trade = docs.trade("kauf_sap.pdf")
    first_export = docs.csv("export_1.csv", [T2_ROW.replace("15.01.2024", "16.01.2024")])
    second_export = docs.csv("export_2.csv", [T2_ROW])

    harness = harness_factory()
    harness.run(trade)
    harness.run(first_export)
    item = only(harness.items(kind="possible_duplicate"))
    held = only(harness.transactions(state="held"))
    assert held.day == "2024-01-16"

    # The other export reports the trade of 15 January itself, so the row of 16 January is no longer
    # a near-date match of it (one report of each kind per transaction): it is a transaction of its own.
    summary = harness.run(second_export)

    assert only(summary.data["review_closed"])["id"] == item.id
    assert only(summary.data["completed"])["id"] == held.id
    closed = only(harness.items(kind="possible_duplicate"))
    assert closed.status == "resolved"
    assert closed.resolution == {"how": "superseded", "by": "export_2.csv"}
    assert {txn.state for txn in harness.transactions()} == {"accepted"}
    assert harness.holdings() == {SAP: Decimal("20")}

    # The same three files as one import give the same ledger and review items.
    together = harness_factory()
    together.run(trade, first_export, second_export)
    assert together.canonical() == harness.canonical()


# --- missing_cost_basis -----------------------------------------------------------------------


def test_missing_cost_basis_is_resolved_as_cost_entered_once_a_cost_input_exists(harness, docs) -> None:
    harness.run(
        docs.csv("depotuebertrag.csv", ["20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;t-1"])
    )
    item = only(harness.items(kind="missing_cost_basis"))
    transfer = only(harness.transactions())

    # Another import does not change it: the transfer in still has no cost input.
    harness.run(docs.csv("zinsen.csv", ["01.07.2024;;Zinsen;;;;;3,12;;;EUR;;z-1"]))
    assert only(harness.items(kind="missing_cost_basis")).status == "open"

    # `pg transfers set-cost` (WP8) stores the input and then refreshes the portfolio like this.
    with harness.engine.begin() as conn:
        conn.execute(
            cost_basis_inputs.insert().values(
                transaction_id=transfer.id,
                acquired_on=date(2020, 3, 2),
                cost_eur=Decimal("800.00"),
                entered_at="2024-12-31T11:00:00Z",
                note=None,
            )
        )
        refresh_portfolio(conn, harness.portfolio_id, clock=harness.clock, cause="cost entered")

    item = only(harness.items(kind="missing_cost_basis"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "cost entered"}
    lot = only(harness.lot_rows())
    assert lot.cost_missing == 0
    assert lot.cost_eur_initial == Decimal("800.00")


# --- oversell and split_unclear ---------------------------------------------------------------


def test_oversell_is_superseded_once_the_rebuilt_lot_book_no_longer_reports_it(harness, docs) -> None:
    # The CSV export alone sells 12 SAP shares but lists only the purchase of 10 (plan 1.2: T8 is a PDF).
    harness.run(docs.golden("tr_transactions_2024.csv"))
    item = only(harness.items(kind="oversell"))
    assert item.status == "open"
    assert SAP in item.message

    summary = harness.run(docs.golden("pdf/2024-04-10_kauf_sap.pdf"))

    assert only(summary.data["review_closed"])["kind"] == "oversell"
    closed = only(harness.items(kind="oversell"))
    assert closed.status == "resolved"
    assert closed.resolution == {"how": "superseded", "by": "2024-04-10_kauf_sap.pdf"}
    assert harness.holdings()[SAP] == Decimal("3")


def test_split_unclear_is_superseded_once_the_shares_held_before_the_split_are_known(harness, docs) -> None:
    harness.run(docs.golden("pdf/2024-06-10_split_nvda.pdf"))
    item = only(harness.items(kind="split_unclear"))
    assert item.status == "open"

    harness.run(docs.golden("pdf/2024-03-20_kauf_nvda.pdf"))

    closed = only(harness.items(kind="split_unclear"))
    assert closed.status == "resolved"
    assert closed.resolution == {"how": "superseded", "by": "2024-03-20_kauf_nvda.pdf"}
    assert harness.holdings() == {NVDA: Decimal("20")}


# --- Files no parser recognised ---------------------------------------------------------------


@dataclass(frozen=True)
class CostInformationParser:
    """A parser "added since then" for the cost information sheet: it reads nothing from it."""

    parser_id: str = "test.kosteninformation"
    doc_type: str = "cost_information"
    version: int = 1

    @property
    def locale(self) -> Literal["de"]:
        return "de"

    def detect(self, text: str) -> bool:
        return "KOSTENINFORMATION" in text.splitlines()

    def parse(self, text: str) -> ParseResult:
        return ParseResult(
            doc_type=self.doc_type, parser_id=self.parser_id, parser_version=self.version, transactions=[], review=[]
        )


def test_an_unknown_file_is_classified_again_and_superseded_once_a_parser_is_added(harness, docs) -> None:
    unknown = docs.golden("pdf/unbekannt_kosteninformation.pdf")
    first = harness.run(unknown)
    item = only(harness.items(kind="unknown_layout"))

    # Classified again, still unknown: skipped like any other known file, and the item stays open.
    again = harness.run(unknown)
    assert again.counts["duplicate_files"] == 1
    assert only(again.data["files"])["status"] == "duplicate_file"
    assert only(harness.items(kind="unknown_layout")).status == "open"

    with_parser = harness.stage(unknown, parsers=(*PARSERS, CostInformationParser()))
    assert with_parser.counts["duplicate_files"] == 0
    assert only(with_parser.data["files"])["parser_id"] == "test.kosteninformation"
    assert only(with_parser.data["review_closed"])["id"] == item.id
    assert only(harness.items(kind="unknown_layout")).status == "open"  # only listed until accept

    harness.accept()

    closed = only(harness.items(kind="unknown_layout"))
    assert closed.status == "resolved"
    assert closed.resolution == {"how": "superseded", "by": "unbekannt_kosteninformation.pdf"}
    rows = {row.batch_id: row.status for row in harness.imports()}
    assert rows[first.batch_id] == "reparsed"
    assert rows[with_parser.batch_id] == "parsed"


def test_an_unknown_csv_header_is_superseded_once_a_profile_for_it_is_added(harness, docs) -> None:
    unknown = docs.file(CSV_DIR / "unknown_header.csv")
    harness.run(unknown)
    assert only(harness.items(kind="unknown_csv_header")).status == "open"

    english = CsvProfile(
        profile_id="test_english_v1",
        delimiter=";",
        locale="en",
        columns={
            "date": "Date",
            "time": "Time",
            "type": "Type",
            "isin": "ISIN",
            "name": "Name",
            "quantity": "Quantity",
            "price": "Price",
            "amount": "Amount",
            "fees": "Fees",
            "taxes": "Tax",
            "currency": "Currency",
            "fx_rate": "FXRate",
            "reference": "Reference",
        },
        row_types={"BUY": RowType(TxnType.BUY, "order")},
    )
    harness.run(unknown, csv_profiles=(*PROFILES, english))

    closed = only(harness.items(kind="unknown_csv_header"))
    assert closed.status == "resolved"
    assert closed.resolution == {"how": "superseded", "by": "unknown_header.csv"}
    txn = only(harness.transactions())
    assert (txn.type, txn.isin, txn.quantity) == ("buy", SAP, Decimal("10"))


def test_an_ambiguous_layout_is_superseded_once_only_one_parser_recognises_the_file(harness, docs) -> None:
    mixed = text_pdf(docs, "unclassified/two_layouts_mixed.txt")
    harness.run(mixed)
    assert only(harness.items(kind="ambiguous_layout")).status == "open"

    parsers = tuple(parser for parser in PARSERS if parser is not WERTPAPIERABRECHNUNG_2019)
    harness.run(mixed, parsers=parsers)

    closed = only(harness.items(kind="ambiguous_layout"))
    assert closed.status == "resolved"
    assert closed.resolution == {"how": "superseded", "by": "two_layouts_mixed.pdf"}


# --- Kinds that stay open until you act -------------------------------------------------------


def test_unparsed_rows_corporate_actions_and_invalid_isins_stay_open_until_you_act(harness, docs) -> None:
    harness.run(
        docs.file(CSV_DIR / "tr_csv_synthetic_v1" / "unknown_row_type.csv"),
        text_pdf(docs, "tr.corporate_action.de/umtausch.txt"),
        docs.csv("export.csv", ["15.03.2024;;Kauf;DE0007164601;SAP SE;1;140,00;-140,00;;;EUR;;bad-isin"]),
    )
    kinds = sorted(item.kind for item in harness.items(status="open"))
    assert kinds == ["corporate_action", "invalid_isin", "unparsed_row"]

    harness.run(
        docs.golden("pdf/2024-01-15_kauf_sap.pdf"), docs.manual("nachtrag.csv", ["2024-06-01;;interest;;;;;1.00;;;"])
    )

    assert sorted(item.kind for item in harness.items(status="open")) == kinds
