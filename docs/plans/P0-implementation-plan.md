# Phase P0 implementation plan: import and value

Version 1.1, 26 September 2026. Build contract: [../playground-spec.md](../playground-spec.md) version 0.3, section 9, phase P0. Rationale: [../playground-design.md](../playground-design.md). Screens: [../wireframes/README.md](../wireframes/README.md).

This plan says how P0 is built, in what order, and how we know it is done. It follows the ten conditions from the design review (listed in section 1.4 with the section that answers each). Where this plan and the spec differ, the difference is named and explained (section 3.1).

**What changed in 1.1 (plan review, round 1).**

- Build order (section 9). The nine work packages became twelve smaller ones. The pure FIFO ledger now comes before the import pipeline, so the import diff can show split-adjusted holdings. `scripts/assert_golden.py` moves to WP1. Every package's done-when names the end-to-end steps that run with only that package and earlier ones. Old to new numbers: WP1 and WP2 keep theirs; WP3 became WP3 and WP4; WP4 became WP5 and WP7; WP5 became WP6 and WP8; WP6, WP7, WP8 and WP9 became WP9, WP10, WP11 and WP12.
- Matching (5.3.4). Every source now has a report key, which identifies one report within its kind of file, and matching is one-to-one per source kind. Two identical savings-plan documents on one day stay two transactions; a new fixture pair checks this in every import order. A statement line with a later booking day merges when it has exactly one candidate.
- Review items (5.3.5). They are checked again on every stage and accept, and closed as superseded once a later file removes the problem. The pipeline raises `missing_cost_basis` at staging. A buy known only from a statement line is held back.
- PDF fixtures (2.2, 6.2). They are written with reportlab, whose standard fonts print the euro sign. Text fixtures use single spaces, which is what pdfplumber returns.
- Also new: an injectable clock (3.3), booking date versus acquisition date for transfers in (5.5), a price basis check for splits the ledger has not seen (5.7), the remaining spec deviations (3.1), a check of the 30-day import reminder (AC16, end-to-end step 31) and a timing check at UAT (section 8).

Terms used throughout:

- **ISIN** (International Securities Identification Number): the 12-character code that identifies a security, for example `DE0007164600` for SAP. It is the instrument key everywhere in the app.
- **Lot**: one purchase (or one transfer in) of a security, with its own quantity, date and cost. Selling consumes lots.
- **FIFO** (first in, first out): the rule German tax law uses for cost basis. A sale consumes the oldest lot first.
- **ECB reference rate**: the daily euro exchange rate published by the European Central Bank, quoted as units of foreign currency per 1 EUR.
- **Idempotent**: doing the same thing twice has the same effect as doing it once. Here: importing the same file, or the same transactions from a different file, adds nothing new.
- **Review queue**: a list of documents, rows or conflicts the app could not handle with certainty. Nothing in it is guessed or dropped. Each item waits for you, unless a later file removes the problem; then the item is closed as superseded.
- **Held back**: a transaction that is stored but kept out of lots, holdings and value until the review item about it is settled.
- **Candidate**: one transaction as read from one file, before it is matched with what other files report.
- **Golden file**: a committed file with the exact expected output of a test. A test fails if the output differs.
- **Fixture**: a committed input file used by tests. In this project every fixture is synthetic (made up) or anonymised.
- **As-of lookup**: taking the latest value dated on or before a given day.

## 1. Goal and acceptance criteria

### 1.1 The phase row (spec section 9)

> | P0. Import and value | Your portfolio in EUR, correct holdings, a value chart | Instrument master, Trade Republic CSV and PDF parsers, FIFO lots, reconciliation, daily prices and ECB FX for your names, MSCI World and S&P 500 series | Import endpoint and diff; CLI first, then a minimal import page | 25 to 35 | 4 to 5 |

In plain words: after P0 you can import your Trade Republic exports, check the rebuilt holdings against what the Trade Republic app shows, accept them, and see a daily value of your portfolio in EUR with correct FIFO cost basis. The command line (CLI) comes first; a small web page for upload, diff and accept comes last.

### 1.2 The golden synthetic portfolio

Acceptance is measured against one invented portfolio, the **golden portfolio**. Its input files live in `tests/fixtures/golden/inputs/`, its expected outputs in `tests/fixtures/golden/expected/`, and the hand computation below is copied into `tests/fixtures/golden/README.md`. All figures are made up. The ISINs are real public identifiers so that mapping looks realistic; no real person, account or IBAN appears anywhere.

**Instruments**

| Key | ISIN | Name in documents | Price source (after mapping) | Listing currency |
|---|---|---|---|---|
| SAP | DE0007164600 | SAP SE | Stooq `sap.de` | EUR |
| MSCIW | IE00B4L5Y983 | iShsIII-Core MSCI World U.ETF | Stooq `eunl.de` | EUR |
| AAPL | US0378331005 | Apple Inc. | Stooq `aapl.us` | USD |
| NVDA | US67066G1040 | NVIDIA Corp. | Stooq `nvda.us` | USD |
| ALV | DE0008404005 | Allianz SE | manual price file `ALV.DE` | EUR |

**Transactions** (times are Europe/Berlin; "src" lists where each one appears: P = PDF document, C = CSV export, S = account statement PDF, imported in the second round)

| No | Date and time | Type | Details | Booking amount (EUR) | src |
|---|---|---|---|---|---|
| T1 | 2024-01-02 | deposit | | +5,000.00 | C, S |
| T2 | 2024-01-15 10:05 | buy | SAP 10 at 140.00, fee 1.00 | -1,401.00 | P, C, S |
| T3 | 2024-02-01 | buy (savings plan) | MSCIW 2.5 at 80.00, no fee | -200.00 | P, C, S |
| T4 | 2024-03-01 | buy (savings plan) | MSCIW 2.5 at 84.00, no fee | -210.00 | C, S |
| T5 | 2024-03-12 14:30 | buy | AAPL 5 at 160.00, fee 1.00 | -801.00 | C, S |
| T6 | 2024-03-20 15:45 | buy | NVDA 2 at 850.00, fee 1.00 | -1,701.00 | P, C, S |
| T7 | 2024-04-02 | deposit | | +1,000.00 | C, S |
| T8 | 2024-04-10 09:12 | buy | SAP 5 at 160.00, fee 1.00 | -801.00 | P, S |
| T9 | 2024-05-15 | dividend | SAP 15 x 2.20 = 33.00 gross; capital gains tax 8.25, solidarity surcharge 0.45 | +24.30 | P, C, S |
| T10 | 2024-05-16 | dividend | AAPL 5 x 0.24 USD = 1.20 USD at 1.0800 USD per EUR = 1.11 EUR; US withholding tax 0.17 | +0.94 | P, S |
| T11 | 2024-06-10 | split | NVDA: 20 shares booked in (document shows only the new total, like the real layout) | none | P |
| T12 | 2024-06-12 11:20 | sell | SAP 12 at 175.00 = 2,100.00, fee 1.00; capital gains tax 94.40, solidarity surcharge 5.19 | +1,999.41 | P, C, S |
| T13 | 2024-06-20 | transfer in | ALV 4 shares, no cost basis in the file | none | C |
| T14 | 2024-07-01 | interest | | +3.12 | C |

Each line of the H1 statement carries the same date as the matching PDF or CSV entry (the execution date for a trade, the document date for a dividend), so every line matches exactly. Real statements may show a later booking day; rule c of section 5.3.4 handles that.

One more PDF, `unbekannt_kosteninformation.pdf`, imitates a Trade Republic cost information sheet that no parser understands. It must land in the review queue.

**Hand-computed results**

- Import round 1 (the CSV and the nine PDFs): 19 parsed candidates (11 CSV rows, 8 PDF transactions), 14 unique transactions, 5 merged pairs (T2, T3, T6, T9, T12), 0 held back, 2 review items, both raised at staging (the unknown layout, and the missing cost basis for T13).
- Import round 2 (the same ten files plus the H1 account statement): 10 files skipped as already imported (the unknown cost information sheet is classified again, still finds no parser, and counts as skipped), 11 statement lines all matched to known transactions, 0 new transactions, 0 new review items.
- FIFO for the sale T12: lot T2 (10 shares, cost 1,401.00) is consumed fully; lot T8 (5 shares, cost 801.00) gives 2 shares at 801.00 x 2 / 5 = 320.40. Consumed cost 1,721.40. Proceeds after fee 2,099.00. Realised gain 377.60 (before tax). Proceeds are split across the two disposals by quantity: 1,749.16666667 and 349.83333333.
- Open lots on 2024-12-31: SAP 3 shares, cost 480.60 (opened 2024-04-10); MSCIW 2.5 at 200.00 and 2.5 at 210.00; AAPL 5 at 801.00; NVDA 20 at 1,701.00 (opened 2024-03-20, quantity scaled by the 10-for-1 split, cost unchanged); ALV 4 at 800.00 (acquired 2020-03-02, cost typed in by the user). Total open cost basis 4,192.60.
- Taxes withheld in 2024: 108.46 EUR. Dividends gross in EUR: 34.11.
- Value on 2024-05-31 (all markets open; ECB 1.0800 USD per EUR): SAP 15 x 170.00 = 2,550.00; MSCIW 5 x 90.00 = 450.00; AAPL 5 x 190.00 / 1.08 = 879.62962963; NVDA 2 shares held, price series split-adjusted, so 2 x 10 x 110.00 / 1.08 = 2,037.03703704. Total **5,916.67** EUR; cost basis 5,114.00 (the ALV lot is left out: it was acquired in 2020 but booked in on 2024-06-20); complete.
- Value on 2024-12-31 (Xetra closed, so SAP and MSCIW use the 2024-12-30 close; US open; ECB 1.0400): SAP 3 x 236.00 = 708.00; MSCIW 5 x 100.00 = 500.00; AAPL 5 x 250.00 / 1.04 = 1,201.92307692; NVDA 20 x 134.00 / 1.04 = 2,576.92307692; ALV 4 x 290.00 = 1,160.00 from the manual file, last price 2024-12-20, flagged stale. Total from unrounded parts **6,146.85** EUR; cost basis 4,192.60.
- Staleness boundary: on 2024-12-25 the ALV price is 5 days old and is not stale; on 2024-12-27 it is 7 days old and is stale. On 2024-12-25 SAP uses the 2024-12-23 close and the ECB rate from 2024-12-24, without a flag.
- These four days (2024-05-31, 2024-12-25, 2024-12-27 and 2024-12-31) are asserted directly in `tests/valuation/test_series.py` and `tests/golden/test_golden_portfolio.py`, not only through the golden files, because the full-year golden file is written by the code itself.
- The Stooq fixture series are declared `split_dividend`, so the value output also carries the info flag `dividend_adjusted_prices` for SAP, MSCIW, AAPL and NVDA. The ALV manual file is declared `raw`.
- Before any mapping, the value series reports every held instrument as `unmapped`, a value of 0.00 and `complete: false`, and exits with status 0.
- With the clock set to 2025-02-03 (`PG_TODAY`, section 3.3), 34 days after the last import on 2024-12-31, `pg status` shows the reminder to import again (spec 3.10). One review item is still open: the unknown layout.

### 1.3 Acceptance criteria

Each criterion is checked by a named test or by a step of the end-to-end scenario in section 7.6.

| Id | Criterion | Checked by |
|---|---|---|
| AC1 | `uv sync --locked` then `./scripts/check.sh` passes on Python 3.13 and on Python 3.11, on Linux here and on macOS at UAT | WP1 smoke test, e2e step 2, UAT step 2 |
| AC2 | Importing the golden round 1 files gives exactly the counts in 1.2 and the listed transactions, field by field | `tests/golden/test_golden_portfolio.py`, e2e steps 5 and 6 |
| AC3 | Every Trade Republic layout listed in section 6.2 parses to its golden JSON; the PDF-to-text layer and the text-to-transaction layer have separate tests | `tests/importer/tr/test_pdf_text.py`, `test_layout_goldens.py` |
| AC4 | An unknown PDF layout, an unknown CSV header, an unknown CSV row type, a missing required field and amounts that do not add up each create a review item with the extracted text or row shown; a buy known only from a statement line is held back with a `missing_field` item; an item whose problem a later file removes is closed as superseded; nothing is guessed and nothing is dropped | `tests/importer/test_review_queue.py`, `test_reevaluation.py` |
| AC5 | Any sequence of imports, with repeats, whose files together form a set S of golden files (the account statement included) ends with the same accepted ledger and the same open review items as one import of S; re-importing adds no transaction and no review item a second time; two savings-plan documents for the same ISIN, day and amount stay two transactions in every import order, with and without the CSV | property test `tests/importer/test_idempotency.py`, `tests/importer/test_same_day_savings_plans.py`, e2e steps 10 and 11 |
| AC6 | The reconciliation diff lists new, merged and already-known transactions, review items, holdings before and after, and the comparison with confirmed holdings; nothing changes until `accept` | `tests/importer/test_reconcile.py`, e2e steps 7 to 9 |
| AC7 | FIFO lots match 1.2 exactly, including fees in cost, the partial sell, the split and the transfer in with user-supplied cost; a sell never consumes a lot booked after it; disposals are stored; FIFO invariants hold for random sequences | `tests/ledger/test_fifo.py`, `test_fifo_properties.py`, e2e step 25 |
| AC8 | The value series matches 1.2 on 2024-05-31 and 2024-12-31 to the cent; on 2024-12-25 and 2024-12-27 the price dates, rate dates and stale flags are as described; the stale, missing, unmapped and price basis flags appear exactly as described | `tests/valuation/test_series.py`, `tests/golden/test_golden_portfolio.py`, e2e steps 16, 17 and 26 |
| AC9 | Prices, ECB rates, benchmark series and ISIN suggestions come through the injectable HTTP layer; the test suite runs with sockets disabled; the CLI can replay recorded responses | `tests/http/test_no_network.py`, e2e steps 20 to 23 |
| AC10 | The ISIN-to-symbol mapping is stored, listed, editable from the CLI and the API, logged on every change, and never set without an explicit command or file | `tests/marketdata/test_instrument_master.py`, e2e steps 18 and 19 |
| AC11 | MSCI World (EUR) and S&P 500 (USD and EUR) daily series are stored and returned for any range | `tests/marketdata/test_benchmarks.py`, e2e step 27 |
| AC12 | The API returns the same holdings, lots and value as the CLI; money is serialised as decimal strings; there is no endpoint that could place an order | `tests/api/test_contract.py`, e2e step 28 |
| AC13 | The import page shows the red badge "NO REAL ORDERS · portfolio read-only", uploads files, shows the diff, accepts, then shows holdings and a value chart | Playwright `web/e2e/import.spec.ts`, e2e step 30 |
| AC14 | No real export, personal data, API key, generated database or data file is committed; data directories are ignored by git; the words "live trading" appear nowhere user-facing, and the word "live" appears in no CLI help, setting or message | `tests/test_repo_hygiene.py`, e2e step 32 |
| AC15 | The macOS UAT guide exists and its commands, including the ones that fetch real prices and rates, run as written on your Mac; the timing check shows the value within one minute of starting an import (spec section 1) | `docs/uat/P0-macos.md`, UAT sign-off |
| AC16 | `pg status` and `GET /portfolio` show a reminder when the last import is more than 30 days old, and not before (spec 3.10) | `tests/importer/test_reminder.py` and `tests/api/test_contract.py` with a fixed clock, e2e step 31 |

