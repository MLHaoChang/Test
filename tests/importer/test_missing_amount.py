"""A transaction that moves cash but has no booking amount (plan 5.3.4, 5.3.5, AC4).

Nothing is booked without its amount: a purchase or sale would open or close a lot with an unknown
cost or proceeds, and a dividend, interest, deposit, withdrawal, fee or tax booking would be a
booking of nothing. So the pipeline holds such a transaction back with a `missing_field` item,
whatever file it came from. Its semantic key has no amount (`?`), so a later file that gives the
amount cannot merge into it: that file adds the transaction as one of its own, and you dismiss the
item. `test_qa_p0_round1.py` has QA's own tests of the same defect.
"""

from datetime import date
from decimal import Decimal

import pytest

from playground.core.types import TxnType
from playground.importer.merge import missing_fields
from playground.importer.model import ParsedTransaction, SourceKind
from playground.importer.tr.layouts.common import berlin_source_time

SAP = "DE0007164600"
BUY_WITHOUT_AMOUNT = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;;1,00;;EUR;;na-1"


def only(items):
    assert len(items) == 1, items
    return items[0]


def _txn(txn_type: TxnType, *, amount: str | None, quantity: str | None = "1") -> ParsedTransaction:
    return ParsedTransaction(
        type=txn_type,
        isin=SAP,
        name="SAP SE",
        time=berlin_source_time(date(2024, 1, 15)),
        value_date=None,
        quantity=Decimal(quantity) if quantity is not None else None,
        price=None,
        currency="EUR",
        amount=Decimal(amount) if amount is not None else None,
        amount_eur=Decimal(amount) if amount is not None else None,
        fx_rate=None,
        fx_source="none",
        fees_eur=None,
        tax_eur=None,
        tax_detail={},
        split_new_quantity=Decimal("20") if txn_type is TxnType.SPLIT else None,
        origin=None,
        source_ref=None,
        source_kind=SourceKind.CSV_EXPORT,
        evidence=(2, 2),
    )


@pytest.mark.parametrize(
    "txn_type",
    [
        TxnType.BUY,
        TxnType.SELL,
        TxnType.DIVIDEND,
        TxnType.INTEREST,
        TxnType.FEE,
        TxnType.TAX,
        TxnType.DEPOSIT,
        TxnType.WITHDRAWAL,
    ],
)
def test_every_type_that_moves_cash_needs_its_amount(txn_type: TxnType) -> None:
    assert "amount" in missing_fields(_txn(txn_type, amount=None))
    assert "amount" not in missing_fields(_txn(txn_type, amount="-1.00"))


@pytest.mark.parametrize("txn_type", [TxnType.SPLIT, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT])
def test_a_type_without_cash_needs_no_amount(txn_type: TxnType) -> None:
    assert missing_fields(_txn(txn_type, amount=None)) == []


def test_a_csv_buy_without_an_amount_is_held_back_and_says_what_to_do(harness, docs) -> None:
    summary = harness.stage(docs.csv("export.csv", [BUY_WITHOUT_AMOUNT]))

    txn = only(harness.transactions())
    assert txn.state == "held"
    item = only(harness.items())
    assert item.kind == "missing_field"
    assert item.fields == {"missing": "amount"}
    assert item.transaction_id == txn.id
    assert item.message == (
        "The purchase of SAP SE (DE0007164600) on 2024-01-15 has no booking amount, so nothing can be booked "
        "for it. It is held back from your holdings. Import the document for it, or a manual CSV row with the "
        "amount: that adds the transaction with its amount. Then dismiss this item."
    )
    assert only(summary.data["held_back"])["reasons"] == ["missing_field"]

    harness.accept()
    assert harness.holdings() == {}
    assert harness.lot_rows() == []


def test_a_manual_row_without_an_amount_is_held_back_too(harness, docs) -> None:
    harness.stage(docs.manual("manuell.csv", ["2024-01-15;10:05;buy;DE0007164600;10;140.00;EUR;;1.00;;no amount"]))

    assert only(harness.transactions()).state == "held"
    assert only(harness.items()).fields == {"missing": "amount"}


def test_a_deposit_without_an_amount_is_held_back(harness, docs) -> None:
    harness.stage(docs.csv("export.csv", ["02.01.2024;;Einzahlung;;;;;;;;EUR;;na-2"]))

    assert only(harness.transactions()).state == "held"
    assert only(harness.items()).message.startswith("The deposit on 2024-01-02 has no booking amount")


def test_use_parsed_is_refused_for_a_transaction_without_an_amount(harness, docs) -> None:
    from playground.importer.review import ReviewError

    harness.run(docs.csv("export.csv", [BUY_WITHOUT_AMOUNT]))

    with pytest.raises(ReviewError, match="still lacks the booking amount"):
        harness.resolve(only(harness.items()).id, "use-parsed")


def test_the_document_with_the_amount_comes_in_as_its_own_transaction(harness, docs) -> None:
    harness.run(docs.csv("export.csv", [BUY_WITHOUT_AMOUNT]))
    harness.dismiss(only(harness.items()).id, reason="The trade confirmation has the amount")

    harness.run(docs.trade("kauf_sap.pdf", day=date(2024, 1, 15)))

    states = sorted((txn.state, txn.amount_eur) for txn in harness.transactions())
    assert states == [("accepted", Decimal("-1401.00")), ("held", None)]
    # Only the document's purchase counts: the row without an amount stays out, it is not counted twice.
    assert harness.holdings() == {SAP: Decimal("10")}
    assert only(harness.lot_rows()).cost_eur_initial == Decimal("1401.00")
