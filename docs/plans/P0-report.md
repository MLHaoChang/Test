# Phase P0 report: import and value

Version 1.0, 28 September 2026. Covers the code on this branch up to commit `f03d9ed` plus the
UAT script and the guide tests added with this report. Build contract:
[P0-implementation-plan.md](P0-implementation-plan.md) (version 1.1). Spec:
[../playground-spec.md](../playground-spec.md), section 9. Your test script:
[../uat/P0-uat.md](../uat/P0-uat.md).

## Summary

- **Built.** All twelve work packages are approved. The full check passes on Python 3.13 and on
  Python 3.11, and all 32 steps of the end-to-end scenario pass.
- **QA.** Three rounds. Rounds 1 and 2 found defects, and every one was fixed. Round 3 passed.
- **Accepted?** Not yet. Fourteen of the sixteen acceptance criteria are met and checked here.
  The other two (AC1 and AC15) each have a part that only your Mac can settle: the self-check on
  macOS, and the guide's steps that use the real internet. The guide exists, and its commands ran
  here on recorded data. Running them on your Mac is the UAT.
- **Open defects.** Four, all minor. None blocks the phase. They are listed with a suggested fix.
- **Biggest risk.** We have never seen a real Trade Republic export. The CSV layout and the PDF
  readers come from made-up files and public samples. Expect your first real CSV to be refused as
  an unknown layout. The app is built for that: it puts the file in the review queue (a waiting
  list for anything it is unsure about) and imports nothing from it.
- **Recommendation.** Run the UAT. Then do a short fix round for what it finds. Then start P1, and
  split it in two (see the last sections).
- **What you do next.** Follow the [UAT script](../uat/P0-uat.md). It takes two to three hours. Send
  us what you find, the way its "Report a problem" section says. Then answer the seven decisions at
  the end of this report.

## Terms

- **Work package (WP)**: one slice of the build with its own tests and review. The plan has twelve.
- **Review round**: one pass of a review of a work package. A package is approved when a round
  finds nothing left to fix.
- **QA**: testing the finished phase from the outside, as a user would, against the plan.
- **Golden portfolio**: an invented portfolio whose expected results were worked out by hand. The
  tests and the end-to-end scenario check the app against it.
- **Fixture**: a committed input file for a test. Every fixture here is made up.
- **Property test**: a test that checks a rule on many generated inputs, not on one example.
- **End-to-end scenario (e2e)**: the 32 numbered steps of plan section 7.6, run in order through
  the command line, the API and the page.
- **AC**: acceptance criterion. AC1 to AC16 are in plan section 1.3.
- **Reader (parser)**: the piece of code that reads one layout of document or file.
- **Review queue**: a list of documents, rows and conflicts the app could not handle with
  certainty. Nothing in it is guessed or dropped.
- **Held back**: a transaction that is stored but kept out of holdings and value until the review
  item about it is settled.
- **ISIN**: the 12-character code that identifies a security. It is the instrument key everywhere.
- **Lot** and **FIFO**: a lot is one purchase, with its own quantity, date and cost. FIFO (first in,
  first out) is the German tax rule that a sale uses up the oldest lot first.
- **pytest, Vitest, Playwright**: the test tools. pytest tests the Python code, Vitest tests the
  page's components, and Playwright drives the page in a real browser.
- **ruff and mypy**: tools that check code style and types.
- **Hygiene test**: a test that scans the repository for what must never be there, such as private
  files or banned wording.

## What was built

| Area | What it does | Where |
|---|---|---|
| Core | Exact decimal numbers, dates with Berlin time, ISIN check, FX conversion, an injectable clock | `core/` |
| Registry | SQLite database with 15 tables and a version table | `storage/` |
| PDF path | Text extraction, a classifier, ten layouts (trade confirmations, savings plans, dividends, tax notes, interest, splits, two account statements), and a recogniser for corporate actions | `importer/tr/` |
| CSV path | One Trade Republic CSV layout, the manual transaction format, the confirmed holdings format | `importer/tr/csv_*`, `importer/manual_csv.py`, `importer/confirmed_csv.py` |
| Import pipeline | Staging, matching of the same transaction across files, field-by-field precedence, the review queue, reconciliation against your holdings, accept | `importer/` |
| Ledger | FIFO lots and disposals, partial sales, splits, transfers in and out, holdings on any day | `ledger/` |
| Market data | An injectable HTTP layer with replay and record, clients for Stooq, the ECB and OpenFIGI, a manual price file, a Parquet price lake, two benchmark series | `http/`, `marketdata/` |
| Valuation | A daily value in EUR with the price, the rate and the flags behind each number | `valuation/` |
| API | 17 paths on `127.0.0.1` only, with no path that could place an order | `api/` |
| CLI | The `pg` command with 32 commands | `cli/` |
| Page | A small Vite and React import page: badge, upload, diff, accept, holdings, value chart | `web/` |
| Tooling | One check script, a golden-file comparer, the e2e scripts | `scripts/` |

Size: about 15,250 lines of Python in 79 files under `src/`, 17,600 lines of Python tests in 70
files, 1,260 lines of page code and 1,160 lines of page tests, and 168 fixture files (34 PDFs, 33
text fixtures, 26 CSV files). At commit `f03d9ed` the branch had 88 commits, 78 of them since the
plan. Work started on 26 September and the last QA fix landed on 28 September, about two and a
half days of elapsed time. The plan estimated about 35 hours of focused work. Effort hours were
not tracked.

