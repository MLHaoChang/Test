# Dashboard wireframes

Low-fidelity, clickable wireframes of the playground's web dashboard, designed around the decision record in [../playground-spec.md](../playground-spec.md) (version 0.2, portfolio-first).

**View them**: the artboards render on the Claude Design canvas at https://claude.ai/artifact/NkeAGz5uqV72dV7dUChFQF (private to the owner until shared). Use Play on any board to click through the navigation.

**Source**: `project/canvas.json` is the canvas index (board positions, row titles, annotation stickies); each `project/*.dc.html` file is one artboard in the Design Component format used by that canvas. They are kept here so the wireframes are versioned with the spec. They are not standalone web pages.

## How the app is organised

Every screen carries the same top navigation and a red badge, **NO REAL ORDERS · portfolio read-only**. The everyday screens are Home, Portfolio, Scenarios, Market, Ideas, Agents and Assistant. The research tools from version 0.1 (runs, sweeps, validation, replay, paper accounts, data) sit behind an **Advanced** menu with their own sub-navigation row; scenarios, ideas and agents call them underneath.

| Row | Board | Screen | What it demonstrates |
|---|---|---|---|
| 1 | `Main.dc.html` | Home | Portfolio value and today's change against MSCI World EUR and the S&P 500, a 12-month sparkline, the market pulse, a proposal awaiting your approval, live news filtered to holdings and watchlist with a one-line "why it matters", what to do next, alerts |
| 1 | `Portfolio.dc.html` | Portfolio | Value against both benchmarks over selectable periods, helpers and detractors, holdings with ISIN and sleeve tags (core, tactical), risk against your own limits, recommendations tagged screen-backed or opinion |
| 1 | `Why.dc.html` | Why did it perform | A plain-language attribution paragraph, the waterfall (selection, allocation, currency, fees, other), by-holding and by-sector tables, the news events behind the numbers, a short-window warning |
| 2 | `ScenarioBuilder.dc.html` | Scenarios: build | Start from the real portfolio, list of changes (add, replace, remove, reweight), rule, period and costs; checks against the preference profile and a hindsight warning before running; weights before and after |
| 2 | `ScenarioCompare.dc.html` | Scenarios: compare | Overlay of three scenarios, the real portfolio and the benchmark; side-by-side metrics in plain words; the "why S1 won, and what it cost" narrative with sources; hindsight and limit warnings; run forward with an agent, turn into a proposal |
| 2 | `Market.dc.html` | Market | Index tiles, risk gauges with a plain-language state, sector heatmap with your weights, watchlist, the weekly note labelled as opinion with sources, sectors to look into tied to your holdings |
| 2 | `Ideas.dc.html` | Ideas | Filters from the preference profile, tier 1 screen-backed ideas each with a five-year scenario test and a fit check, tier 2 opinion cards with confidence and sources, thumbs feedback, what the feedback taught the app |
| 3 | `Agents.dc.html` | Agents and proposals | Core, tactical and scenario agents with their status; a proposal with trades, reasons, evidence, risk check, cost and German tax estimate; approve, edit, reject; the statement that the app never places orders; "what if you had followed" |
| 3 | `Assistant.dc.html` | Assistant and preferences | A cited answer to "why did the trend filter lose", a follow-up that creates a scenario and counts against the trial budget, the preference profile, learned-from-feedback suggestions awaiting confirmation, the budget meter |
| 3 | `Tour.dc.html` | Help system | First-run tour step on the Scenarios page, the "Explain this page" side panel, the searchable glossary (open on hindsight bias) with plain and precise definitions, and the switch to expert mode |
| A | `Runs.dc.html` | Advanced: runs | Run list with the trial counter next to results; a new-run form generated from the strategy's parameter schema |
| A | `RunDetail.dc.html` | Advanced: run detail | Plain-language summary before any number, warnings next to the numbers they concern, an open metric tooltip, fill assumptions, trades with slippage |
| A | `Compare.dc.html` | Advanced: compare runs | Two runs side by side with a plain-language statement of the difference, fairness check, metric deltas, parameter diff |
| A | `Sweep.dc.html` | Advanced: sweep | Trial budget bar, objective over trials, leaderboard ranked by deflated Sharpe with neighbouring values |
| A | `Validation.dc.html` | Advanced: validation | Verdict with reasons, gate table, walk-forward windows, out-of-sample path distribution, robustness grid, one-time holdout |
| A | `Data.dc.html` | Advanced: data | Coverage by dataset and year with point-in-time flags, quality checks, snapshots, recorder status |
| A | `Replay.dc.html` | Advanced: replay | Media-player controls, a chart that never shows the future, a decision log, the virtual account and the realism settings |
| A | `Paper.dc.html` | Advanced: paper accounts | Champion and challengers, risk-limit usage, kill switches, shadow-fill comparison against the Alpaca paper account, Telegram status |

Example numbers on the boards are for layout only (the portfolio, holdings and returns are invented); where real figures exist (the SPY 10-month moving-average results on the advanced boards) they come from the evidence document.
