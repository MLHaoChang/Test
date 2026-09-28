# P0 user acceptance testing guide (your Mac)

This guide checks phase P0 with your own Trade Republic files, on your own Mac. Everything in the
repository itself is synthetic or made up (plan section 1.2); this is the first time real files
are involved. The app never places, routes or automates an order. It holds no broker credentials.
Your real portfolio is read-only, imported from Trade Republic exports (CSV and PDF).

Follow the steps in order. Each one says what to run and what to expect. If a step's result does
not match, stop, write down what you saw, and treat it as a finding for the sign-off checklist at
the end.

## Terms used in this guide

- **ISIN** (International Securities Identification Number): the 12-character code that identifies
  a security, for example `DE0007164600` for SAP. It is the instrument key everywhere in the app.
- **FIFO** (first in, first out): the rule German tax law uses for cost basis. A sale consumes the
  oldest lot first.
- **Review queue**: a list of documents, rows or conflicts the app could not handle with certainty.
  Nothing in it is guessed or dropped. Each item waits for your decision, unless a later file
  removes the problem.
- **Held back**: a transaction that is stored but kept out of your holdings and value until the
  review item about it is settled.
- **Diff**: what an import found, before you accept it: new transactions, ones already known,
  changes to your holdings, and any review items it raised.

## Before you start

- A Mac, Apple silicon or Intel, with the Terminal app and Homebrew installed.
- Your Trade Republic exports (CSV, and any PDF documents you want to import), downloaded from the
  app or the web interface.
- About one hour, most of it spent waiting on your own files rather than on the steps themselves.

## 1. Set up

```bash
brew install uv node
```

Clone the repository, or pull the branch you were given, then from its root:

```bash
uv sync --locked
./scripts/check.sh
```

`uv sync` downloads Python 3.13 for you if your Mac does not already have it. `check.sh` runs
formatting, type checks and the whole test suite with the network disabled. Expect every check to
pass. If one does not, stop here: nothing past this point can be trusted until this step is clean.

## 2. Keep your files private

Nothing under the repository's own `data/` directory is meant to hold your real files for long, and
nothing you put outside the repository is ever committed to it.

```bash
mkdir -p ~/pg-private/exports ~/pg-private/data
export PG_DATA_DIR=~/pg-private/data
```

Put your Trade Republic exports in `~/pg-private/exports`. Nothing under `~/pg-private` is inside
the repository, so it is never at risk of being committed. Keep the `export PG_DATA_DIR=...` line
for the rest of this guide: every `pg` command below reads and writes that directory unless a step
says otherwise.

## 3. Import

Before you import, move one recent PDF document out of `~/pg-private/exports` into a separate
folder (for example `~/pg-private/held-out/`). You will import it later, on its own, for the
timing check in step 14.

```bash
uv run pg init
uv run pg import ~/pg-private/exports
```

Given a folder, `pg import` reads every PDF and CSV file directly in it, and leaves out any other
file and any subfolder. You can also name files one by one.

Read the diff this prints. "Held back" means a transaction is known only from an account statement
line, so it is stored but left out of your holdings and value until you settle the review item
about it. Anything in the review queue is also explained in plain words there.

## 4. Review queue

```bash
uv run pg review list
uv run pg review show <id>
```

For each item of kind `unknown_layout` (a document format the app does not recognise yet):

```bash
uv run pg review export <id> --anonymise --out ~/pg-private/<id>.txt
```

Open that file and check it shows no name, address, IBAN or depot number. Share only that text with
us, never the original document. We turn it into a fixture and a small parser change, and you
import again.

## 5. Reconcile

Open the Trade Republic app and write down what it shows you hold today. Put it in
`~/pg-private/confirmed.csv`: the two lines below, then one line per position with its ISIN, the
quantity and the date you read it (YYYY-MM-DD), separated by semicolons.

```
# decimal=,
isin;quantity;as_of
```

For example, `DE0007164600;3;2024-12-31` says you held 3 SAP shares on 31 December 2024. The first
line says that you write a fractional quantity with a decimal comma, as the app shows it (`0,4534`).
If you write a decimal point instead (`0.4534`), make that line `# decimal=.`.

Then:

```bash
uv run pg reconcile latest --confirmed ~/pg-private/confirmed.csv --as-of <today>
```

Every ISIN should say `match`. A `mismatch` is a finding: write down which ISIN and by how much
before you go further.

## 6. Accept

```bash
uv run pg accept latest
uv run pg transfers list
```

For each position you moved in from another broker (a "transfer in"), its cost is not known from a
Trade Republic document, so enter it yourself from your own records:

