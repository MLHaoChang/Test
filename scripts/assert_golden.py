#!/usr/bin/env python3
"""Compare a JSON output with a golden file, ignoring ids and timestamps."""

import json
import sys
from pathlib import Path
from typing import Any

# Keys to ignore when comparing
IGNORE_KEYS = {
    "id",
    "batch_id",
    "import_id",
    "created_at",
    "parsed_at",
    "accepted_at",
    "fetched_at",
    "stored_path",
}


def normalize_value(value: Any) -> Any:
    """Recursively normalize a value for comparison."""
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items() if k not in IGNORE_KEYS}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, str):
        # Keep decimal strings as-is for exact comparison
        return value
    return value


def compare_values(expected: Any, actual: Any, path: str = "") -> list[str]:
    """Compare two values and return list of differences."""
    differences = []

    # Type mismatch
    if type(expected).__name__ != type(actual).__name__:
        differences.append(f"{path}: type mismatch - expected {type(expected).__name__}, got {type(actual).__name__}")
        return differences

    if isinstance(expected, dict):
        # Check for missing keys in actual
        for key in expected:
            if key not in IGNORE_KEYS:
                if key not in actual:
                    differences.append(f"{path}.{key}: missing in actual")
                else:
                    new_path = f"{path}.{key}" if path else key
                    differences.extend(compare_values(expected[key], actual[key], new_path))

        # Check for extra keys in actual
        for key in actual:
            if key not in IGNORE_KEYS and key not in expected:
                differences.append(f"{path}.{key}: unexpected key in actual")

    elif isinstance(expected, list):
        if len(expected) != len(actual):
            differences.append(f"{path}: list length mismatch - expected {len(expected)}, got {len(actual)}")
        else:
            for i, (exp_item, act_item) in enumerate(zip(expected, actual, strict=False)):
                new_path = f"{path}[{i}]"
                differences.extend(compare_values(exp_item, act_item, new_path))

    elif isinstance(expected, str) and isinstance(actual, str):
        # For decimal strings, check exact match
        if expected != actual:
            differences.append(f"{path}: string mismatch - expected '{expected}', got '{actual}'")

    elif isinstance(expected, (int, float, bool)) or expected is None:
        if expected != actual:
            differences.append(f"{path}: value mismatch - expected {expected}, got {actual}")

    return differences


def main() -> int:
    """Main entry point."""
    if len(sys.argv) != 3:
        print("Usage: assert_golden.py <output.json> <golden.json>")
        return 1

    output_path = Path(sys.argv[1])
    golden_path = Path(sys.argv[2])

    # Load actual output
    if not output_path.exists():
        print(f"ERROR: Output file not found: {output_path}")
        return 1

    try:
        with open(output_path) as f:
            actual = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in output file: {e}")
        return 1

    # Load golden
    if not golden_path.exists():
        print(f"ERROR: Golden file not found: {golden_path}")
        return 1

    try:
        with open(golden_path) as f:
            expected = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in golden file: {e}")
        return 1

    # Normalize both
    actual_normalized = normalize_value(actual)
    expected_normalized = normalize_value(expected)

    # Compare
    differences = compare_values(expected_normalized, actual_normalized)

    if not differences:
        return 0

    # Print differences
    print("DIFFERENCES FOUND:")
    for diff in differences:
        print(f"  {diff}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
