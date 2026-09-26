#!/bin/bash
# Single check command for linting, type checking, and testing

set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

# Default to checking Python 3.13 only
check_all_pythons=false
if [[ "$1" == "--all-pythons" ]]; then
    check_all_pythons=true
fi

echo "=== Ruff format check ==="
uv run ruff format --check . --exclude docs

echo "=== Ruff lint check ==="
uv run ruff check . --exclude docs

echo "=== MyPy type check ==="
if [[ -z "${PG_SKIP_MYPY}" ]]; then
    uv run mypy src
else
    echo "Skipped (PG_SKIP_MYPY set)"
fi

echo "=== Pytest ==="
uv run pytest -q

if [[ "$check_all_pythons" == "true" ]]; then
    echo "=== Pytest on Python 3.11 ==="
    if ! command -v python3.11 &> /dev/null; then
        echo "Python 3.11 not found, creating venv"
        UV_PROJECT_ENVIRONMENT=.venv-py311 uv venv --python 3.11
    fi
    UV_PROJECT_ENVIRONMENT=.venv-py311 uv run --python 3.11 pytest -q -x
fi

if [[ -d web/node_modules ]]; then
    echo "=== Web typecheck and tests ==="
    (cd web && npm run typecheck && npm test -- --run)
fi

echo "=== All checks passed ==="