### 1.4 Design review conditions and where they are met

| Condition | Where |
|---|---|
| 1. Injectable HTTP layer, fixtures only, network disabled in tests | 5.4, 6.4 to 6.7, 7.1 |
| 2. Synthetic German-language fixtures, separate PDF and text layers, unknown goes to review | 6.1 to 6.3 |
| 3. Nothing private committed; data directories ignored | 2.4, 7.5 |
| 4. Decimal end to end, UTC plus Berlin source time, one FX convention | 3.3, 4.1, 5.1 |
| 5. Idempotent re-import with a semantic key and a precedence rule | 5.3.4, 5.3.5, 7.2 |
| 6. FIFO in EUR with fees, disposals, partial sells, splits and transfers in, with property tests | 5.5, 7.2 |
| 7. Documented price basis and as-of rule, flags instead of failures, manual price file | 5.7 |
| 8. Mapping stored, shown, editable, never guessed | 5.6, 6.7 |
| 9. Read-only scope, no broker login, no pytr or scraping, no order path, no "live trading" wording | 1.5, 7.5 |
| 10. Written acceptance criteria, golden portfolio, Playwright test, Python 3.13 via uv, macOS parity and a UAT guide | 1.2, 1.3, 3, 7.6, 8 |

### 1.5 Out of scope for P0

- Any broker connection, login or credential. No `pytr`, no scraping, no Trade Republic API. The only input is files you export yourself.
- Any order, order draft or proposal. There is no code path that sends anything to a broker.
- Time-weighted and money-weighted returns, benchmark comparison, attribution and narratives (P1). P0 stores the benchmark series but does not compare against them.
- Cash balance tracking. Deposits, withdrawals and interest are imported and stored, but the value series covers securities only.
- Sleeves (every holding is tagged `core`), preference profile, news, market pulse, scenarios, agents, assistant.
- Corporate actions other than splits (mergers, spin-offs, subscription rights, exchanges): they go to the review queue.
- Tax reporting. Withheld taxes are stored per transaction; no tax report is produced.
- Home, Portfolio and Why pages, the help system, the installable web app, WebSocket events, login, Docker, Tailscale and the server (P1).
- Other price vendors (Tiingo, Alpaca, Alpha Vantage). The price source interface allows them later.
- More than one portfolio. The schema allows it; the CLI and page use one portfolio named "Trade Republic".
- Entering sector and country. Spec 3.10 says they come from the price source or are entered once. The columns exist, but no P0 command sets them; P1 adds this, because attribution is the first feature that needs them.
- Parsing a file again after its parser changes. P0 only retries files that no parser recognised (5.3.4).

## 2. Repository layout and tooling

### 2.1 Layout

```
pyproject.toml                  uv-managed; package "playground"; console script "pg"
uv.lock                         committed
.python-version                 3.13
.gitignore
scripts/
  check.sh                      the single local check command
  assert_golden.py              compares a JSON output with a golden file (ignores ids and timestamps)
  e2e_api.sh                    starts the API on a data dir, checks read endpoints against goldens (GET only), stops it
  e2e_web_server.sh             seeds a data dir for Playwright, then runs "pg serve"
configs/
  benchmarks.yaml               benchmark definitions (editable)
src/playground/
  __init__.py                   version
  config.py                     settings (data dir, HTTP mode, today); pydantic, unknown keys rejected
  core/        money.py numbers.py dates.py isin.py errors.py clock.py types.py
  storage/     db.py schema.py repos.py
  http/        client.py replay.py record.py
  importer/
    model.py keys.py pipeline.py reconcile.py review.py anonymise.py
    manual_csv.py confirmed_csv.py
    tr/  pdf_text.py classify.py csv_profiles.py csv_parser.py
         layouts/  common.py wertpapierabrechnung.py settlement_en.py dividende.py
                   steuer.py zinsen.py split.py kontoauszug_2023.py kontoauszug_2024.py
  ledger/      fifo.py holdings.py
  marketdata/  lake.py instruments.py stooq.py manual_prices.py ecb.py openfigi.py benchmarks.py
  valuation/   series.py
  api/         app.py schemas.py routes_portfolio.py routes_instruments.py routes_benchmarks.py
  cli/         main.py imports.py review.py instruments.py marketdata.py portfolio.py serve.py
tests/                          mirrors src; plus golden/ and fixtures/
web/                            the minimal import page (Vite, React, TypeScript)
docs/uat/P0-macos.md            UAT guide
```

The package name is `playground`; the CLI entry point is `pg`, as in spec section 3.5 (`pg run`, `pg sweep` come in later phases).

### 2.2 Tooling

- **uv** manages the environment: `uv venv --python 3.13`, `uv sync --locked`, `uv run ...`. `requires-python = ">=3.11"`. Python 3.12 is not installed here and is not downloaded; the floor check runs on 3.11.
- **ruff** for formatting and linting (rules E, F, I, B, UP, S, DTZ, PT, SIM, plus `flake8-tidy-imports` banning `pytr`, `requests_html`, `selenium` and `playwright` inside `src/`). DTZ catches naive datetimes. Tests use `assert` and `subprocess` (the hygiene test runs `git ls-files`), so `pyproject.toml` sets `per-file-ignores` for `tests/**`: S101, S603 and S607.
- **pytest** with `pytest-socket` (`--disable-socket --allow-unix-socket` in `addopts`), **hypothesis** for property tests (the import property test sets `deadline=None`, section 7.2).
- **mypy** on `src/` (not strict; `disallow_untyped_defs` for `core`, `ledger`, `valuation`, `importer`). Blocking in `check.sh`; set `PG_SKIP_MYPY=1` to skip locally while iterating.
- **reportlab** (development only) to regenerate the synthetic PDF fixtures from their text files. It uses the standard PDF font Helvetica, whose WinAnsi encoding covers German umlauts, ß and the euro sign, so no font file is embedded and no font licence is needed. `invariant=1` makes a regenerated PDF byte-identical. Checked in this container with reportlab 5.0 and pdfplumber 0.11: "1.401,00 € Solidaritätszuschlag ÄÖÜß" comes back exactly. fpdf2 is not used: its core fonts cannot print "€" (it raises `FPDFUnicodeEncodingException`), and the 2024 statement layout prints its amounts with "€".

### 2.3 The single check command

`./scripts/check.sh` runs, and stops at the first failure:

1. `uv run ruff format --check .`
2. `uv run ruff check .`
3. `uv run mypy src`
4. `uv run pytest -q`
5. `UV_PROJECT_ENVIRONMENT=.venv-py311 uv run --python 3.11 pytest -q -x` when `--all-pythons` is passed (the floor check in a second, separate environment; run before each commit that closes a work package)
6. `(cd web && npm run typecheck && npm test -- --run)` when `web/node_modules` exists

The script uses only POSIX tools available on macOS (no `sed -i`, no `readlink -f`).

### 2.4 Git hygiene

`.gitignore` contains at least: `data/`, `.e2e/`, `private/`, `uploads/`, `registry/`, `lake/`, `*.sqlite`, `*.sqlite-*`, `*.db`, `*.duckdb`, `*.parquet`, `.env`, `.env.*`, `.venv/`, `.venv-py311/`, `web/node_modules/`, `web/dist/`, `web/test-results/`, `web/playwright-report/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.hypothesis/`. The default data directory is `./data` (ignored); the UAT guide recommends a directory outside the repository, `~/pg-private/data`.

## 3. Technology choices

### 3.1 Choices and their relation to spec section 8

| Area | P0 choice | Note |
|---|---|---|
| Python | 3.13 for development via uv; `>=3.11` supported | Spec says 3.12; this container has 3.11 and 3.13 only. Nothing in P0 needs a 3.12 feature. |
| Models and settings | pydantic 2 | As spec |
| Registry | SQLite through SQLAlchemy 2 Core | As spec. A custom `DecimalText` column type stores decimals as text. |
| Price and FX store | Parquet via pyarrow, `decimal128(20, 8)` columns | As spec (data lake). DuckDB and Polars are not needed until P1. |
| CSV | Python `csv` module | Spec lists pandas for CSV. The standard module keeps every number as text until `Decimal` parses it; pandas would go through floats. |
| PDF to text | pdfplumber | As spec |
| HTTP | httpx behind the project's own `HttpClient` protocol | Spec names requests; httpx gives timeouts and HTTP/2 in one package. Only `NetworkHttpClient` imports it. |
| ISIN lookup | OpenFIGI mapping API, suggestions only, on demand | Not in spec 8; needed to help you map European listings without guessing. Spec 3.10 says outbound requests carry tickers and dates only. `pg instruments suggest` sends one ISIN, and only when you run it; no other command sends an ISIN. An ISIN is a public identifier of a security, not of you. |
| Time zones | `zoneinfo` plus the `tzdata` package | `tzdata` makes Linux and macOS give identical results. |
| CLI | Typer | Not named in spec; small and typed. |
| API | FastAPI, uvicorn, python-multipart | As spec |
| Web page | Vite, React, TypeScript, Vitest; Playwright for the end-to-end test | As spec 3.7, minus Tailwind, TanStack Query and chart libraries, which arrive with the P1 screens. The value chart is a small hand-written SVG. |
| Instrument-name matching | not used | Spec lists rapidfuzz; every Trade Republic document carries the ISIN, so no fuzzy matching is needed. |
| Lake layout | `lake/bars/daily/source=<source>/symbol=<symbol>/part.parquet`, no `year=` level | Spec 3.1 shows `bars/daily/symbol=AAPL/year=2024/part.parquet`. The `source=` level keeps a Stooq series and a manual file for the same symbol apart. One file per symbol holds a few thousand daily rows, so a `year=` level buys nothing yet; P1 can add it when DuckDB queries need it. |
| Manifests | `<data-dir>/lake/manifests/<name>.json` | Spec 3.1 puts them in `data/manifests/`. Keeping them inside `lake/` puts all market data under one directory. The content is as in spec 3.1. |
| API paths | The spec 3.6 paths plus `/health`, the batch calls `confirmed-holdings`, `accept` and `discard`, `/portfolio/lots`, `/portfolio/review`, `/instruments/...` and `/benchmarks/...` (5.8) | Spec 3.6 lists only the upload, the batch view and the read paths. Accepting needs its own call, and the others give the page and the CLI parity test what they need. |
| PDF fixtures | reportlab, development only | Not in spec 8; writes the synthetic test PDFs (2.2). |

### 3.2 Pinned versions

The exact versions are fixed in `uv.lock` and `web/package-lock.json` in WP1 and WP12. `@playwright/test` is pinned to `1.56.1`, whose Chromium build (1194) is the one installed under `/opt/pw-browsers`. The Playwright config also reads `PW_CHROMIUM_PATH` and passes it as `executablePath` when set. Nobody runs `playwright install` in this container; on your Mac the UAT guide shows the one-time `npx playwright install chromium` for the optional browser test.

### 3.3 Numbers, time and currency conventions

These rules hold in every module. They are also written in the docstrings of `src/playground/core/money.py`, `dates.py` and `clock.py`.

- **Decimal everywhere.** Quantities, prices, amounts, rates and costs are `decimal.Decimal`. Floats are never used for money or quantity. The only exception is the web chart, which converts decimal strings to numbers for pixel positions and never for displayed figures.
- **Precision.** Parsed values are kept exactly as written in the document (Trade Republic shows up to 6 decimal places for fractional shares). Computed values are quantised to 8 decimal places with `ROUND_HALF_EVEN`. EUR figures are rounded to 2 decimals with `ROUND_HALF_UP` only when displayed or exported. Totals are always computed from unrounded parts.
- **Allocation.** When a cost or proceeds amount is split (partial sell), each share is quantised to 8 decimals and the last piece takes the remainder, so the pieces always add up exactly.
- **Time.** Every transaction stores `ts_utc` (ISO 8601 with `Z`), `ts_local` (the time as written in the document, without offset), `source_tz` (`Europe/Berlin`) and `ts_precision` (`minute` or `day`). Day-precision entries are placed at 00:00 local time. Conversion uses `zoneinfo.ZoneInfo("Europe/Berlin")`, so summer time is handled (T12 at 11:20 local is 09:20 UTC).
- **Today.** No code reads the system clock directly. Everything that needs the current time or date asks a `Clock` (`core/clock.py`). The setting `today` (environment variable `PG_TODAY`, format `YYYY-MM-DD`) fixes the date: `today()` is then that day, and `now_utc()` is 12:00 Berlin time on it. Without the setting, the system clock is used and `today()` is the Berlin calendar date. Tests, the e2e scenario and the Playwright server set it. The clock decides the 30-day import reminder, the range of the value series rebuilt after accept (5.7), the latest value in `pg status` and `GET /portfolio`, and every timestamp the app writes.
- **FX convention.** A rate is **units of the foreign currency per 1 EUR**, the ECB convention, which is also how Trade Republic prints it ("1,0800 EUR/USD" means 1 EUR = 1.08 USD). EUR amount = foreign amount / rate. One function, `to_eur(amount, currency, rate)`, does this; nothing else divides or multiplies by a rate. Pence (`GBX`) are converted to pounds by dividing by 100 before the rate is applied.
- **Which EUR amount wins.** When a document states the EUR amount (every Trade Republic trade and dividend note does), that amount is used and the document's rate is stored for reference. The ECB rate is used only when no EUR amount is given.

## 4. Data model for this phase

### 4.1 Registry tables (SQLite, `<data-dir>/registry/runs.sqlite`)

The file name follows spec section 3.5 so later phases add their tables to the same database. Types: `TEXT` for strings, `DEC` for a decimal stored as text through `DecimalText`, `TS` for an ISO 8601 UTC timestamp string, `DATE` for `YYYY-MM-DD`, `JSON` for a JSON text column. A `schema_version` table holds one integer; P0 starts at 1 and creates tables with `metadata.create_all`.

