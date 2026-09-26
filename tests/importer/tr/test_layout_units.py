"""Unit tests for the shared line grammar and the four trade layouts (plan 5.3.2).

The golden tests (`test_layout_goldens.py`) pin the full output of every fixture. These tests
pin the rules behind it, mostly on variants of a fixture with one line changed: header values,
the ISIN line, quantities, the settlement block (ABRECHNUNG, "billing"), the booking block
(BUCHUNG), the FX line, times, `source_ref`, the amounts check, and what happens when a
document is damaged. A parser never raises for a layout problem: it returns what it could read
plus a review result.
"""

import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest

from playground.core.isin import is_valid_isin
from playground.importer.model import ParseResult, ReviewKind, SourceKind
from playground.importer.serialise import review_to_dict, transaction_to_dict
from playground.importer.tr.classify import PARSERS
from playground.importer.tr.layouts.common import (
    ENGLISH,
    GERMAN,
    DocText,
    FxLine,
    LayoutProblem,
    berlin_source_time,
    header_value,
    is_trade_republic_document,
    parse_fx_line,
    position_tolerance,
    read_booking_block,
    read_isin,
    read_position_block,
    read_settlement_block,
)
from playground.importer.tr.layouts.corporate_action import CORPORATE_ACTION
from playground.importer.tr.layouts.dividende import DIVIDENDE
from playground.importer.tr.layouts.kontoauszug_2023 import KONTOAUSZUG_2023
from playground.importer.tr.layouts.kontoauszug_2024 import KONTOAUSZUG_2024
from playground.importer.tr.layouts.settlement_en import SETTLEMENT_EN_2023
from playground.importer.tr.layouts.split import SPLIT
from playground.importer.tr.layouts.steuer import STEUER
from playground.importer.tr.layouts.wertpapierabrechnung import (
    SPARPLAN,
    WERTPAPIERABRECHNUNG_2019,
    WERTPAPIERABRECHNUNG_2023,
)
from playground.importer.tr.layouts.zinsen import ZINSEN

TEXT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "tr" / "text"
T02 = "tr.wertpapierabrechnung.de.2023/golden_t02_kauf_sap"
T12 = "tr.wertpapierabrechnung.de.2023/golden_t12_verkauf_sap"
T03 = "tr.sparplan.de/golden_t03_sparplan_msciw"
T09 = "tr.dividende.de/golden_t09_dividende_sap"
T10 = "tr.dividende.de/golden_t10_dividende_aapl"
T11 = "tr.split.de/golden_t11_split_nvda"

# The invented personal data used in the fixtures. None of it may reach a parsed field.
PERSONAL = (
    "Jana Beispiel",
    "Ahornweg",
    "Musterstadt",
    "Tom Probe",
    "Birkenallee",
    "Beispielheim",
    "Lena Fiktiv",
    "Lindenring",
    "Probedorf",
    "0000001111",
    "0000002222",
    "0000003333",
    "DE00000000000000000000",
    "DE00 0000",
)


def fixture(case: str) -> str:
    return (TEXT_DIR / f"{case}.txt").read_text(encoding="utf-8")


def variant(case: str, *replacements: tuple[str, str]) -> str:
    """The fixture text with each `old` replaced by `new`; each `old` must occur exactly once."""
    text = fixture(case)
    for old, new in replacements:
        assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {case}"
        text = text.replace(old, new)
    return text


def without_line(case: str, line: str) -> str:
    return variant(case, (line + "\n", ""))


def only_review(result: ParseResult) -> dict[str, object]:
    assert len(result.review) == 1, result.review
    return review_to_dict(result.review[0])


# --- Header block -------------------------------------------------------------------------


def test_header_values_are_read_from_the_end_of_header_lines() -> None:
    doc = DocText(fixture(T02))
    assert header_value(doc, "DATUM") == "15.01.2024"
    assert header_value(doc, "ORDER") == "b7c2-41e9"
    assert header_value(doc, "AUSFÜHRUNG") == "5d0a-93f1"
    assert header_value(doc, "SPARPLAN") is None
    english = DocText(fixture("tr.settlement.en.2023/buy_market_winter"))
    assert header_value(english, "DATE") == "05.02.2024"
    assert header_value(english, "EXECUTION") == "0b8c-37a9"


def test_header_value_only_searches_the_lines_before_stop() -> None:
    doc = DocText("TRADE REPUBLIC BANK GMBH X\nWERTPAPIERABRECHNUNG\nNotiz ORDER 1234-abcd\n")
    assert header_value(doc, "ORDER", stop=2) is None
    assert header_value(doc, "ORDER") == "1234-abcd"


def test_the_bank_line_must_start_a_line() -> None:
    assert is_trade_republic_document(DocText("TRADE REPUBLIC BANK GMBH BRUNNENSTRASSE 19-21 10119 BERLIN\n"))
    assert not is_trade_republic_document(DocText("Kopie: TRADE REPUBLIC BANK GMBH\n"))
    assert not is_trade_republic_document(DocText(""))


def test_doc_text_splits_lines_on_newlines_and_form_feeds() -> None:
    doc = DocText("a\nb\fc\nd\n")
    assert doc.lines == ("a", "b", "c", "d")


# --- ISIN line ----------------------------------------------------------------------------


@pytest.mark.parametrize("line", ["ISIN: DE0007164600", "ISIN:DE0007164600", "DE0007164600"])
def test_isin_line_with_or_without_the_label(line: str) -> None:
    assert read_isin(line) == "DE0007164600"


