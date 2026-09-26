"""The German trade layouts (plan 6.2): WERTPAPIERABRECHNUNG in its 2019 and 2023 forms, and the savings plan.

All three share the grammar of `common.py`. What tells them apart:

- `tr.wertpapierabrechnung.de.2019`: the title WERTPAPIERABRECHNUNG and the position columns
  "POSITION ANZAHL KURS BETRAG". The execution line reads "Market-Order Kauf am 13.05.2019, um
  12:14 Uhr an der Lang & Schwarz Exchange." without a time zone, which is Berlin time. The ISIN
  stands alone on its line and the booking date is under VALUTA.
- `tr.wertpapierabrechnung.de.2023`: the same title with the columns "POSITION ANZAHL PREIS
  BETRAG". The time carries "(Europe/Berlin)", the ISIN line starts with "ISIN:", the booking
  date is under WERTSTELLUNG, and quantities may use a thousands dot ("1.000 Stk."). Limit and
  stop orders ("Limit-Order", "Stop-Market-Order") read the same way, and so does the side
  printed in English, as real documents from 2024 on do ("Market-Order BUY am ..."), and the
  time without the comma and "Uhr" of documents from 2026 ("am 30.06.2026 um 22:54").
- `tr.sparplan.de`: the title WERTPAPIERABRECHNUNG SPARPLAN. The execution line reads
  "Sparplanausführung am 18.11.2019 ..." with a date only, so the time has day precision (00:00
  Berlin time, plan 3.3). The quantity is often fractional ("0,4534 Stk."), there is no
  settlement block because a savings plan has no fee, and the booking date may be printed as
  dd.mm.yyyy or yyyy-mm-dd. The source reference is the execution number (AUSFÜHRUNG), not the
  plan number (SPARPLAN), which every execution of one plan shares.

Real documents between 2019 and 2023 mix these features (a KURS column with "(Europe/Berlin)",
or WERTSTELLUNG with an ISIN alone), so both order layouts accept every variant of the shared
lines; only the title and the position columns decide which parser a document belongs to.
"""

import re

from playground.core.types import TxnType
from playground.importer.model import ReviewKind
from playground.importer.tr.layouts.common import (
    GERMAN,
    DocText,
    Execution,
    LayoutProblem,
    TradeConfirmationParser,
    execution_from_match,
)

_ORDER_TYPE = r"(?:(?P<order_type>[A-Za-z]+(?:-[A-Za-z]+)*-Order) )?"
_ORDER_LINE = re.compile(
    _ORDER_TYPE
    + r"(?P<side>Kauf|Verkauf|BUY|SELL) am (?P<date>\d{2}\.\d{2}\.\d{4}),? um (?P<time>\d{2}:\d{2})(?: Uhr)?"
    r"(?: \((?P<zone>[^()]+)\))?(?P<rest>.*)"
)
_SAVINGS_PLAN_LINE = re.compile(
    r"Sparplanausführung am (?P<date>\d{2}\.\d{2}\.\d{4})(?:, um (?P<time>\d{2}:\d{2}) Uhr)?"
    r"(?: \((?P<zone>[^()]+)\))?(?P<rest>.*)"
)
# Real German documents from 2024 on print the side in English: "Market-Order BUY am ...".
_SIDES = {"Kauf": TxnType.BUY, "Verkauf": TxnType.SELL, "BUY": TxnType.BUY, "SELL": TxnType.SELL}


def read_order_execution(doc: DocText, start: int) -> Execution:
    """Read "Market-Order Kauf am 15.01.2024, um 10:05 Uhr (Europe/Berlin) ..." from the lines after `start`."""
    found = doc.search(_ORDER_LINE, start)
    if found is None:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            "The document does not show the trade date and time in a form this parser reads.",
            missing="execution",
        )
    index, match = found
    return execution_from_match(match, index, _SIDES)


def read_savings_plan_execution(doc: DocText, start: int) -> Execution:
    """Read "Sparplanausführung am 01.02.2024 ..." from the lines after `start`. A savings plan is a buy."""
    found = doc.search(_SAVINGS_PLAN_LINE, start)
    if found is None:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            "The document does not show the savings plan execution date in a form this parser reads.",
            missing="execution",
        )
    index, match = found
    return execution_from_match(match, index, _SIDES, what="execution")


WERTPAPIERABRECHNUNG_2019 = TradeConfirmationParser(
    parser_id="tr.wertpapierabrechnung.de.2019",
    doc_type="trade_confirmation",
    vocabulary=GERMAN,
    title="WERTPAPIERABRECHNUNG",
    position_headers=("POSITION ANZAHL KURS BETRAG",),
    read_execution=read_order_execution,
    origin="order",
)

WERTPAPIERABRECHNUNG_2023 = TradeConfirmationParser(
    parser_id="tr.wertpapierabrechnung.de.2023",
    doc_type="trade_confirmation",
    vocabulary=GERMAN,
    title="WERTPAPIERABRECHNUNG",
    position_headers=("POSITION ANZAHL PREIS BETRAG",),
    read_execution=read_order_execution,
    origin="order",
)

SPARPLAN = TradeConfirmationParser(
    parser_id="tr.sparplan.de",
    doc_type="savings_plan_confirmation",
    vocabulary=GERMAN,
    title="WERTPAPIERABRECHNUNG SPARPLAN",
    position_headers=(
        "POSITION ANZAHL KURS BETRAG",
        "POSITION ANZAHL DURCHSCHNITTSKURS BETRAG",
        "POSITION ANZAHL PREIS BETRAG",
    ),
    read_execution=read_savings_plan_execution,
    origin="savings_plan",
)
