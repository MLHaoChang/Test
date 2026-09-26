"""Tests for repository hygiene and safety."""

import ast
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Literal, get_args, get_origin

import yaml
from typer.testing import CliRunner

from playground.cli.main import app
from playground.config import Settings


def _repo_root() -> Path:
    """Return the repository root, two levels above this file (tests/test_repo_hygiene.py)."""
    return Path(__file__).parent.parent


def test_no_sensitive_files_committed() -> None:
    """Test that no sensitive files are committed."""
    repo_root = _repo_root()
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    files = result.stdout.splitlines()

    # Check for database files
    assert not any(f.endswith(".sqlite") for f in files), "SQLite files should not be committed"
    assert not any(f.endswith(".db") for f in files), "Database files should not be committed"

    # Check for environment files
    assert not any(".env" in f for f in files), ".env files should not be committed"

    # Check that data directories are not committed
    assert not any(f.startswith("data/") for f in files), "data/ should be in .gitignore"
    assert not any(f.startswith("uploads/") for f in files), "uploads/ should be in .gitignore"
    assert not any(f.startswith("registry/") for f in files), "registry/ should be in .gitignore"


def test_gitignore_exists() -> None:
    """Test that .gitignore file exists and has required entries."""
    repo_root = _repo_root()
    gitignore = repo_root / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist"

    content = gitignore.read_text()
    required_entries = ["data/", "uploads/", "registry/", "lake/", ".env", ".venv"]
    for entry in required_entries:
        assert entry in content, f".gitignore must contain {entry}"


def _contains_phrase(text: str, phrase: str) -> bool:
    """Return True if `phrase` appears in `text` as whole words, case-insensitively.

    A plain substring check would also match "olive" for "live" or flag a
    file that merely mentions "delivering trades", so this matches on word
    boundaries instead.
    """
    return re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE) is not None


def _python_files_containing(root: Path, phrase: str) -> list[Path]:
    """Return the .py files under `root` whose text contains `phrase`.

    Returns an empty list, rather than raising, when `root` does not exist
    yet, since some scanned directories (e.g. web/src) are added in later
    work packages.
    """
    if not root.exists():
        return []
    return [f for f in sorted(root.rglob("*.py")) if _contains_phrase(f.read_text(), phrase)]


def test_contains_phrase_matches_whole_words_only() -> None:
    """Unit test for the _contains_phrase helper used by the wording checks below.

    This guards the matching logic itself, independently of what the repo
    currently contains, so a broken regex cannot silently defeat the checks
    the way the wrong repo_root below once did.
    """
    assert _contains_phrase("This still supports live trading today.", "live trading") is True
    assert _contains_phrase("LIVE TRADING", "live trading") is True
    assert _contains_phrase("an olive branch, delivered", "live") is False


def test_python_files_containing_finds_matches(tmp_path: Path) -> None:
    """Unit test for the scanner helper, independent of real repo content.

    This is the regression test for the bug this fix addresses: a wrong
    number of `.parent` calls silently pointed `src_dir` at the repo's
    parent directory, so `not src_dir.exists()` was always true and
    `test_no_live_trading_wording` never scanned a single file.
    """
    hit = tmp_path / "bad.py"
    hit.write_text("# This module used to support live trading.\n")
    miss = tmp_path / "good.py"
    miss.write_text("# Just an olive branch here.\n")

    assert _python_files_containing(tmp_path, "live trading") == [hit]
    assert _python_files_containing(tmp_path / "does-not-exist", "live trading") == []


def test_no_live_trading_wording() -> None:
    """Test that 'live trading' does not appear in source code."""
    src_dir = _repo_root() / "src"
    assert src_dir.exists(), "src/ must exist by WP1; if this fails, the path above is wrong"

    matches = _python_files_containing(src_dir, "live trading")
    assert not matches, f"'live trading' found in {matches}"


def _cli_help_text() -> str:
    """Return the `pg --help` output."""
    result = CliRunner().invoke(app, ["--help"])
    return result.stdout