@pytest.mark.parametrize("line", ["Inhaber-Aktien o.N.", "ISIN: DE000716460", "SAP SE DE0007164600", ""])
def test_lines_that_are_not_an_isin_line(line: str) -> None:
    assert read_isin(line) is None


def test_an_invalid_isin_gives_an_invalid_isin_review_and_no_transaction() -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse(variant(T02, ("ISIN: DE0007164600", "ISIN: DE0007164601")))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "invalid_isin"
    assert item["fields"]["isin"] == "DE0007164601"  # type: ignore[index]
    assert item["message"] == "The ISIN DE0007164601 in this document is not valid: its check digit does not match."


# --- Position block and quantities --------------------------------------------------------


def test_german_quantity_with_thousands_dot_and_decimal_comma() -> None:
    doc = DocText(
        "POSITION ANZAHL PREIS BETRAG\nBeispiel AG 1.234,5678 Stk. 1,00 EUR 1.234,57 EUR\n"
        "Inhaber-Aktien o.N.\nISIN: DE0007164600\nGESAMT 1.234,57 EUR\n"
    )
    block = read_position_block(doc, GERMAN, ("POSITION ANZAHL PREIS BETRAG",), start=0)
    assert block.name == "Beispiel AG"
    assert block.quantity == Decimal("1234.5678")
    assert block.price == Decimal("1.00")
    assert block.amount == Decimal("1234.57")
    assert block.isin == "DE0007164600"
    assert block.total == Decimal("1234.57")


def test_english_quantity_with_pcs_and_dot_decimals() -> None:
    doc = DocText(
        "POSITION QUANTITY PRICE AMOUNT\nExample Inc. 1,234.5 Pcs. 2.00 EUR 2,469.00 EUR\n"
        "ISIN: US0378331005\nTOTAL 2,469.00 EUR\n"
    )
    block = read_position_block(doc, ENGLISH, ("POSITION QUANTITY PRICE AMOUNT",), start=0)
    assert block.quantity == Decimal("1234.5")
    assert block.amount == Decimal("2469.00")
    assert block.isin == "US0378331005"


def test_the_position_is_read_in_another_currency_only_when_allowed() -> None:
    # A dividend note (WP4) prints its position in the paying currency; the trade layouts only allow EUR.
    doc = DocText(
        "POSITION ANZAHL ERTRÄGNIS BETRAG\nApple Inc. 5 Stk. 0,24 USD 1,20 USD\nISIN: US0378331005\nGESAMT 1,20 USD\n"
    )
    headers = ("POSITION ANZAHL ERTRÄGNIS BETRAG",)
    block = read_position_block(doc, GERMAN, headers, start=0, currencies=("USD", "EUR"))
    assert (block.price, block.amount, block.total, block.currency) == (
        Decimal("0.24"),
        Decimal("1.20"),
        Decimal("1.20"),
        "USD",
    )
    with pytest.raises(LayoutProblem) as problem:
        read_position_block(doc, GERMAN, headers, start=0)
    assert problem.value.missing == "position"


def test_a_position_total_in_another_currency_is_not_the_position_total() -> None:
    doc = DocText(
        "POSITION ANZAHL PREIS BETRAG\nSAP SE 1 Stk. 1,00 EUR 1,00 EUR\nISIN: DE0007164600\nGESAMT 1,00 USD\n"
    )
    with pytest.raises(LayoutProblem) as problem:
        read_position_block(doc, GERMAN, ("POSITION ANZAHL PREIS BETRAG",), start=0)
    assert problem.value.missing == "position_total"


def test_a_trade_in_another_currency_is_not_read() -> None:
    text = variant(T02, ("SAP SE 10 Stk. 140,00 EUR 1.400,00 EUR", "SAP SE 10 Stk. 140,00 USD 1.400,00 USD"))
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert result.transactions == []
    item = only_review(result)
    assert item["fields"]["missing"] == "position"  # type: ignore[index]
    assert item["message"] == "The position line with quantity, price and amount in EUR was not found."


def test_a_german_layout_never_reads_english_numbers() -> None:
    # The layout decides the locale; "140.00" is not a German number, so nothing is guessed.
    text = variant(T02, ("SAP SE 10 Stk. 140,00 EUR 1.400,00 EUR", "SAP SE 10 Stk. 140.00 EUR 1,400.00 EUR"))
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == "position"  # type: ignore[index]
    assert item["message"] == 'The number "140.00" in line 12 could not be read.'


# --- Settlement block (ABRECHNUNG, "billing") ---------------------------------------------


def test_settlement_block_reads_the_fee_and_each_tax_line_by_name() -> None:
    doc = DocText(
        "ABRECHNUNG\nPOSITION BETRAG\nFremdkostenzuschlag -1,00 EUR\nKapitalertragssteuer -10,00 EUR\n"
        "Kapitalertragsteuer -2,00 EUR\nSolidaritätszuschlag -0,66 EUR\nKirchensteuer -0,96 EUR\n"
        "Quellensteuer -1,50 EUR\nGESAMT 100,00 EUR\n"
    )
    block = read_settlement_block(doc, GERMAN, start=0)
    assert block is not None
    assert block.total == Decimal("100.00")
    assert block.fees_eur == Decimal("1.00")
    assert block.tax_detail == {
        "kapitalertragssteuer": Decimal("12.00"),
        "solidaritaetszuschlag": Decimal("0.66"),
        "kirchensteuer": Decimal("0.96"),
        "quellensteuer": Decimal("1.50"),
    }
    assert block.tax_eur == Decimal("15.12")
    assert [entry.category for entry in block.entries] == ["fee", "tax", "tax", "tax", "tax", "tax"]


