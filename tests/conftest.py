"""Pytest configuration and fixtures."""

import tempfile
from datetime import date
from pathlib import Path

import pytest

from playground.config import Settings


@pytest.fixture
def temp_data_dir() -> Path:
    """Provide a temporary data directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def settings(temp_data_dir: Path) -> Settings:
    """Provide test settings."""
    return Settings(data_dir=temp_data_dir, today=date(2024, 12, 31))
