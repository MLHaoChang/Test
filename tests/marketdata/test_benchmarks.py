"""Tests for benchmark definitions and series (plan 4.3, 5.6, 6.8, AC11).

AC11: "MSCI World (EUR) and S&P 500 (USD and EUR) daily series are stored and returned for any
range." The CLI-level test at the bottom drives `pg benchmarks fetch` and `pg benchmarks series`
exactly as e2e steps 23 and 27 do, through `tests/fixtures/http` (5.4), and checks the result
against the committed `tests/fixtures/golden/expected/sp500_eur.json`.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from playground.cli.main import app
from playground.core.errors import PlaygroundError
from playground.core.money import to_eur
from playground.http.replay import ReplayHttpClient
from playground.marketdata.benchmarks import (
    Benchmark,
    BenchmarkConfigError,
    BenchmarkCurrencyError,
    benchmark_series,
    fetch_benchmarks,
    find_benchmark,
    load_benchmarks,
)
from playground.marketdata.lake import FxPoint, FxStore, FxTable, PricePoint, PriceSeries, PriceStore

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "configs" / "benchmarks.yaml"
FETCHED_AT = datetime(2024, 12, 31, 11, 0, tzinfo=UTC)


def test_the_committed_config_has_the_two_p0_benchmarks() -> None:
    benchmarks = load_benchmarks()
    by_id = {benchmark.id: benchmark for benchmark in benchmarks}

    assert by_id["msci_world_eur"].data_source == "stooq"
    assert by_id["msci_world_eur"].data_symbol == "eunl.de"
    assert by_id["msci_world_eur"].currency == "EUR"
    assert by_id["msci_world_eur"].isin == "IE00B4L5Y983"

    assert by_id["sp500"].data_symbol == "^spx"
    assert by_id["sp500"].currency == "USD"
    assert "EUR" in by_id["sp500"].also_in


def test_load_benchmarks_takes_an_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "benchmarks.yaml"
    path.write_text(
        "test_bench:\n  name: Test\n  data_source: stooq\n  data_symbol: ^test\n  currency: USD\n", encoding="utf-8"
    )

    benchmarks = load_benchmarks(path)

    assert benchmarks == [
        Benchmark(id="test_bench", name="Test", isin=None, data_source="stooq", data_symbol="^test", currency="USD")
    ]


def test_load_benchmarks_raises_on_a_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "benchmarks.yaml"
    path.write_text("broken:\n  name: Test\n  data_source: stooq\n", encoding="utf-8")
    with pytest.raises(BenchmarkConfigError):
        load_benchmarks(path)


def test_find_benchmark_raises_for_an_unknown_id() -> None:
    with pytest.raises(BenchmarkConfigError):
        find_benchmark(load_benchmarks(), "nasdaq")


# --- fetch_benchmarks / benchmark_series (in-memory) ---------------------------------------------


def _point(day: date, close: str) -> PricePoint:
    return PricePoint(date=day, open=None, high=None, low=None, close=Decimal(close), volume=None)


def test_fetch_benchmarks_stores_under_the_benchmarks_own_currency(
    replay_http_client: ReplayHttpClient, tmp_path: Path
) -> None:
    store = PriceStore(tmp_path)
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    counts = fetch_benchmarks(
        replay_http_client, store, [benchmark], start=date(2024, 1, 1), end=date(2024, 12, 31), fetched_at=FETCHED_AT
    )

    assert counts == {"sp500": len(store.closes("stooq", "^spx"))}
    assert store.closes("stooq", "^spx")[0].close == Decimal("4700.00")


def test_benchmark_series_in_its_own_currency_needs_no_fx(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="^spx",
            currency="USD",
            adjustment="split_dividend",
            points=(_point(date(2024, 1, 2), "4700.00"),),
        ),
        fetched_at=FETCHED_AT,
    )
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    points = benchmark_series(
        benchmark, store, FxStore(tmp_path), start=date(2024, 1, 1), end=date(2024, 12, 31), currency="USD"
    )

    assert points[0].value == Decimal("4700.00000000")


def test_benchmark_series_converts_to_eur_with_the_as_of_rate(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="^spx",
            currency="USD",
            adjustment="split_dividend",
            points=(_point(date(2024, 1, 2), "4700.00"),),
        ),
        fetched_at=FETCHED_AT,
    )
    fx_store = FxStore(tmp_path)
    fx_store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 2), currency="USD", rate=Decimal("1.0900")),)), fetched_at=FETCHED_AT
    )
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    points = benchmark_series(
        benchmark, store, fx_store, start=date(2024, 1, 1), end=date(2024, 12, 31), currency="EUR"
    )

    assert points[0].value == to_eur(Decimal("4700.00"), "USD", Decimal("1.0900")).quantize(Decimal("0.00000001"))


def test_benchmark_series_defaults_to_the_benchmarks_own_currency(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="^spx",
            currency="USD",
            adjustment="split_dividend",
            points=(_point(date(2024, 1, 2), "4700.00"),),
        ),
        fetched_at=FETCHED_AT,
    )
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    points = benchmark_series(benchmark, store, FxStore(tmp_path), start=date(2024, 1, 1), end=date(2024, 12, 31))

    assert points[0].value == Decimal("4700.00000000")


def test_benchmark_series_skips_a_day_with_no_fx_rate_rather_than_guessing(tmp_path: Path) -> None:
    """The as-of rule (5.7) means a day is only left out when NO earlier rate exists at all."""
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="^spx",
            currency="USD",
            adjustment="split_dividend",
            points=(_point(date(2024, 1, 2), "4700.00"), _point(date(2024, 1, 3), "4710.00")),
        ),
        fetched_at=FETCHED_AT,
    )
    fx_store = FxStore(tmp_path)
    # The first ECB rate is dated AFTER the first price point, so that point has none to fall
    # back to on or before it and is left out; the second point uses this same rate as-of.
    fx_store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 3), currency="USD", rate=Decimal("1.09")),)), fetched_at=FETCHED_AT
    )
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    points = benchmark_series(benchmark, store, fx_store, start=date(2024, 1, 1), end=date(2024, 1, 31), currency="EUR")

    assert [point.date for point in points] == [date(2024, 1, 3)]


def test_benchmark_series_rejects_a_currency_it_cannot_convert_to(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )
    with pytest.raises(BenchmarkCurrencyError) as refused:
        benchmark_series(
            benchmark, store, FxStore(tmp_path), start=date(2024, 1, 1), end=date(2024, 12, 31), currency="GBP"
        )
    # QA P0 round 2, R2-D2: said "benchmark_series can only convert USD to EUR, not to GBP."
    assert str(refused.value) == "S&P 500 can be shown in USD or EUR only, not in GBP."
    assert refused.value.allowed == ("USD", "EUR")
    assert isinstance(refused.value, PlaygroundError)


def test_a_benchmark_in_eur_can_be_shown_in_eur_only(tmp_path: Path) -> None:
    benchmark = Benchmark(
        id="msci_world_eur",
        name="MSCI World in EUR",
        isin=None,
        data_source="stooq",
        data_symbol="eunl.de",
        currency="EUR",
    )
    with pytest.raises(BenchmarkCurrencyError) as refused:
        benchmark_series(
            benchmark,
            PriceStore(tmp_path),
            FxStore(tmp_path),
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            currency="USD",
        )
    assert str(refused.value) == "MSCI World in EUR can be shown in EUR only, not in USD."
    assert refused.value.allowed == ("EUR",)


def test_benchmark_series_reads_a_currency_in_small_letters(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="^spx",
            currency="USD",
            adjustment="split_dividend",
            points=(_point(date(2024, 1, 2), "4700.00"),),
        ),
        fetched_at=FETCHED_AT,
    )
    fx_store = FxStore(tmp_path)
    fx_store.upsert(
        FxTable(points=(FxPoint(date=date(2024, 1, 2), currency="USD", rate=Decimal("1.0900")),)), fetched_at=FETCHED_AT
    )
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    points = benchmark_series(
        benchmark, store, fx_store, start=date(2024, 1, 1), end=date(2024, 12, 31), currency=" eur "
    )

    assert [point.value for point in points] == [
        to_eur(Decimal("4700.00"), "USD", Decimal("1.0900")).quantize(Decimal("0.00000001"))
    ]


def test_benchmark_series_filters_to_the_given_range(tmp_path: Path) -> None:
    store = PriceStore(tmp_path)
    store.upsert(
        PriceSeries(
            source="stooq",
            symbol="^spx",
            currency="USD",
            adjustment="split_dividend",
            points=(_point(date(2024, 1, 2), "1"), _point(date(2024, 6, 1), "2"), _point(date(2024, 12, 31), "3")),
        ),
        fetched_at=FETCHED_AT,
    )
    benchmark = Benchmark(
        id="sp500", name="S&P 500", isin=None, data_source="stooq", data_symbol="^spx", currency="USD"
    )

    points = benchmark_series(
        benchmark, store, FxStore(tmp_path), start=date(2024, 2, 1), end=date(2024, 11, 1), currency="USD"
    )

    assert [point.date for point in points] == [date(2024, 6, 1)]


# --- the CLI, against the golden fixture (plan 7.6 steps 23 and 27) -------------------------------


def test_pg_benchmarks_fetch_and_series_match_the_golden_file(tmp_path: Path, update_goldens: bool) -> None:
    runner = CliRunner()
    data_dir = tmp_path / "data"
    env = {"PG_TODAY": "2024-12-31"}

    init_result = runner.invoke(app, ["--data-dir", str(data_dir), "init"], env=env)
    assert init_result.exit_code == 0, init_result.output

    # e2e step 22: benchmarks series in EUR needs an as-of ECB rate for every day (5.7), so the
    # rates must be fetched first, exactly as the end-to-end scenario orders it (plan 7.6).
    fx_result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "--http-replay", str(REPO / "tests" / "fixtures" / "http"), "fx", "fetch"],
        env=env,
    )
    assert fx_result.exit_code == 0, fx_result.output

    fetch_result = runner.invoke(
        app,
        [
            "--data-dir",
            str(data_dir),
            "--http-replay",
            str(REPO / "tests" / "fixtures" / "http"),
            "benchmarks",
            "fetch",
            "--from",
            "2024-01-01",
            "--to",
            "2024-12-31",
        ],
        env=env,
    )
    assert fetch_result.exit_code == 0, fetch_result.output

    series_result = runner.invoke(
        app,
        [
            "--data-dir",
            str(data_dir),
            "benchmarks",
            "series",
            "sp500",
            "--currency",
            "EUR",
            "--from",
            "2024-01-02",
            "--to",
            "2024-12-31",
            "--json",
        ],
        env=env,
    )
    assert series_result.exit_code == 0, series_result.output
    actual = json.loads(series_result.stdout)

    golden_path = REPO / "tests" / "fixtures" / "golden" / "expected" / "sp500_eur.json"
    if update_goldens:
        golden_path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    assert actual == expected


def test_the_benchmarks_yaml_config_file_is_plain_yaml() -> None:
    """A smoke test that the committed config is exactly what `load_benchmarks` expects."""
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert set(payload) == {"msci_world_eur", "sp500"}