def test_settlement_block_reads_other_real_cost_lines_and_tax_refunds() -> None:
    # Seen in real documents: a flat settlement fee, the French financial transaction tax (a cost
    # of buying, so a fee here) and the "Optimierung" lines that refund tax through loss offsetting.
    doc = DocText(
        "ABRECHNUNG\nPOSITION BETRAG\nAbwicklungspauschale -1,00 EUR\nFrz. Finanztransaktionssteuer -3,00 EUR\n"
        "Finanztransaktionssteuer -0,08 EUR\nKapitalertragsteuer Optimierung 4,56 EUR\n"
        "Kapitalertragssteuer Optimierung 1,00 EUR\nSolidaritätszuschlag Optimierung 0,25 EUR\n"
        "Kirchensteuer Optimierung 0,36 EUR\nGESAMT 100,00 EUR\n"
    )
    block = read_settlement_block(doc, GERMAN, start=0)
    assert block is not None
    assert block.unknown_entries == ()
    assert block.fees_eur == Decimal("4.08")
    assert block.tax_detail == {
        "kapitalertragssteuer": Decimal("-5.56"),
        "solidaritaetszuschlag": Decimal("-0.25"),
        "kirchensteuer": Decimal("-0.36"),
    }
    assert block.tax_eur == Decimal("-6.17")


def test_settlement_block_reads_the_english_labels() -> None:
    doc = DocText(
        "BILLING\nPOSITION AMOUNT\nExternal cost surcharge -1.00 EUR\nCapital Gains Tax -9.12 EUR\n"
        "Solidarity Surcharge -0.50 EUR\nChurch Tax -0.73 EUR\nWithholding Tax -0.30 EUR\nTOTAL 1,000.00 EUR\n"
    )
    block = read_settlement_block(doc, ENGLISH, start=0)
    assert block is not None
    assert block.total == Decimal("1000.00")
    assert block.fees_eur == Decimal("1.00")
    assert block.tax_detail == {
        "kapitalertragssteuer": Decimal("9.12"),
        "solidaritaetszuschlag": Decimal("0.50"),
        "kirchensteuer": Decimal("0.73"),
        "quellensteuer": Decimal("0.30"),
    }


def test_no_settlement_block_gives_none() -> None:
    assert read_settlement_block(DocText(fixture(T03)), GERMAN, start=0) is None


def test_a_settlement_block_without_its_total_is_a_missing_field() -> None:
    with pytest.raises(LayoutProblem) as problem:
        read_settlement_block(DocText("ABRECHNUNG\nPOSITION BETRAG\nFremdkostenzuschlag -1,00 EUR\n"), GERMAN, start=0)
    assert problem.value.kind is ReviewKind.MISSING_FIELD
    assert problem.value.missing == "settlement_total"


def test_an_unknown_settlement_line_gives_unparsed_row_and_keeps_the_transaction() -> None:
    text = variant(
        T12,
        (
            "Solidaritätszuschlag -5,19 EUR\n",
            "Solidaritätszuschlag -5,19 EUR\nSonderposten 4,56 EUR\n",
        ),
        ("GESAMT 1.999,41 EUR", "GESAMT 2.003,97 EUR"),
        ("14.06.2024 1.999,41 EUR", "14.06.2024 2.003,97 EUR"),
    )
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert len(result.transactions) == 1
    txn = result.transactions[0]
    assert txn.amount == Decimal("2003.97")
    assert txn.fees_eur == Decimal("1.00")
    assert txn.tax_eur == Decimal("99.59")  # the unknown line is counted neither as a fee nor as a tax
    item = only_review(result)
    assert item["kind"] == "unparsed_row"
    assert item["message"] == (
        "Line 21 of the settlement block (ABRECHNUNG) is not a known cost or tax: "
        '"Sonderposten 4,56 EUR". Its amount is not counted as a fee or a tax.'
    )
    assert item["fields"]["line"] == "21"  # type: ignore[index]


def test_a_positive_tax_line_is_a_refund() -> None:
    text = variant(
        T12,
        ("Kapitalertragsteuer -94,40 EUR\nSolidaritätszuschlag -5,19 EUR\n", "Kapitalertragsteuer 5,00 EUR\n"),
        ("GESAMT 1.999,41 EUR", "GESAMT 2.104,00 EUR"),
        ("14.06.2024 1.999,41 EUR", "14.06.2024 2.104,00 EUR"),
    )
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert result.review == []
    txn = result.transactions[0]
    assert txn.tax_detail == {"kapitalertragssteuer": Decimal("-5.00")}
    assert txn.tax_eur == Decimal("-5.00")


# --- Booking block (BUCHUNG) --------------------------------------------------------------


@pytest.mark.parametrize("column", ["VALUTA", "WERTSTELLUNG", "BUCHUNGSDATUM"])
def test_booking_block_accepts_each_date_column(column: str) -> None:
    doc = DocText(f"BUCHUNG\nVERRECHNUNGSKONTO {column} BETRAG\nDE00000000000000000000 17.01.2024 -1.401,00 EUR\n")
    entry = read_booking_block(doc, GERMAN, start=0)
    assert entry.date_label == column
    assert entry.booking_date == date(2024, 1, 17)
    assert entry.amount == Decimal("-1401.00")
    assert entry.line_number == 3


