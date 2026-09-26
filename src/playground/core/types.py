"""Shared enum types used across the registry, the parse model and the ledger (plan 4.1, 5.1)."""

from enum import StrEnum


class TxnType(StrEnum):
    """Every kind of transaction the registry stores (4.1). Values are the stored strings."""

    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    FEE = "fee"
    TAX = "tax"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    SPLIT = "split"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
