"""The market-data commands (plan 5.9): `prices`, `fx`, `benchmarks`.

Every command that reaches outside the machine (`prices fetch`, `fx fetch`, `benchmarks fetch`)
goes through `ctx.obj.http_client` -- built once in `cli/main.py` from `--http-replay` /
`--http-record` / the network default (plan 5.4) -- and never opens one itself.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer

from playground.cli.context import AppContext, fail, parse_day, portfolio_transaction, print_json
from playground.core.errors import InvalidIsinError
from playground.core.isin import normalise_isin
from playground.marketdata.benchmarks import (
    Benchmark,
    BenchmarkConfigError,
    SeriesPoint,
    benchmark_series,
    fetch_benchmarks,
    find_benchmark,
    load_benchmarks,
)
from playground.marketdata.ecb import EcbClient, EcbSourceError, load_fx_file
from playground.marketdata.instruments import list_instruments
from playground.marketdata.lake import FxStore, PricePoint, PriceStore, is_stale
from playground.marketdata.manual_prices import ManualPriceFileError, load_price_file
from playground.marketdata.stooq import SourceNoData, SourceUnavailable, StooqClient

prices_app = typer.Typer(help="Daily prices for your mapped instruments.", no_args_is_help=True)
fx_app = typer.Typer(help="ECB reference FX rates.", no_args_is_help=True)
benchmarks_app = typer.Typer(
    help="Benchmark series: MSCI World and the S&P 500 (configs/benchmarks.yaml).", no_args_is_help=True
)

_JSON = typer.Option(False, "--json", help="Print JSON instead of text.")


def _dec(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _required_day(text: str | None, option: str) -> date:
    day = parse_day(text, option)
    if day is None:
        fail(f"Give {option} YYYY-MM-DD.")
    return day


def _normalised_isin(value: str) -> str:
    try:
        return normalise_isin(value)
    except InvalidIsinError as exc:
        fail(str(exc))


def _price_point_record(point: PricePoint) -> dict[str, Any]:
    return {
        "date": point.date.isoformat(),
        "open": _dec(point.open),
        "high": _dec(point.high),
        "low": _dec(point.low),
        "close": _dec(point.close),
        "volume": point.volume,
    }


def _series_point_record(point: SeriesPoint) -> dict[str, Any]:
    return {"date": point.date.isoformat(), "value": _dec(point.value)}


# --- prices ---------------------------------------------------------------------------------


def prices_fetch(
    ctx: typer.Context,
    date_from: str | None = typer.Option(None, "--from", help="First day to fetch (YYYY-MM-DD)."),
    date_to: str | None = typer.Option(None, "--to", help="Last day to fetch (YYYY-MM-DD). Today by default."),
    isin: str | None = typer.Option(None, "--isin", help="Only this ISIN. Every stooq-mapped instrument by default."),
    json_output: bool = _JSON,
) -> None:
    """Fetch daily closes for your stooq-mapped instruments (or one, with --isin) and store them."""
    app_ctx: AppContext = ctx.obj
    start = _required_day(date_from, "--from")
    end = parse_day(date_to, "--to") or app_ctx.clock.today()
    wanted = _normalised_isin(isin) if isin is not None else None

    with portfolio_transaction(app_ctx) as (conn, _):
        candidates = [row for row in list_instruments(conn) if row["data_source"] == "stooq"]
    if wanted is not None:
        candidates = [row for row in candidates if row["isin"] == wanted]
        if not candidates:
            fail(f"{wanted} is not mapped to stooq. See pg instruments list.")

    client = StooqClient(app_ctx.http_client)
    store = PriceStore(app_ctx.data_dir)
    fetched_at = app_ctx.clock.now_utc()
    results: list[dict[str, Any]] = []
    for row in candidates:
        try:
            series = client.daily_bars(row["data_symbol"], start, end, currency=row["currency"])
        except SourceNoData as exc:
            results.append({"isin": row["isin"], "symbol": row["data_symbol"], "error": str(exc)})
            continue
        except SourceUnavailable as exc:
            fail(str(exc))
        store.upsert(series, fetched_at=fetched_at)
        results.append({"isin": row["isin"], "symbol": row["data_symbol"], "points": len(series.points)})

    if json_output:
        print_json({"from": start.isoformat(), "to": end.isoformat(), "fetched": results})
        return
    if not results:
        typer.echo("No stooq-mapped instruments to fetch. See pg instruments map.")
    for result in results:
        if "error" in result:
            typer.echo(f"{result['isin']} ({result['symbol']}): {result['error']}")
        else:
            typer.echo(f"{result['isin']} ({result['symbol']}): {result['points']} daily closes stored.")


def prices_import_file(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="A manual price file: date;data_symbol;close;currency;adjustment."),  # noqa: B008
    json_output: bool = _JSON,
) -> None:
    """Load a manual price file and store its series (the price-source fallback, plan 6.5)."""
    app_ctx: AppContext = ctx.obj
    try:
        data = file.read_bytes()
    except FileNotFoundError:
        fail(f"There is no file {file}.")
    try:
        series_list = load_price_file(data)
    except ManualPriceFileError as exc:
        fail(str(exc))

    with portfolio_transaction(app_ctx):
        store = PriceStore(app_ctx.data_dir)
        fetched_at = app_ctx.clock.now_utc()
        for series in series_list:
            store.upsert(series, fetched_at=fetched_at)

    if json_output:
        print_json(
            {
                "file": str(file),
                "series": [{"symbol": s.symbol, "currency": s.currency, "points": len(s.points)} for s in series_list],
            }
        )
        return
    for series in series_list:
        typer.echo(
            f"{series.symbol} ({series.currency}, {series.adjustment}): {len(series.points)} prices stored from {file}."
        )


def prices_show(
    ctx: typer.Context,
    symbol: str = typer.Argument(..., help="The data symbol, as mapped (for example sap.de or ALV.DE)."),
    source: str | None = typer.Option(None, "--source", help="stooq or manual. Both are searched by default."),
    json_output: bool = _JSON,
) -> None:
    """Show the stored daily closes for one symbol."""
    app_ctx: AppContext = ctx.obj
    store = PriceStore(app_ctx.data_dir)
    sources = [source] if source else ["stooq", "manual"]
    found = {src: points for src in sources if (points := store.closes(src, symbol))}
    if not found:
        fail(f"No stored prices for {symbol}. Fetch or import them first.")

    if json_output:
        print_json(
            {
                "symbol": symbol,
                "series": {src: [_price_point_record(p) for p in points] for src, points in found.items()},
            }
        )
        return
    for src, points in found.items():
        typer.echo(
            f"{symbol} ({src}): {len(points)} prices, {points[0].date.isoformat()} to {points[-1].date.isoformat()}."
        )
        for point in points:
            typer.echo(f"  {point.date.isoformat()} close {point.close}")


# --- fx -------------------------------------------------------------------------------------


def fx_fetch(ctx: typer.Context, json_output: bool = _JSON) -> None:
    """Fetch the ECB's full historical reference rates and store them (no date range: 6.6)."""
    app_ctx: AppContext = ctx.obj
    client = EcbClient(app_ctx.http_client)
    try:
        table = client.history()
    except EcbSourceError as exc:
        fail(str(exc))
    with portfolio_transaction(app_ctx):
        FxStore(app_ctx.data_dir).upsert(table, fetched_at=app_ctx.clock.now_utc())
    if json_output:
        print_json({"points": len(table.points)})
        return
    typer.echo(f"Stored {len(table.points)} ECB rates.")