def test_booking_date_in_iso_form_and_account_with_spaces() -> None:
    doc = DocText("BUCHUNG\nVERRECHNUNGSKONTO WERTSTELLUNG BETRAG\nDE00 0000 0000 0000 0000 00 2024-03-07 536,00 EUR\n")
    entry = read_booking_block(doc, GERMAN, start=0)
    assert entry.booking_date == date(2024, 3, 7)
    assert entry.amount == Decimal("536.00")


def test_english_booking_block() -> None:
    doc = DocText("BOOKING\nCLEARING ACCOUNT VALUE DATE AMOUNT\nDE00000000000000000000 07.02.2024 -1,302.52 EUR\n")
    entry = read_booking_block(doc, ENGLISH, start=0)
    assert entry.date_label == "VALUE DATE"
    assert entry.booking_date == date(2024, 2, 7)
    assert entry.amount == Decimal("-1302.52")


def test_a_missing_booking_block_is_a_missing_field() -> None:
    with pytest.raises(LayoutProblem) as problem:
        read_booking_block(DocText("BUCHUNG\n"), GERMAN, start=0)
    assert problem.value.kind is ReviewKind.MISSING_FIELD
    assert problem.value.missing == "booking"


# --- FX line (used by the dividend and tax layouts of WP4) --------------------------------


def test_fx_line() -> None:
    assert parse_fx_line("Zwischensumme 1,102 EUR/USD 5,11 EUR") == FxLine(
        rate=Decimal("1.102"), currency="USD", amount_eur=Decimal("5.11")
    )
    assert parse_fx_line("Zwischensumme 1,0800 EUR/USD 1,11 EUR") == FxLine(
        rate=Decimal("1.0800"), currency="USD", amount_eur=Decimal("1.11")
    )
    assert parse_fx_line("GESAMT 5,11 EUR") is None
    assert parse_fx_line("Zwischensumme 1,102 EUR/usd 5,11 EUR") is None


# --- Times --------------------------------------------------------------------------------


def test_berlin_source_time_in_winter_summer_and_by_day() -> None:
    winter = berlin_source_time(date(2024, 3, 20), time(15, 45))
    assert winter.ts_utc == datetime(2024, 3, 20, 14, 45, tzinfo=UTC)
    assert winter.ts_local == datetime.combine(date(2024, 3, 20), time(15, 45))
    assert winter.source_tz == "Europe/Berlin"
    assert winter.precision == "minute"
    summer = berlin_source_time(date(2024, 3, 31), time(3, 0))
    assert summer.ts_utc == datetime(2024, 3, 31, 1, 0, tzinfo=UTC)
    by_day = berlin_source_time(date(2024, 2, 1))
    assert by_day.ts_local == datetime.combine(date(2024, 2, 1), time(0, 0))
    assert by_day.ts_utc == datetime(2024, 1, 31, 23, 0, tzinfo=UTC)
    assert by_day.precision == "day"


def test_a_trade_time_that_does_not_exist_gives_a_review() -> None:
    text = variant(T02, ("am 15.01.2024, um 10:05 Uhr", "am 31.03.2024, um 02:30 Uhr"))
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == "execution"  # type: ignore[index]
    assert item["message"] == (
        "The trade time 31.03.2024 02:30 does not exist in Europe/Berlin (it falls in the hour skipped when summer time starts)."
    )


def test_an_impossible_trade_time_gives_a_review() -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse(variant(T02, ("um 10:05 Uhr", "um 25:05 Uhr")))
    assert result.transactions == []
    item = only_review(result)
    assert item["fields"]["missing"] == "execution"  # type: ignore[index]


def test_another_time_zone_gives_a_review() -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse(variant(T02, ("(Europe/Berlin)", "(America/New_York)")))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == "time_zone"  # type: ignore[index]
    assert (
        item["message"] == "The trade time is given in the time zone America/New_York. Only Europe/Berlin is supported."
    )


def test_german_documents_may_print_the_side_in_english() -> None:
    # Real German documents from 2024 on print "Market-Order BUY am ..." and "SELL am ...".
    buy = WERTPAPIERABRECHNUNG_2023.parse(variant(T02, ("Market-Order Kauf am", "Market-Order BUY am")))
    assert buy.review == []
    assert buy.transactions[0].type.value == "buy"
    sell = WERTPAPIERABRECHNUNG_2023.parse(variant(T12, ("Market-Order Verkauf am", "Market-Order SELL am")))
    assert sell.review == []
    assert sell.transactions[0].type.value == "sell"


def test_german_execution_line_without_comma_and_uhr() -> None:
    # Real German documents from 2026 print "am 30.06.2026 um 22:54 (Europe/Berlin)."
    text = variant(T02, ("am 15.01.2024, um 10:05 Uhr (Europe/Berlin)", "am 15.01.2024 um 10:05 (Europe/Berlin)"))
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert result.review == []
    assert result.transactions[0].time.ts_utc == datetime(2024, 1, 15, 9, 5, tzinfo=UTC)


