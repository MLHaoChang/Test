"""The amount check of the Trade Republic CSV parser (plan 5.3.2, 5.3.3, AC4).

Every parser checks that a transaction's numbers add up. For a CSV row of a purchase, a sale or a
dividend the check is: quantity x price, converted with `Wechselkurs` when the price is in another
currency, plus the fees and taxes for a purchase, or minus them for a sale or a dividend, gives
the booking amount (`Betrag`), within the rounding of the printed figures. A row that does not add
up is still read, together with an `amounts_do_not_add_up` item about exactly that row, and the
pipeline holds its transaction back until the item is settled. A row that lacks a figure the
check needs is not checked.

The pipeline half of the check is tested here too: which transaction the item holds back, and
when a later file supersedes it. `test_qa_p0_round1.py` has QA's own tests of the same defect.
"""

from decimal import Decimal

import pytest

from playground.importer.model import ReviewKind
from playground.importer.tr.csv_parser import parse_csv

HEADER = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"
SAP = "DE0007164600"


def csv_bytes(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


def only(items):
    assert len(items) == 1, items
    return items[0]


# --- The parser ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        # 10 x 140.00 + 1.00 fee = 1,401.00 (golden T2)
        "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;ok-1",
        # 2.5 x 80.00 = 200.00, no fee (golden T3)
        "01.02.2024;;Sparplan;IE00B4L5Y983;iShsIII-Core MSCI World U.ETF;2,5;80,00;-200,00;;;EUR;;ok-2",
        # 5.552 x 27.02 = 150.015, printed as 150.00: the rounding of a fractional quantity
        "02.09.2024;;Sparplan;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;5,552;27,02;-150,00;;;EUR;;ok-3",
        # 12 x 175.00 - 1.00 fee - 99.59 taxes = 1,999.41 (golden T12)
        "12.06.2024;11:20;Verkauf;DE0007164600;SAP SE;12;175,00;1999,41;1,00;99,59;EUR;;ok-4",
        # A sale with a tax refund: 10 x 150.00 - 1.00 fee + 5.00 refunded = 1,504.00
        "20.02.2024;10:05;Verkauf;DE0007164600;SAP SE;10;150,00;1504,00;1,00;-5,00;EUR;;ok-5",
        # 15 x 2.20 - 8.70 taxes = 24.30 (golden T9)
        "15.05.2024;;Dividende;DE0007164600;SAP SE;15;2,20;24,30;;8,70;EUR;;ok-6",
        # 5 x 0.24 USD at 1.08 USD per EUR = 1.11 EUR, less 0.17 withholding tax = 0.94 EUR (golden T10)
        "16.05.2024;;Dividende;US0378331005;Apple Inc.;5;0,24;0,94;;0,17;USD;1,0800;ok-7",
        # A distribution with nothing withheld
        "20.02.2024;;Ausschüttung;IE00B4L5Y983;iShsIII-Core MSCI World U.ETF;5;1,00;5,00;;;EUR;;ok-8",
    ],
    ids=["buy", "savings_plan", "fractional", "sell", "sell_refund", "dividend", "usd_dividend", "distribution"],
)
def test_a_row_whose_amounts_add_up_raises_no_item(row: str) -> None:
    result = parse_csv(csv_bytes(row))

    assert result.review == []
    assert len(result.transactions) == 1


def test_a_buy_that_does_not_add_up_is_read_with_an_item_about_that_row() -> None:
    good = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;r-1"
    bad = "18.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-9999,00;1,00;;EUR;;r-2"

    result = parse_csv(csv_bytes(good, bad))

    # Both rows are read: nothing is dropped, and the row that does not add up is not guessed at.
    assert [txn.amount_eur for txn in result.transactions] == [Decimal("-1401.00"), Decimal("-9999.00")]
    item = only(result.review)
    assert item.kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP
    # The item names the transaction it is about, so the pipeline holds exactly that one back.
    assert item.evidence == result.transactions[1].evidence == (3, 3)
    assert item.fields["line"] == "3"
    assert item.fields["line_text"] == bad
    assert item.fields["expected_amount"] == "-1401.00"
    # The item shows the raw row under its header, not the whole file.
    assert item.extracted_text == f"{HEADER}\n{bad}\n"
    assert item.message == (
        "Line 3: the amounts do not add up. 10 x 140.00 EUR plus 1.00 EUR fees gives a booking amount "
        "of -1401.00 EUR, but the Betrag column says -9999.00 EUR. The transaction is held back until you "
        "check it."
    )


