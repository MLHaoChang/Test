# Phase P0 UX review: a first-time user's walkthrough

29 September 2026. Branch `claude/stock-trading-bot-research-c5ygze`. Screenshots are in `screenshots/P0/` next to this file. The terminal transcripts of the walkthrough stayed with the reviewer's working files and are not in the repository.

**Words used here.** The *diff* is the page's summary of an import before you accept it; a *staged* batch is one read but not yet accepted. A *review item* is something the app needs you for. A *flag* is a note on a value, such as an old price. *Mapping* links a security to a public price series. An *ISIN* is a security's 12-character code. The *API* is the interface the page uses to talk to the app; the *CLI* is the `pg` command line. A *module* is a separate panel competing for attention. Sizes are in pixels (px).

## 1. Summary

**1. Remove the dead end after Accept.** After Accept, every value was "-", the chart sat flat at 0 EUR and the page said "Latest value: 0.00 EUR", with no reason given. The fix was about ten terminal commands and a hand-written mapping file, found in the guide. Say "Not valued yet" and add an "Add prices" step to the page. See `21-after-accept-unmapped-1280.png`.

**2. Make the import a guided sequence with one decision per screen.** The diff has 388 words, three tables and seven headings, and its buttons sit 1,900 px down (about four phone screens). Use numbered steps, a three-sentence summary, a file list saying what each file was read as, and Continue right under the summary. See `15-diff-full-390.png`.

**3. Let me act on the page when the app asks something.** A missing purchase cost comes with a terminal command full of placeholders, and the holdings check wants a hand-made CSV file. Offer small forms: a cost and a date, "this is not a booking" for an unknown document, and one quantity box per holding. See `15-diff-full-1280.png` and `17-confirmed-wrong-file-390.png`.

**4. Lead with the answer, and make the chart say one true thing.** The page still opens on the import panel; the total is a sentence at the bottom and no gain is shown. The chart drops 2,000 EUR on 12 June: a sale whose cash is not counted, which reads as a loss. Put value, amount paid and gain in one sentence at the top, and chart the money invested beside the value, with dates. See `22-holdings-and-chart-1280.png` and `23-chart-hover-on-june-sale-1280.png`.

**5. Calm the screens: fewer modules, larger text, red only for errors.** The safety message is a full-width bar in the error red, so every state looks like an alarm. The wireframes are crowded: Home has seven modules, 416 words and 73 numbers, with 45% of words at 12 px or smaller. Set a 12 px floor and at most four modules per screen by default; move the rest one click away. See `04-after-init-empty-state-1280.png` and `W01-home.png`.

## 2. The walkthrough

**Setup.** `uv sync --locked`, `npm ci` and `npm run build`, then `uv run pg serve --port 18461` with a scratchpad data folder, the clock at 31 December 2024 (`PG_TODAY`) and recorded price replies. My files were the golden portfolio, the invented sample in `tests/fixtures/golden`. Headless Chromium at 1280 and 390 px wide.

**First impression**, before reading closely: a red alarm bar and a red error with a long folder path greet me before I have done anything, so the app looks broken and I cannot tell what it is for.

