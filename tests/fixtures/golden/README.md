# The golden synthetic portfolio

This directory holds the **golden portfolio**: one invented portfolio whose import, lots and value are computed by hand below, so tests can check the code against figures a person worked out. It is copied from section 1.2 of the [phase P0 plan](../../../docs/plans/P0-implementation-plan.md).

- `inputs/` holds the files you would export from Trade Republic, all synthetic.
- `expected/` holds the expected outputs of the end-to-end scenario (plan 7.6), added by the packages that produce them.

All figures are made up. The ISINs are real public identifiers (the ISIN, International Securities Identification Number, is the 12-character code that identifies a security) so that mapping looks realistic. No real person, account or IBAN appears anywhere: the only IBAN is the dummy `DE00 0000 0000 0000 0000 00`, and the account holder "Jana Beispiel" is invented. `tests/fixtures/MANIFEST.yaml` lists every input file and the layout it imitates.

## Instruments

| Key | ISIN | Name in documents | Price source (after mapping) | Listing currency |
|---|---|---|---|---|
| SAP | DE0007164600 | SAP SE | Stooq `sap.de` | EUR |
| MSCIW | IE00B4L5Y983 | iShsIII-Core MSCI World U.ETF | Stooq `eunl.de` | EUR |
| AAPL | US0378331005 | Apple Inc. | Stooq `aapl.us` | USD |
| NVDA | US67066G1040 | NVIDIA Corp. | Stooq `nvda.us` | USD |
| ALV | DE0008404005 | Allianz SE | manual price file `ALV.DE` | EUR |

## Transactions

Times are Europe/Berlin. "src" lists where each one appears: P = PDF document, C = CSV export, S = account statement PDF, imported in the second round.

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

Each line of the H1 statement carries the same date as the matching PDF or CSV entry (the execution date for a trade, the document date for a dividend), so every line matches exactly. Real statements may show a later booking day; rule c of plan section 5.3.4 handles that.

One more PDF, `unbekannt_kosteninformation.pdf`, imitates a Trade Republic cost information sheet that no parser understands. It must land in the review queue.

## Hand-computed results

- Import round 1 (the CSV and the nine PDFs): 19 parsed candidates (11 CSV rows, 8 PDF transactions), 14 unique transactions, 5 merged pairs (T2, T3, T6, T9, T12), 0 held back, 2 review items, both raised at staging (the unknown layout, and the missing cost basis for T13).
- Import round 2 (the same ten files plus the H1 account statement): 10 files skipped as already imported (the unknown cost information sheet is classified again, still finds no parser, and counts as skipped), 11 statement lines all matched to known transactions, 0 new transactions, 0 new review items.
- FIFO (first in, first out, the cost basis rule of German tax law) for the sale T12: lot T2 (10 shares, cost 1,401.00) is consumed fully; lot T8 (5 shares, cost 801.00) gives 2 shares at 801.00 x 2 / 5 = 320.40. Consumed cost 1,721.40. Proceeds after fee 2,099.00. Realised gain 377.60 (before tax). Proceeds are split across the two disposals by quantity: 1,749.16666667 and 349.83333333.
- Open lots on 2024-12-31: SAP 3 shares, cost 480.60 (opened 2024-04-10); MSCIW 2.5 at 200.00 and 2.5 at 210.00; AAPL 5 at 801.00; NVDA 20 at 1,701.00 (opened 2024-03-20, quantity scaled by the 10-for-1 split, cost unchanged); ALV 4 at 800.00 (acquired 2020-03-02, cost typed in by the user). Total open cost basis 4,192.60.
- Taxes withheld in 2024: 108.46 EUR. Dividends gross in EUR: 34.11.
- Value on 2024-05-31 (all markets open; ECB 1.0800 USD per EUR): SAP 15 x 170.00 = 2,550.00; MSCIW 5 x 90.00 = 450.00; AAPL 5 x 190.00 / 1.08 = 879.62962963; NVDA 2 shares held, price series split-adjusted, so 2 x 10 x 110.00 / 1.08 = 2,037.03703704. Total **5,916.67** EUR; cost basis 5,114.00 (the ALV lot is left out: it was acquired in 2020 but booked in on 2024-06-20); complete.
- Value on 2024-12-31 (Xetra closed, so SAP and MSCIW use the 2024-12-30 close; US open; ECB 1.0400): SAP 3 x 236.00 = 708.00; MSCIW 5 x 100.00 = 500.00; AAPL 5 x 250.00 / 1.04 = 1,201.92307692; NVDA 20 x 134.00 / 1.04 = 2,576.92307692; ALV 4 x 290.00 = 1,160.00 from the manual file, last price 2024-12-20, flagged stale. Total from unrounded parts **6,146.85** EUR; cost basis 4,192.60.
- Staleness boundary: on 2024-12-25 the ALV price is 5 days old and is not stale; on 2024-12-27 it is 7 days old and is stale. On 2024-12-25 SAP uses the 2024-12-23 close and the ECB rate from 2024-12-24, without a flag.
- These four days (2024-05-31, 2024-12-25, 2024-12-27 and 2024-12-31) are asserted directly in `tests/valuation/test_series.py` and `tests/golden/test_golden_portfolio.py`, not only through the golden files, because the full-year golden file is written by the code itself.
- The Stooq fixture series are declared `split_dividend`, so the value output also carries the info flag `dividend_adjusted_prices` for SAP, MSCIW, AAPL and NVDA. The ALV manual file is declared `raw`.
- Before any mapping, the value series reports every held instrument as `unmapped`, a value of 0.00 and `complete: false`, and exits with status 0.
- With the clock set to 2025-02-03 (`PG_TODAY`, plan section 3.3), 34 days after the last import on 2024-12-31, `pg status` shows the reminder to import again (spec 3.10). One review item is still open: the unknown layout.