```bash
uv run pg transfers set-cost --isin <ISIN> --acquired <YYYY-MM-DD> --cost-eur <AMOUNT>
```

## 7. Map instruments

Nothing is mapped for you: every instrument needs an explicit price source before it can be valued.

```bash
uv run pg instruments list
```

For each ISIN listed as not yet mapped:

```bash
uv run pg instruments suggest <ISIN>
uv run pg instruments map <ISIN> --source <stooq-or-manual> --symbol <SYMBOL> --currency <CCY>
```

`suggest` sends that one ISIN to OpenFIGI, and only when you run it. An ISIN identifies a security,
not you, and no other command in this app sends one anywhere. Prices and exchange rates are fetched
by symbol and date only, never by anything that identifies you or your account.

## 8. Fetch real prices and exchange rates

```bash
uv run pg prices fetch --from 2019-01-01
uv run pg fx fetch
uv run pg benchmarks fetch --from 2019-01-01
```

If a source cannot be reached (no network, a proxy or firewall that refuses, no answer in time),
each command says so in one plain sentence per symbol, still fetches the others, and ends with exit
status 1. Check your connection and run it again.

If a source has no data for one of your instruments (a European listing is the most likely case),
download its daily closes from anywhere you trust into the manual price format described in
`pg prices import-file --help`, map that ISIN to `--source manual`, then:

```bash
uv run pg prices import-file <file>
```

Optional, and only if you want to help us build a real fixture: capture one real response for
market data only, never anything about your account:

```bash
uv run pg --http-record ~/pg-private/http prices fetch --isin US67066G1040 --from 2024-06-01 --to 2024-06-20
```

## 9. Check the numbers

```bash
uv run pg holdings
uv run pg lots
uv run pg value --from 2024-01-01 --csv ~/pg-private/value.csv
```

Compare today's value with the Trade Republic app. Expect a difference within about 1%: the app
uses its own quotes at the moment you look, and this app uses the last daily close. Compare the
cost basis of two positions against your own purchase documents.

If you hold NVIDIA, or any position with a split, check its value around the split date: the value
line must not jump by the split ratio on that day. If a position had a split after your newest
export, import the split document before you trust its value: until then, its value is off by the
split ratio. The value output flags this as `price_basis_mismatch` when the split happened after
that instrument's most recent trade in the last 365 days; a position with no trade in that window is
not checked, so a very old, untouched holding can still miss a split silently.

## 10. Idempotency check

**Idempotent** means doing the same thing twice has the same effect as doing it once. Import
everything again:

```bash
uv run pg import ~/pg-private/exports
```

The diff must say 0 new transactions.

## 11. The import page

```bash
(cd web && npm ci && npm run build)
uv run pg serve
```

Open `http://127.0.0.1:8765` in your browser. Check the red badge at the top, upload one file, read
the diff, accept or discard it, then check the holdings table and the value chart.

## 12. Optional: the browser test

This runs the same page in an automated browser rather than one you click through yourself.

```bash
(cd web && npx playwright install chromium && npx playwright test)
```

## 13. Privacy check

```bash
git status
ls data
```

`git status` must show nothing new or changed. `ls data` (the repository's own default data
directory) must show nothing, or only what you created on purpose: your real files and the
registry the app builds from them stay under `~/pg-private`, never under the repository.

## 14. Timing check

This checks the promise that a newly accepted document shows up in your value within about a
minute (spec section 1), with your instruments already mapped and today's prices already fetched
(steps 7 and 8 above).

Note the time, then upload the one document you set aside in step 3 on the page (step 11) and
accept it. The value chart must show the update within one minute of when you started the upload.
Write the actual time it took on the sign-off checklist below.

## 15. Reminder check

The app reminds you to import again 30 days after your last import (spec section 3.10).

```bash
PG_TODAY=<31 days after your last import> uv run pg status
```

This must show the reminder. Then try one day earlier:

```bash
PG_TODAY=<30 days after your last import> uv run pg status
```

This must not show it.

## 16. Sign-off

Check each line once it holds. Anything unchecked becomes a fixture and a fix before the next
phase.

- [ ] Holdings match the Trade Republic app (step 5).
- [ ] Cost basis is plausible against your own purchase documents (step 9).
- [ ] Today's value is within about 1% of the Trade Republic app (step 9).
- [ ] No unexplained review item is left open (step 4).
- [ ] Importing everything again shows 0 new transactions (step 10).
- [ ] The page works: badge, upload, diff, accept or discard, holdings, chart (step 11).
- [ ] The value chart updated within one minute of accepting the held-out document (step 14). Time
      it actually took: __________.
- [ ] The 30-day reminder appears at 31 days and not at 30 (step 15).