def fx_import_file(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="The ECB CSV, unzipped."),  # noqa: B008
    json_output: bool = _JSON,
) -> None:
    """Load the ECB rates CSV unzipped: the fallback when the network is unreachable (plan 6.6)."""
    app_ctx: AppContext = ctx.obj
    try:
        data = file.read_bytes()
    except FileNotFoundError:
        fail(f"There is no file {file}.")
    try:
        table = load_fx_file(data)
    except EcbSourceError as exc:
        fail(str(exc))
    with portfolio_transaction(app_ctx):
        FxStore(app_ctx.data_dir).upsert(table, fetched_at=app_ctx.clock.now_utc())
    if json_output:
        print_json({"file": str(file), "points": len(table.points)})
        return
    typer.echo(f"Stored {len(table.points)} ECB rates from {file}.")


def fx_show(
    ctx: typer.Context,
    currency: str = typer.Option(..., "--currency", help="The currency, for example USD."),
    on: str | None = typer.Option(None, "--on", help="As of this day (YYYY-MM-DD). Today by default."),
    json_output: bool = _JSON,
) -> None:
    """Show the ECB rate for a currency, as of a day (today by default)."""
    app_ctx: AppContext = ctx.obj
    day = parse_day(on, "--on") or app_ctx.clock.today()
    store = FxStore(app_ctx.data_dir)
    point = store.rate_on_or_before(currency.upper(), day)
    if point is None:
        fail(f"No stored ECB rate for {currency.upper()} on or before {day.isoformat()}. Run pg fx fetch first.")
    stale = is_stale(point.date, day)
    if json_output:
        print_json(
            {
                "currency": point.currency,
                "as_of": day.isoformat(),
                "date": point.date.isoformat(),
                "rate": _dec(point.rate),
                "stale": stale,
            }
        )
        return
    typer.echo(f"{point.currency}: {point.rate} on {point.date.isoformat()}" + (" (stale)" if stale else ""))


