"""Smoke tests for basic functionality."""

from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

import playground
from playground.cli.main import app
from playground.config import Settings


def test_version_available() -> None:
    """Test that version is available."""
    assert playground.__version__ == "0.1.0"


def test_cli_version_prints_version_and_no_real_orders_line() -> None:
    """Test that `pg --version` prints the version and the no-real-orders line."""
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert f"pg {playground.__version__}" in result.stdout
    assert "No real orders. Your portfolio is read-only." in result.stdout


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
