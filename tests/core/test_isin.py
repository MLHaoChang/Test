"""Tests for ISIN validation and normalisation (core/isin.py, plan 5.1, 7.2).

The five ISINs below are the golden portfolio's real, public instrument identifiers
(plan 1.2): SAP SE, the iShares Core MSCI World UCITS ETF, Apple, NVIDIA and Allianz.
"""

import pytest

from playground.core.errors import InvalidIsinError
from playground.core.isin import is_valid_isin, normalise_isin

VALID_ISINS = [
    "DE0007164600",  # SAP SE
    "IE00B4L5Y983",  # iShares Core MSCI World UCITS ETF
    "US0378331005",  # Apple Inc.
    "US67066G1040",  # NVIDIA Corp.
    "DE0008404005",  # Allianz SE
]


@pytest.mark.parametrize("isin", VALID_ISINS)
def test_valid_isins_pass(isin: str) -> None:
    assert is_valid_isin(isin) is True


@pytest.mark.parametrize("isin", VALID_ISINS)
def test_changing_any_single_character_fails(isin: str) -> None:
    for i in range(len(isin)):
        ch = isin[i]
        new_ch = str((int(ch) + 1) % 10) if ch.isdigit() else chr((ord(ch) - ord("A") + 1) % 26 + ord("A"))
        candidate = isin[:i] + new_ch + isin[i + 1 :]
        if candidate == isin:
            continue
        assert is_valid_isin(candidate) is False, f"{candidate} (from {isin}, position {i}) should be invalid"


def test_wrong_length_is_invalid() -> None:
    assert is_valid_isin("DE000716460") is False  # 11 chars
    assert is_valid_isin("DE00071646000") is False  # 13 chars


def test_lowercase_country_code_is_invalid() -> None:
    assert is_valid_isin("de0007164600") is False


def test_empty_string_is_invalid() -> None:
    assert is_valid_isin("") is False


def test_normalise_strips_isin_prefix_and_spaces() -> None:
    assert normalise_isin("ISIN: DE0007164600") == "DE0007164600"
    assert normalise_isin("ISIN:DE0007164600") == "DE0007164600"
    assert normalise_isin("  de0007164600  ") == "DE0007164600"


def test_normalise_removes_internal_spaces() -> None:
    assert normalise_isin("DE 0007164600") == "DE0007164600"


def test_normalise_raises_on_wrong_check_digit() -> None:
    with pytest.raises(InvalidIsinError):
        normalise_isin("DE0007164601")


def test_normalise_raises_on_garbage() -> None:
    with pytest.raises(InvalidIsinError):
        normalise_isin("not an isin")
