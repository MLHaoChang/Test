"""Tests for the semantic key, the report key and the content hash (importer/keys.py, plan 5.3.4).

The **semantic key** is built only from fields every source carries: for cash-moving types the
type, the ISIN (or "-"), the Europe/Berlin booking date and the signed EUR amount booked on the
cash account in cents; for types without cash (split, transfers) the quantity instead of the
amount. The time of day is never in it, because account statements carry only a date.

The **report key** says which report a candidate is within its kind of file: for files that list
many transactions, the semantic key, the time of day and an ordinal that counts identical rows;
for a PDF document, the reference number it prints, or the hash of its text.
"""

import hashlib
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from playground.core.dates import SourceTime, berlin_to_utc
from playground.core.types import TxnType
from playground.importer.keys import (
    PRECEDENCE,
    booking_day,
    content_hash,
    report_keys,
    semantic_key,
)
from playground.importer.model import ParsedTransaction, SourceKind
from playground.importer.tr.classify import classify
from playground.importer.tr.csv_parser import parse_csv
from playground.importer.tr.layouts.common import DocumentParser

TEXT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tr" / "text"
GOLDEN_INPUTS = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "inputs"


def at(year: int, month: int, day: int, hour: int | None = None, minute: int = 0) -> SourceTime:
    local = datetime(year, month, day, hour or 0, minute)  # noqa: DTZ001 - a naive Berlin time, as printed
    return SourceTime(
        ts_utc=berlin_to_utc(local),
        ts_local=local,
        source_tz="Europe/Berlin",
        precision="day" if hour is None else "minute",
    )


def candidate(
    txn_type: TxnType = TxnType.BUY,
    *,
    isin: str | None = "DE0007164600",
    time: SourceTime | None = None,
    amount: str | None = "-1401.00",
    quantity: str | None = "10",
    split_new_quantity: str | None = None,
    source_ref: str | None = None,
    kind: SourceKind = SourceKind.CSV_EXPORT,
) -> ParsedTransaction:
    return ParsedTransaction(
        type=txn_type,
        isin=isin,
        name=None,
        time=time or at(2024, 1, 15, 10, 5),
        value_date=None,
        quantity=None if quantity is None else Decimal(quantity),
        price=None,
        currency="EUR",
        amount=None if amount is None else Decimal(amount),
        amount_eur=None if amount is None else Decimal(amount),
        fx_rate=None,
        fx_source="none",
        fees_eur=None,
        tax_eur=None,
        tax_detail={},
        split_new_quantity=None if split_new_quantity is None else Decimal(split_new_quantity),
        origin=None,
        source_ref=source_ref,
        source_kind=kind,
        evidence=(1, 1),
    )


def parse_text_fixture(relative: str) -> ParsedTransaction:
    text = (TEXT_DIR / relative).read_text(encoding="utf-8")
    parser = classify(text)
    assert isinstance(parser, DocumentParser)
    result = parser.parse(text)
    assert len(result.transactions) == 1
    return result.transactions[0]


# --- The semantic key -------------------------------------------------------------------------


def test_cash_key_is_type_isin_booking_date_amount_in_cents_and_eur() -> None:
    assert semantic_key(candidate()) == "buy|DE0007164600|2024-01-15|-140100|EUR"


def test_cash_key_without_an_isin_has_a_dash() -> None:
    deposit = candidate(TxnType.DEPOSIT, isin=None, time=at(2024, 1, 2), amount="5000.00", quantity=None)
    assert semantic_key(deposit) == "deposit|-|2024-01-02|500000|EUR"


def test_the_usd_dividend_keys_on_the_eur_amount_it_books() -> None:
    # T10: the note shows 1.20 USD gross, but the key uses the 0.94 EUR booked, as the statement line does.
    t10 = parse_text_fixture("tr.dividende.de/golden_t10_dividende_aapl.txt")
    assert t10.fx_rate == Decimal("1.0800")
    assert semantic_key(t10) == "dividend|US0378331005|2024-05-16|94|EUR"


def test_trailing_zeros_of_the_amount_do_not_change_the_key() -> None:
    assert semantic_key(candidate(amount="-1401.0")) == semantic_key(candidate(amount="-1401.00"))


def test_the_key_has_no_time_of_day() -> None:
    minute = candidate(time=at(2024, 1, 15, 10, 5))
    day = candidate(time=at(2024, 1, 15))
    assert semantic_key(minute) == semantic_key(day)