def _literal_values(annotation: object) -> list[str]:
    """Return the allowed values of a `Literal[...]` field annotation, else an empty list."""
    if get_origin(annotation) is Literal:
        return [str(value) for value in get_args(annotation)]
    return []


def test_no_bare_live_word_in_cli_help() -> None:
    """Test that the bare word 'live' does not appear in CLI help output.

    Spec section 7 keeps "live" for a later live-news feature; P0 has no
    such feature, so the word should not appear anywhere in the CLI yet.
    Only "live trading" is checked above; this covers the word on its own.
    """
    help_text = _cli_help_text()
    assert not _contains_phrase(help_text, "live"), f"'live' found in CLI help output: {help_text!r}"


def test_no_bare_live_word_in_settings_model() -> None:
    """Test that 'live' does not appear in the Settings model's field names or allowed values."""
    for field_name, field in Settings.model_fields.items():
        assert not _contains_phrase(field_name, "live"), f"Settings field name '{field_name}' contains 'live'"
        for value in _literal_values(field.annotation):
            assert not _contains_phrase(value, "live"), f"Settings field '{field_name}' allows value '{value}'"


# Broker-scraping and browser-automation packages P0 must never depend on or import (design
# review condition 9: read-only scope, no broker login, no pytr or scraping, no order path).
_BANNED_DEPENDENCIES = frozenset({"pytr", "requests_html", "selenium"})
# The same packages, plus playwright: fine as a dev/test dependency (pytest-playwright drives
# the web end-to-end test), but section 2.2 bans importing it from src/ specifically.
_BANNED_IN_SRC_IMPORTS = _BANNED_DEPENDENCIES | {"playwright"}


def _declared_dependency_names(pyproject_path: Path) -> set[str]:
    """Return the normalized names of every dependency pyproject.toml declares.

    Covers [project.dependencies], every [project.optional-dependencies] extra
    and every [dependency-groups] group (PEP 735). A `{include-group = ...}`
    entry names another group, not a package, and is skipped.
    """
    data = tomllib.loads(pyproject_path.read_text())
    specs: list[str] = list(data.get("project", {}).get("dependencies", []))
    for extra_deps in data.get("project", {}).get("optional-dependencies", {}).values():
        specs.extend(extra_deps)
    for group_deps in data.get("dependency-groups", {}).values():
        specs.extend(dep for dep in group_deps if isinstance(dep, str))

    names = set()
    for spec in specs:
        name = re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0]
        names.add(name.strip().lower().replace("-", "_"))
    return names


def test_declared_dependency_names_covers_groups_and_extras(tmp_path: Path) -> None:
    """Unit test for the pyproject dependency scanner, independent of the real file."""
    sample = tmp_path / "pyproject.toml"
    sample.write_text(
        '[project]\ndependencies = ["Requests>=2.0"]\n'
        '\n[project.optional-dependencies]\nextra = ["some-package"]\n'
        '\n[dependency-groups]\ndev = ["Selenium>=4.0", {include-group = "extra"}]\n'
    )

    assert _declared_dependency_names(sample) == {"requests", "some_package", "selenium"}


def test_pyproject_excludes_scraping_dependencies() -> None:
    """Test that pyproject.toml has no dependency on a broker-scraping or automation package."""
    names = _declared_dependency_names(_repo_root() / "pyproject.toml")
    overlap = names & _BANNED_DEPENDENCIES
    assert not overlap, f"pyproject.toml must not depend on: {sorted(overlap)}"


def _imported_top_level_modules(py_file: Path) -> set[str]:
    """Return the top-level module names a Python file imports."""
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_imported_top_level_modules_detects_every_import_form(tmp_path: Path) -> None:
    """Unit test for the import scanner, independent of real repo content."""
    module = tmp_path / "sample.py"
    module.write_text(
        "import os\nimport selenium.webdriver as webdriver\nfrom playwright.sync_api import Page\n"
        "from . import sibling\n"
    )

    assert _imported_top_level_modules(module) == {"os", "selenium", "playwright"}


