"""`tests/fixtures/http/make_series.py`'s own tests (plan 6.4, 7.2): "the fixture series pass through their anchors".

This loads the generator script as a module (the same trick `tests/golden/test_golden_portfolio.py`
uses for `scripts/assert_golden.py`) so its anchor tables are the single source of truth: if an
anchor changes, this test changes with it, with nothing to keep in sync by hand. It also checks
that the *committed* fixture files on disk still match what the script would write today -- the
same property `--check` gives you on the command line -- and reads the stooq CSV and ECB zip
through the real client code, not just the generator's own in-memory series, so a mismatch between
what the script writes and what the client can read would also fail here.
"""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from playground.http.replay import ReplayHttpClient
from playground.marketdata.ecb import EcbClient
from playground.marketdata.stooq import StooqClient

REPO = Path(__file__).resolve().parents[2]
HTTP_FIXTURES = REPO / "tests" / "fixtures" / "http"


def load_make_series() -> ModuleType:
    if "make_series" in sys.modules:
        return sys.modules["make_series"]
    spec = importlib.util.spec_from_file_location("make_series", HTTP_FIXTURES / "make_series.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_series"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def make_series() -> ModuleType:
    return load_make_series()


def test_every_stooq_symbol_passes_through_its_own_anchors(make_series: ModuleType) -> None:
    for symbol, info in make_series.STOOQ_SYMBOLS.items():
        series = make_series.STOOQ_SERIES[symbol]
        for anchor_date, anchor_value in info["anchors"]:
            assert series[anchor_date] == anchor_value.quantize(make_series.CENTS), f"{symbol} on {anchor_date}"


def test_every_ecb_currency_passes_through_its_own_anchors(make_series: ModuleType) -> None:
    for currency, anchors in make_series.ECB_ANCHORS.items():
        series = make_series.ECB_SERIES[currency]
        for anchor_date, anchor_value in anchors:
            assert series[anchor_date] == anchor_value, f"{currency} on {anchor_date}"


def test_the_committed_fixture_files_are_up_to_date(make_series: ModuleType) -> None:
    """The same property `python make_series.py --check` gives you, run as a normal test."""
    assert make_series.main(["--check"]) == 0


def test_the_nvda_split_adjusted_trade_anchor_recovers_the_documents_eur_price(make_series: ModuleType) -> None:
    """6.4: the price-basis check (5.7) must find no mismatch anywhere in the golden portfolio.

    NVDA's document price (T6, 1.2) is 850.00 EUR for 2 shares bought on 2024-03-20, before the
    10-for-1 split on 2024-06-10. The stored series is `split_dividend` (always post-split terms,
    5.7), so re-deriving the pre-split EUR price means: take the close, convert to EUR at that
    day's fixture ECB rate, then multiply by the split ratio that happened after that day.
    """
    close = make_series.STOOQ_SERIES["nvda.us"][make_series._NVDA_T6_DATE]
    rate = make_series.ecb_rate("USD", make_series._NVDA_T6_DATE)
    recovered_eur = (close / rate) * make_series.NVDA_SPLIT_RATIO
    assert abs(recovered_eur - make_series.NVDA_T6_PRICE_EUR) < Decimal("0.10")


def test_the_aapl_trade_anchor_recovers_the_documents_eur_price(make_series: ModuleType) -> None:
    close = make_series.STOOQ_SERIES["aapl.us"][make_series._AAPL_T5_DATE]
    rate = make_series.ecb_rate("USD", make_series._AAPL_T5_DATE)
    recovered_eur = close / rate
    assert abs(recovered_eur - make_series.AAPL_T5_PRICE_EUR) < Decimal("0.10")


def test_the_sap_and_msciw_trade_anchors_are_exactly_the_documents_eur_price(make_series: ModuleType) -> None:
    # SAP and MSCIW are EUR-listed, so their trade-date anchors need no FX conversion at all.
    assert make_series.STOOQ_SERIES["sap.de"][make_series.date(2024, 1, 15)] == make_series.SAP_T2_PRICE_EUR
    assert make_series.STOOQ_SERIES["sap.de"][make_series.date(2024, 4, 10)] == make_series.SAP_T8_PRICE_EUR
    assert make_series.STOOQ_SERIES["sap.de"][make_series.date(2024, 6, 12)] == make_series.SAP_T12_PRICE_EUR
    assert make_series.STOOQ_SERIES["eunl.de"][make_series.date(2024, 2, 1)] == make_series.MSCIW_T3_PRICE_EUR
    assert make_series.STOOQ_SERIES["eunl.de"][make_series.date(2024, 3, 1)] == make_series.MSCIW_T4_PRICE_EUR


# --- reading the committed files back through the real client code ------------------------------


def test_the_committed_stooq_files_read_back_through_the_client(
    replay_http_client: ReplayHttpClient, make_series: ModuleType
) -> None:
    client = StooqClient(replay_http_client)
    for symbol, series_by_date in make_series.STOOQ_SERIES.items():
        series = client.daily_bars(symbol, make_series.FETCH_FROM, make_series.FETCH_TO, currency="EUR")
        by_date = {point.date: point.close for point in series.points}
        assert by_date == series_by_date, symbol


def test_the_committed_ecb_zip_reads_back_through_the_client(
    replay_http_client: ReplayHttpClient, make_series: ModuleType
) -> None:
    client = EcbClient(replay_http_client)
    table = client.history()
    by_key = {(point.date, point.currency): point.rate for point in table.points}
    for currency, series_by_date in make_series.ECB_SERIES.items():
        for day, rate in series_by_date.items():
            assert by_key[(day, currency)] == rate
