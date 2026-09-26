"""The parse model as JSON-ready data (plan 3.3, 7.3).

Golden files, the `fields_json` of each transaction source (plan 4.1) and the API all need the
parse model in one plain form, so it is defined once, here:

- a `Decimal` becomes its exact text in fixed-point form ("1401.00", "0.0000001"), never a
  float and never an exponent;
- a date becomes "YYYY-MM-DD";
- `ts_utc` becomes ISO 8601 with "Z" and `ts_local` ISO 8601 without an offset, the time as the
  document prints it (plan 3.3);
- enums become their stored strings, and mappings are sorted by key so the output is stable.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from playground.core.dates import format_ts_utc
from playground.importer.model import ParsedTransaction, ParseResult, ReviewNeeded


def decimal_text(value: Decimal | None) -> str | None:
    """Return `value` as exact fixed-point text, or `None` for `None`.

    Trailing zeros are kept exactly as they are in the value ("140.00" stays "140.00"), because
    parsed values keep the digits the document printed (plan 3.3).
    """
    if value is None:
        return None
    return format(value, "f")


def _decimal_mapping(values: Mapping[str, Decimal]) -> dict[str, str]:
    return {key: format(values[key], "f") for key in sorted(values)}


def transaction_to_dict(txn: ParsedTransaction) -> dict[str, Any]:
    """One candidate transaction (plan 5.3.1) as JSON-ready data, in the model's field order."""
    return {
        "type": txn.type.value,
        "isin": txn.isin,
        "name": txn.name,
        "ts_utc": format_ts_utc(txn.time.ts_utc),
        "ts_local": txn.time.ts_local.isoformat(),
        "source_tz": txn.time.source_tz,
        "ts_precision": txn.time.precision,
        "value_date": txn.value_date.isoformat() if txn.value_date is not None else None,
        "quantity": decimal_text(txn.quantity),
        "price": decimal_text(txn.price),
        "currency": txn.currency,
        "amount": decimal_text(txn.amount),
        "amount_eur": decimal_text(txn.amount_eur),
        "fx_rate": decimal_text(txn.fx_rate),
        "fx_source": txn.fx_source,
        "fees_eur": decimal_text(txn.fees_eur),
        "tax_eur": decimal_text(txn.tax_eur),
        "tax_detail": _decimal_mapping(txn.tax_detail),
        "split_new_quantity": decimal_text(txn.split_new_quantity),
        "origin": txn.origin,
        "source_ref": txn.source_ref,
        "source_kind": txn.source_kind.value,
        "evidence": list(txn.evidence),
    }


def review_to_dict(item: ReviewNeeded) -> dict[str, Any]:
    """One review result (plan 5.3.5) as JSON-ready data, with its fields sorted by name."""
    return {
        "kind": item.kind.value,
        "message": item.message,
        "fields": {key: item.fields[key] for key in sorted(item.fields)},
        "extracted_text": item.extracted_text,
    }


def parse_result_to_dict(result: ParseResult) -> dict[str, Any]:
    """Everything one parser run produced, as JSON-ready data."""
    return {
        "parser_id": result.parser_id,
        "parser_version": result.parser_version,
        "doc_type": result.doc_type,
        "transactions": [transaction_to_dict(txn) for txn in result.transactions],
        "review": [review_to_dict(item) for item in result.review],
        "confirmed_holdings": [
            {"isin": isin, "quantity": format(quantity, "f")} for isin, quantity in result.confirmed_holdings
        ],
    }
