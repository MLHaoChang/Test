"""The Parquet data lake for prices and FX rates, with manifests (plan 4.2, 5.6, 3.1).

Layout under `<data-dir>/lake/` (3.1, a deviation from spec 3.1 explained there: a `source=`
level keeps a Stooq series and a manual file for the same symbol apart, and there is no `year=`
level yet)::

    bars/daily/source=<source>/symbol=<symbol>/part.parquet
    fx/ecb/part.parquet
    manifests/<name>.json

`PriceStore` and `FxStore` are the only code that reads or writes these files. `upsert` merges by
date (bars) or by (date, currency) (FX): a row for a date already on disk is replaced, one for a
new date is added, so the newest fetch always wins and calling `upsert` twice for overlapping
ranges keeps exactly one row per date (7.2: "lake upsert keeps one row per date"). Every `upsert`
also writes a manifest under `manifests/` recording the file it wrote, that file's SHA-256, the
source and when it was fetched (spec 3.1, plan 4.2) -- `fetched_at` is passed in by the caller,
never read from the system clock (3.3, 7.1: only `core/clock.py` may do that).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from playground.core.dates import format_ts_utc

_DECIMAL_TYPE = pa.decimal128(20, 8)

_BARS_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("open", _DECIMAL_TYPE),
        ("high", _DECIMAL_TYPE),
        ("low", _DECIMAL_TYPE),
        ("close", _DECIMAL_TYPE),
        ("volume", pa.int64()),
        ("currency", pa.string()),
        ("adjustment", pa.string()),
        ("source", pa.string()),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
    ]
)

_FX_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("currency", pa.string()),
        ("rate", _DECIMAL_TYPE),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
    ]
)


@dataclass(frozen=True)
class PricePoint:
    """One daily bar (plan 5.6). `open`, `high` and `low` are `None` when a source gives only a close."""

    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None


@dataclass(frozen=True)
class PriceSeries:
    """A source's daily bars for one symbol, over some range (plan 5.6).

    `adjustment` is `raw`, `split` or `split_dividend` (5.7): whether the closes already reflect
    later splits and dividends. `currency` is the symbol's own listing currency, not necessarily
    EUR -- the caller (the instrument's mapping, or a benchmark's configured currency) supplies
    it; neither Stooq's nor the manual file's raw rows carry it.
    """

    source: str
    symbol: str
    currency: str
    adjustment: str
    points: tuple[PricePoint, ...]


@dataclass(frozen=True)
class FxPoint:
    """One currency's ECB reference rate on one day: units of `currency` per 1 EUR (3.3)."""

    date: date
    currency: str
    rate: Decimal


