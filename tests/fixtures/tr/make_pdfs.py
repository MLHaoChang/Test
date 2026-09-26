"""Write the synthetic PDF fixtures from their text files (plan 2.2, 6.2).

Run it from the repository root after adding or changing a text fixture:

    uv run python tests/fixtures/tr/make_pdfs.py            # write every PDF that changed
    uv run python tests/fixtures/tr/make_pdfs.py --check    # only check; exit 1 if a PDF is out of date

The text fixtures `tests/fixtures/tr/text/<layout>/<case>.txt` are the source of truth. Each one
becomes `tests/fixtures/tr/pdf/<layout>/<case>.pdf`, except the ones in `SPECIAL_TARGETS`: the
golden portfolio documents go to `tests/fixtures/golden/inputs/pdf/`, and the same-day savings
plan pair goes to `tests/fixtures/idempotency/same_day_savings_plans/`, with a copy of document A
that has another PDF title, so its bytes differ while its text is the same.

The PDFs are drawn with reportlab and the standard Helvetica font, one text line per fixture
line, and pages split at the form feeds. Helvetica's WinAnsi encoding covers German umlauts, ß
and the euro sign, so no font file is embedded. `invariant=1` fixes the dates and the document
id, and page compression is off, so a second run gives the same bytes on every machine. The
script refuses a text that is not in the normal form of `playground.importer.tr.pdf_text`, or
that Helvetica cannot print, so every PDF extracts to exactly its text file.

This is a development tool: reportlab is a development dependency only.
"""

import argparse
import io
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from playground.importer.tr.pdf_text import is_normal_form, normalise_text

FIXTURES_DIR = Path(__file__).resolve().parent.parent
TEXT_DIR = FIXTURES_DIR / "tr" / "text"
PDF_DIR = FIXTURES_DIR / "tr" / "pdf"

# Text fixtures (relative to TEXT_DIR) whose PDFs go elsewhere (relative to FIXTURES_DIR).
SPECIAL_TARGETS: dict[str, tuple[str, ...]] = {
    "tr.wertpapierabrechnung.de.2023/golden_t02_kauf_sap.txt": ("golden/inputs/pdf/2024-01-15_kauf_sap.pdf",),
    "tr.sparplan.de/golden_t03_sparplan_msciw.txt": ("golden/inputs/pdf/2024-02-01_sparplan_msciw.pdf",),
    "tr.wertpapierabrechnung.de.2023/golden_t06_kauf_nvda.txt": ("golden/inputs/pdf/2024-03-20_kauf_nvda.pdf",),
    "tr.wertpapierabrechnung.de.2023/golden_t08_kauf_sap.txt": ("golden/inputs/pdf/2024-04-10_kauf_sap.pdf",),
    "tr.wertpapierabrechnung.de.2023/golden_t12_verkauf_sap.txt": ("golden/inputs/pdf/2024-06-12_verkauf_sap.pdf",),
    "unclassified/unknown_cost_information.txt": ("golden/inputs/pdf/unbekannt_kosteninformation.pdf",),
    "tr.dividende.de/golden_t09_dividende_sap.txt": ("golden/inputs/pdf/2024-05-15_dividende_sap.pdf",),
    "tr.dividende.de/golden_t10_dividende_aapl.txt": ("golden/inputs/pdf/2024-05-16_dividende_aapl.pdf",),
    "tr.split.de/golden_t11_split_nvda.txt": ("golden/inputs/pdf/2024-06-10_split_nvda.pdf",),
    "tr.kontoauszug.de.2024/golden_h1_statement.txt": ("golden/inputs/statement/kontoauszug_2024_h1.pdf",),
    "tr.sparplan.de/same_day_a.txt": (
        "idempotency/same_day_savings_plans/same_day_a.pdf",
        "idempotency/same_day_savings_plans/same_day_a_copy.pdf",
    ),
    "tr.sparplan.de/same_day_b.txt": ("idempotency/same_day_savings_plans/same_day_b.pdf",),
}

