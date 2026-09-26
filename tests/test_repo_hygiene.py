"""Tests for repository hygiene and safety."""

import subprocess
from pathlib import Path


def test_no_sensitive_files_committed() -> None:
    """Test that no sensitive files are committed."""
    repo_root = Path(__file__).parent.parent
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
    repo_root = Path(__file__).parent.parent
    gitignore = repo_root / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist"

    content = gitignore.read_text()
    required_entries = ["data/", "uploads/", "registry/", "lake/", ".env", ".venv"]
    for entry in required_entries:
        assert entry in content, f".gitignore must contain {entry}"


def test_no_live_trading_wording() -> None:
    """Test that 'live trading' does not appear in source code."""
    repo_root = Path(__file__).parent.parent.parent
    src_dir = repo_root / "src"

    if not src_dir.exists():
        return  # Skip if src doesn't exist yet

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text().lower()
        assert "live trading" not in content, f"'live trading' found in {py_file}"