# --- benchmarks -----------------------------------------------------------------------------


def benchmarks_fetch(
    ctx: typer.Context,
    date_from: str | None = typer.Option(None, "--from", help="First day to fetch (YYYY-MM-DD)."),
    date_to: str | None = typer.Option(None, "--to", help="Last day to fetch (YYYY-MM-DD). Today by default."),
    json_output: bool = _JSON,
) -> None:
    """Fetch every benchmark's daily series from configs/benchmarks.yaml and store it (plan 6.8)."""
    app_ctx: AppContext = ctx.obj
    start = _required_day(date_from, "--from")
    end = parse_day(date_to, "--to") or app_ctx.clock.today()
    benchmarks = load_benchmarks()
    with portfolio_transaction(app_ctx):
        store = PriceStore(app_ctx.data_dir)
        try:
            counts = fetch_benchmarks(
                app_ctx.http_client, store, benchmarks, start=start, end=end, fetched_at=app_ctx.clock.now_utc()
            )
        except (SourceNoData, SourceUnavailable) as exc:
            fail(str(exc))
    if json_output:
        print_json({"from": start.isoformat(), "to": end.isoformat(), "fetched": counts})
        return
    for benchmark_id, count in counts.items():
        typer.echo(f"{benchmark_id}: {count} daily closes stored.")


def _find_benchmark_or_fail(benchmarks: list[Benchmark], benchmark_id: str) -> Benchmark:
    try:
        return find_benchmark(benchmarks, benchmark_id)
    except BenchmarkConfigError as exc:
        fail(str(exc))


def benchmarks_series_command(
    ctx: typer.Context,
    benchmark_id: str = typer.Argument(..., help="The benchmark id, for example sp500 or msci_world_eur."),
    currency: str | None = typer.Option(None, "--currency", help="Convert to this currency (its own, or EUR)."),
    date_from: str | None = typer.Option(None, "--from", help="First day (YYYY-MM-DD)."),
    date_to: str | None = typer.Option(None, "--to", help="Last day (YYYY-MM-DD)."),
    json_output: bool = _JSON,
) -> None:
    """Show a benchmark's stored daily series, for any range, already fetched with pg benchmarks fetch."""
    app_ctx: AppContext = ctx.obj
    start = _required_day(date_from, "--from")
    end = _required_day(date_to, "--to")
    benchmark = _find_benchmark_or_fail(load_benchmarks(), benchmark_id)
    store = PriceStore(app_ctx.data_dir)
    fx_store = FxStore(app_ctx.data_dir)
    try:
        points = benchmark_series(benchmark, store, fx_store, start=start, end=end, currency=currency)
    except ValueError as exc:
        fail(str(exc))
    wanted_currency = currency or benchmark.currency

    if json_output:
        print_json(
            {
                "benchmark": benchmark.id,
                "currency": wanted_currency,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "points": [_series_point_record(point) for point in points],
            }
        )
        return
    typer.echo(
        f"{benchmark.name} ({wanted_currency}): {len(points)} points from {start.isoformat()} to {end.isoformat()}."
    )


def register(app: typer.Typer) -> None:
    """Add the market-data commands to `app`."""
    prices_app.command(name="fetch")(prices_fetch)
    prices_app.command(name="import-file")(prices_import_file)
    prices_app.command(name="show")(prices_show)
    app.add_typer(prices_app, name="prices")

    fx_app.command(name="fetch")(fx_fetch)
    fx_app.command(name="import-file")(fx_import_file)
    fx_app.command(name="show")(fx_show)
    app.add_typer(fx_app, name="fx")

    benchmarks_app.command(name="fetch")(benchmarks_fetch)
    benchmarks_app.command(name="series")(benchmarks_series_command)
    app.add_typer(benchmarks_app, name="benchmarks")