| # | Step (screenshots) | Expected | What I saw | Hesitation and its cause | Time to next move |
|---|---|---|---|---|---|
| 1 | Open before `pg init` (01 to 03) | A start page | Red error with the data folder path: "Run pg init first." Upload repeats it. Phone page 7 px too wide. | "Is it broken?" Red text, a path, a terminal command | 2 min, via the README |
| 2 | Empty page (04) | Which files, from where | A pink alarm box and a file picker | Which files? Statements too? The page never says | 10 s; 1 min to choose |
| 3 | Wrong files (06, 07, 10, 12) | A clear refusal | Image: clear refusal. Stored ZIP: read as one document, 9 of 10 ignored. Compressed ZIP: no "unzip it". One stray image blocks 11 files. | I cannot remove a file from "11 files" | 5 to 30 s; the ZIP I never noticed |
| 4 | Upload 10 files (13, 14) | Progress | "Uploading...", then the diff within two seconds | None | Instant |
| 5 | Read the diff (15) | "Did it find everything?" | "14 new transactions, 5 merged, 0 already known, 0 held back, 2 need your review." Three tables, no file list. | "Merged" and "held back" sound like errors | 3 min, still unsure |
| 6 | Review items (15) | A button per problem | Text; one ends in a `pg transfers set-cost` command with placeholders | A terminal command on a web page | 2 min, then the guide |
| 7 | Reload mid-way (16) | Lost work | The staged import came back | No "import waiting" note | 5 s |
| 8 | Holdings check (17 to 19) | Type my holdings | A CSV file request; the wrong file gave a developer error and an 861 px wide phone page; a typo gave "3 of 5 match" | A CSV header to type by hand | 5 min |
| 9 | Accept (20, 21) | "Done" and my value | No warning or message; values "-", chart flat at 0 EUR | "Did I lose everything?" 0.00 EUR, no reason | 5 to 10 min in the guide |
| 10 | Holdings and chart (22, 23) | Total and gain | After ten terminal commands and a reload: values. Total at the bottom, no gain, Value hidden on the phone, a 2,000 EUR drop on 12 June | The drop is the SAP sale; nothing says so | 30 s for the total |
| 11 | Same files again (24 to 27) | "Already have these" | Correct, after five zeros; I must still accept or discard nothing | The useful fact comes last | 20 s |
| 12 | Later (28, 30) | Carry on | App stopped: "Check your connection." After 34 days: an alarm-coloured reminder and "stale price" flags | I checked my Wi-Fi | 1 min |
| 13 | Help (04, 22) | A "?" or a guide link | No links, no product name; the tab title is "Portfolio import" | Every question led to the terminal | Not found |

From `pg serve` to a valued portfolio took me about 30 minutes, two thirds of it in the terminal and the guide. What works: a reload keeps a staged import, and duplicates are recognised by content even when renamed.

### Findings

