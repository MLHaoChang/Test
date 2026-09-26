"""Tests for the assert_golden.py script."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def test_assert_golden_identical() -> None:
    """Test that identical files pass."""
    with TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        data = {
            "id": 123,
            "value": "1.10",
            "created_at": "2024-01-01T00:00:00Z",
        }

        output_file = tmpdir_path / "output.json"
        golden_file = tmpdir_path / "golden.json"

        output_file.write_text(json.dumps(data))
        golden_file.write_text(json.dumps(data))

        result = subprocess.run(
            [sys.executable, "scripts/assert_golden.py", str(output_file), str(golden_file)],
            cwd=Path(__file__).parent.parent.parent,
            capture_output=True,
        )

        assert result.returncode == 0, f"Expected success but got: {result.stderr.decode()}"


def test_assert_golden_ignores_ids() -> None:
    """Test that id fields are ignored."""
    with TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        output_data = {"id": 123, "value": "1.10", "batch_id": 456}
        golden_data = {"id": 999, "value": "1.10", "batch_id": 789}

        output_file = tmpdir_path / "output.json"
        golden_file = tmpdir_path / "golden.json"

        output_file.write_text(json.dumps(output_data))
        golden_file.write_text(json.dumps(golden_data))

        result = subprocess.run(
            [sys.executable, "scripts/assert_golden.py", str(output_file), str(golden_file)],
            cwd=Path(__file__).parent.parent.parent,
            capture_output=True,
        )

        assert result.returncode == 0, f"Expected success but got: {result.stderr.decode()}"


def test_assert_golden_detects_differences() -> None:
    """Test that differences are detected."""
    with TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        output_data = {"value": "1.10"}
        golden_data = {"value": "1.11"}

        output_file = tmpdir_path / "output.json"
        golden_file = tmpdir_path / "golden.json"

        output_file.write_text(json.dumps(output_data))
        golden_file.write_text(json.dumps(golden_data))

        result = subprocess.run(
            [sys.executable, "scripts/assert_golden.py", str(output_file), str(golden_file)],
            cwd=Path(__file__).parent.parent.parent,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1, "Expected failure"
        assert "mismatch" in result.stdout.lower(), f"Expected mismatch message in: {result.stdout}"


def test_assert_golden_decimal_string_precision() -> None:
    """Test that decimal strings are compared exactly."""
    with TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # "1.10" and "1.1" should NOT match
        output_data = {"value": "1.1"}
        golden_data = {"value": "1.10"}

        output_file = tmpdir_path / "output.json"
        golden_file = tmpdir_path / "golden.json"

        output_file.write_text(json.dumps(output_data))
        golden_file.write_text(json.dumps(golden_data))

        result = subprocess.run(
            [sys.executable, "scripts/assert_golden.py", str(output_file), str(golden_file)],
            cwd=Path(__file__).parent.parent.parent,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1, "Expected failure for different decimal strings"
        assert "mismatch" in result.stdout.lower()
