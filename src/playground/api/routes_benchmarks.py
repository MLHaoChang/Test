"""GET routes under `/benchmarks` (plan 5.8, 6.8): the benchmark definitions of
`configs/benchmarks.yaml` and their stored daily series, the same data `pg benchmarks` reads.

Neither route needs the registry: a benchmark is config, and its series lives in the Parquet lake
under the data directory (plan 4.2), read the same way `pg benchmarks series` reads it. Fetching a
benchmark's series from Stooq is a `pg benchmarks fetch`-only write (plan 5.9): the API only serves
what has already been fetched, so it never reaches the network itself.
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Request

from playground.api import schemas
from playground.api.app import BadRequestError, NotFoundError, get_state
from playground.marketdata.benchmarks import BenchmarkConfigError, benchmark_series, find_benchmark, load_benchmarks
from playground.marketdata.lake import FxStore, PriceStore

router = APIRouter(tags=["benchmarks"])


@router.get("/benchmarks", response_model=schemas.BenchmarksResponse)
def list_benchmarks() -> dict[str, Any]:
    """Every benchmark `configs/benchmarks.yaml` defines (`pg benchmarks`, plan 4.3)."""
    return {
        "benchmarks": [
            {
                "id": benchmark.id,
                "name": benchmark.name,
                "isin": benchmark.isin,
                "currency": benchmark.currency,
                "data_source": benchmark.data_source,
                "data_symbol": benchmark.data_symbol,
                "also_in": list(benchmark.also_in),
            }
            for benchmark in load_benchmarks()
        ]
    }


@router.get("/benchmarks/{benchmark_id}/series", response_model=schemas.BenchmarkSeriesResponse)
def get_benchmark_series(
    benchmark_id: str,
    request: Request,
    date_from: date = Query(..., alias="from"),  # noqa: B008
    date_to: date = Query(..., alias="to"),  # noqa: B008
    currency: str | None = Query(None, description="Convert to this currency (the benchmark's own, or EUR)."),
) -> dict[str, Any]:
    """The stored daily series for `benchmark_id` over `[from, to]`, already fetched with `pg benchmarks fetch`."""
    state = get_state(request)
    try:
        benchmark = find_benchmark(load_benchmarks(), benchmark_id)
    except BenchmarkConfigError as exc:
        raise NotFoundError(str(exc)) from exc
    store = PriceStore(state.settings.data_dir)
    fx_store = FxStore(state.settings.data_dir)
    try:
        points = benchmark_series(benchmark, store, fx_store, start=date_from, end=date_to, currency=currency)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return {
        "benchmark": benchmark.id,
        "currency": currency or benchmark.currency,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "points": [{"date": point.date.isoformat(), "value": format(point.value, "f")} for point in points],
    }