def test_the_2019_layout_reads_times_without_a_zone_as_berlin_time() -> None:
    txn = WERTPAPIERABRECHNUNG_2019.parse(fixture("tr.wertpapierabrechnung.de.2019/kauf_market_winter")).transactions[0]
    assert txn.time.ts_utc == datetime(2019, 11, 12, 14, 16, tzinfo=UTC)


# --- source_ref ---------------------------------------------------------------------------


def test_source_ref_prefers_the_execution_number_then_the_order_number() -> None:
    assert WERTPAPIERABRECHNUNG_2023.parse(fixture(T02)).transactions[0].source_ref == "5d0a-93f1"
    no_execution = without_line(T02, "AUSFÜHRUNG 5d0a-93f1")
    assert WERTPAPIERABRECHNUNG_2023.parse(no_execution).transactions[0].source_ref == "b7c2-41e9"
    neither = variant(T02, ("AUSFÜHRUNG 5d0a-93f1\n", ""), ("12345 Musterstadt ORDER b7c2-41e9", "12345 Musterstadt"))
    assert WERTPAPIERABRECHNUNG_2023.parse(neither).transactions[0].source_ref is None


def test_savings_plan_source_ref_is_the_execution_number_not_the_plan_number() -> None:
    assert SPARPLAN.parse(fixture(T03)).transactions[0].source_ref == "2c61-7e0b"


# --- Amounts check ------------------------------------------------------------------------


def test_position_tolerance_allows_for_the_rounding_of_the_printed_figures() -> None:
    # 0.01, plus half a unit of the last printed digit of the quantity (times the price)
    # and of the price (times the quantity). A whole-number quantity is exact.
    assert position_tolerance(Decimal("10"), Decimal("140.00")) == Decimal("0.06")
    assert position_tolerance(Decimal("5.5520"), Decimal("27.02")) == Decimal("0.039111")


def test_a_real_world_rounded_savings_plan_adds_up() -> None:
    # 5.5520 x 27.02 = 150.015, printed as 150.00: the printed quantity and price are rounded.
    text = variant(
        T03,
        ("2,5 Stk. 80,00 EUR 200,00 EUR", "5,5520 Stk. 27,02 EUR 150,00 EUR"),
        ("GESAMT 200,00 EUR", "GESAMT 150,00 EUR"),
        ("05.02.2024 -200,00 EUR", "05.02.2024 -150,00 EUR"),
    )
    result = SPARPLAN.parse(text)
    assert result.review == []
    assert result.transactions[0].quantity == Decimal("5.5520")


def test_a_sell_whose_fee_exceeds_the_proceeds_books_a_negative_amount() -> None:
    text = variant(
        T12,
        ("SAP SE 12 Stk. 175,00 EUR 2.100,00 EUR", "SAP SE 16 Stk. 0,02 EUR 0,34 EUR"),
        ("GESAMT 2.100,00 EUR", "GESAMT 0,34 EUR"),
        ("Kapitalertragsteuer -94,40 EUR\nSolidaritätszuschlag -5,19 EUR\n", ""),
        ("GESAMT 1.999,41 EUR", "GESAMT -0,66 EUR"),
        ("14.06.2024 1.999,41 EUR", "14.06.2024 -0,66 EUR"),
    )
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert result.review == []
    assert result.transactions[0].amount == Decimal("-0.66")


def test_quantity_times_price_must_match_the_position_amount() -> None:
    text = variant(
        T02,
        ("SAP SE 10 Stk. 140,00 EUR 1.400,00 EUR", "SAP SE 10 Stk. 140,00 EUR 1.410,00 EUR"),
        ("GESAMT 1.400,00 EUR", "GESAMT 1.410,00 EUR"),
        ("GESAMT -1.401,00 EUR", "GESAMT -1.411,00 EUR"),
        ("17.01.2024 -1.401,00 EUR", "17.01.2024 -1.411,00 EUR"),
    )
    result = WERTPAPIERABRECHNUNG_2023.parse(text)
    assert len(result.transactions) == 1
    item = only_review(result)
    assert item["kind"] == "amounts_do_not_add_up"
    assert item["message"] == (
        "The amounts in this document do not add up. Quantity x price is 1400.00 EUR, but the position shows 1410.00 EUR."
    )


def test_the_position_total_must_match_the_position_amount() -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse(variant(T02, ("GESAMT 1.400,00 EUR", "GESAMT 1.300,00 EUR")))
    item = only_review(result)
    assert item["message"] == (
        "The amounts in this document do not add up. The position total (GESAMT) is 1300.00 EUR, "
        "but the position shows 1400.00 EUR."
    )


def test_the_booking_must_match_the_settlement_total() -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse(variant(T02, ("17.01.2024 -1.401,00 EUR", "17.01.2024 -1.402,00 EUR")))
    assert result.transactions[0].amount == Decimal("-1402.00")
    item = only_review(result)
    assert item["message"] == (
        "The amounts in this document do not add up. The settlement total is -1401.00 EUR, but the booking shows -1402.00 EUR."
    )


def test_a_savings_plan_booking_must_match_its_position() -> None:
    result = SPARPLAN.parse(variant(T03, ("05.02.2024 -200,00 EUR", "05.02.2024 -201,00 EUR")))
    item = only_review(result)
    assert item["kind"] == "amounts_do_not_add_up"
    assert item["message"] == (
        "The amounts in this document do not add up. The settlement total is -200.00 EUR, but the booking shows -201.00 EUR."
    )