## Reviews and work packages

### Design review and plan review

The design review gave the verdict **approve with conditions**: ten conditions, listed in plan
section 1.4. The plan was then revised and **approved after two review rounds** (version 1.1). All
ten conditions are met:

| Condition | Status |
|---|---|
| 1. Injectable HTTP layer, fixtures only, network off in tests | Met. `HttpClient` protocol with replay and record, sockets disabled for every test |
| 2. Synthetic German fixtures, separate PDF and text layers, unknown goes to review | Met. Two test layers, 33 text fixtures, 34 PDFs, review items for unknown layouts |
| 3. Nothing private committed, data directories ignored | Met. Hygiene tests, fixture manifest, `.gitignore`, e2e step 32 |
| 4. Decimal end to end, UTC plus Berlin time, one FX convention | Met. `DecimalText` refuses floats, both times stored, one `to_eur` |
| 5. Idempotent re-import with a semantic key and precedence | Met. Property test with 200 examples, same-day savings plan test |
| 6. FIFO in EUR with fees, disposals, splits, transfers in, property tests | Met. Eight ledger properties on 500 examples each |
| 7. Documented price basis and as-of rule, flags not failures, manual price file | Met. Nine flag kinds, manual file, nothing raises on missing data |
| 8. Mapping stored, shown, editable, never guessed | Met. Logged mapping, `suggest` prints only |
| 9. Read-only scope, no broker login, no scraping, no order path, no "live trading" wording | Met. Banned imports, path-name test, wording tests, no credential in settings |
| 10. Written acceptance criteria, golden portfolio, Playwright test, Python 3.13 via uv, macOS parity, a UAT guide | Met on Linux. macOS parity is proven only at your UAT |

### Work packages

Every package ended approved with the test suite green. In all, the twelve packages took 16 review
rounds.

| WP | Package | Review rounds |
|---|---|---|
| 1 | Project skeleton, tooling and golden comparer | 2 |
| 2 | Core types, conventions, clock, parse model and registry | 1 |
| 3 | PDF text layer, classifier and trade layouts | 1 |
| 4 | Dividend, tax, interest, split and statement layouts | 1 |
| 5 | CSV family: Trade Republic CSV, manual CSV and confirmed holdings | 1 |
| 6 | FIFO ledger core | 1 |
| 7 | Import pipeline, matching, review queue and reconciliation | 3 |
| 8 | Ledger views, transfers and anonymised export | 1 |
| 9 | HTTP layer, market data clients and instrument master | 1 |
| 10 | Valuation series | 1 |
| 11 | FastAPI backend | 1 |
| 12 | Import page, end-to-end integration and UAT guide | 2 |

WP7 needed the most rounds (3). It holds the hardest logic: matching the same transaction across a
PDF, a CSV row and a statement line, and keeping the review queue right when files arrive in any
order. WP1 and WP12 needed a second round for tooling and page fixes.

## Acceptance criteria

The sixteen criteria are in plan section 1.3. "e2e" is a step of the end-to-end scenario.

| Id | Criterion in short | Status | Evidence |
|---|---|---|---|
| AC1 | `uv sync --locked` and `./scripts/check.sh` pass on Python 3.13 and 3.11 | Met on Linux. macOS at UAT | `check.sh --all-pythons`, QA round 3 on a fresh clone, my run; UAT step 2 |
| AC2 | Round 1 import gives the counts and 14 transactions of the golden portfolio | Met | `tests/golden/test_golden_portfolio.py`; e2e 5 and 6: 19 read, 14 new, 5 merged, 0 held back, 2 review items |
| AC3 | Every layout parses to its golden; PDF and text layers tested apart | Met | `tests/importer/tr/test_layout_goldens.py`, `test_pdf_text.py` |
| AC4 | Unknown layout, header or row type, a missing field, amounts that do not add up each raise a review item; nothing guessed or dropped | Met | `test_review_queue.py`, `test_reevaluation.py`; QA probes with malformed files |
| AC5 | Any sequence of imports gives the same ledger; same-day savings plans stay two | Met | `test_idempotency.py` (200 examples), `test_same_day_savings_plans.py`; e2e 10 and 11 |
| AC6 | The diff lists new, merged and known transactions and compares with confirmed holdings; nothing changes before accept | Met | `test_reconcile.py`; e2e 7 to 9: 5 of 5 match |
| AC7 | FIFO lots match the hand computation; invariants hold for random sequences | Met | `test_fifo.py`, `test_fifo_properties.py`; e2e 25: SAP gain 377.60, open cost 4,192.60 |
| AC8 | Value series matches the hand computation to the cent; the flags behave as described | Met | `tests/valuation/test_series.py`; e2e 16, 17, 26: 5,916.67 and 6,146.85; the stale flag for Allianz starts on 26 December |
| AC9 | Prices, rates and suggestions use the injectable HTTP layer; tests run with sockets off; the CLI replays recordings | Met | `tests/http/`; e2e 20 to 23 |
| AC10 | The mapping is stored, listed, editable, logged and never guessed | Met | `test_instrument_master.py`; e2e 18 and 19 |
| AC11 | MSCI World (EUR) and S&P 500 (USD and EUR) series are stored and returned | Met | `test_benchmarks.py`; e2e 27 |
| AC12 | The API returns what the CLI returns; money is a string; no order path | Met | `tests/api/test_contract.py`; e2e 28 |
| AC13 | The page shows the badge, uploads, shows the diff, accepts, shows holdings and a chart | Met | Playwright, 3 tests; e2e 30 |
| AC14 | Nothing private is committed; no "live trading" wording; no bare word "live" in user-facing text | Met | `tests/test_repo_hygiene.py`; e2e 32 |
| AC15 | The UAT guide exists and its commands run as written on your Mac; the value shows within one minute | Partly | The guide is [../uat/P0-uat.md](../uat/P0-uat.md). `tests/cli/test_uat_guide.py` checks every command and example in it. QA ran its commands with replayed data. On the sample portfolio here, one document takes about 0.6 seconds from upload through accept to a fresh value series. Real internet, your Mac and your own timing are for you |
| AC16 | A reminder appears after 30 days and not before | Met | `test_reminder.py`, the API contract test; e2e 31; shown at 31 days and not at 30 |

