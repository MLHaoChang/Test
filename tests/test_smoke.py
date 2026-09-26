"""Smoke tests for basic functionality."""

from pathlib import Path
from tempfile import TemporaryDirectory

import playground
from playground.config import Settings


def test_version_available() -> None:
    """Test that version is available."""
    assert playground.__version__ == "0.1.0"


def test_settings_accepts_valid_keys() -> None:
    """Test that Settings accepts valid keys."""
    with TemporaryDirectory() as tmpdir:
        settings = Settings(data_dir=Path(tmpdir), http_mode="replay")
        assert settings.http_mode == "replay"


def test_settings_rejects_unknown_keys() -> None:
    """Test that Settings rejects unknown keys."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        Settings(unknown_key="value")  # type: ignore
    assert "unknown_key" in str(exc_info.value)