# --- Missing parts ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "missing", "message"),
    [
        (
            "Market-Order Kauf am 15.01.2024, um 10:05 Uhr (Europe/Berlin) an der Lang & Schwarz Exchange.",
            "execution",
            "The document does not show the trade date and time in a form this parser reads.",
        ),
        (
            "SAP SE 10 Stk. 140,00 EUR 1.400,00 EUR",
            "position",
            "The position line with quantity, price and amount in EUR was not found.",
        ),
        ("ISIN: DE0007164600", "isin", "The ISIN line was not found below the position."),
        ("GESAMT 1.400,00 EUR", "position_total", "The position total (GESAMT) was not found."),
        (
            "DE00000000000000000000 17.01.2024 -1.401,00 EUR",
            "booking",
            "The booking block (BUCHUNG) with the booking date and amount was not found. The document may be cut off.",
        ),
    ],
)
def test_a_missing_part_gives_a_missing_field_review_and_no_transaction(line: str, missing: str, message: str) -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse(without_line(T02, line))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == missing  # type: ignore[index]
    assert item["message"] == message
    assert item["extracted_text"] == without_line(T02, line)


def test_a_page_break_between_blocks_changes_nothing() -> None:
    # A form feed ends a line like a line feed, so line numbers and evidence stay the same.
    one_page = WERTPAPIERABRECHNUNG_2023.parse(fixture(T02))
    two_pages = WERTPAPIERABRECHNUNG_2023.parse(
        variant(T02, ("GESAMT -1.401,00 EUR\nBUCHUNG\n", "GESAMT -1.401,00 EUR\fBUCHUNG\n"))
    )
    assert two_pages.review == []
    assert two_pages.transactions == one_page.transactions


def test_parse_without_the_layout_title_gives_a_review() -> None:
    result = WERTPAPIERABRECHNUNG_2023.parse("Hello\n")
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"] == {"missing": "title"}
    assert item["message"] == "This is not a WERTPAPIERABRECHNUNG document."


def test_the_english_layout_messages_use_its_own_labels() -> None:
    case = "tr.settlement.en.2023/buy_market_winter"
    result = SETTLEMENT_EN_2023.parse(without_line(case, "TOTAL -1,302.52 EUR"))
    item = only_review(result)
    assert item["fields"]["missing"] == "settlement_total"  # type: ignore[index]
    assert item["message"] == (
        "The settlement block (BILLING) has no TOTAL line, so the total cannot be checked. The document may be cut off."
    )


# --- Parsed fields ------------------------------------------------------------------------


def test_every_pdf_document_candidate_has_a_valid_isin_when_one_is_expected(text_fixture: Path) -> None:
    # Every single-document layout (trade, dividend, tax, interest, split) books to the EUR cash
    # account, so amount and amount_eur always agree. Unlike the WP3 trade layouts, a WP4 layout
    # may have no ISIN at all (tax and interest notes are not always about one security) or an
    # FX line (a foreign dividend, plan 6.2); an account statement (source_kind pdf_statement)
    # is a different shape entirely (many lines per file) and is covered by its own tests instead.
    if text_fixture.parent.name == "unclassified":
        return
    parser = {p.parser_id: p for p in PARSERS}[text_fixture.parent.name]
    for txn in parser.parse(text_fixture.read_text(encoding="utf-8")).transactions:
        if txn.source_kind is not SourceKind.PDF_DOCUMENT:
            continue
        if txn.isin is not None:
            assert is_valid_isin(txn.isin)
        assert txn.currency == "EUR"
        assert txn.amount == txn.amount_eur
        assert txn.fx_source in ("none", "document")


def test_personal_data_is_never_copied_into_parsed_fields(text_fixture: Path) -> None:
    text = text_fixture.read_text(encoding="utf-8")
    for parser in PARSERS:
        result = parser.parse(text)
        parsed = [transaction_to_dict(txn) for txn in result.transactions]
        parsed += [dict(item.fields) for item in result.review]
        dumped = json.dumps(parsed, ensure_ascii=False)
        for personal in PERSONAL:
            assert personal not in dumped, f"{parser.parser_id} copied {personal!r} from {text_fixture.name}"


def test_parsers_never_raise_when_a_line_is_missing(text_fixture: Path) -> None:
    lines = text_fixture.read_text(encoding="utf-8").splitlines(keepends=True)
    for index in range(len(lines)):
        damaged = "".join(lines[:index] + lines[index + 1 :])
        for parser in PARSERS:
            result = parser.parse(damaged)
            assert isinstance(result, ParseResult)
            for txn in result.transactions:
                assert txn.isin is not None
                assert is_valid_isin(txn.isin)


def test_parsers_never_raise_when_a_document_is_cut_off(text_fixture: Path) -> None:
    lines = text_fixture.read_text(encoding="utf-8").splitlines(keepends=True)
    for end in range(len(lines)):
        for parser in PARSERS:
            assert isinstance(parser.parse("".join(lines[:end])), ParseResult)


# --- WP4: dividend (tr.dividende.de) -------------------------------------------------------


def test_dividend_paid_in_a_foreign_currency_uses_the_document_fx_line() -> None:
    txn = DIVIDENDE.parse(fixture(T10)).transactions[0]
    assert (txn.currency, txn.amount, txn.amount_eur) == ("EUR", Decimal("0.94"), Decimal("0.94"))
    assert (txn.fx_rate, txn.fx_source) == (Decimal("1.0800"), "document")
    assert (txn.quantity, txn.price) == (Decimal("5"), Decimal("0.24"))
    assert txn.tax_detail == {"quellensteuer": Decimal("0.17")}