| Table | Columns (type) | Notes |
|---|---|---|
| `portfolios` | id INTEGER PK, name TEXT, kind TEXT (`real`, `virtual`), base_currency TEXT (`EUR`), source TEXT (`trade_republic`), last_import_at TS NULL, created_at TS | One real portfolio in P0 |
| `instruments` | id INTEGER PK, isin TEXT UNIQUE, name TEXT, type TEXT (`stock`, `etf`, `fund`, `bond`, `certificate`, `other`, `unknown`), country TEXT NULL, sector TEXT NULL, industry TEXT NULL, currency TEXT NULL (listing currency of `data_symbol`), exchange TEXT NULL, data_source TEXT NULL (`stooq`, `manual`), data_symbol TEXT NULL, mapping_status TEXT (`unmapped`, `confirmed`), mapping_updated_at TS NULL, mapping_note TEXT NULL | Spec 4.4 plus the mapping status columns. No P0 command sets country, sector or industry (1.5). |
| `instrument_mapping_log` | id PK, instrument_id FK, changed_at TS, changed_by TEXT (`cli`, `api`, `file`), old_source, old_symbol, old_currency, new_source, new_symbol, new_currency (all TEXT NULL), note TEXT NULL | Every mapping change; shown by `pg instruments history` |
| `import_batches` | id INTEGER PK, portfolio_id FK, created_at TS, status TEXT (`staged`, `accepted`, `discarded`), accepted_at TS NULL, summary_json JSON | One upload of one or more files |
| `imports` | id PK, batch_id FK, portfolio_id FK, file_hash TEXT (SHA-256 of the bytes), file_name TEXT, stored_path TEXT, doc_type TEXT, parser_id TEXT NULL, parser_version INTEGER NULL, parsed_at TS, status TEXT (`parsed`, `partial`, `needs_review`, `duplicate_file`, `failed`, `reparsed`), review_notes TEXT NULL | Spec 4.4 plus batch, name and parser version. Among accepted batches, at most one row per portfolio and file hash is active (status `parsed`, `partial` or `needs_review`); the pipeline enforces it (5.3.4). |
| `transactions` | id PK, portfolio_id FK, batch_id FK, import_id FK (first source), state TEXT (`staged`, `held`, `accepted`), ts_utc TS, ts_local TEXT, source_tz TEXT, ts_precision TEXT, value_date DATE NULL, type TEXT, instrument_id FK NULL, quantity DEC NULL, price DEC NULL, currency TEXT, amount DEC NULL (signed booking amount in `currency`), amount_eur DEC NULL, fx_rate DEC NULL, fx_source TEXT (`document`, `ecb`, `none`), fees_eur DEC NULL, tax_eur DEC NULL (NULL when no source says, as for a line known only from a statement), tax_detail_json JSON, split_new_quantity DEC NULL, origin TEXT NULL (`order`, `savings_plan`, `transfer`), source_ref TEXT NULL, semantic_key TEXT, occurrence INTEGER, content_hash TEXT, precedence INTEGER (of the highest source) | Spec 4.4 extended. Unique (portfolio_id, content_hash). `held` means held back (5.3.4). |
| `transaction_sources` | id PK, transaction_id FK, import_id FK, source_kind TEXT (`pdf_document`, `csv_export`, `pdf_statement`, `manual_csv`), precedence INTEGER, report_key TEXT, line_from INTEGER, line_to INTEGER, fields_json JSON | Every file that reported this transaction, with the fields it reported. Unique (import_id, report_key). |
| `review_items` | id PK, portfolio_id FK, batch_id FK NULL, import_id FK NULL, transaction_id FK NULL, kind TEXT, status TEXT (`open`, `resolved`, `dismissed`), message TEXT, extracted_text TEXT NULL, fields_json JSON, created_at TS, resolved_at TS NULL, resolution_json JSON NULL (for example `{"how": "superseded", "by": "tr_transactions_2024.csv"}`), dedupe_key TEXT UNIQUE | Kinds and re-evaluation in 5.3.5. `dedupe_key` stops re-imports from repeating an item. |
| `cost_basis_inputs` | id PK, transaction_id FK, acquired_on DATE, cost_eur DEC, entered_at TS, note TEXT NULL | Your cost basis for a transfer in |
| `lots` | id PK, portfolio_id FK, instrument_id FK, open_txn_id FK, origin TEXT (`buy`, `transfer_in`), booked_ts TS (when the lot entered the account: the trade time, or the day the transfer was booked in), opened_ts TS (acquisition time for FIFO order; for a transfer in, your `acquired_on`), quantity_initial DEC, quantity_open DEC, cost_eur_initial DEC NULL, cost_eur_open DEC NULL, cost_missing INTEGER (0 or 1) | Spec 4.4 extended; rebuilt on every accept |
| `disposals` | id PK, portfolio_id FK, lot_id FK, txn_id FK, kind TEXT (`sell`, `transfer_out`), ts_utc TS, quantity DEC, cost_eur DEC NULL, proceeds_eur DEC NULL, fees_eur DEC, realised_eur DEC NULL | New in P0 |
| `confirmed_holdings` | id PK, portfolio_id FK, as_of DATE, isin TEXT, quantity DEC, source TEXT (`typed`, `file`, `statement`), entered_at TS | The holdings you confirm for the diff |
| `holdings_snapshots` | portfolio_id, as_of DATE, instrument_id, quantity DEC, pricing_quantity DEC, price DEC NULL, price_date DATE NULL, price_currency TEXT NULL, fx_rate DEC NULL, fx_date DATE NULL, value_eur DEC NULL, cost_basis_eur DEC NULL, sleeve TEXT (`core`), flags_json JSON; PK (portfolio_id, as_of, instrument_id) | Spec 4.4 extended with the price evidence behind each value |
| `portfolio_values` | portfolio_id, date DATE, value_eur DEC, cost_basis_eur DEC, complete INTEGER, flags_json JSON; PK (portfolio_id, date) | The daily value series |
| `benchmarks` | id TEXT PK (slug), name TEXT, isin TEXT NULL, currency TEXT, data_source TEXT, data_symbol TEXT | Loaded from `configs/benchmarks.yaml` |

Transaction types: `buy`, `sell`, `dividend`, `interest`, `fee`, `tax`, `deposit`, `withdrawal`, `split`, `transfer_in`, `transfer_out`. This is the spec's list plus `interest` and the two transfer types.

### 4.2 Data lake files (`<data-dir>/lake/`)

| Path | Columns | Notes |
|---|---|---|
| `bars/daily/source=<source>/symbol=<symbol>/part.parquet` | date (date32), open, high, low, close (decimal128(20,8), open to low nullable), volume (int64, nullable), currency (string), adjustment (string: `raw`, `split`, `split_dividend`), source (string), fetched_at (timestamp UTC) | One file per symbol; upsert by date, newest fetch wins. Benchmarks use the same layout. |
| `fx/ecb/part.parquet` | date (date32), currency (string), rate (decimal128(20,8)), fetched_at | Units of `currency` per 1 EUR |
| `manifests/<name>.json` | file list, SHA-256, source, fetched_at | Written after every fetch, as in spec 3.1 |

### 4.3 Other files

- `<data-dir>/uploads/<sha256>.<ext>`: a copy of every imported file, so review items can be reopened. Never leaves the machine.
- `configs/benchmarks.yaml` (committed):

```yaml
msci_world_eur:
  name: MSCI World in EUR (iShares Core MSCI World UCITS ETF on Xetra)
  isin: IE00B4L5Y983
  data_source: stooq
  data_symbol: eunl.de
  currency: EUR
sp500:
  name: S&P 500 index
  data_source: stooq
  data_symbol: ^spx
  currency: USD
  also_in: [EUR]
```

## 5. Module design

### 5.1 `core`: numbers, dates, ISIN, money, clock

```python
# core/numbers.py
def parse_de_decimal(text: str) -> Decimal        # "1.825,60" -> 1825.60, "-0,890" -> -0.890, "1.000" -> 1000
def parse_en_decimal(text: str) -> Decimal        # "1,825.60" -> 1825.60
# Raises NumberFormatError on anything ambiguous; never guesses the locale. The layout decides the locale.

# core/dates.py
BERLIN = ZoneInfo("Europe/Berlin")
def parse_de_date(text: str) -> date              # "13.05.2019", "2019-11-18", "01 Apr. 2024", "01 Mai 2024", "3 Dez. 2024"
def berlin_to_utc(local: datetime) -> datetime    # naive local -> aware UTC; rejects non-existent times, picks the first of repeated ones and says so in the docstring
@dataclass(frozen=True)
class SourceTime:
    ts_utc: datetime; ts_local: datetime; source_tz: str; precision: Literal["minute", "day"]

# core/isin.py
def is_valid_isin(value: str) -> bool             # format and Luhn check digit
def normalise_isin(value: str) -> str             # strips "ISIN:" and spaces, upper-cases, validates

# core/money.py
MONEY_Q = Decimal("0.00000001")
def q8(x: Decimal) -> Decimal
def eur2(x: Decimal) -> Decimal                   # display rounding, ROUND_HALF_UP
def to_eur(amount: Decimal, currency: str, rate: Decimal | None) -> Decimal
def allocate(total: Decimal, weights: Sequence[Decimal]) -> list[Decimal]   # quantised pieces summing exactly to total

# core/clock.py
class Clock(Protocol):
    def now_utc(self) -> datetime: ...
    def today(self) -> date: ...                  # the Berlin calendar date
class SystemClock: ...                            # the only code that reads the system clock
class FixedClock: ...                             # FixedClock(date(2024, 12, 31)); now_utc() is 12:00 Berlin time that day
def clock_from_settings(settings: Settings) -> Clock    # PG_TODAY selects FixedClock; a malformed value is refused

# core/types.py
class TxnType(StrEnum): BUY; SELL; DIVIDEND; INTEREST; FEE; TAX; DEPOSIT; WITHDRAWAL; SPLIT; TRANSFER_IN; TRANSFER_OUT
```

### 5.2 `storage`: registry and repositories

- `db.py`: `open_registry(data_dir: Path) -> Engine` (creates directories, enables foreign keys and WAL mode), `DecimalText` type decorator (stores `str(Decimal)`, loads `Decimal`, refuses floats).
- `schema.py`: SQLAlchemy `MetaData` with the tables in 4.1.
- `repos.py`: small functions grouped by table, all taking a `Connection`: `get_or_create_portfolio`, `upsert_instrument_from_document`, `set_mapping(...)` (writes the log row), `insert_batch`, `find_import_by_hash`, `insert_transaction`, `find_by_content_hash`, `find_by_report_key`, `transactions_with_key`, `set_transaction_state`, `add_source`, `open_review_item(dedupe_key, ...)`, `close_review_item(id, how, by)`, `replace_lots_and_disposals`, `replace_value_series`, and read helpers used by the CLI and API.
- `pg init` creates the directory tree `registry/`, `lake/`, `uploads/` under the data directory and the default portfolio.

### 5.3 `importer`: parsing, staging, reconciliation

#### 5.3.1 Parse model (`importer/model.py`, built in WP2 so every parser package can start from it)

```python
class SourceKind(StrEnum): PDF_DOCUMENT; CSV_EXPORT; PDF_STATEMENT; MANUAL_CSV

@dataclass(frozen=True)
class ParsedTransaction:
    type: TxnType
    isin: str | None; name: str | None
    time: SourceTime; value_date: date | None
    quantity: Decimal | None; price: Decimal | None; currency: str
    amount: Decimal | None                 # signed booking amount as printed
    amount_eur: Decimal | None
    fx_rate: Decimal | None; fx_source: Literal["document", "ecb", "none"]
    fees_eur: Decimal | None; tax_eur: Decimal | None     # None: the source does not say (statement lines)
    tax_detail: Mapping[str, Decimal]                      # kapitalertragssteuer, solidaritaetszuschlag, kirchensteuer, quellensteuer
    split_new_quantity: Decimal | None
    origin: str | None; source_ref: str | None
    source_kind: SourceKind; evidence: tuple[int, int]      # first and last line numbers in the text

@dataclass(frozen=True)
class ReviewNeeded:
    kind: ReviewKind; message: str; extracted_text: str; fields: Mapping[str, str]

@dataclass(frozen=True)
class ParseResult:
    doc_type: str; parser_id: str; parser_version: int
    transactions: list[ParsedTransaction]; review: list[ReviewNeeded]
    confirmed_holdings: list[tuple[str, Decimal]] = ()      # reserved for a securities account statement parser
```

A parser never raises for a layout problem. It returns what it could read plus a `ReviewNeeded` for what it could not.

#### 5.3.2 PDF path (`importer/tr/`)

Two layers, tested separately (condition 2):

1. `pdf_text.py`: `extract_text(pdf: bytes) -> str`. pdfplumber, page by page, pages joined with a form feed, then normalised: non-breaking spaces to spaces, runs of spaces and tabs collapsed to one space, leading and trailing spaces removed, empty lines removed, Unicode NFC. pdfplumber already returns text this way (checked here: "BUCHUNGSTAG   WERTSTELLUNG" comes back as "BUCHUNGSTAG WERTSTELLUNG", and indentation and empty lines disappear); the normalisation makes it explicit, so the result does not depend on the pdfplumber version. The `.txt` fixtures are written in this normal form, and `make_pdfs.py` refuses one that is not. No parsing here.
2. `classify.py` and `layouts/`: `classify(text: str) -> DocumentParser | ReviewNeeded` and one parser per layout.

```python
class DocumentParser(Protocol):
    parser_id: str            # e.g. "tr.wertpapierabrechnung.de.2023"
    version: int
    doc_type: str             # e.g. "trade_confirmation"
    locale: Literal["de", "en"]
    def detect(self, text: str) -> bool
    def parse(self, text: str) -> ParseResult
```

`classify` runs every registered parser's `detect`. Exactly one match is required. None gives `unknown_layout`; more than one gives `ambiguous_layout`. Both become review items with the full extracted text. `layouts/common.py` holds the shared line grammar: header block detection (the bank address line, `DATUM`, `SEITE`), the ISIN line with or without `ISIN:`, quantity with `Stk.` or `Pcs.`, the `ABRECHNUNG` block (fees and each tax line by name), the `BUCHUNG` block (booking date under `VALUTA`, `WERTSTELLUNG` or `BUCHUNGSDATUM`), and the FX line `Zwischensumme <rate> EUR/<CCY> <amount> EUR`. Personal lines (name, address, IBAN, depot number) are never copied into parsed fields.

Every parser checks that its numbers add up (for a buy: quantity x price + fees = -booking amount, within 0.01). If not, it returns the transaction together with an `amounts_do_not_add_up` review item, and the pipeline holds that transaction back until you resolve the item.

Every parser also reads the document's own order, execution or reference number into `source_ref` when the layout prints one. Matching uses it to tell two documents apart (5.3.4).

#### 5.3.3 CSV path

- `tr/csv_profiles.py`: a CSV profile is data, not code: delimiter, encoding, decimal style, date format, exact header list, column-to-field mapping, and a table from the row type text (`Kauf`, `Sparplan`, `Verkauf`, `Dividende`, `Ausschüttung`, `Zinsen`, `Einzahlung`, `Auszahlung`, `Steuer`, `Gebühr`, `Depotübertrag eingehend`, `Depotübertrag ausgehend`, `Split`) to a transaction type. P0 ships one profile, `tr_csv_synthetic_v1` (section 6.1).
- `tr/csv_parser.py`: `parse_csv(data: bytes) -> ParseResult`. It sniffs the encoding (UTF-8 with or without BOM, then Windows-1252), matches the header exactly against the known profiles, and parses each row (`Referenz` becomes `source_ref`). An unknown header puts the whole file into one `unknown_csv_header` review item showing the header and the first five rows. An unknown row type or an unparsable field gives an `unparsed_row` item for that row only; the other rows still import. It checks every purchase, sale and dividend row as 5.3.2 says: quantity x price, divided by `Wechselkurs` when `Währung` (the currency of the price) is not EUR, plus the fees and taxes for a purchase or minus them for a sale or a dividend, must give `Betrag`, within 0.01 plus the rounding of the printed quantity, price and rate. A row that does not add up is read, with an `amounts_do_not_add_up` item that shows the row under its header and holds that row's transaction back. This also finds an export whose `Betrag` means something else than the profile assumes, for example an amount without the fees. A row that lacks a figure the check needs (the quantity, the price, the amount, or the rate for a price in another currency) is not checked; the pipeline holds back a purchase or sale without its quantity, and any row that moves cash without its amount (5.3.4).
- `manual_csv.py`: a documented manual transaction format (`date;time;type;isin;quantity;price;currency;amount_eur;fees_eur;tax_eur;note`, semicolon, dot decimals) so you can enter anything the parsers cannot read. It has the highest precedence. It is deliberately exempt from the amount check of 5.3.2, for three reasons: it is how you settle an `amounts_do_not_add_up` item with figures you checked yourself, you typed it on purpose, and its price may be in a foreign currency with no rate column, so the check cannot be done for every row. The diff shows each of its transactions before you accept, and the pipeline's missing-field check (5.3.4) still applies to it.
- `confirmed_csv.py`: `isin;quantity;as_of` (semicolon; decimal comma or dot, declared in an optional first line `# decimal=,` or `# decimal=.`), for the holdings you confirm. Without that line a quantity is read only when it can mean one number: `4,5` and `4.5` are read, `1.234` (1234 or 1.234) is refused with a message that says to add the line. Nothing is guessed (QA P0 round 2: the UAT guide and the import page describe the file without that line).

