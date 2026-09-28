#!/bin/bash
# Playwright's web server (plan 7.6 step 30, WP12): seeds a fresh data directory with the mapping
# and the replayed prices and rates (but does not import or accept any transaction), then runs
# "pg serve" in the foreground. This way the Playwright test itself does the import, the diff, the
# accept and the confirmed-holdings check through the page, and step 30 does not depend on the CLI
# steps (7.6 steps 4-28) having run first.
#
# Usage: scripts/e2e_web_server.sh [DATA_DIR] [PORT]

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

DATA_DIR="${1:-.e2e/pw-data}"
PORT="${2:-8766}"
TODAY="2024-12-31"
GOLDEN=tests/fixtures/golden/inputs
HTTP_FIXTURES=tests/fixtures/http

if [[ ! -f web/dist/index.html ]]; then
    echo "web/dist/index.html is missing. Build the page first: (cd web && npm ci && npm run build)" >&2
    exit 1
fi

echo "=== Seeding a fresh data directory at $DATA_DIR ==="
rm -rf "$DATA_DIR"
mkdir -p "$(dirname "$DATA_DIR")"

run_pg() {
    PG_TODAY="$TODAY" uv run pg --data-dir "$DATA_DIR" "$@"
}

run_pg init
run_pg instruments map --file "$GOLDEN/instrument_mapping.csv"
run_pg --http-replay "$HTTP_FIXTURES" prices fetch --from 2024-01-01 --to 2024-12-31
run_pg prices import-file "$GOLDEN/manual_prices_allianz.csv"
run_pg --http-replay "$HTTP_FIXTURES" fx fetch

echo "=== Starting pg serve on 127.0.0.1:$PORT (no transactions imported yet: the test does that) ==="
# exec, not a plain call: Playwright stops this "web server" by signalling the process it started,
# so pg serve must run as that process itself, not as a child of a shell that might not forward it.
exec env PG_TODAY="$TODAY" uv run pg --data-dir "$DATA_DIR" serve --port "$PORT"