def test_a_eur_dividend_has_no_fx_line() -> None:
    txn = DIVIDENDE.parse(fixture(T09)).transactions[0]
    assert (txn.fx_rate, txn.fx_source) == (None, "none")
    assert txn.tax_detail == {"kapitalertragssteuer": Decimal("8.25"), "solidaritaetszuschlag": Decimal("0.45")}


def test_a_foreign_dividend_without_a_conversion_line_is_a_missing_field() -> None:
    text = without_line(T10, "Zwischensumme 1,0800 EUR/USD 1,11 EUR")
    result = DIVIDENDE.parse(text)
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == "fx"  # type: ignore[index]
    assert item["message"] == "The document shows the position in USD but has no EUR conversion line (Zwischensumme)."


def test_dividend_invalid_isin_gives_a_review_and_no_transaction() -> None:
    result = DIVIDENDE.parse(variant(T09, ("ISIN: DE0007164600", "ISIN: DE0007164601")))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "invalid_isin"
    assert item["message"] == "The ISIN DE0007164601 in this document is not valid: its check digit does not match."


def test_dividend_gross_and_tax_must_add_up_to_the_settlement_total() -> None:
    text = variant(
        T09,
        ("GESAMT 24,30 EUR", "GESAMT 25,30 EUR"),
        ("DE00000000000000000000 15.05.2024 24,30 EUR", "DE00000000000000000000 15.05.2024 25,30 EUR"),
    )
    result = DIVIDENDE.parse(text)
    assert len(result.transactions) == 1
    item = only_review(result)
    assert item["kind"] == "amounts_do_not_add_up"
    assert item["message"] == (
        "The amounts in this document do not add up. The gross amount and the tax lines give 24.30 EUR, "
        "but the settlement total (GESAMT) is 25.30 EUR."
    )


def test_dividend_without_a_booking_block_is_a_missing_field() -> None:
    result = DIVIDENDE.parse(without_line(T09, "BUCHUNG"))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == "booking"  # type: ignore[index]


def test_dividend_without_the_title_gives_a_review() -> None:
    result = DIVIDENDE.parse("Hello\n")
    item = only_review(result)
    assert item["fields"] == {"missing": "title"}


# --- WP4: tax correction and advance lump sum (tr.steuer.de) --------------------------------

STEUERKORREKTUR = "tr.steuer.de/steuerkorrektur_erstattung"
VORABPAUSCHALE = "tr.steuer.de/vorabpauschale_belastung"


def test_a_positive_tax_note_entry_is_a_refund() -> None:
    txn = STEUER.parse(fixture(STEUERKORREKTUR)).transactions[0]
    assert txn.tax_detail == {"kapitalertragssteuer": Decimal("-1.10")}
    assert txn.tax_eur == Decimal("-1.10")
    assert txn.amount == Decimal("1.10")
    assert txn.type.value == "tax"


def test_a_negative_tax_note_has_two_tax_lines() -> None:
    txn = STEUER.parse(fixture(VORABPAUSCHALE)).transactions[0]
    assert txn.tax_detail == {"kapitalertragssteuer": Decimal("0.86"), "solidaritaetszuschlag": Decimal("0.04")}
    assert txn.amount == Decimal("-0.90")


def test_tax_note_has_no_quantity_or_price() -> None:
    txn = STEUER.parse(fixture(STEUERKORREKTUR)).transactions[0]
    assert (txn.quantity, txn.price) == (None, None)


def test_tax_note_without_a_settlement_block_is_a_missing_field() -> None:
    result = STEUER.parse(without_line(STEUERKORREKTUR, "ABRECHNUNG"))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"
    assert item["fields"]["missing"] == "settlement"  # type: ignore[index]


def test_tax_note_without_an_isin_is_a_missing_field() -> None:
    result = STEUER.parse(without_line(STEUERKORREKTUR, "ISIN: DE0007164600"))
    item = only_review(result)
    assert item["fields"]["missing"] == "isin"  # type: ignore[index]


def test_tax_note_booking_must_match_the_settlement_total() -> None:
    result = STEUER.parse(variant(STEUERKORREKTUR, ("20.05.2024 1,10 EUR", "20.05.2024 1,20 EUR")))
    item = only_review(result)
    assert item["kind"] == "amounts_do_not_add_up"


# --- WP4: interest statement (tr.zinsen.de) -------------------------------------------------

ZINSEN_MIT_STEUER = "tr.zinsen.de/zinsen_mit_steuer"
ZINSEN_OHNE_STEUER = "tr.zinsen.de/zinsen_ohne_steuer"


def test_interest_has_no_isin_name_quantity_or_price() -> None:
    txn = ZINSEN.parse(fixture(ZINSEN_MIT_STEUER)).transactions[0]
    assert (txn.isin, txn.name, txn.quantity, txn.price) == (None, None, None, None)
    assert txn.type.value == "interest"


def test_interest_with_no_tax_line_has_a_zero_known_tax() -> None:
    txn = ZINSEN.parse(fixture(ZINSEN_OHNE_STEUER)).transactions[0]
    assert txn.tax_eur == Decimal("0.00")
    assert txn.tax_detail == {}
    assert txn.amount == Decimal("0.45")


