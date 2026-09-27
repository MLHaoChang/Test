"""Write the synthetic Stooq, ECB and OpenFIGI HTTP fixtures, and index.yaml (plan 5.4, 6.4 to 6.7).

Run it from the repository root after changing an anchor below:

    uv run python tests/fixtures/http/make_series.py            # write every fixture
    uv run python tests/fixtures/http/make_series.py --check    # only check; exit 1 if out of date

Everything under `tests/fixtures/http/` is generated from the `ANCHORS` table by straight-line
interpolation between anchor closes on the trading days of each symbol's own market calendar
(plan 6.4), then written out as Stooq's own CSV format, the ECB's zipped historical-rates CSV
format, and OpenFIGI's JSON mapping response -- plus `index.yaml`, which `ReplayHttpClient` reads
to serve them all through one `--http-replay tests/fixtures/http` (plan 5.4).
`tests/marketdata/test_fixture_series.py` checks that every generated series passes through its
anchors, so this table is also the single source of truth those tests are written against.

**Where the anchors come from** (plan 6.4). The Stooq host is not reachable from this container,
so these series are modelled on the documented format, not recorded. Four of the five golden-
portfolio instruments (SAP, MSCIW, AAPL, NVDA -- ALV is priced from a manual file instead, 6.5) get
anchors at: one close on 2024-01-02; the two closes `tests/fixtures/golden/README.md` and plan
1.2 hand-compute the golden portfolio's value from (2024-05-31 and, for SAP/MSCIW, 2024-12-30 --
Xetra's last trading day of the year, since it is closed on the 31st; for AAPL/NVDA/^spx,
2024-12-31 itself, since NYSE is open); and one anchor on each golden trade date, computed below
as that document's own EUR price, converted to the symbol's own listing currency at that day's
*fixture* ECB rate (computed the same way, from the ECB anchors), and, for NVDA before its
10-for-1 split on 2024-06-10, divided by 10 -- because the stored series is declared
`split_dividend` (5.7: always expressed in today's post-split terms, regardless of when a split
is booked). Doing it this way makes the 5.7 price-basis check find no mismatch anywhere in the
golden portfolio, exactly as plan 6.4 says it should, by construction: the check re-derives the
document's own EUR price from the close, the day's rate and the split factor since that day, and
gets back what it started with. `^spx` (the S&P 500 index) and GBP (a second ECB currency, to
exercise multi-currency parsing) have no golden transaction at all, so their anchors are only
"one start, one end", chosen freely, with no other meaning.
"""

import argparse
import io
import json
import sys
import zipfile
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

FIXTURES_DIR = Path(__file__).resolve().parent
STOOQ_DIR = FIXTURES_DIR / "stooq"
ECB_DIR = FIXTURES_DIR / "ecb"
OPENFIGI_DIR = FIXTURES_DIR / "openfigi"
INDEX_PATH = FIXTURES_DIR / "index.yaml"

STOOQ_URL = "https://stooq.com/q/d/l/"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# The exact range every e2e prices/benchmarks fetch step asks for (plan 7.6 steps 20 and 23);
# request matching is exact, so the fixtures must be requested with these same d1/d2 values.
FETCH_FROM = date(2024, 1, 1)
FETCH_TO = date(2024, 12, 31)

YEAR = 2024
CENTS = Decimal("0.01")
BASIS_POINTS = Decimal("0.0001")

# plan 6.4: "skipping Xetra holidays (1 Jan, 29 Mar, 1 Apr, 1 May, 24 to 26 Dec, 31 Dec 2024)".
XETRA_HOLIDAYS = {
    date(2024, 1, 1),
    date(2024, 3, 29),
    date(2024, 4, 1),
    date(2024, 5, 1),
    date(2024, 12, 24),
    date(2024, 12, 25),
    date(2024, 12, 26),
    date(2024, 12, 31),
}
# The real 2024 NYSE holiday calendar (plan 6.4: "NYSE holidays for .us symbols"; not itself
# pinned by the plan, so this is the actual published calendar rather than an invented one).
NYSE_HOLIDAYS = {
    date(2024, 1, 1),
    date(2024, 1, 15),
    date(2024, 2, 19),
    date(2024, 3, 29),
    date(2024, 5, 27),
    date(2024, 6, 19),
    date(2024, 7, 4),
    date(2024, 9, 2),
    date(2024, 11, 28),
    date(2024, 12, 25),
}
# plan 6.6: "TARGET holidays skipped (1 Jan, 29 Mar, 1 Apr, 1 May, 25 and 26 Dec)".
TARGET_HOLIDAYS = {
    date(2024, 1, 1),
    date(2024, 3, 29),
    date(2024, 4, 1),
    date(2024, 5, 1),
    date(2024, 12, 25),
    date(2024, 12, 26),
}