| # | Severity | Where | What happened | Why it confused me | Recommendation |
|---|---|---|---|---|---|
| F1 | Blocking | After Accept (21) | Values "-", chart at 0 EUR; mapping and prices only in the terminal | Looks like a failed import | "Not valued yet"; no chart until prices exist; an "Add prices" step |
| F2 | Blocking | ZIP upload (07) | A ZIP of 10 documents read as one trade confirmation; 9 ignored silently | I would accept an incomplete portfolio | Detect ZIP files: unpack, or refuse with an unzip hint; per-file results |
| F3 | Major | First start (01, 03) | Error with a folder path and a terminal command, twice; phone page too wide | The page cannot help itself | `pg serve` creates the portfolio; never show paths |
| F4 | Major | Diff (15) | No list of files read, as what, or not read, though the API returns it | Did all 10 files count? | One line per file with what it was read as |
| F5 | Major | Diff (15, 19) | 388 to 453 words; buttons 1,900 px down (up to 3,584 px on a phone); zeros in the summary | Too much before one decision | Three sentences, then the decision; tables folded |
| F6 | Major | Review items (15) | Text only, one with a terminal command; the open item vanishes after Accept | Nothing to click, nothing to find later | Inline actions; a "Needs your attention" list that stays |
| F7 | Major | Holdings check (17, 18) | Needs a hand-made CSV; the wrong file gives "Line 1: the header must be..." and an 861 px phone page | A developer task | One quantity box per holding; mismatches in amber with a sentence |
| F8 | Major | Accept, Discard (09, 15, 20, 27) | Same style; Accept is irreversible but silent; no message after either | Was it safe? Did it work? | Accept primary with a "cannot be undone" line; Discard secondary; a message after each |
| F9 | Major | Value chart (22, 23) | A sale drawn as a 2,000 EUR drop; no date axis; hover text under the chart; no keyboard access; "Hover the line" even on a phone | I read it as a loss | Caption; money-invested line; month labels; pointer tooltip; trade marks |
| F10 | Major | After setup (22) | Import panel first; total at the bottom; no total row or gain; ISIN first | The answer is hardest to find | Summary header; Name, Shares, Value, Paid, Gain, total |
| F11 | Major | Phone tables (15, 22) | Amount and Value off-screen with no scroll cue; dates on two lines | I did not know it scrolls | Two-line rows on narrow screens |
| F12 | Major | Flags (22, 30) | Internal words on most rows; the plain explanation only in a hover tooltip | Looks like warnings to fix | Only flags that need action, as a sentence with the fix |
| F13 | Major | Whole page (01, 04, 30, 31) | Badge, errors, empty state and reminder all red; dark badge contrast 3.2:1 | Everything looks like an error | A neutral "Read-only · never places orders" pill, as in the wireframes |
| F14 | Major | Orientation (04, 22) | No product name, heading, help, links or "what next" | What is this, what now? | App name, a "?" panel, a "Next step" card |
| F15 | Major | Terminal (`run/cli`) | About ten commands after Accept; `pg --help` lists 18 commands, no "start here"; `--cost-eur 800,00` refused | I needed the guide for the order | `pg status` ends with "Next step: ..."; accept a decimal comma |
| F16 | Minor | App stopped (28) | "The upload failed. Check your connection and try again." | My Wi-Fi was fine | Say the app is not running and how to start it |
| F17 | Minor | File choice (12) | One image among 11 files blocks them all | I had to choose again | Removable file list; skip unsupported files |
| F18 | Minor | Re-import (24, 25) | Five zeros before "10 files already imported"; Accept offered for nothing | A meaningless decision | "Nothing new" and one OK button |
| F19 | Minor | Loading (05, 14) | "Accept or discard the staged batch below" with no batch; uploading is a greyed button | A false instruction | Drop the hint; "Reading 10 files..." |
| F20 | Minor | Wrong-file text (06, 10) | "the manual CSV format" unexplained; compressed ZIP gets the generic text | No fix named | Name the fix: unzip, or export the CSV |
| F21 | Minor | Counts (18, 26) | "11 already known"; "missing in confirmed"; "-0.1" | Good news looks like a problem | Say it in a sentence |
| F22 | Minor | Held back (32) | One purchase under New, Held back and Review items (open defect O2 in the P0 report) | Three places, one problem | One card per problem |
| F23 | Minor | Contrast (22, 31) | Axis labels 11 px at 3.5:1; button text 4.4:1; the Web Content Accessibility Guidelines ask for 4.5:1 | Hard to read | Darker colours; 12 px floor |
| F24 | Minor | Benchmarks (22) | MSCI World and the S&P 500 are fetched but shown nowhere | "Against benchmarks" is invisible | "Coming in the next phase" by the chart |

Count: 2 blocking, 13 major, 9 minor.

## 3. First-time guidance design

After `uv run pg serve`, the first run should need no guide and no terminal: one decision per screen, one sentence about what happened, the next step always visible.

**Welcome state** (fresh data folder; `pg serve` creates the portfolio):

> **Welcome to Playground**
> See your Trade Republic portfolio in euros: what you own, what you paid, and how its value changed. The app only reads files you give it. It never places orders and never asks for your Trade Republic login. Your files stay on this computer.
> **[Start with my Trade Republic files]**   Try a sample portfolio first

**Guided first import.** A progress bar, **1 Add files · 2 Check · 3 Compare (optional) · 4 Add prices · 5 Your portfolio**, with "Step 2 of 5". Finished steps stay clickable.

