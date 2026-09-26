# Dashboard wireframes

Low-fidelity, clickable wireframes of the playground's web dashboard, designed around the decision record in [../playground-spec.md](../playground-spec.md).

**View them**: the artboards render on the Claude Design canvas at https://claude.ai/artifact/NkeAGz5uqV72dV7dUChFQF (private to the owner until shared). Use Play on any board to click through the navigation.

**Source**: `project/canvas.json` is the canvas index (board positions, row titles, annotation stickies); each `project/*.dc.html` file is one artboard in the Design Component format used by that canvas. They are kept here so the wireframes are versioned with the spec. They are not standalone web pages.

| Board | Screen | What it demonstrates |
|---|---|---|
| `Main.dc.html` | Home | "What to do next" guidance, paper-account heartbeat, alerts, data health, the SIMULATION badge and Explain-mode switch present on every screen |
| `Runs.dc.html` | Runs and new run | Run list with the trial counter next to results; a new-run form generated from the strategy's parameter schema, each field with a definition |
| `RunDetail.dc.html` | Run detail | Plain-language summary before any number, warnings next to the numbers they concern, an open metric tooltip (deflated Sharpe), fill assumptions, trades with slippage against the mid |
| `Compare.dc.html` | Compare | Two runs side by side with a plain-language statement of the difference, fairness check, metric deltas and a highlighted parameter diff |
| `Sweep.dc.html` | Sweep and leaderboard | Trial budget bar, objective over trials, leaderboard ranked by deflated Sharpe with neighbouring values shown to reveal plateaus |
| `Validation.dc.html` | Validation report | A verdict with reasons, the gate table with what each gate protects against, walk-forward windows, out-of-sample path distribution, robustness grid, the one-time holdout evaluation |
| `Data.dc.html` | Data and health | Coverage by dataset and year with point-in-time flags, quality checks, snapshots, recorder status |
| `Replay.dc.html` | Replay | Media-player controls (day, speed, pause), a chart that never shows the future, a plain-language decision log, the virtual account and the realism settings in force |
| `Paper.dc.html` | Paper accounts | Champion and challengers on the same data stream, risk-limit usage bars, kill switches with confirmation, shadow-fill comparison against the Alpaca paper account, pre-registered kill criteria, Telegram status |
| `Assistant.dc.html` | Assistant | Cited answers about your own runs, a proposal as a configuration diff you approve or reject (approval counts as a trial), the budget meter with a hard cap, and the statement of what the assistant cannot do |
| `Tour.dc.html` | Help system | First-run tour step, the "Explain this page" side panel, the searchable glossary with plain and precise definitions, and the switch to expert mode |

Example numbers on the boards are for layout; where real figures exist (the SPY 10-month moving-average results) they come from the evidence document.