def test_src_does_not_import_scraping_or_automation_packages() -> None:
    """Test that no module in src/ imports a broker-scraping or browser-automation package."""
    src_dir = _repo_root() / "src"
    assert src_dir.exists(), "src/ must exist by WP1; if this fails, the path above is wrong"

    for py_file in sorted(src_dir.rglob("*.py")):
        found = _imported_top_level_modules(py_file) & _BANNED_IN_SRC_IMPORTS
        assert not found, f"{py_file} imports banned package(s): {sorted(found)}"


# core/clock.py is the one file allowed to read the real system clock (plan 3.3, 7.1): every
# other module must ask an injected Clock instead, so PG_TODAY can fix "today" everywhere.
# The lookbehind excludes a call on some other object whose name happens to end in "date" or
# "datetime" (e.g. "mydate.today(" or "some_datetime.now("), while still matching a dotted
# access like "datetime.datetime.now(" (the "." right before "datetime" is not a word character).
_CLOCK_READING_PATTERNS = {
    "datetime.now(": re.compile(r"(?<!\w)datetime\.now\s*\("),
    "date.today(": re.compile(r"(?<!\w)date\.today\s*\("),
}


def test_clock_reading_patterns_detect_both_forms_and_no_others(tmp_path: Path) -> None:
    """Unit test for the regexes above, independent of real repo content."""
    hit_now = tmp_path / "a.py"
    hit_now.write_text("ts = datetime.now(UTC)\n")
    hit_today = tmp_path / "b.py"
    hit_today.write_text("d = date.today()\n")
    miss = tmp_path / "c.py"
    miss.write_text("mydate.today()\nsome_datetime.now()\n")

    assert _CLOCK_READING_PATTERNS["datetime.now("].search(hit_now.read_text())
    assert _CLOCK_READING_PATTERNS["date.today("].search(hit_today.read_text())
    assert not _CLOCK_READING_PATTERNS["datetime.now("].search(miss.read_text())
    assert not _CLOCK_READING_PATTERNS["date.today("].search(miss.read_text())


def test_system_clock_is_read_only_from_core_clock() -> None:
    """Test that `datetime.now()` and `date.today()` appear only in core/clock.py (3.3, 7.1).

    Everything else that needs the current time or date must take an
    injected `Clock` instead, so tests, the e2e scenario and the
    Playwright server can fix "today" with PG_TODAY and get reproducible
    timestamps and a reproducible 30-day import reminder.
    """
    src_dir = _repo_root() / "src"
    assert src_dir.exists(), "src/ must exist by WP1; if this fails, the path above is wrong"
    allowed_file = src_dir / "playground" / "core" / "clock.py"

    offenders = []
    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file == allowed_file:
            continue
        text = py_file.read_text()
        for label, pattern in _CLOCK_READING_PATTERNS.items():
            if pattern.search(text):
                offenders.append(f"{py_file}: {label}")
    assert not offenders, f"System clock read outside core/clock.py: {offenders}"


# --- Fixtures: manifest and dummy IBANs (plan 6.2, 7.5) --------------------------------------

