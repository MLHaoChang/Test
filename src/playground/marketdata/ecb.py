"""The ECB reference FX rate source (plan 5.6, 6.6).

The real feed is a zip file holding one CSV: a `Date` column, then one column per currency
(units of that currency per 1 EUR, 3.3), `N/A` for a day the ECB has no rate, and a trailing
comma at the end of every line. `pg fx import-file` accepts the same CSV unzipped, as a fallback,
which is why the CSV parsing itself (`parse_fx_csv`) is a free function `load_fx_file` can call
directly, without going through `EcbClient` or the HTTP layer at all.
"""

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date

from playground.core.errors import NumberFormatError, PlaygroundError
from playground.core.numbers import parse_en_decimal
from playground.http.client import HttpClient, HttpRequest
from playground.marketdata.lake import FxPoint, FxTable

DEFAULT_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
_MISSING_VALUE = "N/A"


class EcbSourceError(PlaygroundError):
    """The ECB's response, or a file given to `load_fx_file`, could not be read as the rates CSV."""


@dataclass(frozen=True)
class EcbClient:
    """`history()` always returns the whole series the ECB publishes; there is no date range to ask for."""

    http: HttpClient
    url: str = DEFAULT_URL

    def history(self) -> FxTable:
        """Fetch and unzip the ECB's historical reference rates.

        Raises `EcbSourceError` for a non-200 status, a response that is not a zip file, or a zip
        with no files in it.
        """
        response = self.http.send(HttpRequest(method="GET", url=self.url))
        if response.status != 200:
            raise EcbSourceError(f"The ECB returned status {response.status}.")
        try:
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                names = archive.namelist()
                if not names:
                    raise EcbSourceError("The ECB's zip file has nothing in it.")
                data = archive.read(names[0])
        except zipfile.BadZipFile as exc:
            raise EcbSourceError("The ECB's response is not a zip file.") from exc
        return parse_fx_csv(data)


def load_fx_file(data: bytes) -> FxTable:
    """Parse the same CSV the ECB zips, given unzipped (`pg fx import-file`, 6.6)."""
    return parse_fx_csv(data)


def parse_fx_csv(data: bytes) -> FxTable:
    """Parse the ECB's `Date,<CCY>,<CCY>,...` CSV (with `N/A` and a trailing comma) into an `FxTable`.

    Raises `EcbSourceError`, naming the 1-based line, for a missing `Date` header or an unparsable
    date or rate. `N/A` and an empty cell are both silently skipped, never stored as a rate.
    """
    text = data.decode("utf-8-sig")
    rows = [row for row in csv.reader(io.StringIO(text)) if row and row[0].strip()]
    if not rows:
        raise EcbSourceError("The ECB file has no rows.")

    header = [cell.strip() for cell in rows[0]]
    if not header or header[0] != "Date":
        raise EcbSourceError(f"Expected the first column to be 'Date', got {header!r}.")
    # A trailing comma (the real ECB format) gives one empty header cell at the end; dropping
    # blanks here, rather than requiring the caller to know about it, is what lets the same
    # function read both the zipped file and a hand-trimmed one.
    currencies = [cell for cell in header[1:] if cell]

    points: list[FxPoint] = []
    for line_no, row in enumerate(rows[1:], start=2):
        date_text = row[0].strip()
        try:
            day = date.fromisoformat(date_text)
        except ValueError as exc:
            raise EcbSourceError(f"Line {line_no}: not a date (expected YYYY-MM-DD): {date_text!r}.") from exc
        for index, currency in enumerate(currencies, start=1):
            if index >= len(row):
                continue
            value = row[index].strip()
            if not value or value == _MISSING_VALUE:
                continue
            try:
                rate = parse_en_decimal(value)
            except NumberFormatError as exc:
                raise EcbSourceError(f"Line {line_no}: not a number for {currency}: {value!r}.") from exc
            points.append(FxPoint(date=day, currency=currency, rate=rate))
    return FxTable(points=tuple(points))
