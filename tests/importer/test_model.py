"""Sanity tests for the shared parse model every parser package builds on (importer/model.py, plan 5.3.1)."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from playground.core.dates import SourceTime
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind


def _sample_time() -> SourceTime:
    return SourceTime(
        ts_utc=datetime(2024, 6, 12, 9, 20, tzinfo=timezone.utc),
        ts_local=datetime(2024, 6, 12, 11, 20),
        source_tz="Europe/Berlin",
        precision="minute",
    )


def _sample_transaction(**overrides: object) -> ParsedTransaction:
    fields: dict[str, object] = {
        "type": TxnType.BUY,
        "isin": "DE0007164600",
        "name": "SAP SE",
        "time": _sample_time(),
        "value_date": None,
        "quantity": Decimal("10"),
        "price": Decimal("140.00"),
        "currency": "EUR",
        "amount": Decimal("-1401.00"),
        "amount_eur": Decimal("-1401.00"),
        "fx_rate": None,
        "fx_source": "none",
        "fees_eur": Decimal("1.00"),
        "tax_eur": None,
        "tax_detail": {},
        "split_new_quantity": None,
        "origin": "order",
        "source_ref": "ABC123",
        "source_kind": SourceKind.PDF_DOCUMENT,
        "evidence": (1, 12),
    }
    fields.update(overrides)
    return ParsedTransaction(**fields)  # type: ignore[arg-type]


def test_parsed_transaction_holds_every_field() -> None:
    txn = _sample_transaction()
    assert txn.type is TxnType.BUY
    assert txn.isin == "DE0007164600"
    assert txn.source_kind is SourceKind.PDF_DOCUMENT
    assert txn.evidence == (1, 12)


def test_parsed_transaction_is_frozen() -> None:
    txn = _sample_transaction()
    with pytest.raises(FrozenInstanceError):
        txn.quantity = Decimal("1")  # type: ignore[misc]


def test_review_needed_holds_kind_message_and_evidence() -> None:
    review = ReviewNeeded(
        kind=ReviewKind.UNKNOWN_LAYOUT,
        message="No parser recognised this document.",
        extracted_text="BUCHUNGSTAG WERTSTELLUNG ...",
        fields={},
    )
    assert review.kind is ReviewKind.UNKNOWN_LAYOUT
    assert "recognised" in review.message


def test_review_needed_is_frozen() -> None:
    review = ReviewNeeded(kind=ReviewKind.UNPARSED_ROW, message="m", extracted_text="t", fields={})
    with pytest.raises(FrozenInstanceError):
        review.message = "other"  # type: ignore[misc]


def test_parse_result_defaults_confirmed_holdings_to_empty() -> None:
    result = ParseResult(
        doc_type="trade_confirmation", parser_id="tr.test", parser_version=1, transactions=[], review=[]
    )
    assert result.confirmed_holdings == ()


def test_parse_result_holds_transactions_and_review_items() -> None:
    txn = _sample_transaction()
    review = ReviewNeeded(kind=ReviewKind.INVALID_ISIN, message="m", extracted_text="t", fields={})
    result = ParseResult(
        doc_type="trade_confirmation",
        parser_id="tr.test",
        parser_version=1,
        transactions=[txn],
        review=[review],
    )
    assert result.transactions == [txn]
    assert result.review == [review]


def test_review_kind_has_every_documented_kind() -> None:
    # The full list from plan 5.3.5.
    assert {member.value for member in ReviewKind} == {
        "unknown_layout",
        "ambiguous_layout",
        "unknown_csv_header",
        "unparsed_row",
        "missing_field",
        "amounts_do_not_add_up",
        "field_conflict",
        "possible_duplicate",
        "missing_cost_basis",
        "oversell",
        "split_unclear",
        "corporate_action",
        "invalid_isin",
    }


def test_source_kind_has_every_documented_kind() -> None:
    assert {member.value for member in SourceKind} == {
        "pdf_document",
        "csv_export",
        "pdf_statement",
        "manual_csv",
    }