def test_the_key_date_is_the_berlin_calendar_date_not_the_utc_date() -> None:
    # T3, a savings plan on 2024-02-01 at 00:00 Berlin time, is 2024-01-31 23:00 UTC.
    t03 = parse_text_fixture("tr.sparplan.de/golden_t03_sparplan_msciw.txt")
    assert t03.time.ts_utc.date() == date(2024, 1, 31)
    assert booking_day(t03) == date(2024, 2, 1)
    assert semantic_key(t03) == "buy|IE00B4L5Y983|2024-02-01|-20000|EUR"


def test_keys_without_cash_use_the_quantity() -> None:
    split = candidate(TxnType.SPLIT, isin="US67066G1040", time=at(2024, 6, 10), amount=None, quantity=None)
    split = replace(split, split_new_quantity=Decimal("20.000"))
    transfer = candidate(TxnType.TRANSFER_IN, isin="DE0008404005", time=at(2024, 6, 20), amount=None, quantity="4")
    transfer_out = candidate(TxnType.TRANSFER_OUT, isin="DE0008404005", time=at(2024, 6, 25), amount=None, quantity="1")

    assert semantic_key(split) == "split|US67066G1040|2024-06-10|20"
    assert semantic_key(transfer) == "transfer_in|DE0008404005|2024-06-20|4"
    assert semantic_key(transfer_out) == "transfer_out|DE0008404005|2024-06-25|1"


def test_every_source_of_a_golden_trade_gives_the_same_key() -> None:
    # T2 as the PDF note, the CSV row and the statement line (plan 1.2).
    pdf = parse_text_fixture("tr.wertpapierabrechnung.de.2023/golden_t02_kauf_sap.txt")
    csv_rows = parse_csv((GOLDEN_INPUTS / "tr_transactions_2024.csv").read_bytes()).transactions
    statement = parse_text_fixture_all("tr.kontoauszug.de.2024/golden_h1_statement.txt")

    csv_t2 = next(txn for txn in csv_rows if txn.source_ref == "csv-2024-0002")
    statement_t2 = next(
        txn for txn in statement if txn.isin == "DE0007164600" and txn.amount_eur == Decimal("-1401.00")
    )
    assert semantic_key(pdf) == semantic_key(csv_t2) == semantic_key(statement_t2)


def parse_text_fixture_all(relative: str) -> list[ParsedTransaction]:
    text = (TEXT_DIR / relative).read_text(encoding="utf-8")
    parser = classify(text)
    assert isinstance(parser, DocumentParser)
    return parser.parse(text).transactions


def test_every_golden_candidate_of_one_transaction_shares_its_key() -> None:
    # The 11 statement lines each have exactly one CSV row or PDF note with the same key (plan 1.2).
    statement_keys = [
        semantic_key(txn) for txn in parse_text_fixture_all("tr.kontoauszug.de.2024/golden_h1_statement.txt")
    ]
    csv_keys = {
        semantic_key(txn) for txn in parse_csv((GOLDEN_INPUTS / "tr_transactions_2024.csv").read_bytes()).transactions
    }
    pdf_keys = {
        semantic_key(parse_text_fixture(path))
        for path in (
            "tr.wertpapierabrechnung.de.2023/golden_t02_kauf_sap.txt",
            "tr.sparplan.de/golden_t03_sparplan_msciw.txt",
            "tr.wertpapierabrechnung.de.2023/golden_t06_kauf_nvda.txt",
            "tr.wertpapierabrechnung.de.2023/golden_t08_kauf_sap.txt",
            "tr.dividende.de/golden_t09_dividende_sap.txt",
            "tr.dividende.de/golden_t10_dividende_aapl.txt",
            "tr.wertpapierabrechnung.de.2023/golden_t12_verkauf_sap.txt",
        )
    }
    assert len(statement_keys) == 11
    assert len(set(statement_keys)) == 11
    assert set(statement_keys) <= csv_keys | pdf_keys


# --- The report key ---------------------------------------------------------------------------


def test_report_key_of_a_listing_row_is_kind_key_time_and_ordinal() -> None:
    row = candidate(kind=SourceKind.CSV_EXPORT)
    assert report_keys([row]) == ["csv_export|buy|DE0007164600|2024-01-15|-140100|EUR|10:05|1"]


def test_report_key_of_a_row_without_time_has_a_dash() -> None:
    row = candidate(time=at(2024, 2, 1), kind=SourceKind.PDF_STATEMENT, amount="-200.00", quantity=None)
    assert report_keys([row]) == ["pdf_statement|buy|DE0007164600|2024-02-01|-20000|EUR|-|1"]