## Test results

I ran every check at commit `f03d9ed` before I changed anything. I ran pytest again after adding
this report's changes, and the end-to-end scenario again at the commit that adds this report. The
counts below are for the final run.

| Check | Count | Result |
|---|---|---|
| pytest on Python 3.13 | 1,606 tests | All passed, none skipped or failed, 98 seconds |
| pytest on Python 3.11 | 1,606 tests | All passed, none skipped or failed, 103 seconds |
| Vitest (page components) | 63 tests in 9 files | All passed |
| Playwright (Chromium) | 3 tests | All passed, 8.6 seconds |
| End-to-end scenario | 32 steps | Every step exited 0, 2 minutes 23 seconds, tree clean at the end |
| ruff format and ruff check | 158 files | Clean |
| mypy | 79 source files | Clean |

The 1,606 pytest tests by area:

| Area | Tests |
|---|---|
| `importer/tr` (PDF text, classifier, layouts, CSV layout) | 500 |
| `importer` (pipeline, matching, keys, review queue, CSV formats, anonymise) | 365 |
| `cli` | 149 |
| `marketdata` | 119 |
| `ledger` | 114 |
| `core` | 106 |
| `api` | 73 |
| `valuation` | 49 |
| `http` | 38 |
| `golden` (the whole golden portfolio through the CLI) | 35 |
| `storage` | 27 |
| Repository hygiene, smoke and script tests | 31 |

Eight of the 1,606 are new with this report. They check the UAT script: every `pg` command and
option in it exists, and every example file in it is read by the app. Before them there were
1,598, the number QA counted.

Property tests: the import order test runs 200 generated sequences. The eight ledger property
tests run 500 examples each. Two warnings appear in the run. One is a Starlette notice about the
test client. The other is raised on purpose by the test that proves sockets are blocked.

## QA

QA tested the finished phase from the outside, in three rounds.

| Round | Found | Result |
|---|---|---|
| 1 | Three defects: a CSV trade whose amounts do not add up was imported without a warning (D1), a transaction with no booking amount raised nothing (D2), and a lost connection ended in a Python traceback (D3). Seven smaller findings (M1 to M7) about the page, the wording, the currency check and the API's error text | All fixed |
| 2 | Three defects: the confirmed holdings file in the guide was refused as written (R2-D1, major), an internal function name in a benchmark message (R2-D2), and a review item that kept the whole CSV export as its text (R2-D3, a privacy risk). Smaller findings, numbers 4 to 11: a silent skip in `prices fetch`, internal codes in `pg transactions`, a UTC time in `pg status`, an ISIN shown twice, missing names in two messages, a shell pattern that zsh refuses, a missing explanation of "held back", and a chart without axis values | All fixed |
| 3 | No blocking defect. Four minor defects left open (below) | **Passed** |

Round 3 in detail, on a fresh clone at commit `33ddc4d`, with a new uv environment on Python 3.13:

- `./scripts/check.sh --all-pythons` passed on 3.13 and on 3.11: ruff, mypy and 1,598 pytest tests,
  none skipped or failed. On 3.13 the web type check passed and Vitest gave 63 of 63.
- All 32 steps of the end-to-end scenario ran as written and every one exited 0.
- QA checked the figures against the hand computation itself, not only through the golden files:
  import round 1 gave 19 candidates, 14 new, 5 merged, 0 held back and 2 review items. Round 2 gave
  10 duplicate files, 11 already known and 0 new. Reconciliation said "5 of 5 match" with NVIDIA at
  20. FIFO gave SAP 3 shares at cost 480.60, a realised gain of 377.60 and a total open cost of
  4,192.60. The value was 5,916.67 on 31 May 2024 and 6,146.85 on 31 December 2024. The stale
  flag for Allianz started on 26 December, not on 25 December. The reminder showed at 31 and 34
  days and not at 30.
- Malformed files were handled: a text file named `.pdf` and an empty CSV were refused with a plain
  message. A truncated PDF, a password-protected PDF, a binary CSV and an unknown CSV header each
  became a review item. A CSV with a bad number, an unknown row type, amounts that do not add up,
  an invalid ISIN, a bad date and a short row gave one item per row, and the row whose amounts do
  not add up was held back.
