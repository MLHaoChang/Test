"""Declarative CSV profiles for the Trade Republic transaction export (plan 5.3.3, 6.1).

A profile is data, not code: the delimiter, the exact column order (as a mapping from this
app's own field names to the column headers a real export prints), the number locale, and the
table from the row-type text (`Typ`) to a transaction type and its origin. `csv_parser.py`
matches an uploaded file's header row against every profile in `PROFILES` and reads each data
row according to the one that matches; a header that matches none of them becomes an
`unknown_csv_header` review item, never a guess.

P0 ships one profile, `tr_csv_synthetic_v1` (6.1), modelled on common German bank CSV
conventions since Trade Republic's own export could not be seen from this container. A real
export at UAT that does not match it becomes a new profile, `tr_csv_<date>_v1`, added here
without touching `csv_parser.py` (6.1): that is the point of keeping the shape declarative.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from playground.core.types import TxnType

Locale = Literal["de", "en"]

# The semantic field names every profile must map, in the order `csv_parser.py` reads them.
FIELDS: tuple[str, ...] = (
    "date",
    "time",
    "type",
    "isin",
    "name",
    "quantity",
    "price",
    "amount",
    "fees",
    "taxes",
    "currency",
    "fx_rate",
    "reference",
)


@dataclass(frozen=True)
class RowType:
    """What one `Typ` value means: the transaction type, and the origin it implies, if any."""

    type: TxnType
    origin: str | None = None


@dataclass(frozen=True)
class CsvProfile:
    """One generation of the Trade Republic CSV export (6.1).

    `columns` maps each name in `FIELDS` to the column header text this generation prints, in
    the order the columns appear; `header` (below) is that order, used to recognise the file.
    `row_types` maps every `Typ` value the export can print (5.3.3) to a `RowType`; a value not
    in it is an unknown row type, reported as `unparsed_row` for that row alone.
    """

    profile_id: str
    delimiter: str
    locale: Locale
    columns: Mapping[str, str]
    row_types: Mapping[str, RowType]

    @property
    def header(self) -> tuple[str, ...]:
        """The column headers in file order, exactly as a matching file's first row must read."""
        return tuple(self.columns[field] for field in FIELDS)


TR_CSV_SYNTHETIC_V1 = CsvProfile(
    profile_id="tr_csv_synthetic_v1",
    delimiter=";",
    locale="de",
    columns={
        "date": "Datum",
        "time": "Uhrzeit",
        "type": "Typ",
        "isin": "ISIN",
        "name": "Name",
        "quantity": "Anzahl",
        "price": "Kurs",
        "amount": "Betrag",
        "fees": "Gebühren",
        "taxes": "Steuern",
        "currency": "Währung",
        "fx_rate": "Wechselkurs",
        "reference": "Referenz",
    },
    # The row-type vocabulary of plan 5.3.3, the same German words the account statement layouts
    # print for the types they share (kontoauszug_2024.py's _TYPE_WORDS), plus the types no
    # statement reports: Steuer, Gebühr, the two transfer directions and Split.
    row_types={
        "Kauf": RowType(TxnType.BUY, "order"),
        "Sparplan": RowType(TxnType.BUY, "savings_plan"),
        "Verkauf": RowType(TxnType.SELL, "order"),
        "Dividende": RowType(TxnType.DIVIDEND),
        "Ausschüttung": RowType(TxnType.DIVIDEND),
        "Zinsen": RowType(TxnType.INTEREST),
        "Einzahlung": RowType(TxnType.DEPOSIT),
        "Auszahlung": RowType(TxnType.WITHDRAWAL),
        "Steuer": RowType(TxnType.TAX),
        "Gebühr": RowType(TxnType.FEE),
        "Depotübertrag eingehend": RowType(TxnType.TRANSFER_IN, "transfer"),
        "Depotübertrag ausgehend": RowType(TxnType.TRANSFER_OUT, "transfer"),
        "Split": RowType(TxnType.SPLIT),
    },
)

PROFILES: tuple[CsvProfile, ...] = (TR_CSV_SYNTHETIC_V1,)

# Row types with no cash booking (plan 1.2: "Booking amount (EUR): none" for a split or a
# transfer in): a value in the Betrag column of one of these rows is never read.
NO_CASH_TYPES = frozenset({TxnType.SPLIT, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT})