## Input files

The PDF documents are written by `tests/fixtures/tr/make_pdfs.py` from text fixtures under `tests/fixtures/tr/text/`, which are the source of truth. Each text fixture also has an expected JSON beside it, checked by `tests/importer/tr/test_layout_goldens.py`. A golden document's text fixture is named `golden_t<number>_<document>_<instrument>.txt`, and its PDF is named `<date>_<document>_<instrument>.pdf`.

| File | Transaction | Layout | Text fixture | Added in |
|---|---|---|---|---|
| `inputs/pdf/2024-01-15_kauf_sap.pdf` | T2 | `tr.wertpapierabrechnung.de.2023` | `tr.wertpapierabrechnung.de.2023/golden_t02_kauf_sap.txt` | WP3 |
| `inputs/pdf/2024-02-01_sparplan_msciw.pdf` | T3 | `tr.sparplan.de` | `tr.sparplan.de/golden_t03_sparplan_msciw.txt` | WP3 |
| `inputs/pdf/2024-03-20_kauf_nvda.pdf` | T6 | `tr.wertpapierabrechnung.de.2023` | `tr.wertpapierabrechnung.de.2023/golden_t06_kauf_nvda.txt` | WP3 |
| `inputs/pdf/2024-04-10_kauf_sap.pdf` | T8 | `tr.wertpapierabrechnung.de.2023` | `tr.wertpapierabrechnung.de.2023/golden_t08_kauf_sap.txt` | WP3 |
| `inputs/pdf/2024-06-12_verkauf_sap.pdf` | T12 | `tr.wertpapierabrechnung.de.2023` | `tr.wertpapierabrechnung.de.2023/golden_t12_verkauf_sap.txt` | WP3 |
| `inputs/pdf/unbekannt_kosteninformation.pdf` | none (unknown layout) | none | `unclassified/unknown_cost_information.txt` | WP3 |
| `inputs/pdf/2024-05-15_dividende_sap.pdf` | T9 | `tr.dividende.de` | `tr.dividende.de/golden_t09_dividende_sap.txt` | WP4 |
| `inputs/pdf/2024-05-16_dividende_aapl.pdf` | T10 | `tr.dividende.de` | `tr.dividende.de/golden_t10_dividende_aapl.txt` | WP4 |
| `inputs/pdf/2024-06-10_split_nvda.pdf` | T11 | `tr.split.de` | `tr.split.de/golden_t11_split_nvda.txt` | WP4 |
| `inputs/statement/kontoauszug_2024_h1.pdf` | the H1 account statement (11 lines, matching T1 to T10 and T12) | `tr.kontoauszug.de.2024` | `tr.kontoauszug.de.2024/golden_h1_statement.txt` | WP4 |
| `inputs/tr_transactions_2024.csv`, `inputs/confirmed_holdings_2024-12-31.csv` | the CSV export and your confirmed holdings | `tr_csv_synthetic_v1` | | WP5 |
| `inputs/instrument_mapping.csv`, `inputs/manual_prices_allianz.csv` | the instrument mapping and the manual Allianz prices | | | WP9 |

What each golden document reads as, field by field, is in the expected JSON next to its text fixture. For example, T12 gives a sell of 12 SAP at 175.00 on 2024-06-12 at 11:20 Berlin time (09:20 UTC, summer time), a booking of +1,999.41 EUR with the value date 2024-06-14, fees of 1.00 EUR and taxes of 99.59 EUR (capital gains tax 94.40, solidarity surcharge 5.19), and the execution number as `source_ref`. T3, a savings plan, has day precision: 2024-02-01 at 00:00 Berlin time, which is 2024-01-31 23:00 UTC.

## Expected outputs

The files in `expected/` are the JSON outputs of the end-to-end scenario (plan 7.6). `scripts/assert_golden.py` compares a run's output with them and ignores ids and timestamps. They are written by the code (`pytest --update-goldens`), so `tests/golden/test_golden_portfolio.py` also asserts the figures above directly. Check every rewrite in the git diff before you commit it.

| File | Command (step of plan 7.6) | What it shows | Added in |
|---|---|---|---|
| `expected/import1.json` | `pg import` of the CSV export and the nine PDF documents (step 5) | 10 files, 19 candidates, 14 new transactions, 5 merged pairs, 0 held back, the 2 review items, holdings 0 before and SAP 3, MSCIW 5, AAPL 5, NVDA 20, ALV 4 after | WP7 |
| `expected/reconcile.json` | `pg reconcile latest --confirmed ... --as-of 2024-12-31 --strict` (step 7) | the same diff with your confirmed holdings: 5 of 5 match | WP7 |
| `expected/import2.json` | `pg import` of the same ten files plus the H1 statement (step 10) | 10 files skipped as already imported, 11 statement lines already known, 0 new | WP7 |
| `expected/review.json` | `pg review list` after round 2 (step 13) | the unknown layout and the missing cost basis, both open | WP7 |