def test_interest_gross_before_tax_is_checked_against_the_net_credit() -> None:
    text = variant(ZINSEN_MIT_STEUER, ("GUTSCHRIFT VOR STEUERN 3,20 EUR", "GUTSCHRIFT VOR STEUERN 4,20 EUR"))
    result = ZINSEN.parse(text)
    assert len(result.transactions) == 1
    item = only_review(result)
    assert item["kind"] == "amounts_do_not_add_up"


def test_interest_note_without_a_settlement_block_is_a_missing_field() -> None:
    result = ZINSEN.parse(without_line(ZINSEN_OHNE_STEUER, "ABRECHNUNG"))
    assert result.transactions == []
    item = only_review(result)
    assert item["fields"]["missing"] == "settlement"  # type: ignore[index]


# --- WP4: split (tr.split.de) ---------------------------------------------------------------


def test_split_records_only_the_new_total_and_no_booking_amount() -> None:
    txn = SPLIT.parse(fixture(T11)).transactions[0]
    assert txn.split_new_quantity == Decimal("20")
    assert (txn.amount, txn.amount_eur, txn.quantity, txn.price, txn.value_date) == (None, None, None, None, None)
    assert txn.type.value == "split"
    assert txn.fees_eur == Decimal("0.00")
    assert txn.tax_eur == Decimal("0.00")


def test_split_without_the_entry_line_is_a_missing_field() -> None:
    result = SPLIT.parse(without_line(T11, "1 Einbuchung NVIDIA Corp. 20 Stk. 0,00 EUR"))
    assert result.transactions == []
    item = only_review(result)
    assert item["fields"]["missing"] == "split_entry"  # type: ignore[index]


def test_split_without_an_isin_is_a_missing_field() -> None:
    result = SPLIT.parse(without_line(T11, "ISIN: US67066G1040"))
    item = only_review(result)
    assert item["fields"]["missing"] == "isin"  # type: ignore[index]


def test_split_invalid_isin_gives_a_review() -> None:
    result = SPLIT.parse(variant(T11, ("ISIN: US67066G1040", "ISIN: US67066G1041")))
    item = only_review(result)
    assert item["kind"] == "invalid_isin"


# --- WP4: corporate actions P0 does not convert (tr.corporate_action.de) -------------------


@pytest.mark.parametrize("case", ["tr.corporate_action.de/umtausch", "tr.corporate_action.de/bezug"])
def test_corporate_action_notices_give_no_transaction(case: str) -> None:
    result = CORPORATE_ACTION.parse(fixture(case))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "corporate_action"


def test_corporate_action_on_unrelated_text_says_so_and_copies_no_personal_data() -> None:
    result = CORPORATE_ACTION.parse(fixture(T02))
    assert result.transactions == []
    item = only_review(result)
    assert item["kind"] == "missing_field"


# --- WP4: account statements (tr.kontoauszug.de.2023, tr.kontoauszug.de.2024) --------------

KONTOAUSZUG_2023_Q1 = "tr.kontoauszug.de.2023/kontoauszug_2023_q1"
KONTOAUSZUG_2024_H1 = "tr.kontoauszug.de.2024/golden_h1_statement"


def test_statement_candidates_never_carry_fees_taxes_quantity_or_price() -> None:
    # Plan 6.3: "They add no information beyond date, type, ISIN and amount."
    for parser, case in ((KONTOAUSZUG_2023, KONTOAUSZUG_2023_Q1), (KONTOAUSZUG_2024, KONTOAUSZUG_2024_H1)):
        for txn in parser.parse(fixture(case)).transactions:
            assert txn.fees_eur is None
            assert txn.tax_eur is None
            assert txn.quantity is None
            assert txn.price is None
            assert txn.source_ref is None
            assert txn.source_kind is SourceKind.PDF_STATEMENT


def test_kontoauszug_2023_uses_the_booking_day_for_the_time_and_the_value_date_separately() -> None:
    # Plan 5.3.4: statement lines key on BUCHUNGSTAG; WERTSTELLUNG is kept only as value_date.
    txn = KONTOAUSZUG_2023.parse(fixture(KONTOAUSZUG_2023_Q1)).transactions[1]
    assert txn.time.ts_local.date().isoformat() == "2023-01-16"
    assert txn.value_date is not None
    assert txn.value_date.isoformat() == "2023-01-18"


def test_an_unrecognised_statement_row_does_not_stop_later_rows_from_importing() -> None:
    result = KONTOAUSZUG_2023.parse(fixture(KONTOAUSZUG_2023_Q1))
    assert len(result.transactions) == 3
    item = only_review(result)
    assert item["kind"] == "unparsed_row"


def test_statement_layouts_are_mutually_exclusive() -> None:
    text_2023 = fixture(KONTOAUSZUG_2023_Q1)
    text_2024 = fixture(KONTOAUSZUG_2024_H1)
    assert KONTOAUSZUG_2023.detect(text_2023)
    assert not KONTOAUSZUG_2024.detect(text_2023)
    assert KONTOAUSZUG_2024.detect(text_2024)
    assert not KONTOAUSZUG_2023.detect(text_2024)


def test_kontoauszug_2024_reads_german_month_names_and_isin_with_name() -> None:
    txn = KONTOAUSZUG_2024.parse(fixture(KONTOAUSZUG_2024_H1)).transactions[1]
    assert (txn.isin, txn.name) == ("DE0007164600", "SAP SE")
    assert txn.time.ts_local.date().isoformat() == "2024-01-15"