1. **Add files.** "From the Trade Republic app, export your transaction list (a CSV file) and your documents (PDF files): trade confirmations, savings plans, dividends and account statements. Drop them all here." Then "10 files: 1 CSV, 9 PDF", each removable. "trade-republic-app-screenshot.png will be skipped: it is an image." "trade-republic-export.zip is a ZIP archive. Unzip it and add the files inside." While reading: "Reading 10 files..."
2. **Check.** "We found 14 transactions in 9 of your 10 files. 5 of them were in two files, for example a PDF and the CSV, and we kept each once. After this import you hold 5 securities." Then one card per item: "**unbekannt_kosteninformation.pdf** could not be read. It looks like a cost information sheet, which is not a booking. [Ignore this file] [Show its text]" and "**4 Allianz shares** were moved in from another broker. No file says what you paid. [Enter the cost] [Later]". Tables sit behind "See the 14 transactions". **[Continue]**
3. **Compare (optional).** "Open the Trade Republic app and type how many shares it shows for each holding. This catches anything we missed." A mismatch turns amber: "iShares Core MSCI World: you typed 5.1, we count 5. A document may be missing." **[Add 14 transactions to my portfolio]**, above "This cannot be undone. Your files are not changed." Afterwards: "Done. 14 transactions are in your portfolio. Next: add prices, so we can value it."
4. **Add prices.** "To value your holdings we need a daily price for each. We suggest where each security trades; you confirm. Only security codes and dates are sent, never your name or amounts." **[Get prices]** gives: "4 of 5 holdings are valued. Allianz SE has no free price: [Upload a price file] or [Skip for now]." Until then the value reads "Not valued yet", never 0.00 EUR.
5. **Your portfolio.** "Your portfolio is worth 6,146.85 EUR on 31 December 2024. You paid 4,192.60 EUR for what you still hold, so it is up 1,954.25 EUR (47%)." Then: "Take a one-minute tour of this page? [Show me] [No thanks]"

**Empty states that teach.** Holdings: "Your holdings appear here after your first import. We rebuild them from your documents, oldest purchase first, as German tax rules do." Chart: "Your value over time appears once prices are added." Benchmarks: "Coming next: your value next to MSCI World and the S&P 500."

**The "what next" element.** One "Next step" card under the header, one sentence and one button: "Next step: add prices for 5 holdings. [Add prices]", "Next step: 1 file needs a decision. [Review]" or "All set. Import new files after your next trade, or in 30 days. [Import files]". `pg status` ends with the same sentence.

**When the tour appears.** Never on an empty app. Offer it once, after the first valuation, with three stops: summary, chart, holdings. Later phases add a short tour the first time a person opens each new page, instead of the six-page tour at the start in `W11-help-tour.png`.

**How help returns.** A "?" in the header opens "About this page", a glossary, "Restart the tour" and the user guide. The 30-day reminder becomes a calm "Next step": "It has been 34 days since your last import. Export your latest files to keep this up to date. [Import files]"

## 4. Visual and layout system

- **Spacing.** One 8 px grid: 4, 8, 16, 24, 32, 48. Card padding 24 px (16 px on phones), 24 px between cards, 48 px between sections.
- **Type scale.** Body 16 px, line height 1.5; table cells 14 px; labels 13 px; minimum 12 px, only for axis ticks and footnotes. Headings 28, 20 and 16 px semibold; headline numbers 32 px. At most five sizes per screen.
- **Line length.** 60 to 75 characters, a text column of about 680 px.
- **Density.** By default at most four modules and one primary action per screen. A card holds a title, one sentence, then one number, one chart or at most five rows, and at most one button.
- **Tables and sentences.** A sentence states each table's conclusion first. At most five columns; the ISIN goes under the name in grey; units go in the header, so money never wraps.
- **Colour.** Neutral surfaces, one blue for actions. Green and red only for gains and losses, with a sign. Amber means "needs you"; red means error. All text at least 4.5:1 contrast.
- **Charts.** One per screen by default, larger, with its message in the title ("Your value rose 47% on what you paid"). At most two lines, labelled at their ends; month labels; trade marks; pointer tooltip; arrow keys.
- **Phone.** One column, 16 px gutter, cards instead of wide tables, the current decision in a bottom bar, 44 px touch targets, nothing hover-only, no sideways scrolling.

**Before and after.**

- **P0 diff** (`15-diff-full-1280.png`). Before: 388 words, three tables, the decision 1,900 px down, review items as terminal commands. After: progress bar, three sentences, a file list, two "needs you" cards with buttons, Continue under the summary; one laptop screen.
- **P0 page after setup** (`22-holdings-and-chart-1280.png`). Before: import panel first; seven columns led by the ISIN; flags on most rows; total at the bottom; no dates on the chart. After: a header, "Your portfolio · 6,146.85 EUR · up 1,954.25 EUR (47%) on what you paid · prices of 31 Dec 2024", with "Import new files"; one chart, "Value and money invested"; holdings with Name, Shares, Value, Paid, Gain and a total; one footnote for the flag that needs action.
- **Home wireframe** (`W01-home.png`). Before: seven modules, 416 words, 73 numbers, "What to do next" at the bottom right. After: four modules: a headline sentence with the value and the one-year result against MSCI World, one chart, the "Next step" card (the waiting proposal) and three headlines about what you own. Market pulse moves to Market, alerts to a bell, the core and tactical split to Portfolio.