def trading_days(holidays: set[date]) -> list[date]:
    """Every Monday-to-Friday date in `YEAR` that is not in `holidays`, ascending."""
    days = []
    day = date(YEAR, 1, 1)
    one_day = timedelta(days=1)
    while day.year == YEAR:
        if day.weekday() < 5 and day not in holidays:
            days.append(day)
        day += one_day
    return days


def interpolate(anchors: Sequence[tuple[date, Decimal]], days: Sequence[date], quant: Decimal) -> dict[date, Decimal]:
    """A straight line between each pair of consecutive `anchors`, sampled at `days` (plan 6.4).

    Every `anchors` date must be one of `days`; interpolation is by POSITION among the trading
    days between two anchors, not by calendar-day count, so a run of holidays never distorts the
    line. Every anchor's own value is reproduced exactly (this is what
    `test_fixture_series.py` checks).
    """
    by_date = dict(anchors)
    result: dict[date, Decimal] = {}
    for (start_date, start_value), (end_date, end_value) in zip(anchors, anchors[1:], strict=False):
        segment = [day for day in days if start_date <= day <= end_date]
        if not segment or segment[0] != start_date or segment[-1] != end_date:
            raise ValueError(f"{start_date} and {end_date} must both be trading days in `days`.")
        steps = len(segment) - 1
        if steps == 0:
            raise ValueError(f"{start_date} and {end_date} are the same trading day.")
        for index, day in enumerate(segment):
            value = start_value + (end_value - start_value) * Decimal(index) / Decimal(steps)
            result[day] = value.quantize(quant, rounding=ROUND_HALF_UP)
    # Make sure every literal anchor value survives quantisation exactly, not just "close enough".
    for anchor_date, anchor_value in by_date.items():
        result[anchor_date] = anchor_value.quantize(quant, rounding=ROUND_HALF_UP)
    return result


TARGET_DAYS = trading_days(TARGET_HOLIDAYS)
XETRA_DAYS = trading_days(XETRA_HOLIDAYS)
NYSE_DAYS = trading_days(NYSE_HOLIDAYS)

# --- ECB reference rates (plan 6.6) -------------------------------------------------------------

# USD anchors are the plan's own figures (1.2, 6.6): 1.0900 on 2024-01-02, 1.0800 on 2024-05-31,
# 1.0400 on 2024-12-31. GBP is a second currency purely to exercise multi-currency parsing; its
# anchors are otherwise free.
ECB_ANCHORS: dict[str, list[tuple[date, Decimal]]] = {
    "USD": [
        (date(2024, 1, 2), Decimal("1.0900")),
        (date(2024, 5, 31), Decimal("1.0800")),
        (date(2024, 12, 31), Decimal("1.0400")),
    ],
    "GBP": [
        (date(2024, 1, 2), Decimal("0.8600")),
        (date(2024, 5, 31), Decimal("0.8500")),
        (date(2024, 12, 31), Decimal("0.8300")),
    ],
}
ECB_SERIES: dict[str, dict[date, Decimal]] = {
    currency: interpolate(anchors, TARGET_DAYS, BASIS_POINTS) for currency, anchors in ECB_ANCHORS.items()
}


def ecb_rate(currency: str, day: date) -> Decimal:
    """The fixture ECB rate for `currency` on `day` (must be a TARGET trading day)."""
    return ECB_SERIES[currency][day]


def eur_to_listing_currency(eur_amount: Decimal, currency: str, day: date) -> Decimal:
    """`eur_amount` converted to `currency` at the fixture ECB rate on `day` (3.3's `to_eur`, inverted)."""
    if currency == "EUR":
        return eur_amount
    return eur_amount * ecb_rate(currency, day)


# --- Stooq daily bars (plan 6.4) ---------------------------------------------------------------

