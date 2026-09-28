"""Benchmark definitions and series, loaded from `configs/benchmarks.yaml` (plan 4.3, 5.6, 6.8).

P0 has no new client for benchmarks (6.8): `fetch_benchmarks` reuses `StooqClient` with each
benchmark's own `data_symbol` and `currency` from the config file, and stores the result exactly
like a mapped instrument's prices, under `source="stooq"`. `benchmark_series` then reads it back
and converts to EUR when asked, the same `to_eur` convention as everywhere else in the app (3.3).
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from playground.core.errors import PlaygroundError
from playground.core.money import q8, to_eur
from playground.http.client import HttpClient
from playground.marketdata.lake import FxStore, PriceStore
from playground.marketdata.stooq import StooqClient

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "benchmarks.yaml"


class BenchmarkConfigError(PlaygroundError):
    """`configs/benchmarks.yaml` is missing a required field, or names an unknown benchmark."""


@dataclass(frozen=True)
class Benchmark:
    """One row of `configs/benchmarks.yaml` (plan 4.3): a slug, its Stooq symbol and its own currency."""

    id: str
    name: str
    isin: str | None
    data_source: str
    data_symbol: str
    currency: str
    also_in: tuple[str, ...] = ()


class BenchmarkCurrencyError(PlaygroundError):
    """A benchmark series was asked for in a currency it cannot be shown in (see `shown_in`)."""

    def __init__(self, benchmark: Benchmark, wanted: str) -> None:
        self.benchmark = benchmark
        self.wanted = wanted
        self.allowed = shown_in(benchmark)
        super().__init__(f"{benchmark.name} can be shown in {' or '.join(self.allowed)} only, not in {wanted}.")


def shown_in(benchmark: Benchmark) -> tuple[str, ...]:
    """The currencies `benchmark_series` can show `benchmark` in: its own, and EUR through the ECB rate."""
    return (benchmark.currency,) if benchmark.currency == "EUR" else (benchmark.currency, "EUR")


def wanted_currency(benchmark: Benchmark, currency: str | None) -> str:
    """The currency a series is asked for, in capitals ("eur" is EUR); the benchmark's own by default."""
    return (currency or "").strip().upper() or benchmark.currency


@dataclass(frozen=True)
class SeriesPoint:
    """One day of a benchmark series, in whatever currency was asked for (plan 5.6)."""

    date: date
    value: Decimal


def load_benchmarks(path: Path = DEFAULT_CONFIG_PATH) -> list[Benchmark]:
    """Load every benchmark from `path` (`configs/benchmarks.yaml`, plan 4.3)."""
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    benchmarks = []
    for slug, fields in payload.items():
        try:
            benchmarks.append(
                Benchmark(
                    id=slug,
                    name=fields["name"],
                    isin=fields.get("isin"),
                    data_source=fields["data_source"],
                    data_symbol=fields["data_symbol"],
                    currency=fields["currency"],
                    also_in=tuple(fields.get("also_in", ())),
                )
            )
        except KeyError as exc:
            raise BenchmarkConfigError(f"{path}: benchmark {slug!r} is missing {exc}.") from exc
    return benchmarks


def find_benchmark(benchmarks: list[Benchmark], benchmark_id: str) -> Benchmark:
    for benchmark in benchmarks:
        if benchmark.id == benchmark_id:
            return benchmark
    known = ", ".join(sorted(bench.id for bench in benchmarks))
    raise BenchmarkConfigError(f"Unknown benchmark {benchmark_id!r}. Known benchmarks: {known}.")


def fetch_benchmark(
    http: HttpClient,
    price_store: PriceStore,
    benchmark: Benchmark,
    *,
    start: date,
    end: date,
    fetched_at: datetime,
) -> int:
    """Fetch and store one benchmark's daily series over `[start, end]`; return the points stored.

    Uses `StooqClient` (6.8: P0 has no other benchmark client) and raises what it raises
    (`SourceNoData`, `SourceUnavailable`). The benchmark's own `currency` is stored alongside its
    closes, so `benchmark_series` can convert from it later.
    """
    client = StooqClient(http, manual_file_hint=False)
    series = client.daily_bars(benchmark.data_symbol, start, end, currency=benchmark.currency)
    price_store.upsert(series, fetched_at=fetched_at)
    return len(series.points)


def fetch_benchmarks(
    http: HttpClient,
    price_store: PriceStore,
    benchmarks: list[Benchmark],
    *,
    start: date,
    end: date,
    fetched_at: datetime,
) -> dict[str, int]:
    """Fetch and store every benchmark's daily series over `[start, end]` (`fetch_benchmark`).

    Returns the number of points stored per benchmark id, and stops at the first that fails.
    `pg benchmarks fetch` calls `fetch_benchmark` itself, so it can report a failure and go on.
    """
    return {
        benchmark.id: fetch_benchmark(http, price_store, benchmark, start=start, end=end, fetched_at=fetched_at)
        for benchmark in benchmarks
    }


def benchmark_series(
    benchmark: Benchmark,
    price_store: PriceStore,
    fx_store: FxStore,
    *,
    start: date,
    end: date,
    currency: str | None = None,
) -> list[SeriesPoint]:
    """The stored series for `benchmark` over `[start, end]`, in `currency` (default: its own).

    Converts to EUR with the as-of ECB rate (3.3's `to_eur`) when `currency` is `"EUR"` and the
    benchmark's own currency is not; a day with no ECB rate on or before it is left out rather
    than guessed. `currency` may be in small letters. Raises `BenchmarkCurrencyError`, with a
    plain message that names the benchmark, for any other currency than its own or EUR.
    """
    wanted = wanted_currency(benchmark, currency)
    if wanted not in shown_in(benchmark):
        raise BenchmarkCurrencyError(benchmark, wanted)
    points = [
        point
        for point in price_store.closes(benchmark.data_source, benchmark.data_symbol)
        if start <= point.date <= end
    ]
    if wanted == benchmark.currency:
        return [SeriesPoint(date=point.date, value=q8(point.close)) for point in points]

    series: list[SeriesPoint] = []
    for point in points:
        fx = fx_store.rate_on_or_before(benchmark.currency, point.date)
        if fx is None:
            continue
        series.append(SeriesPoint(date=point.date, value=q8(to_eur(point.close, benchmark.currency, fx.rate))))
    return series
