#!/bin/bash
# The end-to-end scenario for QA (plan 7.6): the 32 steps below, run in order, from the repository
# root, on a fresh clone. Every step must exit with status 0; this script stops at the first
# failure (set -e), exactly as QA running them one by one would notice the first one that fails.
#
# Every "pg" command carries PG_TODAY=2024-12-31 (the injectable clock, plan 3.3), except step 31,
# which moves the clock 34 days past the last import to check the 30-day reminder (AC16). This
# script does not accept arguments; it always starts from "rm -rf .e2e" (step 3), so it is safe to
# run more than once and always exercises the same fixed golden portfolio (plan 1.2).
#
# Usage: scripts/e2e.sh

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

GOLDEN=tests/fixtures/golden
INPUTS="$GOLDEN/inputs"
EXPECTED="$GOLDEN/expected"
HTTP_FIXTURES=tests/fixtures/http
TODAY=2024-12-31

step() {
    echo
    echo "=== Step $1: $2 ==="
}

assert_golden() {
    uv run python scripts/assert_golden.py "$1" "$2"
}

step 1 "uv sync --locked"
uv sync --locked

step 2 "./scripts/check.sh"
./scripts/check.sh

step 3 "clean scratch area"
rm -rf .e2e && mkdir -p .e2e

step 4 "pg init"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data init

step 5 "round 1 import"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data import \
    "$INPUTS/tr_transactions_2024.csv" "$INPUTS"/pdf/*.pdf \
    --json >.e2e/import1.json

step 6 "check import1 against its golden (14 new, 5 merged, 0 held back, 2 review items)"
assert_golden .e2e/import1.json "$EXPECTED/import1.json"

step 7 "reconcile against the confirmed holdings"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data reconcile latest \
    --confirmed "$INPUTS/confirmed_holdings_2024-12-31.csv" --as-of "$TODAY" --strict \
    --json >.e2e/reconcile.json

step 8 "check the reconcile diff (5 of 5 match, NVDA at 20 after the split)"
assert_golden .e2e/reconcile.json "$EXPECTED/reconcile.json"

step 9 "accept the staged batch"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data accept latest --json >.e2e/accept.json

step 10 "round 2 import (same files plus the H1 account statement)"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data import \
    "$INPUTS/tr_transactions_2024.csv" "$INPUTS"/pdf/*.pdf "$INPUTS/statement/kontoauszug_2024_h1.pdf" \
    --json >.e2e/import2.json

step 11 "check import2 against its golden (10 duplicate files, 11 already known, 0 new)"
assert_golden .e2e/import2.json "$EXPECTED/import2.json"

step 12 "accepting an empty batch is allowed"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data accept latest

step 13 "the review queue"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data review list --json >.e2e/review.json

step 14 "check the review queue (the unknown layout and the missing cost basis, both open)"
assert_golden .e2e/review.json "$EXPECTED/review.json"

step 15 "enter the transfer's cost basis"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data transfers set-cost \
    --isin DE0008404005 --acquired 2020-03-02 --cost-eur 800.00

step 16 "value before mapping"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data value --from 2024-01-02 --to "$TODAY" \
    --json >.e2e/value_unmapped.json

step 17 "check value_unmapped (every holding flagged unmapped, no failure)"
assert_golden .e2e/value_unmapped.json "$EXPECTED/value_unmapped.json"

step 18 "map the instruments"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data instruments map --file "$INPUTS/instrument_mapping.csv"

step 19 "check the instrument mapping (all confirmed)"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data instruments list --json >.e2e/instruments.json
assert_golden .e2e/instruments.json "$EXPECTED/instruments.json"

step 20 "fetch prices through the Stooq client (replay)"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data --http-replay "$HTTP_FIXTURES" prices fetch \
    --from 2024-01-01 --to "$TODAY"

step 21 "the manual price fallback (Allianz)"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data prices import-file "$INPUTS/manual_prices_allianz.csv"

step 22 "fetch ECB rates through the replay client"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data --http-replay "$HTTP_FIXTURES" fx fetch

step 23 "fetch the benchmark series"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data --http-replay "$HTTP_FIXTURES" benchmarks fetch \
    --from 2024-01-01 --to "$TODAY"

step 24 "holdings, cost and value"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data holdings --as-of "$TODAY" --json >.e2e/holdings.json
assert_golden .e2e/holdings.json "$EXPECTED/holdings_2024-12-31.json"

step 25 "FIFO lots and disposals"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data lots --json >.e2e/lots.json
assert_golden .e2e/lots.json "$EXPECTED/lots.json"

step 26 "the daily value series (5,916.67 and 6,146.85, stale flags)"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data value --from 2024-01-02 --to "$TODAY" \
    --csv .e2e/value.csv --json >.e2e/value.json
assert_golden .e2e/value.json "$EXPECTED/value.json"

step 27 "the S&P 500 benchmark in EUR"
PG_TODAY="$TODAY" uv run pg --data-dir .e2e/data benchmarks series sp500 --currency EUR \
    --from 2024-01-02 --to "$TODAY" --json >.e2e/sp500_eur.json
assert_golden .e2e/sp500_eur.json "$EXPECTED/sp500_eur.json"

step 28 "the API returns the same holdings, lots and value (GET only)"
./scripts/e2e_api.sh .e2e/data

step 29 "the import page builds"
(cd web && npm ci && npm run build)

step 30 "the Playwright test (upload, diff, accept, badge, holdings, chart, re-upload)"
(cd web && npx playwright test)

step 31 "the 30-day import reminder, 34 days after the last import (AC16, spec 3.10)"
PG_TODAY=2025-02-03 uv run pg --data-dir .e2e/data status --json >.e2e/status.json
assert_golden .e2e/status.json "$EXPECTED/status_2025-02-03.json"

step 32 "the run left nothing untracked or changed in git"
test -z "$(git status --porcelain)"

echo
echo "=== All 32 end-to-end steps passed ==="
