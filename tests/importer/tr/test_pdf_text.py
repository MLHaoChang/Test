"""Tests for the PDF-to-text layer (plan 5.3.2 layer 1, 7.3, AC3).

`extract_text(pdf)` runs pdfplumber page by page, joins the pages with a form feed and then
normalises the text explicitly, so the result does not depend on the pdfplumber version:
non-breaking spaces become spaces, runs of spaces and tabs become one space, lines are trimmed,
empty lines and empty pages are dropped, and the text is in Unicode NFC. Every line ends with a
line feed, except the last line of a page that another page follows, which ends with the form
feed. The `.txt` fixtures are written in this normal form, so every PDF extracts to exactly its
`.txt`. No parsing happens in this layer.
"""

import io
import unicodedata
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from playground.core.errors import PlaygroundError
from playground.importer.tr.pdf_text import (
    PdfTextError,
    extract_text,
    is_normal_form,
    normalise_pages,
    normalise_text,
)


def test_every_generated_pdf_extracts_to_exactly_its_text(pdf_job: Any) -> None:
    expected = pdf_job.source.read_text(encoding="utf-8")
    assert extract_text(pdf_job.target.read_bytes()) == expected


def test_euro_sign_umlauts_and_sharp_s_survive_the_pdf_layer(make_pdfs: ModuleType) -> None:
    text = "1.401,00 € Solidaritätszuschlag ÄÖÜß äöü\nDividende 1,20 € Kirchensteuer\n"
    assert extract_text(make_pdfs.render_pdf(text, "special characters")) == text


def test_pages_are_joined_with_a_form_feed(make_pdfs: ModuleType) -> None:
    text = "SEITE 1 von 2\nerste Seite\fSEITE 2 von 2\nzweite Seite\n"
    assert extract_text(make_pdfs.render_pdf(text, "two pages")) == text


def test_a_pdf_with_a_blank_page_only_gives_empty_text() -> None:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1)
    pdf.showPage()
    pdf.save()
    assert extract_text(buffer.getvalue()) == ""


@pytest.mark.parametrize("data", [b"", b"this is not a PDF", b"%PDF-1.4\n1 0 obj\n"])
def test_bytes_that_are_not_a_readable_pdf_raise_a_plain_error(data: bytes) -> None:
    with pytest.raises(PdfTextError) as error:
        extract_text(data)
    assert isinstance(error.value, PlaygroundError)
    assert str(error.value) == "This file could not be read as a PDF."


def test_a_cut_off_pdf_raises_a_plain_error(make_pdfs: ModuleType) -> None:
    data = make_pdfs.render_pdf("eine Zeile\n", "cut off")
    with pytest.raises(PdfTextError, match="could not be read as a PDF"):
        extract_text(data[: len(data) // 2])


def test_a_password_protected_pdf_raises_a_plain_error() -> None:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1, encrypt=pdfencrypt.StandardEncryption("secret", canPrint=1))
    pdf.drawString(50, 700, "geheim")
    pdf.showPage()
    pdf.save()
    with pytest.raises(PdfTextError) as error:
        extract_text(buffer.getvalue())
    assert str(error.value) == "This PDF is protected by a password. Remove the password and import it again."


# --- Normalisation ------------------------------------------------------------------------


def test_non_breaking_spaces_become_spaces() -> None:
    assert normalise_text("1.401,00 EUR netto x") == "1.401,00 EUR netto x\n"


def test_runs_of_spaces_and_tabs_collapse_and_lines_are_trimmed() -> None:
    assert normalise_text("   BUCHUNGSTAG   WERTSTELLUNG \t BETRAG  ") == "BUCHUNGSTAG WERTSTELLUNG BETRAG\n"


def test_empty_lines_and_blank_lines_are_removed() -> None:
    assert normalise_text("a\n\n   \nb\n\n") == "a\nb\n"


def test_text_is_put_in_nfc() -> None:
    decomposed = "Solidaritätszuschlag"
    assert unicodedata.is_normalized("NFD", decomposed)
    assert normalise_text(decomposed) == "Solidaritätszuschlag\n"


def test_carriage_returns_and_other_line_breaks_become_line_feeds() -> None:
    assert normalise_text("a\r\nb\rc d\u0085e") == "a\nb\nc\nd\ne\n"


def test_zero_width_characters_and_control_characters_are_removed() -> None:
    assert normalise_text("﻿SAP​ SE\x00\x07") == "SAP SE\n"


def test_pages_are_separated_by_a_form_feed_and_empty_pages_dropped() -> None:
    assert normalise_pages(["a\n b ", "", "  \n ", "c"]) == "a\nb\fc\n"
    assert normalise_text("a\n\f\fb\n") == "a\fb\n"


def test_empty_text_stays_empty() -> None:
    assert normalise_text("") == ""
    assert normalise_pages([]) == ""
    assert normalise_pages(["", " \n "]) == ""


def test_normal_form_check() -> None:
    assert is_normal_form("a b\nc\fd\n")
    assert is_normal_form("")
    assert not is_normal_form("a  b\n")
    assert not is_normal_form("a\n\nb\n")
    assert not is_normal_form(" a\n")
    assert not is_normal_form("a")  # the last line must end with a line feed
    assert not is_normal_form("a\n\f")


@given(st.text())
def test_normalisation_is_idempotent(text: str) -> None:
    once = normalise_text(text)
    assert normalise_text(once) == once
    assert is_normal_form(once)


@given(st.lists(st.text()))
def test_normalising_pages_is_normalising_the_joined_text(pages: list[str]) -> None:
    assert normalise_pages(pages) == normalise_text("\f".join(pages))


def test_the_text_fixtures_are_in_normal_form(text_fixture: Path) -> None:
    assert is_normal_form(text_fixture.read_text(encoding="utf-8"))