#### 5.3.4 Semantic key, precedence and idempotency (`importer/keys.py`, `pipeline.py`)

Two levels make re-import idempotent (condition 5):

1. **File level.** The SHA-256 of the file bytes. A file already imported in a non-discarded batch is recorded as `duplicate_file` and not parsed again. One exception: a file whose earlier import failed or found no parser (`unknown_layout`, `ambiguous_layout`, `unknown_csv_header`) is classified again, so a parser added since then takes effect. If a parser now recognises it, it is parsed in the new batch; when that batch is accepted, the earlier `imports` row is marked `reparsed` and its review item is closed as superseded. If not, it counts as `duplicate_file` like any other known file.
2. **Transaction level.** Different files report the same transaction (the PDF note, the CSV row, the account statement line). Each parsed transaction, called a **candidate**, gets two keys.

The **semantic key** is built only from fields every source carries:

- Cash-moving types: `type | isin or "-" | local booking date (Europe/Berlin) | signed amount booked on the EUR cash account, in cents | EUR`. The amount is always the booked EUR amount, never a foreign-currency figure. The T10 note shows its details in USD, but its key uses the 0.94 EUR it books, as the statement line does.
- Types without cash (split, transfer in and out): `type | isin | local date | quantity` (for a split, the new total).
- The time of day is not in the key, because account statements carry only a date.
- The booking date is the execution date for trades, the document date (`DATUM`) for dividend, interest and tax notes, `BUCHUNGSTAG` for statement lines, and `Datum` for CSV rows. Where these differ by a few days, the near-date rule below catches it.

The **report key** says which report a candidate is within its source kind, so the same report arriving in another file is recognised:

- CSV export, manual CSV and account statement (files that list many transactions): `kind | semantic key | time of day or "-" | ordinal`. The ordinal counts the rows with that key and time inside the file: 1 for the first, 2 for a second identical row. A re-exported CSV, or a later statement, that covers the same days has the same rows, so its rows get the same report keys and map to the same transactions.
- PDF document (one transaction per file): `pdf_document | source_ref`, the order, execution or reference number the document prints. For a layout that prints none: `pdf_document | text | <SHA-256 of the extracted text>`.

**Matching.** Candidates are matched one at a time against the accepted, staged and held transactions of the portfolio. Within a batch, files are taken in a fixed order (precedence from high to low, then file name, then file hash) and candidates in line order, so one batch gives the same result whatever order you name the files in. For a candidate of source kind K:

- a. **Same report.** If a transaction already has a source with the same report key, the candidate merges into it.
- b. **Same key.** Otherwise it merges into a transaction with the same semantic key that has no source of kind K yet. This is the one-to-one rule: a transaction holds at most one report of each source kind. If several qualify, it takes the lowest occurrence among those that agree on every detail both sides carry (time of day, quantity, fees); if none agrees, the lowest occurrence.
- c. **Near date.** Otherwise it looks for transactions whose key differs only in the date, by up to 3 calendar days, and that have no source of kind K. If they all share one date, and either the candidate is a statement line or those transactions are known only from statement lines, the candidate merges into the lowest occurrence automatically, and the diff shows both dates. Real statements show the booking day, which can be a settlement date after the trade; without this rule every trade would wait for you at UAT. Any other near-date match is held back as `possible_duplicate` until you choose `merge` or `keep-both`.
- d. **New.** Otherwise it becomes a new transaction with the next free **occurrence** for its semantic key: 1 for the first, 2 for a second transaction with the same key.

`content_hash = sha256(semantic_key | occurrence)`; unique per portfolio. A transaction's key always follows its merged fields. When a merge gives it a booking date from a higher-precedence source (a CSV or PDF date replacing a statement date), its key and occurrence are recomputed, so later files match it exactly.

Rule b keeps two identical savings-plan executions on one day as two transactions. Each has its own document with its own execution number. The second document cannot merge into the transaction the first one already reports, so it takes occurrence 2. A CSV that lists both rows then fills both transactions, in whichever order the files arrive. The fixture pair in 6.2 and `tests/importer/test_same_day_savings_plans.py` check this.

**Precedence rule.** When two sources report the same transaction, one transaction is kept and every source is listed in `transaction_sources`. Each field is taken from the source with the highest precedence that has it:

| Precedence | Source | Why |
|---|---|---|
| 4 | manual CSV | You typed it on purpose |
| 3 | PDF document (trade confirmation, dividend, tax, interest, split note) | Carries fees, taxes, time of day and the document FX rate |
| 2 | CSV export | Complete list, fewer details |
| 1 | PDF account statement | Date and amount only |

If sources of two different kinds disagree on a field both carry (for example quantity), the higher-precedence value is kept and a `field_conflict` review item names both values. Between two files of the same kind (a re-export, a corrected manual CSV), the later import wins without a review item, because it is the newer report. Review items have a `dedupe_key`, so re-importing never repeats them.

**Known only from a statement line.** A statement line carries a date, a type, an ISIN and an amount, but no quantity, fees or taxes. A buy or sell known only from statement lines is held back with a `missing_field` review item that names the quantity. It stays out of lots, holdings and value, and the diff lists it under held back. When a PDF, CSV or manual CSV row for it arrives (rule b or c), the item is closed as superseded and the transaction is released. A deposit, withdrawal, interest, dividend or tax line known only from a statement is kept, with fees and taxes unknown.

**Without its amount.** A transaction that moves cash (every type but a split and the two transfers) and has no booking amount is held back with a `missing_field` item that names the amount, whatever file it came from: nothing is booked without it. A purchase would open a lot whose cost nobody knows, and a dividend would book nothing. Its semantic key has no amount (`?`), so a later file that gives the amount cannot merge into it. That file, the document or a manual CSV row, adds the transaction with its amount, and you dismiss the item; the item says so. `resolve --use-parsed` is refused for it.

**Order does not matter.** Matching is one-to-one per source kind, fields follow precedence, and review items are checked again on every stage (5.3.5). So the result depends on which files were imported, not on their order or on repeats. The property test in 7.2 states it as: any sequence of imports, with repeats, whose files together form a set S gives the same accepted ledger and the same open review items as one import of S. Two cases are outside the property, on purpose. A re-export or corrected file that changes a value wins because it came later. A `possible_duplicate` waits for your decision, and until then the accepted side depends on which file came first; unit tests check that both orders reach the same ledger once you decide. The golden files contain neither case.

#### 5.3.5 Review queue (`importer/review.py`)

Kinds: `unknown_layout`, `ambiguous_layout`, `unknown_csv_header`, `unparsed_row`, `missing_field`, `amounts_do_not_add_up`, `field_conflict`, `possible_duplicate`, `missing_cost_basis`, `oversell`, `split_unclear`, `corporate_action`, `invalid_isin`. Every item stores the extracted text or the raw row, the fields read so far, and the transaction it concerns, if any.

Who raises what:

- The parsers and the classifier raise the layout, header, row, field, amount, corporate action and ISIN kinds (5.3.2, 5.3.3).
- The pipeline raises `field_conflict` and `possible_duplicate` while matching (5.3.4), and `missing_cost_basis` at staging for every transfer in that has no cost input. The ledger only marks such a lot `cost_missing`.
- The ledger reports `oversell` and `split_unclear` (5.5). The pipeline turns them into items each time it builds the lot book, for the diff and on accept.

**Checked again.** Items are checked again on every stage, accept, discard and resolution. An item whose condition no longer holds is marked `resolved` with the resolution `superseded by <file name>` (the file whose data removed the problem), and a transaction it held back is released into that file's batch. A stage only lists these closures in the diff (`review_closed`); accept applies them, so a discarded batch closes nothing. The conditions:

| Kind | Stays open while |
|---|---|
| `missing_field` | the merged transaction still lacks the field |
| `amounts_do_not_add_up` | the fields in use still come from the document or CSV row whose numbers do not add up (a report of higher precedence for the same transaction supersedes it: a manual CSV row, or the PDF document for a CSV row) |
| `field_conflict` | the sources still disagree on the field and no manual CSV row sets it |
| `possible_duplicate` | the held candidate still has a near-date match and no exact one |
| `missing_cost_basis` | the transfer in still has no cost input (`transfers set-cost` resolves it with `cost entered`) |
| `oversell`, `split_unclear` | the rebuilt lot book still reports it |
| `unknown_layout`, `ambiguous_layout`, `unknown_csv_header` | no parser recognises the file (5.3.4, file level) |

The other kinds stay open until you resolve or dismiss them. Items raised by a discarded batch are closed as `batch discarded`. A dismissed item stays dismissed; its `dedupe_key` stops it from coming back.

**Resolutions.** `dismiss --reason`, `resolve --merge | --keep-both` (duplicates), `resolve --use-parsed` (keep a held-back transaction as parsed), `transfers set-cost` (cost basis), or importing a manual CSV for anything a parser could not read. A resolution applies at once. A transaction it releases joins its batch: it is accepted at once if that batch was already accepted, otherwise with the batch. Lots and values are then rebuilt.

`anonymise.py` provides `anonymise_text(text) -> str`, which masks the address block, IBANs, depot and order numbers, so you can share a new layout's text (never the file) to turn it into a fixture.

#### 5.3.6 Pipeline and reconciliation

```python
def stage_files(conn, portfolio_id: int, paths: Sequence[Path], *, clock: Clock,
                extract: Callable[[bytes], str] = extract_text) -> BatchSummary
def build_diff(conn, batch_id: int, *, confirmed: Sequence[ConfirmedHolding] | None, as_of: date | None) -> ReconciliationDiff
def accept_batch(conn, batch_id: int, *, clock: Clock) -> AcceptResult   # staged -> accepted, closures, lots, then snapshots and values
def discard_batch(conn, batch_id: int) -> None
```

`stage_files` copies each file to `uploads/`, detects its kind (PDF by magic bytes, CSV by extension and content), parses it, matches the candidates (5.3.4), stores new transactions as `staged` or `held`, adds sources, opens review items (including `missing_cost_basis` for every transfer in without a cost input), and checks the open items again (5.3.5). The text extractor is a parameter, so the property test can pass one that caches by file hash.

`ReconciliationDiff` contains: per-file results; counts (`files`, `duplicate_files`, `candidates`, `new`, `merged`, `already_known`, `held_back`, `completed`, `review_new`, `review_closed`); the new, merged, held-back and completed transactions (completed means held back before and released by this import); holdings before (accepted only) and after (accepted plus staged, held ones left out) per ISIN, computed with the ledger's quantity timeline (5.5), so splits and transfers in count; and, when confirmed holdings are given, per ISIN the computed quantity, your quantity, the difference and `match`, `mismatch`, `missing_in_import` or `missing_in_confirmed`. `as_of` defaults to today (3.3).

Only one batch can be staged at a time: `pg import` and `POST /portfolio/imports` refuse to start another, with a message that asks you to accept or discard the staged one first. Nothing touches accepted data until `accept_batch`. It marks the batch accepted, applies the closures, rebuilds the lot book from all accepted transactions (replacing `lots` and `disposals` in one database transaction, and turning ledger issues into review items), then the snapshots and the value series (5.7), and sets `last_import_at`. `pg status` and `GET /portfolio` show a reminder when `last_import_at` is more than 30 days before today (spec 3.10).

### 5.4 `http`: the injectable HTTP layer

```python
@dataclass(frozen=True)
class HttpRequest:  method: str; url: str; params: Mapping[str, str]; headers: Mapping[str, str]; body: bytes | None = None
@dataclass(frozen=True)
class HttpResponse: status: int; headers: Mapping[str, str]; body: bytes

class HttpClient(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...

class NetworkHttpClient:   # httpx; 20 s timeout; 3 retries with backoff on 5xx and timeouts; 1 request per second per host; User-Agent "playground/<version>"
class ReplayHttpClient:    # serves responses from <dir>/index.yaml; raises UnrecordedRequestError for anything else
class RecordingHttpClient: # wraps the network client; writes responses and index entries to a directory; strips token and apikey parameters and headers
def make_http_client(settings: Settings) -> HttpClient
```

Every data client takes an `HttpClient` in its constructor and never creates one. `index.yaml` matches on method, URL, sorted query parameters without secrets, and a body hash. The setting `http_mode` is `network` (the default), `replay` or `record`; the CLI global options `--http-replay DIR` and `--http-record DIR` select the last two (environment variables `PG_HTTP_REPLAY`, `PG_HTTP_RECORD`). No mode, option, help text or error message uses the word "live" (spec section 7 keeps it for live news). Recorded directories go under the data directory, which is ignored by git.

A request that gets no response at all raises `NoResponseError`, never an `httpx` exception: `NetworkError` from `NetworkHttpClient` when the connection fails, a proxy refuses it, or no answer comes after the retries (a failed connection is not retried), and `UnrecordedRequestError` from `ReplayHttpClient`. Each data client turns it into its own error with a plain message that says what to do instead (6.4, 6.6, 6.7). `pg prices fetch` and `pg benchmarks fetch` report each symbol that could not be fetched, fetch the rest, and then stop with exit status 1.

### 5.5 `ledger`: FIFO lots and holdings

The ledger is pure: it depends only on `core`, never reads or writes the registry, and gives the same result for the same input. The pipeline feeds it the accepted transactions (plus the staged ones, for the diff), leaves held ones out, and stores its output (5.3.6).

```python
@dataclass(frozen=True)
class LedgerTxn:        # an accepted or staged transaction, as the ledger sees it
    id: int; type: TxnType; isin: str | None
    ts_utc: datetime                                   # booking time
    quantity: Decimal | None; amount_eur: Decimal | None; fees_eur: Decimal | None
    split_new_quantity: Decimal | None
    cost_input: CostInput | None                       # transfer_in: the acquired_on and cost_eur you entered

@dataclass(frozen=True)
class LedgerIssue:      # the pipeline turns these into review items (5.3.5)
    kind: Literal["oversell", "split_unclear"]; txn_id: int; isin: str; message: str

@dataclass
class LotBook:
    lots: list[Lot]; disposals: list[Disposal]; issues: list[LedgerIssue]
    def holdings(self, as_of: date) -> dict[str, Decimal]            # lots count from their booking date
    def open_cost(self, as_of: date) -> dict[str, Decimal | None]    # None when an open lot has cost_missing

def build_lots(txns: Sequence[LedgerTxn]) -> LotBook       # pure, deterministic
def quantity_timeline(txns: Sequence[LedgerTxn]) -> dict[str, list[tuple[date, Decimal]]]   # by booking date, splits applied
```