_FIXTURES_PREFIX = "tests/fixtures/"
# Committed binary and data inputs that must sit under tests/fixtures/ and be listed in the manifest.
_MANIFEST_SUFFIXES = (".pdf", ".csv", ".zip")
# The PDF text fixtures are the source of truth for the PDF fixtures, so they are listed too.
_TEXT_FIXTURES_PREFIX = "tests/fixtures/tr/text/"
# Text files under tests/fixtures/ scanned for IBANs.
_TEXT_SUFFIXES = (".txt", ".csv", ".json", ".yaml", ".yml", ".md")
# An IBAN: two letters, two check digits, then 11 to 30 letters or digits, written either in one
# piece or in groups of four separated by single spaces. An ISIN (12 characters) is too short.
_IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}(?:[A-Z0-9]{11,30}|(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?)\b")
# The documented dummy: DE00 followed by zeros only, written with or without spaces.
_DUMMY_IBAN = "DE" + "0" * 20


def _tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=_repo_root(), capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def _files_the_manifest_must_list(tracked: list[str]) -> set[str]:
    """Tracked PDF, CSV and zip files, plus the PDF text fixtures, relative to tests/fixtures/."""
    required = set()
    for path in tracked:
        if not path.startswith(_FIXTURES_PREFIX):
            continue
        if path.endswith(_MANIFEST_SUFFIXES) or (path.startswith(_TEXT_FIXTURES_PREFIX) and path.endswith(".txt")):
            required.add(path.removeprefix(_FIXTURES_PREFIX))
    return required


def _foreign_ibans(text: str) -> list[str]:
    """Every IBAN in `text` that is not the documented dummy."""
    return [match for match in _IBAN_PATTERN.findall(text) if match.replace(" ", "") != _DUMMY_IBAN]


def test_files_the_manifest_must_list_selects_the_right_files() -> None:
    tracked = [
        "README.md",
        "tests/fixtures/MANIFEST.yaml",
        "tests/fixtures/tr/text/tr.sparplan.de/a.txt",
        "tests/fixtures/tr/text/tr.sparplan.de/a.expected.json",
        "tests/fixtures/tr/pdf/tr.sparplan.de/a.pdf",
        "tests/fixtures/tr/csv/x.csv",
        "tests/fixtures/http/ecb/rates.zip",
        "tests/fixtures/http/stooq/no_data.txt",
    ]
    assert _files_the_manifest_must_list(tracked) == {
        "tr/text/tr.sparplan.de/a.txt",
        "tr/pdf/tr.sparplan.de/a.pdf",
        "tr/csv/x.csv",
        "http/ecb/rates.zip",
    }


def test_foreign_ibans_allows_only_the_dummy() -> None:
    assert _foreign_ibans("DE00000000000000000000 17.01.2024 -1.401,00 EUR") == []
    assert _foreign_ibans("DE00 0000 0000 0000 0000 00 2024-03-07 536,00 EUR") == []
    assert _foreign_ibans("Konto DE12500105170648489890 ist echt") == ["DE12500105170648489890"]
    assert _foreign_ibans("DE89 3704 0044 0532 0130 00") == ["DE89 3704 0044 0532 0130 00"]
    # ISINs, dates and order numbers are not IBANs.
    assert _foreign_ibans("ISIN: DE0007164600 IE00B4L5Y983 US67066G1040 2024-01-15 5d0a-93f1") == []


def test_pdf_csv_and_zip_files_are_committed_only_under_tests_fixtures() -> None:
    offenders = [
        path for path in _tracked_files() if path.endswith(_MANIFEST_SUFFIXES) and not path.startswith(_FIXTURES_PREFIX)
    ]
    assert not offenders, f"data files outside tests/fixtures/: {offenders}"


def test_manifest_lists_every_fixture_input_as_synthetic_with_the_layout_it_imitates() -> None:
    manifest_path = _repo_root() / "tests" / "fixtures" / "MANIFEST.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["files"] or []
    paths = [entry["path"] for entry in entries]
    assert len(paths) == len(set(paths)), "a path is listed twice in MANIFEST.yaml"
    for entry in entries:
        assert entry.get("synthetic") is True, f"{entry['path']}: synthetic must be true"
        assert isinstance(entry.get("imitates"), str), f"{entry['path']}: says nothing about what it imitates"
        assert entry["imitates"].strip(), f"{entry['path']}: says nothing about what it imitates"
        assert (manifest_path.parent / entry["path"]).is_file(), f"{entry['path']}: listed but missing"

    missing = _files_the_manifest_must_list(_tracked_files()) - set(paths)
    assert not missing, f"not listed in tests/fixtures/MANIFEST.yaml: {sorted(missing)}"


def test_no_iban_in_any_fixture_other_than_the_dummy() -> None:
    offenders = []
    for path in _tracked_files():
        if path.startswith(_FIXTURES_PREFIX) and path.endswith(_TEXT_SUFFIXES):
            found = _foreign_ibans((_repo_root() / path).read_text(encoding="utf-8", errors="replace"))
            if found:
                offenders.append(f"{path}: {found}")
    assert not offenders, f"IBANs other than the dummy {_DUMMY_IBAN}: {offenders}"
