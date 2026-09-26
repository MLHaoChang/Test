"""Pytest configuration and fixtures."""

import tempfile
from datetime import date
from pathlib import Path

import pytest

from playground.config import Settings


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add `--update-goldens` (plan 7.1): rewrite golden files instead of comparing with them.

    Any rewrite must be reviewed in the git diff before it is committed.
    """
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Rewrite golden files from the current output instead of comparing with them.",
    )


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    """True when the run was started with `--update-goldens`."""
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture
def temp_data_dir() -> Path:
    """Provide a temporary data directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def settings(temp_data_dir: Path) -> Settings:
    """Provide test settings."""
    return Settings(data_dir=temp_data_dir, today=date(2024, 12, 31))