- Re-imports were safe: the same file twice, the same file after accept, the same bytes under
  another name, and a copy of the export with Windows line endings all added nothing.
- A missing price mapping gave the `unmapped` flag and an incomplete value, and no crash. An
  instrument mapped but with no price data exited 1 with a plain message.
- Unknown layouts (a random text, a PDF that mixes two layouts, a blank PDF, the cost information
  sheet) all landed in the review queue.
- The UAT guide's commands ran with `--http-replay` in place of the network. `instruments suggest`
  stored nothing. The anonymised export masked the name, address and depot number.
- The import page was driven with headless Chromium: the badge text is exact, the diff matches the
  CLI, a wrong confirmed holdings file shows "2 of 5 match", accept to chart took 0.8 seconds, the
  chart tooltip works, a second upload gives 0 new transactions, the page does not scroll sideways
  at 390 pixels, and the word "live" appears nowhere on it. Eleven screenshots are in
  `docs/uat/screenshots/p0-r3-*.png`.

### Open defects

All four are minor. None blocks the phase. I suggest fixing them in the fix round before P1.

| Id | What | How to see it | Suggested fix |
|---|---|---|---|
| O1 | A PDF with no text layer (a scan, or a blank page) gets the same message as an unknown layout. It shows no text and does not say the PDF has none. You cannot tell what went wrong | Make a blank PDF, import it, run `pg review show 1` | When the extracted text is empty, keep the `unknown_layout` item but say: "This PDF has no readable text (it may be a scan). Nothing was imported from it. Download the document again from Trade Republic, or enter it in the manual CSV format." |
| O2 | On the import page, a held-back transaction also appears in the "New transactions" table with no mark. A reader of that table alone may think it counts toward holdings | Upload a CSV with a purchase whose amounts do not add up. The row shows under "New transactions" and again under "Held back until the review item is settled" | Mark held-back rows in the New transactions table, or list them only under Held back. Keep the counts as they are |
| O3 | Some command output still shows internal codes and jargon: review kinds (`unknown_layout`, `missing_cost_basis`), flags (`dividend_adjusted_prices`) and a number error that says "Not an English-formatted number" | `pg review list`, `pg holdings`, `pg transfers set-cost --cost-eur abc` | Print a plain label beside or instead of each code, as the page does. Say: "--cost-eur must be an amount with a dot for decimals, for example 800.00. Got: 'abc'." |
| O4 | The value chart can be read only with a mouse: it cannot take keyboard focus, so a keyboard or screen reader user cannot read a day's value. The browser also logs a 404 for `/favicon.ico` on every load | Focus the chart and press the left arrow: nothing happens. Open the browser console | Give the chart `tabIndex=0` and an `aria-label`, and let the arrow keys move the highlighted day and update the tooltip. Add a small icon, or `<link rel="icon" href="data:,">`, to `web/index.html` |

The UAT script's "Known limitations" section tells you about O1 to O4, so none of them will
surprise you during the test.

## Deviations from the plan

None of these weakened a requirement. Most add a module, a parameter or an error type that the plan
did not name. A few changed how the app behaves. Those come first.

### Changes in behaviour

- **A merge of accepted data waits while a batch is staged** (WP7). `pg review resolve --merge`, and
  any resolution whose check would merge, is refused until you accept or discard the staged batch.
  Then it goes ahead. Plan 5.3.5 says a resolution "applies at once", and the plan text was not
  changed. The reason: a file of the staged batch may already report one of the two transactions,
  and one transaction holds at most one report of each kind of file.
- **`--merge` needs `--into` when the choice is not clear** (WP7). If a held transaction is near
  more than one candidate, the command is refused. Before, it picked one.
- **A dismissed item keeps its transaction out of your holdings** (WP7). Only `--use-parsed`,
  `--merge`, `--keep-both`, or a later file that removes the problem releases it. An item the app
  closed as superseded opens again if its problem comes back. No command reopens a dismissed item.
- **CSV rows are now checked** (QA round 1, D1). WP5 first left the amounts check out for CSV rows.
  The parser now checks every purchase, sale and dividend row: quantity times price, plus fees and
  taxes, must give the amount, within 0.01 plus the rounding of the printed figures. A row that
  does not add up is read, raises `amounts_do_not_add_up`, and is held back.
- **The decimal line of the confirmed holdings file is optional** (QA round 2, R2-D1). WP5 first
  made `# decimal=,` or `# decimal=.` mandatory. Without it, a quantity is read only when it can
  mean one number (`4,5` and `4.5` yes, `1.234` no).
- **Valuation and views** (WP10). `pg holdings` builds its lots from the stored accepted
  transactions. While a batch is staged, that batch's files do not change accepted transactions in
  that view. `pg value` refuses `--to` after today and `--from` after `--to`, and with an explicit
  range it replaces only the stored days in that range. The latest value in `pg status` is worked
  out when you ask, for the last weekday on or before today, so it follows the clock and newer
  prices.
- **Files that are not PDF or CSV** (WP7) are refused before anything is stored, with exit status 1,
  and a damaged PDF is recorded as failed with an `unknown_layout` item. `pg import` with no files
  exits 1. `pg review list` shows open items unless you ask for another status.