FONT = "Helvetica"
FONT_SIZE = 8
LEADING = 11
MARGIN = 50
PAGE_WIDTH, PAGE_HEIGHT = A4
MAX_LINE_WIDTH = PAGE_WIDTH - 2 * MARGIN
MAX_LINES_PER_PAGE = int((PAGE_HEIGHT - 2 * MARGIN) // LEADING) + 1
AUTHOR = "Synthetic test fixture (playground)"


class FixtureTextError(ValueError):
    """A text fixture cannot be turned into a PDF that extracts to exactly that text."""


@dataclass(frozen=True)
class PdfJob:
    """One PDF to write: from the text file `source` to `target`, with the PDF title `title`."""

    source: Path
    target: Path
    title: str


def pdf_jobs() -> list[PdfJob]:
    """Every PDF this script writes, in a stable order."""
    sources = sorted(TEXT_DIR.glob("*/*.txt"))
    known = {source.relative_to(TEXT_DIR).as_posix() for source in sources}
    stale = sorted(set(SPECIAL_TARGETS) - known)
    if stale:
        raise FixtureTextError(f"SPECIAL_TARGETS names text fixtures that do not exist: {stale}")

    jobs = []
    for source in sources:
        relative = source.relative_to(TEXT_DIR)
        if relative.as_posix() in SPECIAL_TARGETS:
            targets = [FIXTURES_DIR / path for path in SPECIAL_TARGETS[relative.as_posix()]]
        else:
            targets = [PDF_DIR / relative.with_suffix(".pdf")]
        jobs.extend(PdfJob(source=source, target=target, title=target.stem) for target in targets)
    return jobs


def check_fixture_text(text: str) -> None:
    """Raise `FixtureTextError` unless `text` is in normal form and every line fits on a page in Helvetica."""
    if not is_normal_form(text):
        raise FixtureTextError(f"the text is not in normal form ({_first_difference(text)})")
    for page_number, page in enumerate(text.split("\f"), start=1):
        lines = page.splitlines()
        if len(lines) > MAX_LINES_PER_PAGE:
            raise FixtureTextError(
                f"page {page_number} has too many lines ({len(lines)}; at most {MAX_LINES_PER_PAGE} fit on a page)"
            )
        for line in lines:
            try:
                line.encode("cp1252")
            except UnicodeEncodeError as exc:
                raise FixtureTextError(
                    f"the character {line[exc.start]!r} in {line!r} cannot be printed with the standard "
                    "Helvetica font (WinAnsi encoding)"
                ) from exc
            if stringWidth(line, FONT, FONT_SIZE) > MAX_LINE_WIDTH:
                raise FixtureTextError(f"the line {line!r} is too wide for the page")


def _first_difference(text: str) -> str:
    """Say where normalising `text` would change it."""
    normal = normalise_text(text)
    if not text.endswith("\n"):
        return "the last line must end with a line feed"
    for number, (line, expected) in enumerate(zip(text.splitlines(), normal.splitlines(), strict=False), start=1):
        if line != expected:
            return f"line {number} would become {expected!r}"
    return "an empty line, a blank page or trailing spaces would be removed"


def render_pdf(text: str, title: str) -> bytes:
    """Draw `text` as a PDF: one line of text per line, a new page at every form feed."""
    check_fixture_text(text)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1, pageCompression=0)
    pdf.setTitle(title)
    pdf.setAuthor(AUTHOR)
    pdf.setCreator("tests/fixtures/tr/make_pdfs.py")
    for page in text.split("\f"):
        pdf.setFont(FONT, FONT_SIZE)
        y = PAGE_HEIGHT - MARGIN
        for line in page.splitlines():
            pdf.drawString(MARGIN, y, line)
            y -= LEADING
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the synthetic PDF fixtures from their text files.")
    parser.add_argument("--check", action="store_true", help="only check that every PDF is up to date")
    args = parser.parse_args(argv)

    out_of_date = []
    written = 0
    try:
        jobs = pdf_jobs()
        for job in jobs:
            try:
                data = render_pdf(job.source.read_text(encoding="utf-8"), job.title)
            except FixtureTextError as exc:
                raise FixtureTextError(f"{job.source.relative_to(FIXTURES_DIR)}: {exc}") from exc
            if job.target.is_file() and job.target.read_bytes() == data:
                continue
            out_of_date.append(job.target.relative_to(FIXTURES_DIR).as_posix())
            if not args.check:
                job.target.parent.mkdir(parents=True, exist_ok=True)
                job.target.write_bytes(data)
                written += 1
    except FixtureTextError as exc:
        print(f"Cannot write the PDF fixtures: {exc}.", file=sys.stderr)
        return 1

    if args.check:
        if out_of_date:
            print(
                "These PDF fixtures are out of date. Run tests/fixtures/tr/make_pdfs.py to rewrite them:",
                file=sys.stderr,
            )
            for path in out_of_date:
                print(f"  {path}", file=sys.stderr)
            return 1
        print(f"All {len(jobs)} PDF fixtures are up to date.")
        return 0
    print(f"Wrote {written} of {len(jobs)} PDF fixtures; the others were already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
