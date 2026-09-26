"""Shared helpers for every CSV format the importer reads (plan 5.3.3, WP5).

Three unrelated formats are all plain-text CSV: the Trade Republic export (`tr/csv_parser.py`),
the manual transaction format (`manual_csv.py`) and the confirmed-holdings format
(`confirmed_csv.py`). All three decode their bytes the same way, and the two transaction formats
both read an optional "HH:MM" time-of-day column the same way, so that much lives here; each
format's own columns, row-type vocabulary and error handling stay in its own module.
"""

import re
from datetime import time

_HH_MM = re.compile(r"^(?P<hours>\d{2}):(?P<minutes>\d{2})$")


def decode_csv_bytes(data: bytes) -> str:
    """Decode CSV bytes as UTF-8 (with or without a byte-order mark), else Windows-1252 (5.3.3).

    `utf-8-sig` decodes plain UTF-8 correctly too (it only strips a BOM when one is present), so
    it is tried first; only bytes that are not valid UTF-8 fall back to `cp1252`. That codec
    leaves five bytes undefined (0x81, 0x8D, 0x8F, 0x90 and 0x9D), so bytes that are neither
    raise `UnicodeDecodeError`. Such a file is not a text CSV file, and each caller refuses it
    in its own words.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def parse_hh_mm(text: str) -> time:
    """Parse a plain, two-digit "HH:MM" time of day, the CSV formats' optional time-of-day column.

    Raises `ValueError` for anything else, including a single-digit hour or an out-of-range hour
    or minute. Callers turn that into their own row-level review result or error (5.3.3): only
    they know which line, and which file, it came from.
    """
    match = _HH_MM.match(text)
    if match is None:
        raise ValueError(f"Not an HH:MM time: {text!r}.")
    hours, minutes = int(match.group("hours")), int(match.group("minutes"))
    if hours > 23 or minutes > 59:
        raise ValueError(f"Not a valid time of day: {text!r}.")
    return time(hours, minutes)