- **`GET /portfolio/value` stores the series it computes** (WP11), as `pg value` does. This keeps
  the API "over the same services as the CLI".
- **The UAT guide is `docs/uat/P0-uat.md`**, not `docs/uat/P0-macos.md` as plan 1.3 (AC15), 2.1, 8
  and WP12 said. Those four references now name the new file, and section 8 says that the finished
  guide has more steps than the outline.
- **The plan was kept in step with QA.** Eight QA commits changed the plan text to match a fix, for
  example the folder form of `pg import` and the optional decimal line.

### By work package

**WP1, skeleton and tooling**

- `check.sh` keeps `--exclude docs` for ruff. It is not redundant: without it ruff reformats the
  code-like blocks in `docs/*.md`.
- Dev tools sit in a PEP 735 `[dependency-groups]` table, which `uv sync` installs by default.
- The ruff `TID` rules were missing from `select`, so `ban-relative-imports` had never been
  enforced. Now they are. The ban on `pytr`, `selenium` and `playwright` applies to `src/` only.
- The only new file beyond the plan's list is `src/playground/cli/__init__.py`.

**WP2, core, clock and registry**

- Extra error types (`DateFormatError`, `NonExistentLocalTimeError`, `InvalidIsinError`,
  `InvalidClockSettingError`) and one extra helper, `format_ts_utc`.
- Every `*_json` column is nullable, even where the plan's prose does not say so. Nothing wrote to
  them yet.
- `open_registry` creates directories, the file and its settings. `ensure_schema` creates the
  tables. This avoids a circular import.
- `PG_TODAY` is read from the environment in the CLI callback, not added as a Typer option. A
  malformed value prints a plain message and exits 1.

**WP3, PDF text layer and trade layouts**

- Layout PDFs are under `tests/fixtures/tr/pdf/<parser id>/<case>.pdf`, because case names repeat
  across layouts. The negative cases sit with the parser that claims them, or under `unclassified/`.
- The amounts check allows for the rounding of the printed quantity and price when it compares
  quantity times price with the printed amount (5.5520 x 27.02 prints as 150.00). The printed cent
  sums still must match within 0.01. A one euro error is still caught.
- `reportlab` moved to the dev group and `PyYAML` was added to it; `uv.lock` changed only there.
- A new module, `importer/serialise.py`, turns the parse model into exact JSON. The text normal form
  ends with one line feed. An unknown line in a settlement block gives an `unparsed_row` item and
  the transaction is still returned. `classify()` takes an optional list of parsers.

**WP4, dividend, tax, interest, split and statement layouts**

- Two blanket WP3 tests were fixed, not weakened: they assumed every transaction has an ISIN, EUR
  and no document FX line. A third was scoped to PDF documents, and a statement line has no
  `source_ref` by design.
- The fixtures invent a `REFERENZ` header field so every single document has a `source_ref`.
- `common.py` was not changed. A few extra fixtures cover edge cases the plan implies.

**WP5, CSV family**

- A new module, `csv_common.py`, holds the encoding sniff and the time parser for all three formats.
- The manual format's `note` column is carried in `ParsedTransaction.name`, because the parse model
  has no free-text field and adding one would change every earlier golden file.
- The per-row rules for the CSV export (what a split or a transfer row means) were designed to
  match the PDF conventions and are covered by a fixture with every row type.

**WP6, FIFO ledger**

- `LedgerTxn` has an extra field, `tax_eur`: proceeds are gross minus fees, which equals the booking
  amount plus the taxes withheld. Without it the golden gain of 377.60 cannot be reached.
- Order at one booking time: splits, then purchases and transfers in, then sales and transfers out,
  then the rest, then transaction id. With this a sale can use a lot booked at the same moment.
- `LotBook` has extra fields (`splits`, `positions`) and a `quantity_timeline()` method.
- A clean split ratio means p:q with both between 1 and 1000, and the holding times p/q matching
  the new quantity within 0.000001. A split scales the initial and the open quantity of every open
  lot. A split that would round a lot to 0 is `split_unclear`.
- A disposal points to its lot by `lot_open_txn_id`. A purchase without an amount opens a lot with
  `cost_missing`. A sale with unknown fees gets fees of 0. Contract violations raise `ValueError`.
- Figures that are exact at the total's decimals are written without padding zeros (320.40, not
  320.40000000). The ledger property tests set `deadline=None`, which raised the pytest run from
  about 5 to about 26 seconds at that point.

**WP7, import pipeline**

- Matching runs on an in-memory copy of the portfolio (`workspace.py`) instead of the per-candidate
  repository lookups the plan lists, so a stage can compute exactly what accept will do. Three
  modules are not in the plan: `merge.py`, `workspace.py` and `persist.py`.
- Signatures differ from 5.3.6: `stage_files` also takes `uploads_dir`, `parsers` and
  `csv_profiles`, `stage_inputs` takes in-memory files, and `build_diff` and `discard_batch` take
  the clock. `refresh_portfolio` is new; `transfers set-cost` calls it.
- Rules the plan left open, decided here: a new occurrence is the next number after the highest in
  use; `stored_path` is relative to the data folder; the diff of an accepted batch is labelled
  "without this batch -> with it, as your portfolio is now" because the registry keeps no history of
  later merges.