Rules:

- Order: by booking time (`ts_utc`), then splits before trades at the same timestamp, then transaction id.
- **Booking date and acquisition date.** Holdings and open cost as of a date count a lot from its booking date (`booked_ts`: the trade time, or the day the transfer was booked in). FIFO order uses the acquisition date (`opened_ts`: the trade time, or the `acquired_on` you entered for a transfer in). So the golden cost basis on 2024-05-31 (5,114.00) leaves out the ALV lot: it was acquired in 2020 but booked in on 2024-06-20.
- **Buy**: opens a lot. Cost in EUR = the absolute booking amount in EUR, which includes the purchase fee (quantity x price + fees).
- **Sell**: consumes the open lots of that ISIN that were booked on or before the sell, oldest acquisition first. A lot booked later is never consumed, even if its acquisition date is older. Proceeds = gross amount minus fees (in EUR). Each disposal gets its share of quantity, cost, proceeds and fees through `allocate`, so the pieces add up exactly. Realised gain = proceeds minus cost, before tax. Taxes withheld are stored on the transaction, not in the cost basis.
- **Partial sell**: the consumed lot keeps `quantity_open` and `cost_eur_open` reduced by exactly the allocated shares.
- **Split**: every open lot of the ISIN has its quantity multiplied by the ratio; cost is unchanged. The ratio is `split_new_quantity / quantity held just before the split`. If the ratio is not a clean ratio of small integers (up to 1:1000 after rounding the quantity to 6 decimals), or nothing was held, the ledger reports `split_unclear` and does not apply the split.
- **Transfer in**: opens a lot with `origin = transfer_in`, booked on the transfer's date, with quantity from the document and cost and acquisition date from `cost_basis_inputs`. FIFO order uses the acquisition date you entered, as German tax rules do. Without your input the lot has `cost_missing = 1` and is ordered by its booking date; quantities and value stay correct, and cost and realised figures for that lot show as unknown. The review item for the missing cost comes from the pipeline at staging (5.3.5), not from the ledger.
- **Transfer out**: consumes lots FIFO like a sell, with `kind = transfer_out` and no proceeds or realised gain.
- **Oversell**: a sell larger than the open quantity consumes what exists and reports `oversell`; lots never go negative.
- `accept_batch`, review resolutions and `transfers set-cost` rebuild the whole lot book from all accepted transactions and replace `lots` and `disposals` in one database transaction.

### 5.6 `marketdata`: instrument master, prices, FX, benchmarks

- `instruments.py`: `upsert_from_documents(isin, name)` creates an instrument as `unmapped` and never sets a price symbol. `set_mapping(isin, source, symbol, currency, note, changed_by)` validates the source name and currency (EUR, GBX for pence, or a currency the ECB publishes a reference rate for, so every mapped listing can be valued in EUR; anything else is refused at once, and "GBp" is refused rather than read as pence or pounds), writes the log row and sets `mapping_status = confirmed`. `load_mapping_file(path)` applies `isin;data_source;data_symbol;currency;note` rows through `set_mapping` and creates instruments that do not exist yet. `list_instruments()` returns the mapping with status.
- `stooq.py`: `StooqClient(http).daily_bars(symbol, start, end) -> PriceSeries` (section 6.4).
- `manual_prices.py`: `load_price_file(path) -> list[PriceSeries]` (section 6.5).
- `ecb.py`: `EcbClient(http).history() -> FxTable` and `load_fx_file(path) -> FxTable` (section 6.6).
- `openfigi.py`: `OpenFigiClient(http, api_key=None).suggest(isin) -> list[ListingSuggestion]` (section 6.7). Output is printed only. It runs only when you call `pg instruments suggest`.
- `lake.py`: `PriceStore(data_dir)` with `upsert(series)`, `closes(source, symbol) -> list[PricePoint]`, `latest_on_or_before(source, symbol, day) -> PricePoint | None`; `FxStore(data_dir)` with `upsert(table)`, `rate_on_or_before(currency, day) -> FxPoint | None`. Both write a manifest after each write.
- `benchmarks.py`: `load_benchmarks(path) -> list[Benchmark]`, `fetch_benchmarks(...)`, and `benchmark_series(id, start, end, currency) -> list[SeriesPoint]` (EUR series by dividing by the as-of ECB rate).

`PriceSeries` carries `source`, `symbol`, `currency`, `adjustment` and a list of `PricePoint(date, open, high, low, close, volume)`.

### 5.7 `valuation`: the daily value series

```python
@dataclass(frozen=True)
class Flag: isin: str | None; kind: FlagKind; detail: str
# FlagKind: unmapped, missing_price, stale_price, missing_fx, stale_fx, dividend_adjusted_prices, cost_basis_missing,
#           near_split_raw_prices, price_basis_mismatch

@dataclass(frozen=True)
class DayValue:
    date: date; value_eur: Decimal; cost_basis_eur: Decimal | None; complete: bool
    holdings: list[HoldingValue]; flags: list[Flag]

def value_series(book: LotBook, timeline, instruments, prices: PriceStore, fx: FxStore,
                 start: date, end: date, *, stale_after_days: int = 5) -> list[DayValue]
```

The rules, which also go into the module docstring and the UAT guide (condition 7):

- **Calendar.** One value per weekday (Monday to Friday) from `start` to `end`. Weekends are skipped; exchange holidays are not, because a holiday on one exchange is a trading day on another.
- **Range.** `pg value` without `--from` and `--to`, and the rebuild after an accept, a resolution or `transfers set-cost`, cover the weekdays from the first accepted transaction's booking date to the last weekday on or before today (3.3). `pg status`, `GET /portfolio` and the page's chart use that range; the latest value is the one on its last day. With `PG_TODAY=2024-12-31` the golden range is 2024-01-02 to 2024-12-31.
- **Price basis.** The daily close in the listing currency of the mapped symbol. Each stored series declares its `adjustment`: `raw`, `split` (split-adjusted) or `split_dividend` (split and dividend adjusted). For Stooq the plan assumes `split_dividend` until UAT confirms it (section 6.4).
- **Quantity for pricing.** For a `raw` series: the quantity held on that day. For a `split` or `split_dividend` series: the quantity held times the product of all later split ratios in the ledger, so that quantity and price are on the same share basis. Because this product is the same before and after the split, the value does not depend on the exact day the split was booked. For `split_dividend` series a `dividend_adjusted_prices` info flag says past values are slightly understated. For `raw` series, days within 3 weekdays of a split get `near_split_raw_prices`.
- **As-of alignment.** For each day and instrument: the latest close dated on or before that day. For each foreign currency: the latest ECB rate dated on or before that day. The date actually used is stored in `holdings_snapshots`.
- **Staleness.** A price or rate more than `stale_after_days` (default 5) calendar days older than the valuation day is still used, and is flagged `stale_price` or `stale_fx`.
- **Missing.** No close on or before the day: the holding is left out of that day's value and flagged `missing_price`. The same for a missing rate (`missing_fx`). An instrument without a confirmed mapping is flagged `unmapped` and left out. A day with any left-out holding has `complete = false`. Nothing raises.
- **Totals.** Day total = sum of unrounded holding values; rounded to cents only for output.
- **Fallback source.** Any instrument can be mapped to `data_source = manual` and priced from a file you provide.
- **Price basis check.** For an instrument priced from a `split` or `split_dividend` series, the price of its most recent buy or sell in the 365 days before the end of the range (in EUR, as Trade Republic prints it) is compared with the close on the day of that trade, converted to EUR at that day's ECB rate and multiplied by the ledger's split factor after that day. If the two differ by more than a factor of 1.4, the instrument gets `price_basis_mismatch` on every day of the range, as a warning; `complete` does not change. The usual cause is a split that happened after your last import: the source has already adjusted the whole history, but the ledger has not booked the split, so the value is off by the split ratio. Importing the split document clears the flag. Older trades are not checked, because dividend adjustment alone can move old prices by that much.
- **Known limit.** An instrument with no buy or sell in the last 365 days is not checked, so a split the ledger has not seen can go unnoticed for it. The UAT guide says to import split documents before trusting a value.

`pg value` computes the series, stores `holdings_snapshots` and `portfolio_values`, prints a summary with all flags, and can write a CSV.

### 5.8 `api`: FastAPI backend

Binds to `127.0.0.1` only. All money and quantities are JSON strings (for example `"6146.85"`). Errors are `{"error": {"code": "...", "message": "..."}}` in plain English.

| Method and path | Purpose |
|---|---|
| `GET /health` | `{"status": "ok", "version": ..., "real_orders": false, "portfolio_read_only": true}` |
| `POST /portfolio/imports` | Multipart `files`; stages a batch; returns the diff (201) |
| `GET /portfolio/imports/{id}` | The batch and its diff |
| `POST /portfolio/imports/{id}/confirmed-holdings` | Multipart CSV or JSON rows plus `as_of`; returns the diff with the comparison |
| `POST /portfolio/imports/{id}/accept` | Accepts a staged batch (409 if not staged) |
| `POST /portfolio/imports/{id}/discard` | Discards a staged batch |
| `GET /portfolio` | Name, currency, last import, reminder (clock of 3.3), open review count, latest value (the last day of the range in 5.7) and completeness |
| `GET /portfolio/holdings?as_of=` | Holdings with quantity, cost, value, price evidence, flags |
| `GET /portfolio/transactions?from=&to=&type=&limit=&offset=` | Paginated transactions with their sources |
| `GET /portfolio/lots` | Open lots and disposals |
| `GET /portfolio/value?from=&to=` | The value series with flags |
| `GET /portfolio/review?status=` | Review items |
| `GET /instruments`, `PUT /instruments/{isin}/mapping`, `GET /instruments/{isin}/mapping-history` | Instrument master |
| `GET /benchmarks`, `GET /benchmarks/{id}/series?from=&to=&currency=` | Benchmark series |

The paths `/portfolio/imports`, `/portfolio`, `/portfolio/holdings`, `/portfolio/transactions` and `/portfolio/value` are the spec 3.6 paths; the others go beyond spec 3.6 and are named in 3.1. When `web/dist` exists, `app.py` serves it at `/`.

### 5.9 `cli`: the `pg` command

Global options: `--data-dir PATH` (env `PG_DATA_DIR`, default `./data`), `--http-replay DIR`, `--http-record DIR`. The environment variable `PG_TODAY` (`YYYY-MM-DD`) fixes today's date (3.3). Every read command takes `--json`. Exit status 0 on success, 1 on a usage or input error, 3 when `reconcile --strict` finds a mismatch. `pg --version` prints the version and the line "No real orders. Your portfolio is read-only."

| Command | Does |
|---|---|
| `pg init` | Creates the data directory, registry and default portfolio |
| `pg status` | Portfolio summary, last import and reminder, open review items, latest value |
| `pg import FILE...` | Stages one batch; prints the diff |
| `pg imports list`, `pg imports show BATCH` | Batches and their diffs (`latest` is accepted for BATCH) |
| `pg reconcile BATCH [--confirmed FILE] [--as-of DATE] [--strict]` | The diff with the confirmed-holdings comparison |
| `pg accept BATCH`, `pg discard BATCH` | Accept or discard a staged batch |
| `pg review list [--status]`, `show ID`, `dismiss ID --reason`, `resolve ID --merge / --keep-both / --use-parsed`, `export ID --anonymise --out FILE` | The review queue |
| `pg transfers list`, `pg transfers set-cost --isin --acquired --cost-eur [--txn]` | Cost basis for transfers in |
| `pg transactions`, `pg holdings [--as-of]`, `pg lots [--isin]` | Ledger views |
| `pg instruments list`, `map ISIN --source --symbol --currency [--note]`, `map --file FILE`, `suggest ISIN`, `history ISIN` | Instrument master |
| `pg prices fetch [--from] [--to] [--isin]`, `prices import-file FILE`, `prices show SYMBOL` | Prices for mapped instruments |
| `pg fx fetch`, `fx import-file FILE`, `fx show --currency --on` | ECB rates |
| `pg benchmarks fetch [--from] [--to]`, `benchmarks series ID [--currency] [--from] [--to]` | Benchmarks |
| `pg value [--from] [--to] [--csv FILE]` | The value series |
| `pg serve [--port 8765]` | The API and the import page on 127.0.0.1 |

### 5.10 `web`: the minimal import page

One page, plain English, built with Vite, React and TypeScript under `web/`:

- A fixed red badge at the top: **NO REAL ORDERS · portfolio read-only** (`data-testid="no-real-orders-badge"`).
- Upload: a file input for several CSV and PDF files and an Upload button (`POST /portfolio/imports`).
- The diff: counts (new, merged, already known, held back, and files skipped as already imported), the new, merged, already-known and held-back transactions, review items with their file and reason, holdings before and after, and an optional confirmed-holdings upload with the match table. Types and match rules are shown in plain words, never as ids. Every table scrolls sideways inside its own box on a narrow screen, so the page itself never does.
- Accept and Discard buttons. Accept is worded "Accept these transactions into my portfolio copy".
- After accept: the holdings table (ISIN, name, quantity, cost, value in EUR, mapping symbol and status, flags) and a small SVG line chart of `GET /portfolio/value` over the range of 5.7.
- No other navigation; the P1 screens replace this page's shell later.

Vite proxies API calls to `127.0.0.1:8765` in development; in UAT the built page is served by `pg serve`.

## 6. External data and documents

Real files from your Trade Republic account are seen only at UAT, on your Mac. Everything in this repository is synthetic or anonymised. The parsers are fixture-driven: each supported layout is a set of golden files, so a new layout is a new golden file plus a small parser change, not a rewrite. Nothing in the test suite touches the network.

### 6.1 Trade Republic CSV export

- **Client structure.** A file reader, no network. `parse_csv(bytes)` with declarative profiles (5.3.3).
- **What we know.** Trade Republic's app and web interface offer a CSV transaction export. Its exact columns could not be checked from this container (the export needs a logged-in account, and there is no public format document). Spec section 13 already lists "which export formats you actually have" as open.
- **Synthetic profile `tr_csv_synthetic_v1`.** It imitates common German bank CSV conventions: UTF-8 with BOM, semicolon delimiter, decimal comma, thousands dot, `dd.mm.yyyy` dates, German column names: `Datum;Uhrzeit;Typ;ISIN;Name;Anzahl;Kurs;Betrag;Gebühren;Steuern;Währung;Wechselkurs;Referenz`. `Betrag` is the signed booking amount; `Uhrzeit` may be empty.
- **Fixtures.** `tests/fixtures/tr/csv/tr_csv_synthetic_v1/`: `all_types.csv` (every row type), `bom_and_crlf.csv`, `windows1252.csv`, `empty_time.csv`, `unknown_row_type.csv`, `bad_number.csv`; `tests/fixtures/tr/csv/unknown_header.csv`; each with an `.expected.json`.
- **At UAT.** Your first real CSV will almost certainly hit `unknown_csv_header`. The review item shows the header and first rows. We then add a profile `tr_csv_<date>_v1` and a golden file made from an anonymised copy of a few rows. The synthetic profile stays as a regression test.

### 6.2 Trade Republic PDF documents

