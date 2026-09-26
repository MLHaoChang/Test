"""The English trade layout, SECURITIES SETTLEMENT (plan 6.2: `tr.settlement.en.2023`).

The same document as the German 2023 trade confirmation, for an account set to English: the
execution line reads "Market-Order Buy on 28.04.2023 at 11:13 (Europe/Berlin).", quantities are
in "Pcs.", numbers use a decimal point and a thousands comma ("1,301.52 EUR"), the fee line is
"External cost surcharge", and the blocks are BILLING (the costs and taxes, ending in TOTAL)
and BOOKING (CLEARING ACCOUNT, VALUE DATE, AMOUNT). Dates stay dd.mm.yyyy.

The English tax labels ("Capital Gains Tax", "Solidarity Surcharge", "Church Tax", "Withholding
Tax") could not be checked against a real document. A label the parser does not know becomes an
`unparsed_row` review item, so a different real label is shown to you, never guessed.
"""

import re

from playground.core.types import TxnType
from playground.importer.model import ReviewKind
from playground.importer.tr.layouts.common import (
    ENGLISH,
    DocText,
    Execution,
    LayoutProblem,
    TradeConfirmationParser,
    execution_from_match,
)

_ORDER_LINE = re.compile(
    r"(?:(?P<order_type>[A-Za-z]+(?:-[A-Za-z]+)*-Order) )?(?P<side>Buy|Sell) on (?P<date>\d{2}\.\d{2}\.\d{4}) "
    r"at (?P<time>\d{2}:\d{2})(?: \((?P<zone>[^()]+)\))?(?P<rest>.*)"
)
_SIDES = {"Buy": TxnType.BUY, "Sell": TxnType.SELL}


def read_order_execution(doc: DocText, start: int) -> Execution:
    """Read "Market-Order Buy on 05.02.2024 at 09:31 (Europe/Berlin) ..." from the lines after `start`."""
    found = doc.search(_ORDER_LINE, start)
    if found is None:
        raise LayoutProblem(
            ReviewKind.MISSING_FIELD,
            "The document does not show the trade date and time in a form this parser reads.",
            missing="execution",
        )
    index, match = found
    return execution_from_match(match, index, _SIDES)


SETTLEMENT_EN_2023 = TradeConfirmationParser(
    parser_id="tr.settlement.en.2023",
    doc_type="trade_confirmation",
    vocabulary=ENGLISH,
    title="SECURITIES SETTLEMENT",
    position_headers=("POSITION QUANTITY PRICE AMOUNT",),
    read_execution=read_order_execution,
    origin="order",
)