- Test corrections, not weakenings: a check now uses `ReviewNeeded` instead of `isinstance` on a
  protocol, and the canonical form ignores the ordinal at the end of a listing row's report key.
- The four golden JSON files were written by the code and reviewed by hand. Commit `78e7810` is
  labelled `test` but also carries small source fixes; it was already pushed, so it stayed.
- The three review rounds added: a dismissed `amounts_do_not_add_up` item is released by a manual
  CSV row; `parse_confirmed_csv` refuses undecodable bytes; occurrences of a staged batch's rows
  are reserved during a resolution, which fixed an `IntegrityError`.

**WP8, ledger views and export**

- A new module, `importer/transfers.py`, holds the `set-cost` logic apart from the Typer wiring.
  `repos.py` gained `list_lots`, `list_disposals` and `insert_cost_basis_input`.
- `anonymise_text` masks exactly what the plan names: the address block, IBANs, depot numbers and
  order numbers. It leaves execution, reference and savings plan numbers alone.
- `pg review export` needs `--out`; `--anonymise` is a flag that defaults to off.
- One WP7 assertion moved from 2 to 1 open review items, as its own comment said it would once
  `set-cost` landed.

**WP9, HTTP layer and market data**

- `StooqClient.daily_bars` takes a `currency`, because a Stooq CSV carries none. The stores take an
  explicit `fetched_at` so only `core/clock.py` reads the clock. `is_stale` is a free function.
- The fixture series use the real 2024 NYSE holiday calendar. The `index.yaml` schema, the secret
  stripping lists and the CLI flag shapes not given by the plan are my own design.

**WP10, valuation**

- `value_series` has no separate timeline parameter, because the lot book carries it, and it takes
  a `trades` parameter for the price basis check. `PriceLookup` and `FxLookup` are protocols.
- A new module, `valuation/portfolio.py`, holds the service that the CLI and the API share.
  `PriceStore.series()` and `FxStore.rates()` were added to `lake.py`.
- `price_basis_mismatch` is flagged on every day the instrument is held. The value step is an
  optional `market` parameter of `accept_batch` and the resolution functions; without it, the step
  is skipped. `pg value --json` lists flags in a short form plus a summary. Deleting an unused
  instrument now also counts holdings snapshots as a reference.

**WP11, API**

- The second test file is `test_api_errors.py`, because `tests/core/test_errors.py` already claims
  that base name and the test tree has no `__init__.py` files.
- The confirmed holdings endpoint takes multipart with a `file` and an optional `as_of`, or JSON
  with `rows`. The lot and disposal record helpers are copied into the API module so WP11 touches
  no file another package owns; golden parity tests keep the two copies equal. Static serving of
  `web/dist` was left to WP12, as section 9 says.

**WP12, page and end-to-end**

- `create_app` has an optional `web_dist_dir`, so the static tests do not depend on a built page.
- `e2e_web_server.sh` also loads the Allianz manual price file, so Allianz has a price after the
  test accepts the golden batch. The page has an import reminder banner the plan did not list.
- `npm audit` reports 5 problems, all in dev tools (the Vite, esbuild and Vitest chain). They were
  not fixed, because the only fix is a breaking major upgrade of the whole toolchain.
- The Vitest cases for the confirmed holdings table cover all four statuses and the real golden
  `reconcile.json`, not the minimum the review asked for.

## Known limitations and risks

### What P0 does not do

These are out of scope by plan section 1.5. They are also in the UAT script.

- No returns over time, no comparison with the benchmarks, no attribution and no narrative. The
  benchmark series are stored and nothing more. That is P1.
- No cash balance. Deposits, withdrawals, interest and dividends are stored, but the value covers
  securities only.
- Corporate actions other than splits go to the review queue and are not converted.
- No tax report. Withheld taxes are stored for each transaction. Realised gains are before tax.
- Only two price paths: Stooq and a manual price file. No other vendor.
- One portfolio, one person, one machine, no login.
- No sector and country entry. The columns exist and no command sets them.
- A file is not read again when its parser changes. Only files that no parser recognised are tried
  again.

### Risks that only the UAT can retire

| Risk | Why it is open | What limits the damage |
|---|---|---|
| The real CSV export differs from the built-in layout | We have never seen one. The layout comes from common German bank exports | An unknown header refuses the whole file and shows the header. A new layout is a small profile plus a golden file |
| Real PDFs differ from the fixtures | The fixtures were written from public samples of the layouts, with made-up content | One reader per layout, an unknown layout goes to review with its text, and an anonymised export shares the layout without the file |
| Stooq differs from the assumptions | Its host was not reachable from the build machine. The format, the `.de` symbols and the adjustment (assumed adjusted for splits and dividends) are assumptions | The manual price file, an explicit adjustment for each series, and `--http-record` to capture a real answer |
| macOS was never used | Everything ran on Linux. One library (`cryptography`) has no Intel Mac download in the locked version | Apple silicon is the expected setup. The UAT script says what to do on Intel |
| The real network client is tested with mock transports only | Sockets are blocked in every test | QA round 1 found a traceback on a failed connection and it was fixed. A real fetch is UAT step 14 |
| Many files at once | The sample has 10 files. Nine PDFs import in about 0.3 seconds beyond start-up, roughly 0.04 seconds a file, so 300 files should take about 10 seconds. Hundreds of real files were not tried | UAT step 8 is the first real test. If the import is slow, tell us the number of files and the time |
| Statements show settlement days, not trade days | Only the golden statement was tried | A line with exactly one near-date candidate merges and shows both dates. A purchase known only from a statement is held back, never guessed |
| Rounding differs from Trade Republic's documents | Only made-up documents were used | The document's EUR amount always wins, and a check within 0.01 raises a review item instead of correcting silently |