## 5. Information architecture

Two densities: **Calm**, the default, and **Detailed**, a remembered switch in the header. Detailed adds the last column below, tighter rows (28 px instead of 36 px), and folds explanations into "i" icons. It replaces the spec's "Expert mode", which only hides explanations and leaves the crowding.

| Screen | Calm default | One click away | Detailed adds |
|---|---|---|---|
| Import and data (P0 page; no wireframe yet) | The current step; open review items; last import and price dates | Transaction tables; document text | Reader names, price evidence |
| Home | Headline sentence and value; one chart against MSCI World; Next step; three headlines | Market pulse, alerts, core and tactical split | Today's change per holding |
| Portfolio | Summary sentence; value, gain, one-year return against MSCI World; one two-line chart; five-column holdings | Risk and Recommendations tabs; S&P 500 line; worst-fall band | Core or tactical, sector, price, contribution |
| Why | Verdict, three bullets, a coloured waterfall (bars that add up to the result) | By holding, By sector, Events tabs | Formulas, full tables |
| Scenarios overview | Tiles with a one-line reading; New scenario | Scoreboard as table view; Lessons tab | Worst fall, agent columns |
| Scenario builder | Three steps: change, rule and period, check and run | Weights before and after | Cost model details |
| Scenario compare | You against one scenario; verdict; three numbers; warnings; two actions | More lines, all metrics, full "why" text | Seven-metric table |
| Market | This week in plain language; your sectors | Indices, gauges, heat map, watchlist | Gauge history |
| Ideas | Three to five idea cards, one action each | Filters; feedback summary | Screen scores |
| Agents | The waiting proposal | Agent list; history | Evidence tables |
| Assistant | Chat and suggested questions | Preferences page; learned suggestions | Budget detail |

No calm screen needs scrolling at 1280 by 800 except long lists, which scroll inside their card.

## 6. Board-by-board notes

Figures from rendering each board at 1280 px: modules, words, numbers shown, smallest text, share of words at 12 px or smaller.

| Board | Modules / words / numbers / smallest / small | Verdict | Top change |
|---|---|---|---|
| Home (W01) | 7 / 416 / 73 / 10 px / 45% | The value headline works; seven panels compete; the next step sits bottom right. | Four modules, "Next step" second. |
| Portfolio (W02) | 11 / 371 / 98 / 10 px / 39% | Every figure at once; 86 px past its frame. | Sentence and three numbers; Risk and Recommendations as tabs. |
| Why (W03) | 5 / 377 / 71 / 10 px / 18% | The best board: words before numbers. Long paragraph; waterfall caption cut off. | Verdict plus three bullets; coloured waterfall. |
| Scenario overview (W04) | 4 / 718 / 92 / 10 px / 38% | The most text of any board; lessons overflow under the next card. | Tiles only; scoreboard and lessons one click away. |
| Scenario builder (W05) | 4 / 377 / 62 / 10 px / 17% | Checks before running are excellent; the table spills 96 px out. | Three numbered steps; no scenario list here. |
| Scenario compare (W06) | 4 / 555 / 98 / 10 px / 21% | Five lines and a 35-number table crowd a strong narrative. | You against one scenario, verdict first. |
| Market (W07) | 6 / 516 / 81 / 10 px / 41% | The plain-language note is the heart but sits at the bottom. | Note first; indices and gauges one click away. |
| Ideas (W08) | 4 / 528 / 49 / 10 px / 41% | Four controls per row; up and down arrows read as "move", not "like". | Cards with one action; "Interesting" and "Not for me". |
| Agents (W09) | 4 / 500 / 58 / 10 px / 16% | The proposal card is clear and safe. | Proposal full width; agents as a status line. |
| Assistant (W10) | 4 / 568 / 58 / 10 px / 30% | Good suggested questions; answer lines exceed 100 characters. | Chat 70 characters wide; preferences on their own page. |
| Help (W11) | 5 / 554 / 5 / 12 px / 13% | Three help layers at once; glossary text runs through its border. | A three-stop tour after data exists. |
| Runs (W12) | 2 / 301 / 72 / 10 px / 48% | Clear for experts. | Trial count as a sentence by the button. |
| Run detail (W13) | 5 / 499 / 90 / 10 px / 45% | Plain language first, warnings beside it: the pattern to copy. | Fill assumptions into a tab. |
| Compare runs (W14) | 4 / 263 / 38 / 10 px / 22% | Calm and readable. | Only differing parameters. |
| Sweep (W15) | 3 / 284 / 71 / 10 px / 35% | The luck warning is well placed. | Axis names in words. |
| Validation (W16) | 5 / 367 / 61 / 10 px / 35% | Verdict first works; three small charts compete. | Charts behind "Show evidence". |
| Data (W17) | 4 / 301 / 72 / 11 px / 45% | Research data only, nothing about my prices. | A "Your portfolio data" section. |
| Replay (W18) | 6 / 309 / 78 / 10 px / 32% | A clear media-player idea. | Merge "Realism" and "When finished". |
| Paper (W19) | 4 / 317 / 59 / 12 px / 38% | Dense but purposeful. | Kill criteria by the equity figure. |

