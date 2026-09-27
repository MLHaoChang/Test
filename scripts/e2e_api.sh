#!/bin/bash
# End-to-end scenario step 28 (plan 7.6): start the API on an already-imported data directory,
# check a handful of read endpoints against the same golden files the CLI is checked against, then
# stop it. Sends GET requests only, so it leaves the data directory unchanged.
#
# Usage: scripts/e2e_api.sh DATA_DIR

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

DATA_DIR="${1:-}"
if [[ -z "$DATA_DIR" ]]; then
    echo "Usage: $0 DATA_DIR" >&2
    exit 1
fi

PORT="${PG_E2E_API_PORT:-8765}"
BASE_URL="http://127.0.0.1:${PORT}"
WORK_DIR=$(mktemp -d)
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "=== Starting pg serve on $DATA_DIR (port $PORT) ==="
PG_TODAY=2024-12-31 uv run pg --data-dir "$DATA_DIR" serve --port "$PORT" >"$WORK_DIR/serve.log" 2>&1 &
SERVER_PID=$!

echo "=== Waiting for the API to answer /health ==="
ready=false
for _ in $(seq 1 50); do
    if curl -sf "$BASE_URL/health" >"$WORK_DIR/health.json" 2>/dev/null; then
        ready=true
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "pg serve exited before it answered /health. Its log:" >&2
        cat "$WORK_DIR/serve.log" >&2
        exit 1
    fi
    sleep 0.2
done
if [[ "$ready" != "true" ]]; then
    echo "The API never answered /health within 10 seconds. Its log:" >&2
    cat "$WORK_DIR/serve.log" >&2
    exit 1
fi

echo "=== Checking GET endpoints (GET only: nothing here changes $DATA_DIR) ==="

# GET_endpoint_path golden_file: fetches the endpoint and compares it with the golden file the way
# the CLI's own JSON output is compared (scripts/assert_golden.py, written in WP1: ignores ids and
# timestamps).
check() {
    local path="$1" golden="$2"
    local out="$WORK_DIR/$(basename "$golden")"
    if ! curl -sf "$BASE_URL$path" >"$out"; then
        echo "GET $path failed." >&2
        cat "$WORK_DIR/serve.log" >&2
        exit 1
    fi
    if ! uv run python scripts/assert_golden.py "$out" "$golden"; then
        echo "GET $path did not match $golden." >&2
        exit 1
    fi
    echo "  GET $path matches $golden"
}

if ! grep -q '"status": *"ok"' "$WORK_DIR/health.json" || ! grep -q '"real_orders": *false' "$WORK_DIR/health.json"; then
    echo "GET /health did not say status ok and real_orders false:" >&2
    cat "$WORK_DIR/health.json" >&2
    exit 1
fi
echo "  GET /health says real_orders: false"

check "/portfolio/holdings?as_of=2024-12-31" tests/fixtures/golden/expected/holdings_2024-12-31.json
check "/portfolio/lots" tests/fixtures/golden/expected/lots.json
check "/portfolio/value?from=2024-01-02&to=2024-12-31" tests/fixtures/golden/expected/value.json
check "/instruments" tests/fixtures/golden/expected/instruments.json
check "/benchmarks/sp500/series?from=2024-01-02&to=2024-12-31&currency=EUR" tests/fixtures/golden/expected/sp500_eur.json

echo "=== All API checks passed ==="