## Observations from writing the UAT script

These are not QA defects. I found them while writing and testing the script against the app. None
blocks P0. Each is a candidate for the fix round.

1. **Intel Macs.** `uv.lock` has a macOS download for `cryptography` only for Apple silicon. Intel
   Macs may have to build it, which needs Rust. Check with `uv lock --upgrade-package cryptography`
   whether a newer version ships an Intel download, or pin an older one that does.
2. **`pg benchmarks series` is awkward.** It needs both `--from` and `--to`, unlike `fetch`, where
   `--to` defaults to today. In text mode it prints only a count of points, so you must add
   `--json` to see any. The UAT script works around both. Suggest: default `--to` to today and print
   the first and last point.
3. **The anonymised export of an unknown CSV header keeps real rows.** `pg review export
   --anonymise` masks five patterns (name, address, IBAN, depot and order numbers). For an
   `unknown_csv_header` item the text is the header plus the first rows of your export, with real
   dates and amounts. The UAT script tells you to send only the header. Suggest: write only the
   header line for a CSV item, unless you ask for rows.
4. **The first accept looks alarming.** It prints `Latest value: 0.00 EUR ... incomplete` because
   nothing is mapped yet. It is correct, and the UAT script warns about it. Suggest wording such as
   "no prices yet: 5 instruments are not mapped".
5. **A test that checks less than it says.** `tests/cli/test_cli_wording.py` walks every command's
   help, but Typer 0.27 ships its own copy of click, so `isinstance(command, click.Group)` never
   matches and the walk visits only `pg --help`. Walking all 40 command paths finds no wording
   problem, but it trips the plan-reference pattern on the text "127.0.0.1" of `pg serve --help`,
   so the pattern needs a small fix too. The new guide test does not depend on click's classes. I
   left the wording test alone, because it is outside this documentation commit. Put it in the fix
   round. The same walk is needed in `test_no_bare_live_word_in_cli_help`, which also checks only
   the root help.
6. **Holding a document out can cause an `oversell` item.** If a later sale depends on the held-out
   purchase, the sale looks too big. The UAT script says to hold out your newest document.
7. **A repeated mapping writes a history line** even when nothing changed. Harmless.

## Recommended scope for the next phase

The spec's next phase is **P1, "Performance, why, news, Home"** (spec section 9): 60 to 75 hours of
focused work, cumulative weeks 13 to 16. It is the everyday app: the Home, Portfolio and "Why did it
perform" pages, a help system, and a service that runs all day on a small server and sends
Telegram alerts. Its core work is time-weighted and money-weighted returns, Brinson attribution
with currency, template narratives, a news poller with filtering, and a market pulse, on Docker
Compose, Tailscale and a heartbeat, in an installable web app with tooltips and a first-run tour.

A **time-weighted return** takes deposits and withdrawals out, so it measures the investing, not
your cash flows. A **money-weighted return** depends on when you paid money in. **Brinson
attribution** splits a return into what came from your sector weights, from your stock picks, from
currency and from fees.

I recommend three steps.

**Step 0. Finish P0 first.** Run the UAT and sign it off. Then do a short fix round. My estimate
is 4 to 8 hours, depending on how many new layouts your files reveal:

1. A CSV profile for your real export.
2. A reader for each new PDF layout the UAT shows.
3. A fix for the price source, if Stooq differs from the assumptions.
4. The four open defects (O1 to O4) and the observations above.

The reason is that every P1 figure is only as good as the transactions and prices under it. One
missing purchase changes every return.

**Step 1. P1a: performance and the everyday pages.** About 40 to 50 hours (my estimate, to settle in
the P1 plan). In this order:

1. Decide the cash question (see below). If cash counts, add it to the ledger.
2. Time-weighted and money-weighted returns for 1 year, 3 years, 5 years, since the start and a
   custom range. Test them against a golden portfolio whose returns are worked out by hand, as P0
   did.
3. The comparison with MSCI World in EUR and the S&P 500 in USD and EUR, indexed to 100. P0 already
   stores both series.
4. The Portfolio and Home pages, replacing the P0 page shell (import stays reachable). The red
   badge stays on every page.
5. Sector and country for each instrument, and a source for the MSCI World sector weights.
   Attribution needs both. The columns exist and nothing sets them.
6. Attribution with currency, fees and taxes, the template narratives, and the Why page.
7. The explanation registry with tooltips, page explainers and the first-run tour. Start from
   Appendix A and B of the UAT script, which already say in plain words what each review item and
   flag means.

**Step 2. P1b: the always-on service.** About 20 to 25 hours (my estimate). The news poller and
filter, the market pulse, Docker Compose, Tailscale, the heartbeat, Telegram alerts and the
installable web app. It needs outside accounts and keys, and a small server at 5 to 10 USD a month
from this point (spec section 10). P1a does not depend on any of it and runs on your Mac.