Missing from all boards: a first-run board, empty states, import, review and prices screens, and any phone layout. Nine boards overflow their frame or a card: W02, W03, W04, W05, W06, W08, W11, W13, W16.

## 7. Backlog for the next phase

UX1 to UX5 can go into the next implementation plan as written. O3 and O4 are open defects from the P0 report.

| ID | Size | Item | Screens | Acceptance test |
|---|---|---|---|---|
| UX1 | M | First run and the value dead end: `pg serve` creates the portfolio; welcome state; progress bar; "Next step" card; "Not valued yet" instead of 0.00 EUR | Import page | Browser test (Playwright), empty data folder: welcome text shows; after Accept without prices, no "0.00 EUR" and the card reads "Next step: add prices for 5 holdings." |
| UX2 | M | Diff redesign: three-sentence summary, per-file list, decision under it (bottom bar on phones), tables folded, "Nothing new", ZIP detection | Import page, upload API | Golden import at 390 by 844: Continue visible without scrolling; 10 file lines; re-upload shows "Nothing new: these 10 files were already imported."; a stored ZIP gets the unzip message |
| UX3 | M | Act on the page: transfer cost form (comma or dot), dismiss an unknown document with a reason, quantity boxes, "cannot be undone" line, messages after Accept and Discard, open items kept visible | Import page, review and transfer API | From the page alone the golden import reaches "5 of 5 match", accepts "800,00" and ends with no open items; after Accept: "Done. 14 transactions are in your portfolio." |
| UX4 | M | Prices on the page: suggested symbols (lookup behind a button that says what it sends), mapping confirmation, "Get prices" for prices and rates, price file upload | Import page, instrument and price API | Fresh data folder, recorded replies: from `pg serve` to "6,146.85 EUR" with no terminal command |
| UX5 | M | Visual baseline: neutral read-only pill, 12 px floor, 4.5:1 contrast, button hierarchy, summary header, five-column holdings with a total, phone cards | Import page | Automated contrast check passes; no text under 12 px; at 390 px every Value and Gain shows without sideways scrolling |
| UX6 | M | Chart: caption, month labels, money-invested line, trade marks, keyboard (closes O4) | Import page, Portfolio | Arrow keys move the day; 12 June has a sale mark |
| UX7 | S | Flags as sentences with their fix; information-only flags hidden | Import page, `pg holdings` | No flag words in the golden table; Allianz shows one sentence with its fix |
| UX8 | S | Messages of F16 to F21 | Import page | Component tests match section 3's wording |
| UX9 | S | Terminal: "Next step: ..." in `pg status`; `--cost-eur` accepts 800,00; plain labels beside codes (closes O3) | CLI | After the golden accept: "Next step: map 5 instruments to a price source" |
| UX10 | M | Help panel with a P0 glossary; three-stop tour after the first valuation | Import page, P1 pages | "?" works everywhere; the tour never returns after "No thanks" |
| UX11 | M | Wireframes: first-run, empty, import and prices boards; fix the nine overflows; phone Home and Portfolio | Wireframes | Every board fits its frame, no text under 12 px |
| UX12 | M | Calm and Detailed switch with section 5's defaults | Home, Portfolio, Why | Calm screens: at most four modules, fit 1280 by 800 |
| UX13 | M | Home calm default | Home | Four modules; "Next step" second |
| UX14 | M | Portfolio and Why calm defaults | Portfolio, Why | Sentence and three numbers above the chart |
| UX15 | L | Scenarios: tiles with readings, one-scenario compare, three-step builder | Scenario pages | A first-timer builds and compares a scenario without scrolling |
| UX16 | M | Section 6 changes to Market, Ideas, Agents, Assistant | Those pages | Each opens with a sentence or its one decision |

