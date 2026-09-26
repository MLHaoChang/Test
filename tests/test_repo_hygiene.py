"""Tests for repository hygiene and safety."""

import re
import subprocess
from pathlib import Path


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