def test_identical_rows_of_one_file_are_counted_by_the_ordinal() -> None:
    same = candidate(time=at(2024, 9, 2), amount="-50.00", quantity="0.4798")
    other_time = candidate(time=at(2024, 9, 2, 9, 30), amount="-50.00", quantity="0.4798")
    keys = report_keys([same, other_time, same])
    assert keys == [
        "csv_export|buy|DE0007164600|2024-09-02|-5000|EUR|-|1",
        "csv_export|buy|DE0007164600|2024-09-02|-5000|EUR|09:30|1",
        "csv_export|buy|DE0007164600|2024-09-02|-5000|EUR|-|2",
    ]


def test_report_key_ignores_the_row_reference() -> None:
    # A re-export may number its rows differently; the same rows must still map to the same reports.
    first = candidate(source_ref="csv-2024-0002")
    second = candidate(source_ref="export-2-0017")
    assert report_keys([first]) == report_keys([second])


def test_manual_rows_have_their_own_report_kind() -> None:
    row = candidate(kind=SourceKind.MANUAL_CSV)
    assert report_keys([row])[0].startswith("manual_csv|buy|DE0007164600|2024-01-15|-140100|EUR|")


def test_report_key_of_a_pdf_document_is_its_reference_number() -> None:
    note = candidate(kind=SourceKind.PDF_DOCUMENT, source_ref="5d0a-93f1")
    assert report_keys([note], text="any text") == ["pdf_document|5d0a-93f1"]


def test_report_key_of_a_pdf_without_a_reference_is_the_hash_of_its_text() -> None:
    note = candidate(kind=SourceKind.PDF_DOCUMENT, source_ref=None)
    text = "TRADE REPUBLIC BANK GMBH\nDIVIDENDE\n"
    expected = "pdf_document|text|" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert report_keys([note], text=text) == [expected]


# --- Content hash and precedence --------------------------------------------------------------


def test_content_hash_is_the_sha256_of_key_and_occurrence() -> None:
    key = "buy|DE0007164600|2024-01-15|-140100|EUR"
    assert content_hash(key, 1) == hashlib.sha256(f"{key}|1".encode()).hexdigest()
    assert content_hash(key, 1) != content_hash(key, 2)


def test_precedence_of_the_source_kinds() -> None:
    assert PRECEDENCE == {
        SourceKind.MANUAL_CSV: 4,
        SourceKind.PDF_DOCUMENT: 3,
        SourceKind.CSV_EXPORT: 2,
        SourceKind.PDF_STATEMENT: 1,
    }


# --- A re-exported CSV (through the pipeline) -------------------------------------------------


def test_a_re_exported_csv_covering_the_same_days_maps_to_the_same_occurrences(harness, docs) -> None:
    # Two identical savings plan rows on one day, a buy and a deposit. The re-export lists the same
    # rows in another order, numbers them differently and adds one row for a later day.
    first = docs.csv(
        "export_2024-09.csv",
        [
            "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;a-1",
            "02.09.2024;;Sparplan;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;0,4798;104,20;-50,00;;;EUR;;a-2",
            "02.09.2024;;Sparplan;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;0,4798;104,20;-50,00;;;EUR;;a-3",
            "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;a-4",
        ],
    )
    harness.run(first)
    before = {txn.id: (txn.semantic_key, txn.occurrence, txn.content_hash) for txn in harness.transactions()}
    assert sorted(occurrence for _, occurrence, _ in before.values()) == [1, 1, 1, 2]

    re_export = docs.csv(
        "export_2024-10.csv",
        [
            "15.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-1401,00;1,00;;EUR;;b-9",
            "02.09.2024;;Sparplan;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;0,4798;104,20;-50,00;;;EUR;;b-8",
            "02.01.2024;;Einzahlung;;;;;5000,00;;;EUR;;b-7",
            "02.09.2024;;Sparplan;IE00BK5BQT80;Vanguard FTSE All-World U.ETF;0,4798;104,20;-50,00;;;EUR;;b-6",
            "01.10.2024;;Einzahlung;;;;;250,00;;;EUR;;b-5",
        ],
    )
    summary = harness.run(re_export)

    assert summary.counts["candidates"] == 5
    assert summary.counts["already_known"] == 4
    assert summary.counts["new"] == 1
    after = {txn.id: (txn.semantic_key, txn.occurrence, txn.content_hash) for txn in harness.transactions()}
    assert {txn_id: after[txn_id] for txn_id in before} == before
    assert len(after) == 5
    # Each old transaction now has one report from each export, under the same report key.
    for txn in harness.transactions():
        if txn.id in before:
            assert txn.kinds == ("csv_export", "csv_export")
            assert len({source.report_key for source in txn.sources}) == 1