## 8. Appendix: screenshots

All in `screenshots/P0/`. Walkthrough shots exist at `-1280` (desktop) and `-390` (phone); some also at `-390-fold` (the phone's first screen). Board shots are 1280 wide.

- 01-first-open-no-init: first visit before `pg init`
- 02-no-init-files-chosen: files chosen with nothing set up
- 03-no-init-after-upload: the error twice; phone page 7 px too wide
- 04-after-init-empty-state: empty state in alarm colours
- 05-loading-state: loading, with a false "staged batch" hint
- 06-wrong-file-png: an image refused
- 07-wrong-file-zip: a stored ZIP read as one document
- 08-upload-blocked-while-zip-batch-staged: the next upload blocked
- 09-after-discard: Discard leaves no message
- 10-wrong-file-zip-compressed: a compressed ZIP, no unzip hint
- 11-wrong-file-bank-csv: another bank's CSV; Accept offered for nothing
- 12-mixed-files-with-one-image: one image blocks eleven files
- 13-ten-files-chosen: my ten files chosen
- 14-uploading: the only progress sign
- 15-diff-full: the diff
- 16-reload-while-staged: the diff after a reload
- 17-confirmed-wrong-file: developer error; phone page 861 px wide
- 18-confirmed-mismatch: a typo in the holdings check
- 19-confirmed-all-match: 5 of 5 match
- 20-accepting: buttons grey out, no message
- 21-after-accept-unmapped: values "-", chart at 0 EUR
- 22-holdings-and-chart: valued holdings and chart
- 23-chart-hover-on-june-sale: the sale that reads as a loss
- 24-same-files-again: re-import of the same files
- 25-renamed-duplicate-pdf: a renamed copy
- 26-statement-already-known-expanded: the statement check
- 27-after-accepting-statement: Accept of an empty batch, no message
- 28-server-stopped-upload-error: app stopped: "check your connection"
- 29-server-stopped-reload: the browser error page
- 30-reminder-34-days-later: reminder and stale flags
- 31-dark-mode: dark mode
- 32-held-back-row: one problem in three places
- W01-home: Home
- W02-portfolio: Portfolio
- W03-why-did-it-perform: Why did it perform
- W04-scenario-overview: Scenarios overview
- W05-scenario-builder: Scenario builder
- W06-scenario-compare: Scenario compare
- W07-market: Market
- W08-ideas: Ideas
- W09-agents: Agents
- W10-assistant: Assistant
- W11-help-tour: Help and tour
- W12-adv-runs: Advanced: runs
- W13-adv-run-detail: Advanced: run detail
- W14-adv-compare-runs: Advanced: compare runs
- W15-adv-sweep: Advanced: sweep
- W16-adv-validation: Advanced: validation
- W17-adv-data: Advanced: data
- W18-adv-replay: Advanced: replay
- W19-adv-paper: Advanced: paper accounts

Terminal transcripts: `run/cli/c01` to `c04`. Board metrics: `run/wireframe-metrics.json`.
