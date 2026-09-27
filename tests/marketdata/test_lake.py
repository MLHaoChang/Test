"""Tests for the Parquet lake (plan 4.2, 5.6, 7.2): "lake upsert keeps one row per date"."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from playground.marketdata.lake import FxPoint, FxStore, FxTable, PricePoint, PriceSeries, PriceStore, is_stale

FETCHED_AT = datetime(2024, 12, 31, 11, 0, tzinfo=UTC)


def _series(points: list[PricePoint], *, currency: str = "EUR", adjustment: str = "split_dividend") -> PriceSeries:
    return PriceSeries(source="stooq", symbol="sap.de", currency=currency, adjustment=adjustment, points=tuple(points))


def _point(day: date, close: str) -> PricePoint:
    return PricePoint(date=day, open=None, high=None, low=None, close=Decimal(close), volume=None)


def test_upsert_then_closes_round_trips(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        _series([_point(date(2024, 1, 2), "138.00"), _point(date(2024, 1, 3), "138.50")]), fetched_at=FETCHED_AT
    )

    closes = store.closes("stooq", "sap.de")

    assert [point.date for point in closes] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert closes[0].close == Decimal("138.00")
    assert closes[1].close == Decimal("138.50")


def test_upsert_replaces_the_row_for_a_date_already_stored(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(_series([_point(date(2024, 1, 2), "138.00")]), fetched_at=FETCHED_AT)

    store.upsert(_series([_point(date(2024, 1, 2), "999.00")]), fetched_at=FETCHED_AT)

    closes = store.closes("stooq", "sap.de")
    assert len(closes) == 1
    assert closes[0].close == Decimal("999.00")


def test_upsert_merges_a_new_date_in_without_losing_older_ones(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(_series([_point(date(2024, 1, 2), "138.00")]), fetched_at=FETCHED_AT)

    store.upsert(_series([_point(date(2024, 1, 3), "139.00")]), fetched_at=FETCHED_AT)

    closes = store.closes("stooq", "sap.de")
    assert [point.date for point in closes] == [date(2024, 1, 2), date(2024, 1, 3)]


def test_closes_is_empty_for_an_unknown_symbol(tmp_path: Path) -> None:
    assert PriceStore(tmp_path).closes("stooq", "nope.de") == []


def test_latest_on_or_before_finds_the_latest_date_not_after_the_given_day(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        _series([_point(date(2024, 1, 2), "138.00"), _point(date(2024, 1, 5), "140.00")]), fetched_at=FETCHED_AT
    )

    assert store.latest_on_or_before("stooq", "sap.de", date(2024, 1, 4)).close == Decimal("138.00")
    assert store.latest_on_or_before("stooq", "sap.de", date(2024, 1, 5)).close == Decimal("140.00")
    assert store.latest_on_or_before("stooq", "sap.de", date(2024, 1, 1)) is None


def test_a_stooq_series_and_a_manual_series_for_the_same_symbol_stay_apart(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="alv.de",
            currency="EUR",
            adjustment="split_dividend",
            points=(_point(date(2024, 6, 20), "260.00"),),
        ),
        fetched_at=FETCHED_AT,
    )
    store.upsert(
        PriceSeries(
            source="manual",
            symbol="ALV.DE",
            currency="EUR",
            adjustment="raw",
            points=(_point(date(2024, 6, 20), "999.00"),),
        ),
        fetched_at=FETCHED_AT,
    )

    assert store.closes("stooq", "alv.de")[0].close == Decimal("260.00")
    assert store.closes("manual", "ALV.DE")[0].close == Decimal("999.00")


def test_upsert_writes_a_manifest_with_the_file_its_sha256_the_source_and_when(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(_series([_point(date(2024, 1, 2), "138.00")]), fetched_at=FETCHED_AT)

    manifest_path = tmp_path / "lake" / "manifests" / "bars_stooq_sap.de.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"] == "stooq"
    assert manifest["fetched_at"] == "2024-12-31T11:00:00Z"
    (stored_file,) = manifest["files"]
    stored_path = tmp_path / stored_file
    assert stored_path.is_file()
    expected_sha256 = manifest["sha256"][stored_path.name]

    import hashlib

    assert hashlib.sha256(stored_path.read_bytes()).hexdigest() == expected_sha256


def test_fx_upsert_keeps_one_row_per_date_and_currency(tmp_path: Path) -> None:
    store = FxStore(tmp_path)
    store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 2), currency="USD", rate=Decimal("1.09")),)), fetched_at=FETCHED_AT
    )

    store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 2), currency="USD", rate=Decimal("1.11")),)), fetched_at=FETCHED_AT
    )
    store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 2), currency="GBP", rate=Decimal("0.86")),)), fetched_at=FETCHED_AT
    )

    assert store.rate_on_or_before("USD", date(2024, 1, 2)).rate == Decimal("1.11")
    assert store.rate_on_or_before("GBP", date(2024, 1, 2)).rate == Decimal("0.86")


def test_fx_rate_on_or_before_is_none_before_any_data(tmp_path: Path) -> None:
    assert FxStore(tmp_path).rate_on_or_before("USD", date(2024, 1, 2)) is None


def test_fx_upsert_writes_a_manifest(tmp_path: Path) -> None:
    store = FxStore(tmp_path)
    store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 2), currency="USD", rate=Decimal("1.09")),)), fetched_at=FETCHED_AT
    )

    manifest = json.loads((tmp_path / "lake" / "manifests" / "fx_ecb.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "ecb"
    assert manifest["files"] == ["lake/fx/ecb/part.parquet"]


def test_is_stale_boundary_matches_the_golden_portfolio() -> None:
    """1.2: ALV's price is 5 days old on 2024-12-25 (not stale) and 7 days old on 2024-12-27 (stale)."""
    priced_on = date(2024, 12, 20)

    assert is_stale(priced_on, date(2024, 12, 25)) is False
    assert is_stale(priced_on, date(2024, 12, 27)) is True


def test_is_stale_respects_a_different_window() -> None:
    priced_on = date(2024, 12, 20)

    assert is_stale(priced_on, date(2024, 12, 21), stale_after_days=0) is True
    assert is_stale(priced_on, date(2024, 12, 20), stale_after_days=0) is False