- **Client structure.** `extract_text(bytes)` then `classify(text)` then one layout parser (5.3.2).
- **Where the layouts come from.** The public test fixtures of the Portfolio Performance project (`name.abuchen.portfolio.tests/.../datatransfer/pdf/traderepublic/*.txt` on GitHub, read in September 2026) show the text of real, anonymised Trade Republic documents. We read them to learn the layouts only. No code and no fixture text is copied. Our fixtures are written from scratch with invented names, numbers and IBANs, and `tests/fixtures/MANIFEST.yaml` records which layout each one imitates.

| Parser id | Document (German title) | Layout features the fixtures imitate |
|---|---|---|
| `tr.wertpapierabrechnung.de.2019` | Trade confirmation, buy or sell (WERTPAPIERABRECHNUNG) | `KURS` column; "Market-Order Kauf am dd.mm.yyyy, um hh:mm Uhr an der Lang & Schwarz Exchange"; ISIN alone on a line; `VALUTA`; fee line `Fremdkostenzuschlag` |
| `tr.wertpapierabrechnung.de.2023` | Same, newer layout | `PREIS` column; "um hh:mm Uhr (Europe/Berlin)"; `ISIN:` prefix; `WERTSTELLUNG`; quantity with thousands dot ("1.000 Stk."); limit and stop orders |
| `tr.sparplan.de` | Savings plan execution (WERTPAPIERABRECHNUNG SPARPLAN) | "Sparplanausführung am dd.mm.yyyy"; fractional quantity ("0,4534 Stk."); no fee block; booking date as dd.mm.yyyy or yyyy-mm-dd |
| `tr.settlement.en.2023` | Trade confirmation in English (SECURITIES SETTLEMENT) | "Buy on dd.mm.yyyy at hh:mm (Europe/Berlin)"; "Pcs."; dot decimals; `External cost surcharge` |
| `tr.dividende.de` | Dividend and fund distribution (DIVIDENDE, AUSSCHÜTTUNG) | `ERTRÄGNIS` column; "Ex-Tag"; FX line "Zwischensumme 1,102 EUR/USD 5,11 EUR"; tax lines Quellensteuer, Kapitalertragssteuer, Solidaritätszuschlag, Kirchensteuer |
| `tr.steuer.de` | Tax correction and advance lump sum (STEUERKORREKTUR, VORABPAUSCHALE) | Tax-only `ABRECHNUNG`; positive or negative total; FX line |
| `tr.zinsen.de` | Interest statement (ABRECHNUNG ZINSEN) | "zum dd.mm.yyyy"; `BUCHUNGSDATUM`; "GUTSCHRIFT NACH STEUERN" |
| `tr.split.de` | Split (SPLIT) | "NR. BUCHUNG WERTPAPIER BETRAG" table; "Einbuchung ... n Stk."; may show only the new total |
| `tr.kontoauszug.de.2023` | Account statement (KONTOAUSZUG, BUCHUNGEN) | "BUCHUNGSTAG / WERTSTELLUNG BUCHUNGSTEXT BETRAG IN EUR"; two-line entries ("Ausführung Handel Direktkauf Kauf <ISIN> <name>" then the value date) |
| `tr.kontoauszug.de.2024` | Account statement, newer layout (UMSATZÜBERSICHT) | Date as "01 Apr. 2024"; `TYP` column; amounts with "€"; incoming, outgoing and balance columns |

  Documents recognised but not converted in P0 go to the review queue with kind `corporate_action`: exchange or subscription (UMTAUSCH/BEZUG) and other corporate actions. Anything not recognised (for example a cost information sheet) is `unknown_layout`.

