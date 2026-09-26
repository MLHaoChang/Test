"""Tests for importer/serialise.py: the parse model as JSON-ready data (plan 3.3, 7.3).

Golden files, the transaction sources' `fields_json` and the API all need the same form:
decimals as exact strings (never floats, never an exponent), dates in ISO form, UTC times
with "Z", local times without an offset.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

from playground.core.dates import SourceTime
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewKind, ReviewNeeded, SourceKind
from playground.importer.serialise import decimal_text, parse_result_to_dict, review_to_dict, transaction_to_dict


def _transaction(**overrides: object) -> ParsedTransaction:
    fields: dict[str, object] = {
        "type": TxnType.SELL,
        "isin": "DE0007164600",
        "name": "SAP SE",
        "time": SourceTime(
            ts_utc=datetime(2024, 6, 12, 9, 20, tzinfo=UTC),
            ts_local=datetime(2024, 6, 12, 11, 20),  # noqa: DTZ001 -- ts_local is the document's own time, no offset
            source_tz="Europe/Berlin",
            precision="minute",
        ),
        "value_date": datetime(2024, 6, 14, tzinfo=UTC).date(),
        "quantity": Decimal("12"),
        "price": Decimal("175.00"),
        "currency": "EUR",
        "amount": Decimal("1999.41"),
        "amount_eur": Decimal("1999.41"),
        "fx_rate": None,
        "fx_source": "none",
        "fees_eur": Decimal("1.00"),
        "tax_eur": Decimal("99.59"),
        "tax_detail": {"solidaritaetszuschlag": Decimal("5.19"), "kapitalertragssteuer": Decimal("94.40")},
        "split_new_quantity": None,
        "origin": "order",
        "source_ref": "c2e8-7195",
        "source_kind": SourceKind.PDF_DOCUMENT,
        "evidence": (9, 24),
    }
    fields.update(overrides)
    return ParsedTransaction(**fields)  # type: ignore[arg-type]


def test_decimal_text_is_exact_and_never_uses_an_exponent() -> None:
    assert decimal_text(Decimal("1401.00")) == "1401.00"
    assert decimal_text(Decimal("0.4534")) == "0.4534"
    assert decimal_text(Decimal("1E-7")) == "0.0000001"
    assert decimal_text(Decimal("1E+3")) == "1000"
    assert decimal_text(Decimal("-0.66")) == "-0.66"
    assert decimal_text(None) is None


def test_transaction_to_dict() -> None:
    assert transaction_to_dict(_transaction()) == {
        "type": "sell",
        "isin": "DE0007164600",
        "name": "SAP SE",
        "ts_utc": "2024-06-12T09:20:00Z",
        "ts_local": "2024-06-12T11:20:00",
        "source_tz": "Europe/Berlin",
        "ts_precision": "minute",
        "value_date": "2024-06-14",
        "quantity": "12",
        "price": "175.00",
        "currency": "EUR",
        "amount": "1999.41",
        "amount_eur": "1999.41",
        "fx_rate": None,
        "fx_source": "none",
        "fees_eur": "1.00",
        "tax_eur": "99.59",
        "tax_detail": {"kapitalertragssteuer": "94.40", "solidaritaetszuschlag": "5.19"},
        "split_new_quantity": None,
        "origin": "order",
        "source_ref": "c2e8-7195",
        "source_kind": "pdf_document",
        "evidence": [9, 24],
    }


def test_tax_detail_keys_are_sorted() -> None:
    data = transaction_to_dict(_transaction())
    assert list(data["tax_detail"]) == ["kapitalertragssteuer", "solidaritaetszuschlag"]  # type: ignore[call-overload]


def test_unknown_values_stay_null() -> None:
    data = transaction_to_dict(_transaction(value_date=None, quantity=None, price=None, tax_eur=None, source_ref=None))
    assert data["value_date"] is None
    assert data["quantity"] is None
    assert data["price"] is None
    assert data["tax_eur"] is None
    assert data["source_ref"] is None


def test_review_to_dict_sorts_fields() -> None:
    item = ReviewNeeded(kind=ReviewKind.MISSING_FIELD, message="m", extracted_text="t\n", fields={"z": "1", "a": "2"})
    data = review_to_dict(item)
    assert data == {"kind": "missing_field", "message": "m", "fields": {"a": "2", "z": "1"}, "extracted_text": "t\n"}
    assert list(data["fields"]) == ["a", "z"]  # type: ignore[call-overload]


def test_parse_result_to_dict_is_json_serialisable() -> None:
    result = ParseResult(
        doc_type="trade_confirmation",
        parser_id="tr.test",
        parser_version=1,
        transactions=[_transaction()],
        review=[ReviewNeeded(kind=ReviewKind.UNPARSED_ROW, message="m", extracted_text="t", fields={})],
        confirmed_holdings=(("DE0007164600", Decimal("3")),),
    )
    data = parse_result_to_dict(result)
    assert data["parser_id"] == "tr.test"
    assert data["parser_version"] == 1
    assert data["doc_type"] == "trade_confirmation"
    assert data["confirmed_holdings"] == [{"isin": "DE0007164600", "quantity": "3"}]
    assert json.loads(json.dumps(data)) == data
