"""Shared fixtures for the ledger tests (plan 5.5, 1.2).

`golden_txns` is the golden portfolio of plan 1.2 as the ledger sees it: its fourteen
transactions T1 to T14, with ids 1 to 14, the booking time of each (Europe/Berlin, converted to
UTC) and the cost basis you enter for the transfer in T13 (4 Allianz shares acquired on
2020-03-02 for 800.00 EUR). Test modules must not import from this file (pytest may load
another `conftest` module under the same name); they ask for the fixture instead.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from playground.core.dates import berlin_to_utc
from playground.core.types import TxnType
from playground.ledger.fifo import CostInput, LedgerTxn

SAP = "DE0007164600"
MSCIW = "IE00B4L5Y983"
AAPL = "US0378331005"
NVDA = "US67066G1040"
ALV = "DE0008404005"


def _berlin(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    # A naive local time, exactly as a document prints it; berlin_to_utc attaches the zone.
    return berlin_to_utc(datetime(year, month, day, hour, minute))  # noqa: DTZ001


def _txn(
    txn_id: int,
    txn_type: TxnType,
    isin: str | None,
    ts_utc: datetime,
    *,
    quantity: str | None = None,
    amount: str | None = None,
    fees: str | None = "0.00",
    tax: str | None = "0.00",
    new_quantity: str | None = None,
    cost_input: CostInput | None = None,
) -> LedgerTxn:
    return LedgerTxn(
        id=txn_id,
        type=txn_type,
        isin=isin,
        ts_utc=ts_utc,
        quantity=None if quantity is None else Decimal(quantity),
        amount_eur=None if amount is None else Decimal(amount),
        fees_eur=None if fees is None else Decimal(fees),
        tax_eur=None if tax is None else Decimal(tax),
        split_new_quantity=None if new_quantity is None else Decimal(new_quantity),
        cost_input=cost_input,
    )


@pytest.fixture
def golden_txns() -> list[LedgerTxn]:
    """T1 to T14 of the golden portfolio (plan 1.2), in table order, ids 1 to 14."""
    alv_cost = CostInput(acquired_on=date(2020, 3, 2), cost_eur=Decimal("800.00"))
    return [
        _txn(1, TxnType.DEPOSIT, None, _berlin(2024, 1, 2), amount="5000.00"),
        _txn(2, TxnType.BUY, SAP, _berlin(2024, 1, 15, 10, 5), quantity="10", amount="-1401.00", fees="1.00"),
        _txn(3, TxnType.BUY, MSCIW, _berlin(2024, 2, 1), quantity="2.5", amount="-200.00"),
        _txn(4, TxnType.BUY, MSCIW, _berlin(2024, 3, 1), quantity="2.5", amount="-210.00"),
        _txn(5, TxnType.BUY, AAPL, _berlin(2024, 3, 12, 14, 30), quantity="5", amount="-801.00", fees="1.00"),
        _txn(6, TxnType.BUY, NVDA, _berlin(2024, 3, 20, 15, 45), quantity="2", amount="-1701.00", fees="1.00"),
        _txn(7, TxnType.DEPOSIT, None, _berlin(2024, 4, 2), amount="1000.00"),
        _txn(8, TxnType.BUY, SAP, _berlin(2024, 4, 10, 9, 12), quantity="5", amount="-801.00", fees="1.00"),
        _txn(9, TxnType.DIVIDEND, SAP, _berlin(2024, 5, 15), quantity="15", amount="24.30", tax="8.70"),
        _txn(10, TxnType.DIVIDEND, AAPL, _berlin(2024, 5, 16), quantity="5", amount="0.94", tax="0.17"),
        _txn(11, TxnType.SPLIT, NVDA, _berlin(2024, 6, 10), new_quantity="20"),
        _txn(
            12,
            TxnType.SELL,
            SAP,
            _berlin(2024, 6, 12, 11, 20),
            quantity="12",
            amount="1999.41",
            fees="1.00",
            tax="99.59",
        ),
        _txn(13, TxnType.TRANSFER_IN, ALV, _berlin(2024, 6, 20), quantity="4", cost_input=alv_cost),
        _txn(14, TxnType.INTEREST, None, _berlin(2024, 7, 1), amount="3.12"),
    ]