**Why split P1.** The spec's P1 mixes two kinds of work: features that need only your own data, and
infrastructure that needs accounts and a server. Split, each part can be built and accepted the
way P0 was: a golden case, written criteria, an end-to-end script and a UAT script. And the
returns you asked for first are not held up by server setup.

**Carry into P1:**

- Charts with keyboard access and text labels from the start (O4).
- The wording test fix (observation 5).
- An upgrade of the web tools (Vite, Vitest) when the web stack grows. `npm audit` reports 5
  problems in them now.
- A second price source (Tiingo, named in the spec) if the UAT shows Stooq is unreliable.

**What P1 must not change:** the app never places an order, holds no broker credential and reads
Trade Republic files only; EUR is the reporting currency; the ISIN is the instrument key; cost
basis is FIFO; the badge is on every page.

### Decisions we need from you before P1 is planned

1. **Cash.** Should the portfolio value include your cash balance? I recommend yes. The Trade
   Republic app shows the whole account, and every transaction that moves cash is already stored.
   The other choice is securities only, with purchases and sales as cash flows, which needs care
   with dividends.
2. **MSCI World.** Keep the iShares ETF as the series (P0), or use the index with the currency
   conversion done in the app (spec section 13).
3. **Sector, country and benchmark weights.** Enter sector and country once for each instrument, or
   take them from a source. And where the MSCI World sector weights come from.
4. **The server.** Confirm the always-on server with Tailscale (spec 0.3 says "to confirm") and its
   cost, before P1b starts.
5. **News.** Create the free accounts for the news feeds (Alpaca or Finnhub, spec section 13).
6. **Tactical sleeve.** Keep the default of 15% or choose another size (spec section 13). P1 can
   tag everything as core until you decide.
7. **Your export formats.** Which Trade Republic exports you actually have. The UAT answers this.

## UX walkthrough (added 29 September)

After QA, a reviewer used the app as a first-time Trade Republic customer, at laptop and phone
widths, and then rendered and judged all 19 wireframe boards. The full review, with 90 screenshots,
is [../ux/P0-ux-review.md](../ux/P0-ux-review.md). It found 24 issues: 2 blocking, 13 major and
9 minor.

The five changes it asks for first:

1. **Remove the dead end after Accept.** Until prices are mapped, every value shows "-" and the
   chart sits at 0 EUR with no reason given. Say "Not valued yet" and add an "Add prices" step on
   the page.
2. **Make the import a guided sequence** with one decision per screen: a three-sentence summary, a
   list of files with what each was read as, and the Continue button right under the summary.
3. **Let the user act on the page**: a small form for a missing purchase cost, a quantity box per
   holding instead of a hand-made CSV file, and a way to dismiss an unknown document.
4. **Lead with the answer**: value, amount paid and gain in one sentence at the top, and a chart
   that shows money invested next to value so a sale does not read as a loss.
5. **Calm the screens**: red only for errors, a 12 px minimum text size, at most four modules per
   screen by default, the rest one click away.

One finding is a functional defect, not only a usability one, and joins the fix round:

| Id | What | How to see it | Suggested fix |
|---|---|---|---|
| O5 | A ZIP file of ten documents, stored without compression, is read as a single trade confirmation. The other nine are ignored without a warning, so a user could accept an incomplete portfolio | Zip ten fixture PDFs with `zip -0`, upload the ZIP on the import page | Detect ZIP files by their signature before parsing: unpack them and import each member, or refuse the file with an "unzip it first" message. Show one result line per file |

The review's backlog runs from UX1 to UX16. Items UX1 to UX5 (first run and the value dead end,
the diff redesign, acting on the page, prices on the page, the visual baseline) are written so
that the P1 plan can take them as work packages, and the planning stage now requires that. The
UAT script's known limitations were updated for O5.

## Process notes

- **Commit trailers.** The workflow text asked for `Claude Fable 5.1` on every commit. Each session
  also carried its own attribution reminder that named the model that actually ran. So the branch
  has four names: 54 commits say Fable 5.1, 27 say Opus 5.5, 4 say Sonnet 5 and 2 say Haiku 4.5,
  before this report's commit, which says Sonnet 5.5. History was not rewritten.
- **Branch.** Work stayed on `claude/stock-trading-bot-research-c5ygze`. This report's commit was
  pushed to that branch and to no other, without force.
- **This report's commit** also renames the UAT guide (see the deviations), updates the README's
  "Running the app" section, edits four plan references, and adapts two QA tests to the new file
  name. It adds one test file for the guide.

## How to reproduce these numbers

From the repository root, on a machine with uv:

```bash
uv sync --locked
./scripts/check.sh --all-pythons
./scripts/e2e.sh
```

`check.sh --all-pythons` runs ruff, mypy, pytest on Python 3.13 and 3.11, and the web type check
and Vitest if `web/node_modules` exists. It took 3 minutes 41 seconds on the build machine, which
has 4 CPUs. `e2e.sh` runs the 32 steps, including the page build and the Playwright test, and takes
about two and a half minutes. Its last step checks that git shows no change, so run it on a clean
tree.
