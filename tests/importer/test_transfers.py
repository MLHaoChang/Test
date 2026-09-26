"""Tests for `importer/transfers.py` (plan 5.3.4, 5.3.5, 5.5, WP8): `pg transfers list` and `set-cost`.

A transfer in books a lot without a cost, so its lot is `cost_missing` and the pipeline raises
`missing_cost_basis` at staging (plan 5.3.5). `set_cost` stores the cost you enter and rebuilds the
portfolio: the item is resolved as "cost entered" and the lot's cost and acquisition date follow
what you gave. `set_cost` on an ISIN with more than one transfer in needs `--txn` to say which.
"""

from datetime import date
from decimal import Decimal

import pytest

from playground.importer.transfers import TransferError, list_transfers, set_cost

ALV = "DE0008404005"
SAP = "DE0007164600"
ONE_TRANSFER = "20.06.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;4;;;;;EUR;;t-1"
OTHER_TRANSFER = "01.08.2024;;Depotübertrag eingehend;DE0008404005;Allianz SE;2;;;;;EUR;;t-2"
SAP_BUY = "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;t-3"


def only(items):
    assert len(items) == 1, items
    return items[0]


def transfer_txn_id(harness, isin: str = ALV):
    return only([txn for txn in harness.transactions() if txn.type == "transfer_in" and txn.isin == isin]).id


def transfers(harness):
    with harness.engine.begin() as conn:
        return list_transfers(conn, harness.portfolio_id)


def enter(harness, *, isin: str, acquired_on: date, cost_eur: Decimal, txn_id: int | None = None):
    with harness.engine.begin() as conn:
        return set_cost(
            conn,
            harness.portfolio_id,
            isin=isin,
            acquired_on=acquired_on,
            cost_eur=cost_eur,
            clock=harness.clock,
            txn_id=txn_id,
        )


# --- set_cost -------------------------------------------------------------------------------------


def test_set_cost_stores_the_input_resolves_missing_cost_basis_and_rebuilds_the_lots(harness, docs) -> None:
    harness.run(docs.csv("depotuebertrag.csv", [ONE_TRANSFER]))
    assert only(harness.items(kind="missing_cost_basis")).status == "open"
    txn_id = transfer_txn_id(harness)

    result = enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))

    assert result["isin"] == ALV
    assert result["transaction_id"] == txn_id
    assert result["acquired_on"] == "2020-03-02"
    assert result["cost_eur"] == "800.00"
    assert result["lots"] == 1
    assert result["disposals"] == 0

    item = only(harness.items(kind="missing_cost_basis"))
    assert item.status == "resolved"
    assert item.resolution == {"how": "cost entered"}
    lot = only(harness.lot_rows())
    assert lot.cost_missing == 0
    assert lot.cost_eur_initial == Decimal("800.00")
    assert lot.cost_eur_open == Decimal("800.00")
    # 00:00 Berlin time on 2020-03-02 (winter, CET = UTC+1) is 23:00 UTC the day before.
    assert lot.opened_ts == "2020-03-01T23:00:00Z"


def test_set_cost_on_an_isin_with_two_transfers_in_asks_for_txn(harness, docs) -> None:
    harness.run(docs.csv("depotuebertrag.csv", [ONE_TRANSFER, OTHER_TRANSFER]))
    ids = sorted(txn.id for txn in harness.transactions() if txn.type == "transfer_in")
    assert len(ids) == 2

    with pytest.raises(TransferError) as excinfo:
        enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))
    message = str(excinfo.value)
    assert "2 transfers in" in message
    assert f"transaction {ids[0]}" in message
    assert f"transaction {ids[1]}" in message
    assert "--txn" in message

    # Naming the one to set with --txn works, and only that one gets a cost.
    enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"), txn_id=ids[0])

    by_id = {record["id"]: record for record in transfers(harness)}
    assert by_id[ids[0]]["cost_basis"] == {"acquired_on": "2020-03-02", "cost_eur": "800.00"}
    assert by_id[ids[1]]["cost_basis"] is None
    # Its own missing_cost_basis item is resolved; the other transfer's item stays open.
    open_items = harness.items(kind="missing_cost_basis", status="open")
    assert len(open_items) == 1
    assert open_items[0].transaction_id == ids[1]