# Golden-portfolio document prices this file turns into anchors (plan 1.2): each is the EUR price
# the transaction's own document or CSV row prints, exactly as tests/fixtures/golden/README.md
# 6.79 shows it (verified against tests/fixtures/golden/expected/import1.json, which every trade
# anchor below matches field for field).
SAP_T2_PRICE_EUR = Decimal("140.00")  # 2024-01-15
SAP_T8_PRICE_EUR = Decimal("160.00")  # 2024-04-10
SAP_T12_PRICE_EUR = Decimal("175.00")  # 2024-06-12 (sell)
MSCIW_T3_PRICE_EUR = Decimal("80.00")  # 2024-02-01
MSCIW_T4_PRICE_EUR = Decimal("84.00")  # 2024-03-01
AAPL_T5_PRICE_EUR = Decimal("160.00")  # 2024-03-12
NVDA_T6_PRICE_EUR = Decimal("850.00")  # 2024-03-20, pre-split (10-for-1 on 2024-06-10)
NVDA_SPLIT_RATIO = Decimal(10)

_AAPL_T5_DATE = date(2024, 3, 12)
_NVDA_T6_DATE = date(2024, 3, 20)

STOOQ_SYMBOLS: dict[str, dict[str, Any]] = {
    "sap.de": {
        "currency": "EUR",
        "days": XETRA_DAYS,
        "anchors": [
            (date(2024, 1, 2), Decimal("138.00")),
            (date(2024, 1, 15), SAP_T2_PRICE_EUR),
            (date(2024, 4, 10), SAP_T8_PRICE_EUR),
            (date(2024, 5, 31), Decimal("170.00")),
            (date(2024, 6, 12), SAP_T12_PRICE_EUR),
            (date(2024, 12, 30), Decimal("236.00")),
        ],
    },
    "eunl.de": {
        "currency": "EUR",
        "days": XETRA_DAYS,
        "anchors": [
            (date(2024, 1, 2), Decimal("79.00")),
            (date(2024, 2, 1), MSCIW_T3_PRICE_EUR),
            (date(2024, 3, 1), MSCIW_T4_PRICE_EUR),
            (date(2024, 5, 31), Decimal("90.00")),
            (date(2024, 12, 30), Decimal("100.00")),
        ],
    },
    "aapl.us": {
        "currency": "USD",
        "days": NYSE_DAYS,
        "anchors": [
            (date(2024, 1, 2), Decimal("165.00")),
            (_AAPL_T5_DATE, eur_to_listing_currency(AAPL_T5_PRICE_EUR, "USD", _AAPL_T5_DATE)),
            (date(2024, 5, 31), Decimal("190.00")),
            (date(2024, 12, 31), Decimal("250.00")),
        ],
    },
    "nvda.us": {
        "currency": "USD",
        "days": NYSE_DAYS,
        "anchors": [
            (date(2024, 1, 2), Decimal("85.00")),
            (_NVDA_T6_DATE, eur_to_listing_currency(NVDA_T6_PRICE_EUR, "USD", _NVDA_T6_DATE) / NVDA_SPLIT_RATIO),
            (date(2024, 5, 31), Decimal("110.00")),
            (date(2024, 12, 31), Decimal("134.00")),
        ],
    },
    "^spx": {
        "currency": "USD",
        "days": NYSE_DAYS,
        "anchors": [(date(2024, 1, 2), Decimal("4700.00")), (date(2024, 12, 31), Decimal("5900.00"))],
    },
}
STOOQ_SERIES: dict[str, dict[date, Decimal]] = {
    symbol: interpolate(info["anchors"], info["days"], CENTS) for symbol, info in STOOQ_SYMBOLS.items()
}

# Stooq symbols that trigger an error path instead of a real series (plan 6.4, 7.2). None of
# these is a real Stooq convention; they exist only so a test can ask for them.
STOOQ_ERROR_SYMBOLS = {
    "no-data.invalid": ("stooq/no_data.txt", 200),
    "html-block.invalid": ("stooq/html_block.html", 200),
    "http-500.invalid": ("stooq/http_500.txt", 500),
}

