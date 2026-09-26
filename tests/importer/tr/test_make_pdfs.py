"""Tests for `tests/fixtures/tr/make_pdfs.py`, the synthetic PDF fixture generator (plan 2.2, 6.2).

It writes one PDF per text fixture with reportlab and the standard Helvetica font, one text line
per fixture line, with `invariant=1` so a second run gives the same bytes. It refuses a text
that is not in the normal form of plan 5.3.2, or that Helvetica's WinAnsi encoding cannot print.
"""

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from playground.importer.tr.pdf_text import extract_text

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
TEXT_DIR = FIXTURES_DIR / "tr" / "text"


def test_a_second_run_gives_byte_identical_pdfs_equal_to_the_committed_ones(
    pdf_job: Any, make_pdfs: ModuleType
) -> None:
    text = pdf_job.source.read_text(encoding="utf-8")
    first = make_pdfs.render_pdf(text, pdf_job.title)
    second = make_pdfs.render_pdf(text, pdf_job.title)
    assert first == second
    assert pdf_job.target.read_bytes() == first, "committed PDF is out of date: run tests/fixtures/tr/make_pdfs.py"


def test_every_text_fixture_has_at_least_one_pdf(make_pdfs: ModuleType) -> None:
    sources = {job.source for job in make_pdfs.pdf_jobs()}
    assert sources == set(TEXT_DIR.glob("*/*.txt"))


def test_every_pdf_under_the_fixtures_comes_from_exactly_one_job(make_pdfs: ModuleType) -> None:
    targets = [job.target for job in make_pdfs.pdf_jobs()]
    assert len(targets) == len(set(targets)), "two jobs write the same PDF"
    assert set(FIXTURES_DIR.rglob("*.pdf")) == set(targets)


def test_default_pdf_location_mirrors_the_text_tree(make_pdfs: ModuleType) -> None:
    by_source = {job.source.relative_to(TEXT_DIR).as_posix(): job for job in make_pdfs.pdf_jobs()}
    job = by_source["tr.wertpapierabrechnung.de.2019/kauf_market_winter.txt"]
    assert job.target == FIXTURES_DIR / "tr" / "pdf" / "tr.wertpapierabrechnung.de.2019" / "kauf_market_winter.pdf"
    assert job.title == "kauf_market_winter"


def test_the_golden_portfolio_pdfs_are_written_to_the_golden_inputs(make_pdfs: ModuleType) -> None:
    targets = {job.target.relative_to(FIXTURES_DIR).as_posix() for job in make_pdfs.pdf_jobs()}
    assert {
        "golden/inputs/pdf/2024-01-15_kauf_sap.pdf",
        "golden/inputs/pdf/2024-02-01_sparplan_msciw.pdf",
        "golden/inputs/pdf/2024-03-20_kauf_nvda.pdf",
        "golden/inputs/pdf/2024-04-10_kauf_sap.pdf",
        "golden/inputs/pdf/2024-06-12_verkauf_sap.pdf",
        "golden/inputs/pdf/unbekannt_kosteninformation.pdf",
    } <= targets


def test_the_same_day_savings_plan_pair_and_the_copy_of_a(make_pdfs: ModuleType) -> None:
    directory = FIXTURES_DIR / "idempotency" / "same_day_savings_plans"
    a = (directory / "same_day_a.pdf").read_bytes()
    b = (directory / "same_day_b.pdf").read_bytes()
    copy = (directory / "same_day_a_copy.pdf").read_bytes()
    text_a = (TEXT_DIR / "tr.sparplan.de" / "same_day_a.txt").read_text(encoding="utf-8")
    text_b = (TEXT_DIR / "tr.sparplan.de" / "same_day_b.txt").read_text(encoding="utf-8")
    # A and B differ only in their execution numbers.
    differing = [(x, y) for x, y in zip(text_a.splitlines(), text_b.splitlines(), strict=True) if x != y]
    assert differing == [("12345 Musterstadt AUSFÜHRUNG e1f0-2a7c", "12345 Musterstadt AUSFÜHRUNG c9d4-86b3")]
    # The copy of A has other bytes (another PDF title) but exactly the same text.
    assert copy != a
    assert extract_text(copy) == extract_text(a) == text_a
    assert extract_text(b) == text_b


def test_a_different_title_gives_different_bytes_but_the_same_text(make_pdfs: ModuleType) -> None:
    first = make_pdfs.render_pdf("eine Zeile\n", "one")
    other = make_pdfs.render_pdf("eine Zeile\n", "two")
    assert first != other
    assert extract_text(first) == extract_text(other) == "eine Zeile\n"


@pytest.mark.parametrize(
    "text",
    ["two  spaces\n", " leading space\n", "trailing space \n", "a\n\nb\n", "no final line feed", "tab\there\n"],
)
def test_text_not_in_normal_form_is_refused(make_pdfs: ModuleType, text: str) -> None:
    with pytest.raises(make_pdfs.FixtureTextError, match="normal form"):
        make_pdfs.render_pdf(text, "bad")


@pytest.mark.parametrize("text", ["Pfeil → rechts\n", "Emoji \U0001f600\n"])
def test_characters_helvetica_cannot_print_are_refused(make_pdfs: ModuleType, text: str) -> None:
    with pytest.raises(make_pdfs.FixtureTextError, match="cannot be printed"):
        make_pdfs.render_pdf(text, "bad")


def test_a_line_too_wide_for_the_page_is_refused(make_pdfs: ModuleType) -> None:
    with pytest.raises(make_pdfs.FixtureTextError, match="too wide"):
        make_pdfs.render_pdf("W" * 200 + "\n", "bad")


def test_a_page_with_too_many_lines_is_refused(make_pdfs: ModuleType) -> None:
    with pytest.raises(make_pdfs.FixtureTextError, match="too many lines"):
        make_pdfs.render_pdf("".join(f"Zeile {n}\n" for n in range(200)), "bad")


def test_check_mode_reports_up_to_date_pdfs_and_writes_nothing(make_pdfs: ModuleType) -> None:
    before = {path: path.stat().st_mtime_ns for path in FIXTURES_DIR.rglob("*.pdf")}
    assert make_pdfs.main(["--check"]) == 0
    assert {path: path.stat().st_mtime_ns for path in FIXTURES_DIR.rglob("*.pdf")} == before


def test_the_manifest_names_the_text_each_pdf_was_generated_from(make_pdfs: ModuleType) -> None:
    manifest = yaml.safe_load((FIXTURES_DIR / "MANIFEST.yaml").read_text(encoding="utf-8"))
    generated_from = {entry["path"]: entry.get("generated_from") for entry in manifest["files"]}
    for job in make_pdfs.pdf_jobs():
        target = job.target.relative_to(FIXTURES_DIR).as_posix()
        assert generated_from.get(target) == job.source.relative_to(FIXTURES_DIR).as_posix(), target