def test_a_sell_that_does_not_add_up_names_its_fees_and_taxes() -> None:
    row = "12.06.2024;11:20;Verkauf;DE0007164600;SAP SE;12;175,00;2099,00;1,00;99,59;EUR;;r-3"

    item = only(parse_csv(csv_bytes(row)).review)

    assert item.kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP
    assert "12 x 175.00 EUR minus 1.00 EUR fees minus 99.59 EUR taxes" in item.message
    assert "gives a booking amount of 1999.41 EUR, but the Betrag column says 2099.00 EUR" in item.message


def test_fees_left_out_of_the_booking_amount_are_found() -> None:
    # An export whose Betrag leaves the fee out: exactly what the check is for (plan 10, CSV risk).
    row = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1400,00;1,00;;EUR;;r-4"

    item = only(parse_csv(csv_bytes(row)).review)

    assert item.kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP


def test_a_booking_amount_with_the_wrong_sign_is_found() -> None:
    row = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;1401,00;1,00;;EUR;;r-5"

    assert only(parse_csv(csv_bytes(row)).review).kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP


def test_a_dividend_in_another_currency_is_converted_with_the_rate_before_the_check() -> None:
    # 5 x 0.24 USD at 1.08 = 1.11 EUR, less 0.17 = 0.94 EUR; the row books 1.94 EUR.
    row = "16.05.2024;;Dividende;US0378331005;Apple Inc.;5;0,24;1,94;;0,17;USD;1,0800;r-6"

    item = only(parse_csv(csv_bytes(row)).review)

    assert item.kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP
    assert "5 x 0.24 USD at 1.0800 USD per EUR minus 0.17 EUR taxes" in item.message
    assert "gives a booking amount of 0.94 EUR, but the Betrag column says 1.94 EUR" in item.message


def test_the_rounding_of_a_fractional_quantity_has_a_limit() -> None:
    # 5.552 x 27.02 = 150.015: 150.00 is within the rounding of the printed figures, 151.00 is not.
    row = "02.09.2024;;Sparplan;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;5,552;27,02;-151,00;;;EUR;;r-7"

    assert only(parse_csv(csv_bytes(row)).review).kind is ReviewKind.AMOUNTS_DO_NOT_ADD_UP


@pytest.mark.parametrize(
    "row",
    [
        # No price: nothing to multiply.
        "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;;-1401,00;1,00;;EUR;;n-1",
        # A price in US dollars with no rate: nothing to convert it with.
        "16.05.2024;;Dividende;US0378331005;Apple Inc.;5;0,24;0,94;;0,17;USD;;n-2",
        # A dividend with the amount only, as an account statement gives it.
        "15.05.2024;;Dividende;DE0007164600;SAP SE;;;24,30;;8,70;EUR;;n-3",
        # No booking amount: the pipeline holds this one back as a missing field instead.
        "20.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;;1,00;;EUR;;n-4",
        # Types without a price are never checked.
        "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;n-5",
        "10.09.2024;;Steuer;DE0007164600;SAP SE;;;-12,50;;12,50;EUR;;n-6",
    ],
    ids=["no_price", "no_rate", "amount_only", "no_amount", "deposit", "tax"],
)
def test_a_row_without_the_figures_the_check_needs_is_not_checked(row: str) -> None:
    result = parse_csv(csv_bytes(row))

    assert result.review == []
    assert len(result.transactions) == 1


# --- The pipeline -------------------------------------------------------------------------------

# Says the fee was 1.00, but books 1,402.00: one euro more than 10 x 140.00 + 1.00.
OFF_BY_ONE_ROW = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1402,00;1,00;;EUR;;csv-0002"


def test_only_the_row_that_does_not_add_up_is_held_back(harness, docs) -> None:
    good = "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;csv-0001"
    summary = harness.stage(docs.csv("export.csv", [good, OFF_BY_ONE_ROW]))

    states = {txn.type: txn.state for txn in harness.transactions()}
    assert states == {"deposit": "staged", "buy": "held"}
    item = only(harness.items())
    assert item.kind == "amounts_do_not_add_up"
    assert item.status == "open"
    assert item.file_name == "export.csv"
    buy = only([txn for txn in harness.transactions() if txn.type == "buy"])
    assert item.transaction_id == buy.id
    assert summary.counts["held_back"] == 1
    assert summary.counts["review_new"] == 1
    assert only(summary.data["held_back"])["reasons"] == ["amounts_do_not_add_up"]

    harness.accept()
    assert harness.holdings() == {}