- **Fixtures.** `tests/fixtures/tr/text/<parser id>/<case>.txt` is the source of truth, with `<case>.expected.json` beside it. Text files are in the normal form of 5.3.2: single spaces, no indentation, no empty lines, pages separated by a form feed. At least two cases per parser (the golden portfolio documents plus one variant each: sell with taxes, USD dividend, split with only the new total, 2024 statement with German month names and "€" amounts, a summer-time and a winter-time trade). Every case that prints an order, execution or reference number has it in `source_ref` in its expected JSON.
- **PDF generation.** `tests/fixtures/tr/pdf/<case>.pdf` are generated from the text files by `tests/fixtures/tr/make_pdfs.py` and committed. The script uses reportlab with the standard Helvetica font, whose WinAnsi encoding covers umlauts, ß and "€" (fpdf2's core fonts cannot print "€", so the 2024 statement could not be written with them). It draws one text line per fixture line, sets `invariant=1` so a second run gives the same bytes, and refuses a `.txt` that is not in normal form. The PDF layer test extracts each PDF and compares with its `.txt`, so the two layers are tested separately. Negative cases: `unknown_cost_information.txt`, `truncated_trade.txt` (missing `GESAMT`), `amounts_off_by_one_euro.txt`, `two_layouts_mixed.txt`.
- **Same-day savings plans.** `tests/fixtures/tr/text/tr.sparplan.de/same_day_a.txt` and `same_day_b.txt` are two savings-plan executions for the same ETF on the same day and for the same amount, different only in their execution numbers. `make_pdfs.py` writes them to `tests/fixtures/idempotency/same_day_savings_plans/`, together with `same_day_a_copy.pdf` (the text of A with a different PDF title, so the bytes differ). A `transactions.csv` beside them lists both executions. These files are not golden portfolio inputs; 5.3.4 and 7.2 say what they test.
- **New layouts.** A new layout means: anonymise the text with `pg review export ID --anonymise`, add it as a `.txt` fixture, write its expected JSON by hand, then extend or add a parser until the golden test passes.

### 6.3 Account statement PDFs as a reconciliation source

The statement parsers produce `source_kind = pdf_statement` transactions with precedence 1. They add no information beyond date, type, ISIN and amount, but they prove completeness: in the golden portfolio every statement line must match a known transaction, and a statement line with no match becomes a new transaction (for example a deposit missing from the CSV). A buy or sell known only from a statement line is held back until its quantity is known (5.3.4). Each line of the golden H1 statement carries the same date as the matching PDF or CSV entry, so every line matches exactly. A real statement may show a later booking day; rule c of 5.3.4 merges such a line when it has exactly one candidate date.

### 6.4 Price source: Stooq

- **Client.** `StooqClient(http: HttpClient, base_url="https://stooq.com/q/d/l/")`, request `GET ?s=<symbol>&d1=YYYYMMDD&d2=YYYYMMDD&i=d`. The response is CSV `Date,Open,High,Low,Close,Volume` with dot decimals. The body "No data" means no data for that symbol (`SourceNoData`, reported per instrument, not fatal). An HTML body, a status other than 200, or no response at all (no network, a proxy that refuses, no answer after the retries) means the source is unavailable (`SourceUnavailable`, with a message pointing to the manual price file; for a benchmark, which is read from its configured series, the message says to try again instead). Symbols are the ones you mapped (`sap.de`, `aapl.us`, `^spx`); the client never builds a symbol from an ISIN.
- **Fixtures.** `tests/fixtures/http/stooq/`: synthetic series for the five golden symbols and `^spx` over 2024, generated by `tests/fixtures/http/make_series.py` from anchor prices (see Anchors below) by straight lines between anchors, skipping Xetra holidays (1 Jan, 29 Mar, 1 Apr, 1 May, 24 to 26 Dec, 31 Dec 2024) for `.de` symbols and NYSE holidays for `.us` symbols; plus `no_data.txt`, `html_block.html`, `http_500`. `index.yaml` maps each request to its file. The Stooq host is not reachable from this container, so these are modelled on the documented format, not recorded.
- **Anchors.** `make_series.py` draws straight lines between anchor closes on the trading days of each symbol, rounded to 2 decimals. The anchors are the valuation closes of 1.2 (for example SAP 170.00 on 2024-05-31 and 236.00 on 2024-12-30), one close on 2024-01-02, and on each golden trade date the document price converted to the listing currency at that day's fixture ECB rate (for NVDA divided by 10 before 2024-06-10, because the series is split-adjusted). So the price basis check of 5.7 finds no mismatch in the golden portfolio. The anchors are listed in the script and in the golden README, and `tests/marketdata/test_fixture_series.py` checks that every generated series passes through them.
- **Assumptions to confirm at UAT.** The response format; the symbol convention for Xetra (`.de`); and the adjustment basis (the plan assumes `split_dividend`). The UAT guide checks NVIDIA around the 10 June 2024 split and a dividend date. If Stooq refuses requests, the manual price file is the fallback, and recording one real response with `--http-record` gives us a real fixture.

### 6.5 Price source: manual price file

- **Format.** `date;data_symbol;close;currency;adjustment`, semicolon, dot decimals, ISO dates, `#` comment lines. `adjustment` is `raw`, `split` or `split_dividend`. The file is strict: an unknown header, a bad number or a date twice for one symbol is refused with a message naming the line.
- **Fixtures.** `tests/fixtures/manual_prices/prices_ok.csv`, `prices_bad_header.csv`, `prices_duplicate_date.csv`, and the golden `manual_prices_allianz.csv` (weekdays from 2024-06-20 to 2024-12-20, basis `raw`).

### 6.6 FX source: ECB reference rates

- **Client.** `EcbClient(http, url="https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip")`. The zip holds one CSV with a `Date` column and one column per currency, `N/A` for missing values and a trailing comma. Rates are parsed as `Decimal` and stored per currency and date. `pg fx import-file` accepts the same CSV unzipped, as a fallback.
- **Fixtures.** `tests/fixtures/http/ecb/eurofxref-hist.zip`, synthetic, 2024, USD and GBP columns, TARGET holidays skipped (1 Jan, 29 Mar, 1 Apr, 1 May, 25 and 26 Dec), anchor values 1.0900 on 2024-01-02, 1.0800 on 2024-05-31 and 1.0400 on 2024-12-31 with straight lines between them, rounded to 4 decimals; plus `eurofxref-hist-na.csv` (N/A values) and `not_a_zip.bin`.

### 6.7 ISIN mapping help: OpenFIGI

- **Client.** `OpenFigiClient(http, api_key: str | None)`, `POST https://api.openfigi.com/v3/mapping` with `[{"idType": "ID_ISIN", "idValue": isin}]`, optional header `X-OPENFIGI-APIKEY` from the environment variable `OPENFIGI_API_KEY`. It returns listings (ticker, exchange code, name, security type). `pg instruments suggest ISIN` prints them with a possible Stooq symbol (exchange code `GY` or `GR` to `.de`, `US` to `.us`, `LN` to `.uk`), marked "suggestion, not applied". Nothing is stored until you run `pg instruments map`.
- **Privacy.** The lookup runs only on demand, when you call `pg instruments suggest`, and sends only that ISIN. Spec 3.10 says outbound requests carry tickers and dates only. An ISIN is a public identifier of a security, not of you, and no other command sends one. The UAT guide says the same.
- **Fixtures.** `tests/fixtures/http/openfigi/`: one response per golden ISIN, an empty result, and a 429 (rate limited) response.

### 6.8 Benchmark series

- **Client.** No new client. `benchmarks fetch` uses the price source named in `configs/benchmarks.yaml`: MSCI World in EUR through the iShares Core MSCI World ETF on Xetra (`eunl.de`), and the S&P 500 index (`^spx`) in USD, converted to EUR with the as-of ECB rate. Spec section 13 leaves open whether to use the ETF or the index for MSCI World; P0 uses the ETF and the file makes the choice editable.
- **Fixtures.** Shared with 6.4 (`eunl.de`, `^spx`).

## 7. Test plan

### 7.1 Rules for all tests

- `pytest-socket` disables sockets for the whole suite. `tests/http/test_no_network.py` checks that opening a socket fails and that `ReplayHttpClient` raises on an unrecorded request.
- `tests/conftest.py` gives each test a temporary data directory, a `ReplayHttpClient` on `tests/fixtures/http` and a `FixedClock` on 2024-12-31. No test reads `~/`, the real `data/` directory, the system clock or environment secrets. A hygiene test checks that `date.today()` and `datetime.now()` appear only in `core/clock.py`.
- Golden tests compare against committed JSON. `pytest --update-goldens` rewrites them, and any rewrite is reviewed in the diff before commit.
- Tests are written before the code in each work package (section 9 lists them).

### 7.2 Unit and property tests

| Area | Tests |
|---|---|
| core | German and English number parsing (thousands, negatives, 6 decimals, rejects "1,234.5" in German mode); date parsing including German month abbreviations; `berlin_to_utc` on both sides of the March and October 2024 changes; ISIN check digit on valid and one-digit-changed ISINs; `to_eur` convention and GBX; the clock (`PG_TODAY` gives a `FixedClock` whose `now_utc()` is 12:00 Berlin time, a malformed value is refused); property: `allocate` pieces always sum to the total and each is within 1e-8 of its exact share |
| storage | `DecimalText` round trip, refuses float; schema creation idempotent; foreign keys on |
| importer | Classifier: every text fixture matches exactly one parser; negative fixtures give the right review kind; CSV profile matching, encodings, row-level errors; the semantic key (booked EUR amount, `EUR`) and the report key; a re-exported CSV covering the same days maps to the same occurrences; matching rules a to d, including a statement line dated two days after its trade merging in both orders, with the key recomputed so a later PDF matches exactly; the one-to-one rule per source kind; precedence merge field by field; `field_conflict`, and the later import winning between two files of the same kind; `possible_duplicate`, and both orders reaching the same ledger after `merge` or `keep-both`; a buy known only from a statement line held back, then released when the CSV arrives; `missing_cost_basis` raised at staging; re-evaluation and supersession of every kind in the 5.3.5 table; an unknown file classified again after a parser is added; review `dedupe_key`; `anonymise_text` masks IBANs, depot numbers and address lines in every fixture |
| importer, same-day documents | `test_same_day_savings_plans.py`: the two savings-plan PDFs of 6.2 give exactly two transactions, each with one PDF source, in every order of {A, B} and of {A, B, CSV}, as one batch and as one batch per file; with the CSV each transaction also has one CSV source; no review item is raised; the copy of A with different bytes but the same text merges into A |
| importer, properties | `test_idempotency.py`: any sequence of imports (each staged, then accepted), with repeats, whose files together form a set S of the eleven golden files gives the same accepted ledger and the same open review items as one import of S. Both sides are compared in a canonical form without ids, occurrence numbers, content hashes and timestamps. Hypothesis draws S and the sequence: 200 examples, `deadline=None`. The test passes the pipeline a text extractor that caches by file hash, so each PDF goes through pdfplumber once per test run and `check.sh` stays fast |
| ledger | The golden lots and disposals; partial sell arithmetic; split with and without an explicit old quantity; `split_unclear`; transfer in with and without cost; a sell that happens before a transfer in with an older acquisition date consumes only lots booked before it, and holdings on a day between the two leave the transferred shares out; transfer out; oversell |
| ledger, properties | For random sequences of buys, sells, splits and transfers: open quantity per ISIN = buys + transfers in - sells - transfers out (split-scaled); no lot ever negative; cost initial = cost open + consumed cost for every lot; realised = proceeds - consumed cost; disposals always consume the oldest open lot first among the lots booked by then; a sell never consumes a lot booked after it; building twice gives identical results |
| marketdata | Stooq parsing, "No data", HTML block and 500; manual price file strictness; ECB zip and N/A; OpenFIGI mapping and 429; the fixture series pass through their anchors (6.4); lake upsert keeps one row per date; mapping log written on every change; `upsert_from_documents` never sets a symbol |
| valuation | The four hand-computed days of 1.2 as direct assertions: the totals to the cent on 2024-05-31 and 2024-12-31, and the price dates, rate dates and flags on 2024-12-25 and 2024-12-27 (holiday as-of, stale boundary). So a golden file rewritten by the code cannot drift unnoticed. Also: weekend skip; stale boundary at 5 and 6 days; missing price; missing FX; unmapped; split factor on a split-adjusted series equals the value on a raw series with the split booked on the right day; `price_basis_mismatch` when the ledger lacks a split, and none on the golden portfolio; the default range from a fixed clock; `complete` flag |

### 7.3 Golden-file tests for parsers

`tests/importer/tr/test_layout_goldens.py` walks `tests/fixtures/tr/text/**/*.txt`, parses each and compares with `.expected.json` (decimals as strings, times as ISO). `test_pdf_text.py` walks `tests/fixtures/tr/pdf/*.pdf` and compares extraction with the matching `.txt`. `tests/importer/test_csv_goldens.py` does the same for CSV fixtures. `tests/golden/test_golden_portfolio.py` runs the whole golden portfolio through the CLI in-process (Typer's `CliRunner`) and checks every figure in section 1.2. Besides comparing with the golden files, it asserts the figures of the four hand-computed days directly. It grows with the packages: import counts and transactions (WP7), lots, disposals and the entered transfer cost (WP8), values (WP10).

### 7.4 API contract tests

`tests/api/test_contract.py` uses FastAPI's `TestClient` (no sockets):

- The set of paths in `/openapi.json` equals `tests/api/openapi_paths.json` (a golden file), and no path contains `order`, `trade`, `broker` or `execute`.
- Each endpoint's response validates against its pydantic model, and all money fields are strings.
- Uploading the golden files through `POST /portfolio/imports` yields the same diff as `pg import`; accept, holdings, lots and value match the CLI goldens.
- Accepting twice returns 409; a second upload while a batch is staged returns 409; an unknown batch returns 404; an upload with no files returns 422 with a plain-English message.
- `GET /health` reports `real_orders: false`.

### 7.5 Hygiene and safety tests

`tests/test_repo_hygiene.py`:

- `git ls-files` contains no `*.sqlite`, `*.parquet`, `.env` or file under `data/`, `uploads/`, `registry/` or `private/`; every committed PDF, CSV and zip is under `tests/fixtures/` and listed in `tests/fixtures/MANIFEST.yaml` with `synthetic: true` and the layout it imitates.
- `.gitignore` contains the directories in 2.4.
- No IBAN in any fixture other than the documented dummy pattern `DE00 0000 ...`; no string that looks like an API key.
- The phrase "live trading" (any case) does not appear in `src/`, `web/src/`, `docs/uat/` or CLI help output. The word "live" on its own does not appear in CLI help output, the settings model (names and allowed values), string literals under `src/`, `web/src/` or `docs/uat/`; spec section 7 keeps it for live news, which P0 does not have.
- `pyproject.toml` has no dependency on `pytr` or any scraping or browser automation package; no module in `src/` imports one.
- The settings model rejects unknown keys, including anything named like a broker credential.

### 7.6 End-to-end scenario for QA

Run from the repository root, in this order. Every step must exit with status 0. The same steps are bundled in `scripts/e2e.sh` for convenience; QA runs them one by one. Every `pg` command carries `PG_TODAY=2024-12-31` (the clock of 3.3), so accept, the value range and every stored timestamp are the same on whatever day QA runs the scenario, and each step also works in a fresh shell. Step 31 moves the clock to 34 days after the last import.

| Step | Command | Checks |
|---|---|---|
| 1 | `uv sync --locked` | environment |
| 2 | `./scripts/check.sh` | lint, types, all tests with network disabled |
| 3 | `rm -rf .e2e && mkdir -p .e2e` | clean scratch area (ignored by git) |
| 4 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data init` | registry created |
| 5 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data import tests/fixtures/golden/inputs/tr_transactions_2024.csv tests/fixtures/golden/inputs/pdf/*.pdf --json > .e2e/import1.json` | round 1 import |
| 6 | `uv run python scripts/assert_golden.py .e2e/import1.json tests/fixtures/golden/expected/import1.json` | 14 new, 5 merged, 0 held back, 2 review items |
| 7 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data reconcile latest --confirmed tests/fixtures/golden/inputs/confirmed_holdings_2024-12-31.csv --as-of 2024-12-31 --strict --json > .e2e/reconcile.json` | diff against your confirmed holdings |
| 8 | `uv run python scripts/assert_golden.py .e2e/reconcile.json tests/fixtures/golden/expected/reconcile.json` | 5 of 5 match, NVDA at 20 after the split |
| 9 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data accept latest --json > .e2e/accept.json` | accepted |
| 10 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data import tests/fixtures/golden/inputs/tr_transactions_2024.csv tests/fixtures/golden/inputs/pdf/*.pdf tests/fixtures/golden/inputs/statement/kontoauszug_2024_h1.pdf --json > .e2e/import2.json` | round 2 import |
| 11 | `uv run python scripts/assert_golden.py .e2e/import2.json tests/fixtures/golden/expected/import2.json` | 10 duplicate files, 11 already known, 0 new |
| 12 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data accept latest` | accepting an empty batch is allowed |
| 13 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data review list --json > .e2e/review.json` | review queue |
| 14 | `uv run python scripts/assert_golden.py .e2e/review.json tests/fixtures/golden/expected/review.json` | the unknown layout and the missing cost basis, both open |
| 15 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data transfers set-cost --isin DE0008404005 --acquired 2020-03-02 --cost-eur 800.00` | transfer cost entered |
| 16 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data value --from 2024-01-02 --to 2024-12-31 --json > .e2e/value_unmapped.json` | value before mapping |
| 17 | `uv run python scripts/assert_golden.py .e2e/value_unmapped.json tests/fixtures/golden/expected/value_unmapped.json` | every holding flagged unmapped, no failure |
| 18 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data instruments map --file tests/fixtures/golden/inputs/instrument_mapping.csv` | mapping applied |
| 19 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data instruments list --json > .e2e/instruments.json && uv run python scripts/assert_golden.py .e2e/instruments.json tests/fixtures/golden/expected/instruments.json` | mapping shown, all confirmed |
| 20 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data --http-replay tests/fixtures/http prices fetch --from 2024-01-01 --to 2024-12-31` | Stooq client through replay |
| 21 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data prices import-file tests/fixtures/golden/inputs/manual_prices_allianz.csv` | manual price fallback |
| 22 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data --http-replay tests/fixtures/http fx fetch` | ECB client through replay |
| 23 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data --http-replay tests/fixtures/http benchmarks fetch --from 2024-01-01 --to 2024-12-31` | benchmark series |
| 24 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data holdings --as-of 2024-12-31 --json > .e2e/holdings.json && uv run python scripts/assert_golden.py .e2e/holdings.json tests/fixtures/golden/expected/holdings_2024-12-31.json` | holdings, cost and value |
| 25 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data lots --json > .e2e/lots.json && uv run python scripts/assert_golden.py .e2e/lots.json tests/fixtures/golden/expected/lots.json` | FIFO lots and disposals |
| 26 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data value --from 2024-01-02 --to 2024-12-31 --csv .e2e/value.csv --json > .e2e/value.json && uv run python scripts/assert_golden.py .e2e/value.json tests/fixtures/golden/expected/value.json` | 5,916.67 and 6,146.85, stale flags |
| 27 | `PG_TODAY=2024-12-31 uv run pg --data-dir .e2e/data benchmarks series sp500 --currency EUR --from 2024-01-02 --to 2024-12-31 --json > .e2e/sp500_eur.json && uv run python scripts/assert_golden.py .e2e/sp500_eur.json tests/fixtures/golden/expected/sp500_eur.json` | benchmark in EUR |
| 28 | `./scripts/e2e_api.sh .e2e/data` | API on 127.0.0.1:8765 returns the same holdings, lots and value; `/health` says no real orders |
| 29 | `(cd web && npm ci && npm run build)` | page builds |
| 30 | `(cd web && npx playwright test)` | upload, diff, accept in Chromium; badge; holdings; chart; re-upload gives 0 new |
| 31 | `PG_TODAY=2025-02-03 uv run pg --data-dir .e2e/data status --json > .e2e/status.json && uv run python scripts/assert_golden.py .e2e/status.json tests/fixtures/golden/expected/status_2025-02-03.json` | 34 days after the last import: the reminder to import again (spec 3.10) |
| 32 | `test -z "$(git status --porcelain)"` | the run left nothing untracked or changed in git |

`scripts/assert_golden.py` (written in WP1) compares JSON structurally, ignores the keys `id`, `batch_id`, `import_id`, `created_at`, `parsed_at`, `accepted_at`, `fetched_at` and `stored_path`, and prints a readable difference on failure. `scripts/e2e_api.sh` sets `PG_TODAY=2024-12-31` and sends only GET requests, so it leaves `.e2e/data` unchanged. The Playwright web server (`scripts/e2e_web_server.sh`) seeds its own data directory `.e2e/pw-data` with `PG_TODAY=2024-12-31`, the mapping, the replayed prices and rates, then runs `pg serve --port 8766`, so step 30 does not depend on steps 4 to 28.

Each work package's done-when (section 9) names the subset of these steps that must pass with only that package and earlier ones in place. A subset keeps the order of this table and always starts at step 1.

## 8. UAT outline (your Mac)

The full guide is `docs/uat/P0-macos.md`, written in WP12. It assumes macOS on Apple silicon or Intel, the Terminal, Homebrew, and your Trade Republic exports downloaded from the app or the web interface. Outline:

1. **Set up.** `brew install uv node`; clone or pull the branch; `uv sync --locked` (uv downloads Python 3.13 if needed); `./scripts/check.sh`. Expect all tests to pass.
2. **Keep your files private.** `mkdir -p ~/pg-private/exports ~/pg-private/data`; put the exports in `~/pg-private/exports`; `export PG_DATA_DIR=~/pg-private/data`. Nothing in `~/pg-private` is inside the repository.
3. **Import.** Keep one recent PDF document out of `~/pg-private/exports` for the timing check in step 14. Then `uv run pg init`; `uv run pg import ~/pg-private/exports/*.csv ~/pg-private/exports/*.pdf`. Read the diff. Held back means known only from an account statement line, or waiting for your decision in the review queue.
4. **Review queue.** `uv run pg review list`; `uv run pg review show <id>`. For each unknown layout: `uv run pg review export <id> --anonymise --out ~/pg-private/<id>.txt`, check that the text shows no name, address, IBAN or depot number, and share only that text. We turn it into a fixture and a parser update, and you import again.
5. **Reconcile.** Type your current holdings from the Trade Republic app into `~/pg-private/confirmed.csv` (`isin;quantity;as_of`). `uv run pg reconcile latest --confirmed ~/pg-private/confirmed.csv --as-of <today>`. Every ISIN should say `match`.
6. **Accept.** `uv run pg accept latest`; then `uv run pg transfers list` and `uv run pg transfers set-cost ...` for any position you transferred in from another broker.
7. **Map instruments.** `uv run pg instruments list`; for each ISIN `uv run pg instruments suggest <ISIN>`, then `uv run pg instruments map <ISIN> --source stooq --symbol <symbol> --currency <CCY>`. Nothing is mapped for you. `suggest` sends that one ISIN to OpenFIGI, and only when you run it. An ISIN identifies a security, not you, and no other command sends one; prices and rates are fetched by symbol and date only.
8. **Fetch real prices and rates.** `uv run pg prices fetch --from 2019-01-01`; `uv run pg fx fetch`; `uv run pg benchmarks fetch --from 2019-01-01`. If Stooq refuses or has no data for a European listing, download the closes from any source into the manual format and run `uv run pg prices import-file <file>` after mapping that ISIN to `--source manual`. Optional: `uv run pg --http-record ~/pg-private/http prices fetch --isin US67066G1040 --from 2024-06-01 --to 2024-06-20` to capture a real response for us to model a fixture on (market data only).
9. **Check the numbers.** `uv run pg holdings`; `uv run pg lots`; `uv run pg value --from 2024-01-01 --csv ~/pg-private/value.csv`. Compare today's value with the Trade Republic app (expect a difference within about 1%, because the app uses its own real-time quotes and we use the last close). Compare the cost basis of two positions with the purchase documents. Check NVIDIA (or any position with a split) around the split date: the value line must not jump by the split ratio. If a position had a split after your newest export, import the split document before you trust its value: until then the value is off by the split ratio. The value output flags `price_basis_mismatch` when a trade from the last 365 days shows this, but a position without a recent trade is not checked.
10. **Idempotency.** Import all files again. The diff must say 0 new transactions.
11. **Page.** `(cd web && npm ci && npm run build)`; `uv run pg serve`; open `http://127.0.0.1:8765`. Check the red badge, upload one file, read the diff, accept or discard, see the holdings and the chart.
12. **Optional browser test.** `(cd web && npx playwright install chromium && npx playwright test)`.
13. **Privacy check.** `git status` shows nothing new; `ls data` in the repository shows nothing, or only what you created on purpose.
14. **Timing.** With your instruments mapped and prices fetched, upload the document you kept aside in step 3 on the page and accept it. The updated value chart must appear within one minute of starting the upload (spec section 1). Note the time on the sign-off checklist.
15. **Reminder.** `PG_TODAY=<a date 31 days after your last import> uv run pg status` shows the reminder to import again; with a date 30 days after, it does not.
16. **Sign-off.** A short checklist in the guide: holdings match, cost basis plausible, value within 1%, no unexplained review items, re-import clean, page works, value within one minute, reminder shown. Anything that fails becomes a fixture and a fix before P1.

## 9. Work packages

Order is strict: each package depends only on earlier ones. Packages whose dependencies are met can run in parallel: WP3, WP5, WP6 and WP9 need only WP2, and WP4 needs only WP3. Complexity guides which model builds it: low for a small model, medium for an ordinary implementation model, high for design judgement or tricky logic. Hours are rough and add up to 35, the top of the spec's 25 to 35.

"Done when" names the e2e steps of section 7.6 that must pass with only that package and earlier ones in place. A step that a later package provides is never in the list. Every package also ends with `./scripts/check.sh --all-pythons` passing on Python 3.13 and 3.11, and a commit.

### WP1. Project skeleton, tooling and golden comparer

- **Goal.** A working uv project with the `pg` entry point, lint, type check, tests with the network disabled, the check script, the golden-file comparer and git hygiene.
- **Files.** `pyproject.toml` (with the ruff `per-file-ignores` for `tests/**` of 2.2), `uv.lock`, `.python-version`, `.gitignore`, `scripts/check.sh`, `scripts/assert_golden.py`, `src/playground/__init__.py`, `src/playground/config.py` (data dir, `http_mode` of `network`, `replay` or `record`, `today`), `src/playground/cli/main.py` (`pg --version`, `pg init` stub), `tests/conftest.py`, `tests/test_smoke.py`, `tests/test_repo_hygiene.py`, `tests/http/test_no_network.py` (socket part), `tests/scripts/test_assert_golden.py`, `tests/fixtures/MANIFEST.yaml` (empty list), `README.md` (a short "Develop" section).
- **Tests first.** `pg --version` prints the version and the no-real-orders line; opening a socket in a test fails; the hygiene checks from 7.5 that do not need later code; settings reject unknown keys; `assert_golden.py` ignores exactly the keys listed in 7.6, compares decimal strings exactly ("1.10" is not "1.1"), prints a readable difference and exits 1 on any difference.
- **Depends on.** Nothing.
- **Complexity.** Low. About 1.5 hours.
- **Done when.** e2e steps 1 to 3, then 32; `uv run pg --version` works.

### WP2. Core types, conventions, clock, parse model and registry

- **Goal.** Decimal, date, time zone, ISIN and FX helpers, the injectable clock, the shared transaction types and parse model, and the SQLite registry with all P0 tables.
- **Files.** `src/playground/core/{money,numbers,dates,isin,errors,clock,types}.py`, `src/playground/importer/model.py`, `src/playground/storage/{db,schema,repos}.py`, `pg init` completed in `cli/main.py`; tests under `tests/core/` and `tests/storage/`.
- **Tests first.** Everything in the core and storage rows of 7.2, including the `allocate` property test, the summer-time cases and the clock.
- **Depends on.** WP1.
- **Complexity.** Medium. About 3 hours.
- **Done when.** The conventions of 3.3 are implemented and documented in docstrings; `pg init` creates the directory tree and the default portfolio; floats are refused by `DecimalText`; the hygiene test finds no clock call outside `core/clock.py`; e2e steps 1 to 4, then 32.

### WP3. PDF text layer, classifier and trade layouts

- **Goal.** PDF to text, the classifier, the shared line grammar and the four trade layouts (`tr.wertpapierabrechnung.de.2019`, `tr.wertpapierabrechnung.de.2023`, `tr.sparplan.de`, `tr.settlement.en.2023`), driven by synthetic text goldens; unknown and broken documents become review results.
- **Files.** `src/playground/importer/tr/{pdf_text,classify}.py`, `src/playground/importer/tr/layouts/{common,wertpapierabrechnung,settlement_en}.py`, `tests/fixtures/tr/make_pdfs.py` (reportlab), the text fixtures, expected JSON and PDFs of these layouts, the negative fixtures and the same-day savings-plan pair of 6.2, the golden PDFs for T2, T3, T6, T8 and T12 and `unbekannt_kosteninformation.pdf` under `tests/fixtures/golden/inputs/pdf/`, `tests/fixtures/golden/README.md` (the hand computation of 1.2), `tests/importer/tr/{test_pdf_text,test_classify,test_layout_goldens,test_layout_units}.py`, MANIFEST entries.
- **Tests first.** Write the text fixtures in the normal form of 5.3.2 and their expected JSON by hand from 1.2 and 6.2, then the golden tests, then the parsers. PDF layer tests after `make_pdfs.py`.
- **Depends on.** WP2.
- **Complexity.** High. About 3.5 hours.
- **Done when.** Every text fixture of these layouts parses to its golden, `source_ref` included; every PDF extracts to exactly its `.txt`; a second run of `make_pdfs.py` gives byte-identical PDFs; the negative fixtures give the right review kind (`two_layouts_mixed` mixes the 2019 and 2023 trade layouts, so it is ambiguous); no personal field is kept; MANIFEST lists every file with the layout it imitates; e2e steps 1 to 3, then 32.

### WP4. Dividend, tax, interest, split and statement layouts

- **Goal.** The other six layouts of 6.2 (`tr.dividende.de`, `tr.steuer.de`, `tr.zinsen.de`, `tr.split.de`, `tr.kontoauszug.de.2023`, `tr.kontoauszug.de.2024`) and the `corporate_action` recognition, on the grammar of WP3.
- **Files.** `src/playground/importer/tr/layouts/{dividende,steuer,zinsen,split,kontoauszug_2023,kontoauszug_2024}.py`, their text fixtures, expected JSON and PDFs, the golden PDFs for T9, T10 and T11, `tests/fixtures/golden/inputs/statement/kontoauszug_2024_h1.pdf`, new cases in the WP3 test modules, MANIFEST entries.
- **Tests first.** As in WP3: text fixtures and expected JSON first, including the 2024 statement with German month names and "€" amounts, then the parsers.
- **Depends on.** WP3.
- **Complexity.** Medium. About 3 hours.
- **Done when.** Every text fixture parses to its golden; "€" and German month names survive the PDF layer; the H1 statement gives 11 `pdf_statement` candidates dated as in 1.2; exchange and subscription documents give `corporate_action`; e2e steps 1 to 3, then 32.

### WP5. CSV family: Trade Republic CSV, manual CSV and confirmed holdings

- **Goal.** The declarative CSV profile and parser, the manual transaction format and the confirmed-holdings format, all returning the parse model of WP2.
- **Files.** `src/playground/importer/tr/{csv_profiles,csv_parser}.py`, `src/playground/importer/{manual_csv,confirmed_csv}.py`, `tests/fixtures/tr/csv/**`, `tests/fixtures/manual_csv/*`, `tests/fixtures/confirmed/*`, `tests/fixtures/golden/inputs/{tr_transactions_2024.csv,confirmed_holdings_2024-12-31.csv}`, `tests/importer/{test_csv_goldens,test_manual_csv,test_confirmed_csv}.py`, MANIFEST entries.
- **Tests first.** CSV goldens for every fixture of 6.1; encodings, BOM and CRLF; an unknown header, an unknown row type and a bad number give the right review results while the other rows still import; the manual and confirmed formats, including the `# decimal=,` comment.
- **Depends on.** WP2.
- **Complexity.** Medium. About 2 hours.
- **Done when.** Every CSV fixture parses to its golden; the golden CSV gives the 11 candidates of 1.2; e2e steps 1 to 3, then 32.

### WP6. FIFO ledger core

- **Goal.** The pure lot book of 5.5: lots, disposals, the quantity timeline, splits, transfers in and out, and holdings and open cost as of any date, with property tests.
- **Files.** `src/playground/ledger/{fifo,holdings}.py`, `tests/ledger/{test_fifo,test_fifo_properties,test_holdings}.py`.
- **Tests first.** The golden lots and disposals of 1.2, built from in-memory transactions; each rule in 5.5 as a unit test, including a sell that happens before a transfer in with an older acquisition date; the ledger property tests of 7.2.
- **Depends on.** WP2.
- **Complexity.** High. About 3 hours.
- **Done when.** Lots, disposals and realised figures match 1.2 exactly; the property tests pass with 500 examples; e2e steps 1 to 3, then 32.

### WP7. Import pipeline, matching, review queue and reconciliation

- **Goal.** Staging batches, semantic and report keys, one-to-one matching and the precedence merge, held-back transactions, the review queue with re-evaluation and resolutions, the reconciliation diff, accept with the lot rebuild, and the import commands.
- **Files.** `src/playground/importer/{keys,pipeline,reconcile,review}.py`, `src/playground/cli/{imports,review}.py` (`import`, `imports`, `reconcile`, `accept`, `discard`, `review list/show/dismiss/resolve`, `transactions`, and `status` without the latest value), `tests/fixtures/golden/expected/{import1,import2,reconcile,review}.json`, `tests/fixtures/idempotency/same_day_savings_plans/transactions.csv`, `tests/importer/{test_keys,test_matching,test_review_queue,test_reevaluation,test_reconcile,test_reminder,test_idempotency,test_same_day_savings_plans}.py`, `tests/golden/test_golden_portfolio.py` (import counts and transactions field by field).
- **Tests first.** The importer rows of 7.2: keys and report keys, including a re-exported CSV that maps to the same occurrences; matching rules a to d; held-back statement-only buys; re-evaluation of every kind in the 5.3.5 table; an unknown file classified again after a parser is added; `missing_cost_basis` at staging; the same-day savings-plan test; the idempotency property test; the reconciliation diff golden; review dedupe; the 30-day reminder with a fixed clock.
- **Depends on.** WP4, WP5 and WP6.
- **Complexity.** High. About 4.5 hours.
- **Done when.** Golden round 1 and round 2 counts match 1.2 through the CLI, and the diff shows "5 of 5 match" before accept; e2e steps 1 to 14, then 32.

### WP8. Ledger views, transfers and anonymised export

- **Goal.** The ledger commands on the stored lot book, cost basis entry for transfers in, and the anonymised export of review items.
- **Files.** `src/playground/cli/portfolio.py` (`holdings` with quantity and cost, `lots`, `transfers list`, `transfers set-cost`), `src/playground/importer/anonymise.py`, `pg review export` in `cli/review.py`, `tests/fixtures/golden/expected/lots.json`, `tests/importer/{test_anonymise,test_transfers}.py`, more cases in `tests/golden/test_golden_portfolio.py` (lots, disposals and realised gain after `set-cost`).
- **Tests first.** `set-cost` stores the input, resolves `missing_cost_basis` with `cost entered` and rebuilds the lots; `set-cost` on an ISIN with two transfers in asks for `--txn`; the lots golden; `anonymise_text` masks IBANs, depot numbers, order numbers and address lines in every text and CSV fixture.
- **Depends on.** WP7.
- **Complexity.** Medium. About 2 hours.
- **Done when.** e2e steps 1 to 15, then 25, then 32.

### WP9. HTTP layer, market data clients and instrument master

- **Goal.** The injectable HTTP layer with replay and record, the Stooq, manual price, ECB and OpenFIGI clients, the Parquet stores, benchmarks from config, and the editable, logged instrument mapping.
- **Files.** `src/playground/http/{client,replay,record}.py`, `src/playground/marketdata/{lake,instruments,stooq,manual_prices,ecb,openfigi,benchmarks}.py`, `configs/benchmarks.yaml`, `src/playground/cli/{instruments,marketdata}.py`, the global options `--http-replay` and `--http-record` in `cli/main.py`, `tests/fixtures/http/**` with `make_series.py` and `index.yaml`, `tests/fixtures/manual_prices/*`, `tests/fixtures/golden/inputs/{instrument_mapping.csv,manual_prices_allianz.csv}`, `tests/fixtures/golden/expected/sp500_eur.json`, tests under `tests/http/` and `tests/marketdata/`.
- **Tests first.** Replay matching and the unrecorded-request error; record mode strips secrets (with a fake inner client); each client against its fixtures, including error bodies; the fixture series pass through their anchors; lake upsert and manifest; mapping log; benchmark EUR conversion; `suggest` stores nothing.
- **Depends on.** WP2.
- **Complexity.** Medium. About 3.5 hours.
- **Done when.** e2e steps 1 to 4, then 18, 20 to 23 and 27, then 32, on a data directory with no imports (the mapping file creates the instruments). This subset needs no imported data, which is why WP9 depends only on WP2. Step 19 lists the instruments with the names read from documents, so it is checked in WP10. No client can be built without an `HttpClient`.

### WP10. Valuation series

- **Goal.** The daily EUR value series with the documented price basis, as-of alignment, split factor, staleness, flags and default range, stored as snapshots and series and rebuilt after accept.
- **Files.** `src/playground/valuation/series.py`, `pg value` and the value columns of `pg holdings` in `cli/portfolio.py`, the value step in `accept_batch`, resolutions and `transfers set-cost`, the latest value in `pg status`, `tests/valuation/test_series.py`, `tests/fixtures/golden/expected/{value_unmapped,value,holdings_2024-12-31,instruments,status_2025-02-03}.json`, the value part of `tests/golden/test_golden_portfolio.py`.
- **Tests first.** The valuation row of 7.2, starting with the direct assertions for the four hand-computed days of 1.2; every rule in 5.7 as a unit test, with small in-memory price and rate stores; the price basis check with a split missing from the ledger; the default range from a fixed clock; the whole golden portfolio in-process.
- **Depends on.** WP8 and WP9.
- **Complexity.** High. About 3 hours.
- **Done when.** e2e steps 1 to 27, then 31 and 32; `pg value` never fails on missing data and lists every flag.

### WP11. FastAPI backend

- **Goal.** The API of 5.8 over the same services the CLI uses, with contract tests.
- **Files.** `src/playground/api/{app,schemas,routes_portfolio,routes_instruments,routes_benchmarks}.py`, `pg serve` in `cli/serve.py`, `scripts/e2e_api.sh` (GET requests only), `tests/api/{test_contract,test_errors}.py`, `tests/api/openapi_paths.json`.
- **Tests first.** The contract tests of 7.4, including the forbidden path words, string money and the reminder in `GET /portfolio` with a fixed clock.
- **Depends on.** WP10.
- **Complexity.** Medium. About 2.5 hours.
- **Done when.** Contract tests pass; `pg serve` binds to 127.0.0.1 only; e2e steps 1 to 28, then 31 and 32.

### WP12. Import page, end-to-end integration and UAT guide

- **Goal.** The minimal page, the full end-to-end scenario across CLI, API and page, the Playwright test, and the macOS UAT guide.
- **Files.** `web/package.json`, `web/package-lock.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/src/{main.tsx,App.tsx,api.ts,format.ts}`, `web/src/components/{Badge,ImportPanel,DiffView,ConfirmedUpload,HoldingsTable,ValueChart}.tsx`, `web/src/**/*.test.tsx` (Vitest), `web/playwright.config.ts`, `web/e2e/import.spec.ts`, `scripts/e2e_web_server.sh`, `scripts/e2e.sh`, static serving in `api/app.py`, `docs/uat/P0-macos.md`, `README.md` (how to run P0).
- **Tests first.** Vitest: the badge text is exact and always rendered; the diff view renders the golden `import1.json`; money is shown from strings without float parsing. Playwright: open the page, see the badge, upload the golden files, see "14 new transactions" and "2 need your review", upload confirmed holdings and see "5 of 5 match", accept, see 5 holdings with SAP at quantity 3 and the chart from 2024-01-02 to 2024-12-31, reload and see the same, upload again and see "0 new transactions", and find no "live trading" text on the page.
- **Depends on.** WP11.
- **Complexity.** Medium. About 3.5 hours.
- **Done when.** All 32 steps of 7.6 pass in order on a fresh clone; the UAT guide's commands have been run here with `--http-replay` in place of the real network; hygiene tests cover `web/src` and `docs/uat`.

## 10. Risks for this phase

| Risk | Mitigation |
|---|---|
| The real CSV export differs from the synthetic profile | Profiles are data; unknown header goes to review with the header shown; a new profile is a small change plus a golden file |
| A real PDF layout differs from the fixtures | One parser per layout, golden files, `unknown_layout` review items, anonymised export for new fixtures |
| Stooq refuses requests, has no data for a European listing, or adjusts prices differently than assumed | Manual price file, explicit `adjustment` per series, `--http-record` to capture a real response, UAT check around a split |
| The semantic key merges two distinct transactions or misses a match | Report keys and one-to-one matching per source kind, occurrence numbers, the same-day savings-plan fixture pair, `possible_duplicate` held back for you, the order-independence property test, and reconciliation against your confirmed holdings before accept |
| Real account statements show settlement dates, not trade dates | A statement line with exactly one near-date candidate merges automatically, and the diff shows both dates; a buy known only from a statement is held back, never guessed |
| A split happens after your last import | `price_basis_mismatch` from the most recent trade (5.7); the UAT guide says to import split documents before trusting a value |
| Rounding differences against Trade Republic's documents | The document's EUR amount always wins; an amounts check within 0.01 raises a review item instead of silently correcting |
