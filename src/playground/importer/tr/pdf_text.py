"""PDF to text with explicit normalisation (plan 5.3.2, layer 1).

This layer only turns PDF bytes into text. It does no parsing: the classifier and the layout
parsers (layer 2, `classify.py` and `layouts/`) work on its output, and the two layers are
tested separately (design review condition 2).

pdfplumber reads the PDF page by page. Its text is then normalised explicitly, so the result
does not depend on the pdfplumber version and the text fixtures can be written by hand:

1. Line breaks: CR LF and CR become LF. Other line separators (vertical tab, NEL, U+2028,
   U+2029 and the file, group and record separators) also become LF.
2. Zero-width characters (zero-width space, joiners, word joiner, byte order mark) are removed.
3. Tabs and every Unicode space character, the non-breaking spaces included, become a space.
4. Other control characters are removed; LF and the form feed (FF, the page break) are kept.
5. The text is put in Unicode NFC, so "a" plus a combining diaeresis becomes one "ä".
6. Pages are split on FF and lines on LF. In every line, runs of spaces become one space and
   leading and trailing spaces are removed. Empty lines are dropped, and so are pages with no
   line left.

The normal form joins the lines of a page with LF and the pages with FF, and ends with one LF.
So every line ends with LF, except the last line of a page that another page follows, which
ends with FF; `str.splitlines()` gives the lines, the page breaks included. An empty document
gives "". The `.txt` fixtures are written in this form, and `tests/fixtures/tr/make_pdfs.py`
refuses one that is not, so every fixture PDF extracts to exactly its `.txt`.
"""

import io
import re
import unicodedata
from collections.abc import Sequence

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from playground.core.errors import PlaygroundError

PAGE_BREAK = "\f"

# Step 1: every line break that is not LF, and the form feed kept apart (it is the page break).
_LINE_BREAKS = re.compile("\r\n|[\r\v\x1c\x1d\x1e\x85  ]")
# Step 2.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_SPACE_RUNS = re.compile(" {2,}")


class PdfTextError(PlaygroundError):
    """A file could not be read as a PDF (it is damaged, protected or not a PDF at all)."""


def extract_text(pdf: bytes) -> str:
    """Return the text of every page of `pdf`, in the normal form described in the module docstring.

    Raises `PdfTextError` with a plain-English message when the bytes are not a readable PDF.
    A PDF without any text (a scan, a blank page) gives "".
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf)) as document:
            pages = [page.extract_text() or "" for page in document.pages]
    except Exception as exc:  # pdfminer raises many kinds of error on a damaged file
        if _caused_by_password(exc):
            raise PdfTextError("This PDF is protected by a password. Remove the password and import it again.") from exc
        raise PdfTextError("This file could not be read as a PDF.") from exc
    return normalise_pages(pages)


def _caused_by_password(exc: BaseException) -> bool:
    """True when pdfminer refused the file because of a password (pdfplumber wraps its errors)."""
    if isinstance(exc, PDFPasswordIncorrect):
        return True
    return any(isinstance(arg, PDFPasswordIncorrect) for arg in exc.args)


def normalise_pages(pages: Sequence[str]) -> str:
    """Normalise the texts of consecutive pages into one text, pages separated by a form feed."""
    return normalise_text(PAGE_BREAK.join(pages))


def normalise_text(text: str) -> str:
    """Return `text` in the normal form described in the module docstring. Form feeds separate pages."""
    text = _LINE_BREAKS.sub("\n", text)
    text = text.translate(_ZERO_WIDTH)
    text = "".join(_normalise_character(character) for character in text)
    text = unicodedata.normalize("NFC", text)

    pages = []
    for page in text.split(PAGE_BREAK):
        lines = [_SPACE_RUNS.sub(" ", line).strip(" ") for line in page.split("\n")]
        lines = [line for line in lines if line]
        if lines:
            pages.append("\n".join(lines))
    if not pages:
        return ""
    return PAGE_BREAK.join(pages) + "\n"


def _normalise_character(character: str) -> str:
    """Steps 3 and 4 for one character: spaces become " ", control characters other than LF and FF go."""
    if character in "\n\f":
        return character
    if character == "\t" or unicodedata.category(character) == "Zs":
        return " "
    if unicodedata.category(character) == "Cc":
        return ""
    return character


def is_normal_form(text: str) -> bool:
    """True when `text` is already in the normal form, so normalising it changes nothing."""
    return normalise_text(text) == text
