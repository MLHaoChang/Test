"""Tests for repository hygiene and safety."""

import ast
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Literal, get_args, get_origin

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