def test_set_cost_with_no_transfer_in_of_the_isin_is_an_error(harness, docs) -> None:
    harness.run(docs.csv("sap.csv", [SAP_BUY]))

    with pytest.raises(TransferError) as excinfo:
        enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))
    assert "no transfer in" in str(excinfo.value)
    assert ALV in str(excinfo.value)


def test_set_cost_with_a_txn_of_the_wrong_type_or_isin_is_an_error(harness, docs) -> None:
    harness.run(docs.csv("mixed.csv", [ONE_TRANSFER, SAP_BUY]))
    buy_id = only([txn for txn in harness.transactions() if txn.type == "buy"]).id
    transfer_id = transfer_txn_id(harness)

    with pytest.raises(TransferError) as excinfo:
        enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("1"), txn_id=buy_id)
    assert f"Transaction {buy_id}" in str(excinfo.value)
    assert "not a transfer in" in str(excinfo.value)

    with pytest.raises(TransferError) as excinfo:
        enter(harness, isin=SAP, acquired_on=date(2020, 3, 2), cost_eur=Decimal("1"), txn_id=transfer_id)
    assert "not a transfer in" in str(excinfo.value)

    with pytest.raises(TransferError) as excinfo:
        enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("1"), txn_id=999)
    assert "no transaction 999" in str(excinfo.value)


def test_set_cost_rejects_a_negative_cost(harness, docs) -> None:
    harness.run(docs.csv("depotuebertrag.csv", [ONE_TRANSFER]))

    with pytest.raises(TransferError) as excinfo:
        enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("-1"))
    assert "must not be negative" in str(excinfo.value)
    # Nothing was written: the item is still open.
    assert only(harness.items(kind="missing_cost_basis")).status == "open"


def test_set_cost_can_correct_an_earlier_entry(harness, docs) -> None:
    harness.run(docs.csv("depotuebertrag.csv", [ONE_TRANSFER]))

    enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))
    enter(harness, isin=ALV, acquired_on=date(2021, 4, 1), cost_eur=Decimal("850.00"))

    lot = only(harness.lot_rows())
    assert lot.cost_eur_initial == Decimal("850.00")
    # 00:00 Berlin time on 2021-04-01 (summer, CEST = UTC+2) is 22:00 UTC the day before.
    assert lot.opened_ts == "2021-03-31T22:00:00Z"


# --- list_transfers ---------------------------------------------------------------------------


def test_list_transfers_shows_quantity_and_cost_basis(harness, docs) -> None:
    harness.run(docs.csv("depotuebertrag.csv", [ONE_TRANSFER]))

    before = transfers(harness)
    assert before == [
        {
            "id": before[0]["id"],
            "isin": ALV,
            "name": "Allianz SE",
            "quantity": "4",
            "booked_on": "2024-06-20",
            "state": "accepted",
            "cost_basis": None,
        }
    ]

    enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))

    after = transfers(harness)
    assert after[0]["cost_basis"] == {"acquired_on": "2020-03-02", "cost_eur": "800.00"}


def test_a_transfer_in_still_only_staged_is_left_out_until_accepted(harness, docs) -> None:
    """`set_cost` and `list_transfers` see the accepted portfolio only, exactly as `refresh_portfolio`
    does (plan 5.3.5): a batch that is only staged is left out until you accept it."""
    harness.stage(docs.csv("depotuebertrag.csv", [ONE_TRANSFER]))

    assert transfers(harness) == []
    with pytest.raises(TransferError):
        enter(harness, isin=ALV, acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))

    harness.accept()
    assert len(transfers(harness)) == 1
