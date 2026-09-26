"""Tests for `anonymise.anonymise_text` (plan 5.3.5, WP8).

`pg review export ID --anonymise` runs this over a review item's extracted text, so a new,
unrecognised layout can be shared and turned into a fixture without ever sending the file itself.
These tests check the masking rules one at a time with small examples, then sweep every text and
CSV fixture in the repository, as the plan's tests-first list asks: "anonymise_text masks IBANs,
depot numbers, order numbers and address lines in every text and CSV fixture."
"""

import re
from pathlib import Path

import pytest

from playground.importer.anonymise import anonymise_text

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# The full, closed set of synthetic personal data used anywhere in the fixture corpus (checked by
# test_every_personal_token_used_in_the_fixtures_is_covered below, so this list cannot go stale).
NAMES = ("Jana Beispiel", "Lena Fiktiv", "Tom Probe")
STREETS = ("Ahornweg 7", "Birkenallee 12", "Lindenring 3", "Lindenring 4")
CITY_LINES = ("10999 Probedorf", "12345 Musterstadt", "54321 Beispielheim", "54321 Probedorf")
DUMMY_IBAN = "DE" + "0" * 20


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # windows1252.csv (plan 6.1): one CSV fixture is deliberately not UTF-8.
        return path.read_text(encoding="cp1252")


# --- One rule at a time --------------------------------------------------------------------------


def test_masks_the_name_before_seite_or_page() -> None:
    assert anonymise_text("Jana Beispiel SEITE 1 von 1\n") == "[name] SEITE 1 von 1\n"
    assert anonymise_text("Lena Fiktiv PAGE 1 from 1\n") == "[name] PAGE 1 from 1\n"
    # A name with more than two words is masked whole, and the page label is untouched.
    assert anonymise_text("Anna Maria Muster SEITE 1 von 2\n") == "[name] SEITE 1 von 2\n"


def test_masks_the_street_before_datum_or_date() -> None:
    assert anonymise_text("Ahornweg 7 DATUM 31.03.2023\n") == "[address] DATUM 31.03.2023\n"
    assert anonymise_text("Lindenring 4 DATE 05.02.2024\n") == "[address] DATE 05.02.2024\n"


@pytest.mark.parametrize(
    "line",
    [
        "12345 Musterstadt ORDER 3f8a-0c21\n",
        "54321 Beispielheim DEPOT 0000002222\n",
        "12345 Musterstadt REFERENZ 9c3f-51ad\n",
        "12345 Musterstadt AUSFÜHRUNG 2c61-7e0b\n",
        "10999 Probedorf ORDER 6d1e-f402\n",
    ],
)
def test_masks_the_postal_code_and_city_whatever_follows_it(line: str) -> None:
    masked = anonymise_text(line)
    assert masked.startswith("[address] ")
    for city_line in CITY_LINES:
        assert city_line not in masked


def test_masks_a_postal_code_and_city_with_nothing_after_it() -> None:
    assert anonymise_text("12345 Musterstadt\n") == "[address]\n"


def test_masks_the_iban_written_in_one_piece_or_grouped_in_four() -> None:
    assert anonymise_text(f"{DUMMY_IBAN} 17.01.2024 -1.401,00 EUR\n") == "[iban] 17.01.2024 -1.401,00 EUR\n"
    assert anonymise_text("DE89 3704 0044 0532 0130 00\n") == "[iban]\n"


def test_does_not_mask_an_isin_as_an_iban() -> None:
    # An ISIN is 12 characters; the shortest IBAN this app treats as one is 14 (plan 6.1's real
    # ISINs, and every fixture's bare or "ISIN:"-prefixed ISIN, must never be touched).
    text = "ISIN: DE0007164600 IE00B4L5Y983 US67066G1040\nDE0008404005\n"
    assert anonymise_text(text) == text


def test_masks_only_the_number_after_depot_or_securities_account() -> None:
    assert anonymise_text("DEPOT 0000001111\n") == "DEPOT [depot]\n"
    assert anonymise_text("SECURITIES ACCOUNT 0000003333\n") == "SECURITIES ACCOUNT [depot]\n"


def test_masks_only_the_number_after_order() -> None:
    assert anonymise_text("ORDER 3f8a-0c21\n") == "ORDER [order]\n"


@pytest.mark.parametrize("label", ["AUSFÜHRUNG", "REFERENZ", "SPARPLAN", "EXECUTION"])
def test_leaves_transaction_reference_numbers_alone(label: str) -> None:
    # These only ever name a transaction, never you (plan 5.3.5 names IBANs, depot and order
    # numbers, and the address, not these), so they, and their numbers, are left untouched.
    line = f"{label} 5d0a-93f1\n"
    assert anonymise_text(line) == line


