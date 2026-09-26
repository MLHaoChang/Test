"""Tests for the CSV helpers shared by the Trade Republic, manual and confirmed-holdings formats.

Encoding sniffing and the small "HH:MM" time parser are the two pieces every format needs, so
they are unit-tested directly here (plan 5.3.3), in addition to being exercised end-to-end
through the golden fixtures of `test_csv_goldens.py` and `test_manual_csv.py`.
"""

from datetime import time

import pytest

from playground.importer.csv_common import decode_csv_bytes, parse_hh_mm


def test_plain_utf8_decodes_unchanged() -> None:
    assert decode_csv_bytes("Kauf;Kürs".encode()) == "Kauf;Kürs"


def test_utf8_with_bom_has_the_bom_stripped() -> None:
    assert decode_csv_bytes(b"\xef\xbb\xbfKauf;Kurs") == "Kauf;Kurs"


def test_bytes_that_are_not_valid_utf8_fall_back_to_windows_1252() -> None:
    data = "Gebühren;Währung".encode("cp1252")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    assert decode_csv_bytes(data) == "Gebühren;Währung"


@pytest.mark.parametrize("byte", [b"\x81", b"\x8d", b"\x8f", b"\x90", b"\x9d"])
def test_bytes_that_are_neither_utf8_nor_windows_1252_raise(byte: bytes) -> None:
    # Windows-1252 leaves these five bytes undefined, so the fallback cannot decode them either.
    with pytest.raises(UnicodeDecodeError):
        decode_csv_bytes(b"Datum;Typ\n" + byte + b"\n")


def test_hh_mm_reads_a_plain_time() -> None:
    assert parse_hh_mm("09:05") == time(9, 5)
    assert parse_hh_mm("23:59") == time(23, 59)
    assert parse_hh_mm("00:00") == time(0, 0)


@pytest.mark.parametrize("text", ["9:05", "24:00", "12:60", "12-05", "", "10:5", "noon"])
def test_hh_mm_rejects_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match="time"):
        parse_hh_mm(text)