# OpenFIGI fixtures (plan 6.7): one listing per golden ISIN, an empty result, and a 429.
OPENFIGI_LISTINGS: dict[str, dict[str, str]] = {
    "DE0007164600": {
        "figi": "BBG000C216C0",
        "ticker": "SAP",
        "exchCode": "GY",
        "name": "SAP SE",
        "securityType": "Common Stock",
    },
    "IE00B4L5Y983": {
        "figi": "BBG00B4L5Y98",
        "ticker": "EUNL",
        "exchCode": "GY",
        "name": "ISHARES III PLC-CORE MSCI WORLD",
        "securityType": "ETP",
    },
    "US0378331005": {
        "figi": "BBG000B9XRY4",
        "ticker": "AAPL",
        "exchCode": "US",
        "name": "APPLE INC",
        "securityType": "Common Stock",
    },
    "US67066G1040": {
        "figi": "BBG000BBJQV0",
        "ticker": "NVDA",
        "exchCode": "US",
        "name": "NVIDIA CORP",
        "securityType": "Common Stock",
    },
    "DE0008404005": {
        "figi": "BBG000BB5476",
        "ticker": "ALV",
        "exchCode": "GY",
        "name": "ALLIANZ SE-REG",
        "securityType": "Common Stock",
    },
}
# Synthetic ISINs (valid check digit, no real security) used only to exercise the "nothing found"
# and "rate limited" responses without reusing a golden ISIN for something it does not mean.
OPENFIGI_EMPTY_ISIN = "GB9999999998"
OPENFIGI_RATE_LIMITED_ISIN = "US9999999991"


def openfigi_body(isin: str) -> str:
    """The exact request body `OpenFigiClient.suggest` sends for `isin` (must match byte for byte)."""
    return json.dumps([{"idType": "ID_ISIN", "idValue": isin}])


def _rows_equal(existing: bytes, content: bytes) -> bool:
    return existing == content


def _write(path: Path, content: bytes, *, out_of_date: list[str], check: bool) -> None:
    relative = path.relative_to(FIXTURES_DIR.parent.parent).as_posix()
    if path.is_file() and _rows_equal(path.read_bytes(), content):
        return
    out_of_date.append(relative)
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def stooq_csv(series: dict[date, Decimal]) -> bytes:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for day in sorted(series):
        close = series[day]
        lines.append(f"{day.isoformat()},{close},{close},{close},{close},100000")
    return ("\n".join(lines) + "\n").encode("ascii")


def ecb_csv_text(series_by_currency: dict[str, dict[date, Decimal]]) -> str:
    """The ECB's own CSV shape: `Date,<CCY>,...` with `N/A` for a missing day, trailing comma (6.6)."""
    currencies = sorted(series_by_currency)
    lines = ["Date, " + ", ".join(currencies) + ", "]
    for day in sorted(TARGET_DAYS, reverse=True):  # the real feed lists newest first
        cells = [day.isoformat()]
        for currency in currencies:
            value = series_by_currency[currency].get(day)
            cells.append(str(value) if value is not None else "N/A")
        lines.append(", ".join(cells) + ", ")
    return "\n".join(lines) + "\n"