def test_leaves_everything_else_untouched() -> None:
    text = (
        "WERTPAPIERABRECHNUNG\n"
        "POSITION ANZAHL PREIS BETRAG\n"
        "SAP SE 10 Stk. 140,00 EUR 1.400,00 EUR\n"
        "ISIN: DE0007164600\n"
        "GESAMT -1.401,00 EUR\n"
    )
    assert anonymise_text(text) == text


def test_is_idempotent() -> None:
    """Anonymising already-anonymised text changes nothing further."""
    text = "Jana Beispiel SEITE 1 von 1\nAhornweg 7 DATUM 31.03.2023\n12345 Musterstadt DEPOT 0000001111\n"
    once = anonymise_text(text)
    assert anonymise_text(once) == once


def test_handles_text_with_nothing_to_mask() -> None:
    assert anonymise_text("") == ""
    assert anonymise_text("hello world\nno personal data here\n") == "hello world\nno personal data here\n"


# --- Every fixture in the repository --------------------------------------------------------------


def _all_text_fixtures() -> list[Path]:
    return sorted((FIXTURES / "tr" / "text").rglob("*.txt"))


def _all_csv_fixtures() -> list[Path]:
    return sorted(
        {
            *FIXTURES.glob("tr/csv/**/*.csv"),
            *FIXTURES.glob("manual_csv/*.csv"),
            *FIXTURES.glob("confirmed/*.csv"),
            *FIXTURES.glob("golden/inputs/*.csv"),
            *FIXTURES.glob("idempotency/**/*.csv"),
        }
    )


def test_every_personal_token_used_in_the_fixtures_is_covered() -> None:
    """A regression test for this file's own token lists, so a new fixture cannot go unchecked."""
    name_pattern = re.compile(r"^([A-ZÄÖÜ][a-zäöüß]+(?: [A-ZÄÖÜ][a-zäöüß]+)+) (?:SEITE|PAGE) ", re.MULTILINE)
    street_pattern = re.compile(
        r"^([A-ZÄÖÜ][a-zäöüß]+(?:ring|weg|allee|stra(?:ß|ss)e) \d+) (?:DATUM|DATE) ", re.MULTILINE
    )
    city_pattern = re.compile(r"^(\d{4,5} [A-ZÄÖÜ][a-zäöüß]+)(?: |$)", re.MULTILINE)

    found_names, found_streets, found_cities = set(), set(), set()
    for path in _all_text_fixtures():
        text = _read(path)
        found_names.update(name_pattern.findall(text))
        found_streets.update(street_pattern.findall(text))
        found_cities.update(city_pattern.findall(text))

    assert found_names == set(NAMES)
    assert found_streets == set(STREETS)
    assert found_cities == set(CITY_LINES)


@pytest.mark.parametrize("path", _all_text_fixtures(), ids=lambda path: str(path.relative_to(FIXTURES)))
def test_every_text_fixture_is_safe_to_share_once_anonymised(path: Path) -> None:
    masked = anonymise_text(_read(path))

    for name in NAMES:
        assert name not in masked, f"{path}: name {name!r} survived anonymisation"
    for street in STREETS:
        assert street not in masked, f"{path}: street {street!r} survived anonymisation"
    for city_line in CITY_LINES:
        assert city_line not in masked, f"{path}: address {city_line!r} survived anonymisation"
    assert DUMMY_IBAN not in masked, f"{path}: the dummy IBAN survived anonymisation"

    for match in re.finditer(r"\bORDER\s+(\S+)", masked):
        assert match.group(1) == "[order]", f"{path}: an order number survived anonymisation"
    for match in re.finditer(r"\b(?:DEPOT|SECURITIES ACCOUNT)\s+(\S+)", masked):
        assert match.group(1) == "[depot]", f"{path}: a depot number survived anonymisation"


@pytest.mark.parametrize("path", _all_csv_fixtures(), ids=lambda path: str(path.relative_to(FIXTURES)))
def test_every_csv_fixture_is_unaffected(path: Path) -> None:
    """None of the CSV formats (plan 5.3.3) carry a name, address, IBAN, depot or order number, so
    anonymising one changes nothing; this fixes that invariant so a future format cannot break it
    unnoticed."""
    text = _read(path)
    assert anonymise_text(text) == text
