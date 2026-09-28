"""QA phase P0, round 1: defects found in the CSV path of the import pipeline (AC4).

QA wrote these tests as strict xfails while the defects were open. D1 and D2 are fixed, the
markers are gone, and the tests now guard the fixes (see also `test_csv_amount_check.py` and
`test_missing_amount.py`).

AC4 says an amount that does not add up and a missing required field each create a review item,
and plan 5.3.2 says every parser checks that its numbers add up (for a buy: quantity x price +
fees = -booking amount, within 0.01). The PDF layouts do this; the Trade Republic CSV parser does
not, so a wrong CSV row goes straight into the lots with a wrong cost basis.
"""

import pytest

from playground.importer.model import ReviewKind
from playground.importer.tr.csv_parser import parse_csv

HEADER = "Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz"
# 10 x 140.00 + 1.00 fee = 1,401.00, but the row books 9,999.00.
BUY_DOES_NOT_ADD_UP = "18.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;-9999,00;1,00;;EUR;;qa-1"
# 10 x 150.00 - 1.00 fee = 1,499.00, but the row books 5.00.
SELL_DOES_NOT_ADD_UP = "20.02.2024;10:05;Verkauf;DE0007164600;SAP SE;10;150,00;5,00;1,00;;EUR;;qa-2"
# A buy with no booking amount at all: its cost basis cannot be known.
BUY_WITHOUT_AMOUNT = "20.01.2024;10:05;Kauf;DE0007164600;SAP SE;10;140,00;;1,00;;EUR;;qa-3"
# A dividend with no amount: nothing about it can be booked.
DIVIDEND_WITHOUT_AMOUNT = "21.01.2024;;Dividende;DE0007164600;SAP SE;;;;;;EUR;;qa-4"


def csv_bytes(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


def only(items):
    assert len(items) == 1, items
    return items[0]


@pytest.mark.parametrize("row", [BUY_DOES_NOT_ADD_UP, SELL_DOES_NOT_ADD_UP], ids=["buy", "sell"])
def test_parse_csv_flags_a_trade_whose_amounts_do_not_add_up(row: str) -> None:
    result = parse_csv(csv_bytes(row))

    assert [review.kind for review in result.review] == [ReviewKind.AMOUNTS_DO_NOT_ADD_UP]


def test_a_csv_buy_whose_amounts_do_not_add_up_is_held_back(harness, docs) -> None:
    harness.stage(docs.csv("mismatch.csv", [BUY_DOES_NOT_ADD_UP]))

    assert only(harness.transactions()).state == "held"
    assert only(harness.items(kind="amounts_do_not_add_up")).status == "open"
    harness.accept()
    assert harness.holdings() == {}


def test_a_csv_buy_without_an_amount_is_held_back_with_a_review_item(harness, docs) -> None:
    harness.stage(docs.csv("no_amount.csv", [BUY_WITHOUT_AMOUNT]))

    assert only(harness.transactions()).state == "held"
    assert only(harness.items(status="open")).kind in {"missing_field", "unparsed_row"}


def test_a_csv_dividend_without_an_amount_raises_a_review_item(harness, docs) -> None:
    harness.stage(docs.csv("no_amount_dividend.csv", [DIVIDEND_WITHOUT_AMOUNT]))

    assert only(harness.items(status="open")).kind in {"missing_field", "unparsed_row"}