def ecb_zip_bytes(csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # A fixed timestamp keeps the zip byte-identical between runs (like make_pdfs.py's
        # invariant=1 for the PDF fixtures), so re-running this script twice changes nothing.
        info = zipfile.ZipInfo("eurofxref-hist.csv", date_time=(2024, 1, 1, 0, 0, 0))
        archive.writestr(info, csv_text)
    return buffer.getvalue()


def ecb_na_csv_bytes() -> bytes:
    """A short, standalone CSV (not the full year) with an explicit N/A value (6.6's `-na` fixture)."""
    days = sorted(TARGET_DAYS)[:3]
    lines = ["Date, USD, GBP, "]
    for index, day in enumerate(days):
        usd = "N/A" if index == 1 else str(ecb_rate("USD", day))
        lines.append(f"{day.isoformat()}, {usd}, {ecb_rate('GBP', day)}, ")
    return ("\n".join(lines) + "\n").encode("ascii")


def openfigi_response_bytes(isin: str) -> bytes:
    listing = OPENFIGI_LISTINGS.get(isin)
    payload = [{"data": [listing]}] if listing else [{"warning": "No identifier found."}]
    return json.dumps(payload, indent=2).encode("utf-8") + b"\n"


def openfigi_empty_bytes() -> bytes:
    return json.dumps([{"data": []}], indent=2).encode("utf-8") + b"\n"


def openfigi_rate_limited_bytes() -> bytes:
    return b'{"error": "Too many requests. Wait a while, then try again."}\n'


def build_index_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for symbol in STOOQ_SYMBOLS:
        entries.append(
            {
                "method": "GET",
                "url": STOOQ_URL,
                "query": {
                    "s": symbol,
                    "d1": FETCH_FROM.strftime("%Y%m%d"),
                    "d2": FETCH_TO.strftime("%Y%m%d"),
                    "i": "d",
                },
                "response": {"status": 200, "file": f"stooq/{_stooq_filename(symbol)}"},
            }
        )
    for symbol, (file, status) in STOOQ_ERROR_SYMBOLS.items():
        entries.append(
            {
                "method": "GET",
                "url": STOOQ_URL,
                "query": {
                    "s": symbol,
                    "d1": FETCH_FROM.strftime("%Y%m%d"),
                    "d2": FETCH_TO.strftime("%Y%m%d"),
                    "i": "d",
                },
                "response": {"status": status, "file": file},
            }
        )
    entries.append(
        {
            "method": "GET",
            "url": ECB_URL,
            "query": {},
            "response": {
                "status": 200,
                "file": "ecb/eurofxref-hist.zip",
                "headers": {"Content-Type": "application/zip"},
            },
        }
    )
    for isin in OPENFIGI_LISTINGS:
        entries.append(
            {
                "method": "POST",
                "url": OPENFIGI_URL,
                "body": openfigi_body(isin),
                "response": {
                    "status": 200,
                    "file": f"openfigi/{isin}.json",
                    "headers": {"Content-Type": "application/json"},
                },
            }
        )
    entries.append(
        {
            "method": "POST",
            "url": OPENFIGI_URL,
            "body": openfigi_body(OPENFIGI_EMPTY_ISIN),
            "response": {"status": 200, "file": "openfigi/empty.json", "headers": {"Content-Type": "application/json"}},
        }
    )
    entries.append(
        {
            "method": "POST",
            "url": OPENFIGI_URL,
            "body": openfigi_body(OPENFIGI_RATE_LIMITED_ISIN),
            "response": {
                "status": 429,
                "file": "openfigi/rate_limited.json",
                "headers": {"Content-Type": "application/json"},
            },
        }
    )
    return entries


def _stooq_filename(symbol: str) -> str:
    # "^spx" keeps its symbol in the URL and in this filename mapping, but not as a literal
    # filename on disk (a leading "^" is legal on Linux, but needless friction elsewhere).
    return {"^spx": "spx_index.csv"}.get(symbol, f"{symbol}.csv")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only check that every fixture is up to date")
    args = parser.parse_args(argv)

    out_of_date: list[str] = []
    for symbol, series in STOOQ_SERIES.items():
        _write(STOOQ_DIR / _stooq_filename(symbol), stooq_csv(series), out_of_date=out_of_date, check=args.check)

    _write(STOOQ_DIR / "no_data.txt", b"No data\n", out_of_date=out_of_date, check=args.check)
    _write(
        STOOQ_DIR / "html_block.html",
        b"<html><body><h1>Service Temporarily Unavailable</h1></body></html>\n",
        out_of_date=out_of_date,
        check=args.check,
    )
    _write(STOOQ_DIR / "http_500.txt", b"Internal Server Error\n", out_of_date=out_of_date, check=args.check)

    ecb_text = ecb_csv_text(ECB_SERIES)
    _write(ECB_DIR / "eurofxref-hist.zip", ecb_zip_bytes(ecb_text), out_of_date=out_of_date, check=args.check)
    _write(ECB_DIR / "eurofxref-hist-na.csv", ecb_na_csv_bytes(), out_of_date=out_of_date, check=args.check)
    _write(ECB_DIR / "not_a_zip.bin", b"this is not a zip file\n", out_of_date=out_of_date, check=args.check)

    for isin in OPENFIGI_LISTINGS:
        _write(OPENFIGI_DIR / f"{isin}.json", openfigi_response_bytes(isin), out_of_date=out_of_date, check=args.check)
    _write(OPENFIGI_DIR / "empty.json", openfigi_empty_bytes(), out_of_date=out_of_date, check=args.check)
    _write(OPENFIGI_DIR / "rate_limited.json", openfigi_rate_limited_bytes(), out_of_date=out_of_date, check=args.check)

    index_text = yaml.safe_dump({"requests": build_index_entries()}, sort_keys=False, allow_unicode=True)
    _write(INDEX_PATH, index_text.encode("utf-8"), out_of_date=out_of_date, check=args.check)

    if args.check:
        if out_of_date:
            print(
                "These HTTP fixtures are out of date. Run tests/fixtures/http/make_series.py to rewrite them:",
                file=sys.stderr,
            )
            for path in out_of_date:
                print(f"  {path}", file=sys.stderr)
            return 1
        print("All HTTP fixtures are up to date.")
        return 0
    print(f"Wrote {len(out_of_date)} fixture file(s); the rest were already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