def test_use_parsed_keeps_the_row_as_the_export_states_it(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [OFF_BY_ONE_ROW]))

    harness.resolve(only(harness.items()).id, "use-parsed")

    assert only(harness.transactions()).state == "accepted"
    assert harness.holdings() == {SAP: Decimal("10")}
    assert only(harness.lot_rows()).cost_eur_initial == Decimal("1402.00")


def test_a_dismissed_item_keeps_the_row_out(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [OFF_BY_ONE_ROW]))

    harness.dismiss(only(harness.items()).id, reason="The export is wrong")

    assert only(harness.transactions()).state == "held"
    assert harness.holdings() == {}


def test_a_manual_row_for_the_same_transaction_supersedes_the_item(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [OFF_BY_ONE_ROW]))

    summary = harness.run(
        docs.manual("korrektur.csv", ["2024-01-15;10:05;buy;DE0007164600;10;140.00;EUR;-1402.00;2.00;;fee was 2.00"])
    )

    assert summary.counts["completed"] == 1
    item = only(harness.items(kind="amounts_do_not_add_up"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "superseded", "by": "korrektur.csv"}
    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.fees_eur == Decimal("2.00")


def test_the_trade_confirmation_supersedes_the_item_of_a_csv_row_imported_earlier(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [OFF_BY_ONE_ROW]))
    assert only(harness.transactions()).state == "held"

    # The document books the same 1,402.00 and adds up: its fee was 2.00. Its fields are now in use.
    summary = harness.run(docs.trade("kauf_sap.pdf", fee="2.00"))

    assert summary.counts["completed"] == 1
    item = only(harness.items(kind="amounts_do_not_add_up"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "superseded", "by": "kauf_sap.pdf"}
    txn = only(harness.transactions())
    assert txn.state == "accepted"
    assert txn.fees_eur == Decimal("2.00")
    assert harness.holdings() == {SAP: Decimal("10")}
    # The two files disagree on the fee: that is a field conflict, which holds nothing back.
    assert only(harness.items(kind="field_conflict")).status == "open"


def test_a_csv_row_that_does_not_add_up_raises_no_item_when_its_document_comes_in_the_same_batch(harness, docs) -> None:
    summary = harness.run(docs.trade("kauf_sap.pdf", fee="2.00"), docs.csv("export.csv", [OFF_BY_ONE_ROW]))

    assert summary.counts["held_back"] == 0
    assert harness.items(kind="amounts_do_not_add_up") == []
    assert only(harness.transactions()).state == "accepted"
    assert harness.holdings() == {SAP: Decimal("10")}


def test_a_csv_row_that_does_not_add_up_does_not_hold_back_a_known_document(harness, docs) -> None:
    harness.run(docs.trade("kauf_sap.pdf", fee="2.00"))

    summary = harness.run(docs.csv("export.csv", [OFF_BY_ONE_ROW]))

    assert summary.counts["review_new"] == 1  # the field conflict on the fee
    assert harness.items(kind="amounts_do_not_add_up") == []
    assert only(harness.transactions()).state == "accepted"


def test_a_re_exported_csv_with_the_same_rows_raises_the_item_only_once(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [OFF_BY_ONE_ROW]))

    # Different bytes (another row and CRLF line ends), the same row that does not add up.
    again = docs.csv("export_2.csv", ["02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;csv-0001", OFF_BY_ONE_ROW])
    summary = harness.run(type(again)(name=again.name, data=again.data.replace(b"\n", b"\r\n")))

    assert summary.counts["review_new"] == 0
    assert only(harness.items(kind="amounts_do_not_add_up")).status == "open"
    assert only([txn for txn in harness.transactions() if txn.type == "buy"]).state == "held"


def test_the_golden_csv_raises_no_amount_item(harness, docs) -> None:
    harness.stage(docs.golden("tr_transactions_2024.csv"))

    assert harness.items(kind="amounts_do_not_add_up") == []