@dataclass(frozen=True)
class FxTable:
    """A batch of `FxPoint`s, over any number of currencies and days (plan 5.6)."""

    points: tuple[FxPoint, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(lake_root: Path, *, name: str, path: Path, source: str, fetched_at: datetime) -> None:
    """Write `<lake_root>/manifests/<name>.json`: the file, its SHA-256, the source and when (spec 3.1)."""
    manifests_dir = lake_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "files": [str(path.relative_to(lake_root.parent))],
        "sha256": {path.name: _sha256_file(path)},
        "source": source,
        "fetched_at": format_ts_utc(fetched_at),
    }
    (manifests_dir / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_path_segment(value: str) -> str:
    """A symbol as a path segment. Stooq symbols can carry '^' and '/'; only '/' is unsafe here."""
    return value.replace("/", "_")


class PriceStore:
    """Daily bars under `<data_dir>/lake/bars/daily/` (plan 5.6)."""

    def __init__(self, data_dir: Path) -> None:
        self._lake_root = Path(data_dir) / "lake"

    def _path(self, source: str, symbol: str) -> Path:
        return (
            self._lake_root
            / "bars"
            / "daily"
            / f"source={_safe_path_segment(source)}"
            / f"symbol={_safe_path_segment(symbol)}"
            / "part.parquet"
        )

    def upsert(self, series: PriceSeries, *, fetched_at: datetime) -> None:
        """Merge `series.points` into the stored series for `(series.source, series.symbol)`.

        A point for a date already stored is replaced; the newest write always wins, so
        re-fetching an overlapping range is safe and idempotent. Writes a manifest (spec 3.1).
        """
        path = self._path(series.source, series.symbol)
        merged: dict[date, PricePoint] = {point.date: point for point in self._read_points(path)}
        for point in series.points:
            merged[point.date] = point
        ordered = sorted(merged.values(), key=lambda point: point.date)

        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table(
            {
                "date": [point.date for point in ordered],
                "open": [point.open for point in ordered],
                "high": [point.high for point in ordered],
                "low": [point.low for point in ordered],
                "close": [point.close for point in ordered],
                "volume": [point.volume for point in ordered],
                "currency": [series.currency for _ in ordered],
                "adjustment": [series.adjustment for _ in ordered],
                "source": [series.source for _ in ordered],
                "fetched_at": [fetched_at for _ in ordered],
            },
            schema=_BARS_SCHEMA,
        )
        pq.write_table(table, path)
        _write_manifest(
            self._lake_root,
            name=f"bars_{_safe_path_segment(series.source)}_{_safe_path_segment(series.symbol)}",
            path=path,
            source=series.source,
            fetched_at=fetched_at,
        )

    def _read_points(self, path: Path) -> list[PricePoint]:
        if not path.is_file():
            return []
        table = pq.read_table(path)
        return [
            PricePoint(
                date=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in table.to_pylist()
        ]

    def closes(self, source: str, symbol: str) -> list[PricePoint]:
        """Every stored point for `(source, symbol)`, oldest first."""
        return sorted(self._read_points(self._path(source, symbol)), key=lambda point: point.date)

    def latest_on_or_before(self, source: str, symbol: str, day: date) -> PricePoint | None:
        """The latest point for `(source, symbol)` dated on or before `day` (the as-of rule, 5.7)."""
        candidates = [point for point in self._read_points(self._path(source, symbol)) if point.date <= day]
        if not candidates:
            return None
        return max(candidates, key=lambda point: point.date)


class FxStore:
    """ECB reference rates under `<data_dir>/lake/fx/ecb/part.parquet` (plan 5.6)."""

    def __init__(self, data_dir: Path) -> None:
        self._lake_root = Path(data_dir) / "lake"
        self._path = self._lake_root / "fx" / "ecb" / "part.parquet"

    def upsert(self, table: FxTable, *, fetched_at: datetime) -> None:
        """Merge `table.points` in by (date, currency); the newest write wins. Writes a manifest."""
        merged: dict[tuple[date, str], FxPoint] = {(point.date, point.currency): point for point in self._read_points()}
        for point in table.points:
            merged[(point.date, point.currency)] = point
        ordered = sorted(merged.values(), key=lambda point: (point.date, point.currency))

        self._path.parent.mkdir(parents=True, exist_ok=True)
        arrow_table = pa.table(
            {
                "date": [point.date for point in ordered],
                "currency": [point.currency for point in ordered],
                "rate": [point.rate for point in ordered],
                "fetched_at": [fetched_at for _ in ordered],
            },
            schema=_FX_SCHEMA,
        )
        pq.write_table(arrow_table, self._path)
        _write_manifest(self._lake_root, name="fx_ecb", path=self._path, source="ecb", fetched_at=fetched_at)

    def _read_points(self) -> list[FxPoint]:
        if not self._path.is_file():
            return []
        table = pq.read_table(self._path)
        return [FxPoint(date=row["date"], currency=row["currency"], rate=row["rate"]) for row in table.to_pylist()]

    def rate_on_or_before(self, currency: str, day: date) -> FxPoint | None:
        """The latest ECB rate for `currency` dated on or before `day` (the as-of rule, 5.7)."""
        candidates = [point for point in self._read_points() if point.currency == currency and point.date <= day]
        if not candidates:
            return None
        return max(candidates, key=lambda point: point.date)


#: 5.7's default staleness window: a price or rate older than this many calendar days is flagged.
DEFAULT_STALE_AFTER_DAYS = 5


def is_stale(point_date: date, as_of: date, *, stale_after_days: int = DEFAULT_STALE_AFTER_DAYS) -> bool:
    """True when `point_date` is more than `stale_after_days` calendar days before `as_of` (plan 5.7).

    Works for both a `PricePoint.date` and an `FxPoint.date`: 5.7 flags `stale_price` and
    `stale_fx` with the same rule and the same default window. Plan 1.2's boundary example: a
    price dated 2024-12-20 is not stale as of 2024-12-25 (5 days), and is stale as of 2024-12-27
    (7 days) -- the comparison is strictly greater than, so exactly `stale_after_days` is not stale.
    """
    return (as_of - point_date) > timedelta(days=stale_after_days)
