"""Instrument master, price and FX clients, the Parquet lake, and benchmarks (plan 5.6).

Every client here (`stooq.py`, `manual_prices.py`, `ecb.py`, `openfigi.py`) takes an `HttpClient`
(`playground.http`, or nothing at all for `manual_prices`, which only ever reads a local file) and
never builds one itself, so the whole package runs against recorded fixtures in tests. `lake.py`
is the Parquet storage underneath `stooq` and `ecb`; `instruments.py` is the editable, logged
ISIN-to-symbol mapping; `benchmarks.py` reads `configs/benchmarks.yaml` and reuses `stooq.py` to
fetch and store the MSCI World and S&P 500 series.
"""
