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
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from playground.core.dates import SourceTime, format_ts_utc
from playground.core.types import TxnType
from playground.importer.model import ParsedTransaction, ParseResult, ReviewNeeded, SourceKind


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


def transaction_from_dict(data: Mapping[str, Any]) -> ParsedTransaction:
    """The inverse of `transaction_to_dict`: a candidate back from its JSON form (for example `fields_json`)."""
    precision = data["ts_precision"]
    if precision not in ("minute", "day"):
        raise ValueError(f"Unknown time precision {precision!r}.")
    fx_source = data["fx_source"]
    if fx_source not in ("document", "ecb", "none"):
        raise ValueError(f"Unknown FX source {fx_source!r}.")
    evidence = data["evidence"]
    return ParsedTransaction(
        type=TxnType(data["type"]),
        isin=data["isin"],
        name=data["name"],
        time=SourceTime(
            ts_utc=datetime.fromisoformat(data["ts_utc"].replace("Z", "+00:00")),
            ts_local=datetime.fromisoformat(data["ts_local"]),
            source_tz=data["source_tz"],
            precision=precision,
        ),
        value_date=date.fromisoformat(data["value_date"]) if data["value_date"] is not None else None,
        quantity=_decimal_or_none(data["quantity"]),
        price=_decimal_or_none(data["price"]),
        currency=data["currency"],
        amount=_decimal_or_none(data["amount"]),
        amount_eur=_decimal_or_none(data["amount_eur"]),
        fx_rate=_decimal_or_none(data["fx_rate"]),
        fx_source=fx_source,
        fees_eur=_decimal_or_none(data["fees_eur"]),
        tax_eur=_decimal_or_none(data["tax_eur"]),
        tax_detail={key: Decimal(value) for key, value in data["tax_detail"].items()},
        split_new_quantity=_decimal_or_none(data["split_new_quantity"]),
        origin=data["origin"],
        source_ref=data["source_ref"],
        source_kind=SourceKind(data["source_kind"]),
        evidence=(int(evidence[0]), int(evidence[1])),
    )


def _decimal_or_none(text: str | None) -> Decimal | None:
    return Decimal(text) if text is not None else None


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
