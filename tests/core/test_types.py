"""Tests for the shared TxnType enum (core/types.py, plan 4.1, 5.1)."""

from playground.core.types import TxnType


def test_values_match_registry_convention() -> None:
    # The registry stores these exact strings (plan 4.1); StrEnum members compare equal to them.
    assert TxnType.BUY == "buy"
    assert TxnType.SELL == "sell"
    assert TxnType.DIVIDEND == "dividend"
    assert TxnType.INTEREST == "interest"
    assert TxnType.FEE == "fee"
    assert TxnType.TAX == "tax"
    assert TxnType.DEPOSIT == "deposit"
    assert TxnType.WITHDRAWAL == "withdrawal"
    assert TxnType.SPLIT == "split"
    assert TxnType.TRANSFER_IN == "transfer_in"
    assert TxnType.TRANSFER_OUT == "transfer_out"


def test_has_exactly_the_documented_members() -> None:
    assert {member.value for member in TxnType} == {
        "buy",
        "sell",
        "dividend",
        "interest",
        "fee",
        "tax",
        "deposit",
        "withdrawal",
        "split",
        "transfer_in",
        "transfer_out",
    }
