# Playground: Stock Trading Portfolio Analysis

Import your Trade Republic portfolio, reconcile holdings, compute FIFO cost basis and daily value in EUR with correct FX conversion.

## Documentation

Research and planning for an automated stock and options trading bot.

- [Trading bot research and recommendations](docs/trading-bot-research.md): brokerage APIs, open-source frameworks, market data, rules and risks, reference architecture, roadmap, and the scope questions to answer before implementation.
- [Decisions and implementation plan](docs/implementation-plan.md): broker, data, framework, architecture and roadmap decisions for a Germany-based, intraday US stocks and options bot with an LLM component.
- [How trading bots actually perform: the evidence](docs/bot-performance-evidence.md): measured results from academic studies, regulators, trading platforms, LLM trading contests and long-run strategy indices, with a synthesis for this project.
- [Designing a trading-bot playground](docs/playground-design.md): requirements, buy-versus-build survey, architecture with three clocks, simulation realism defaults, experiment layer and validation protocol, safety, build plan and recommendation for a fake-money playground on real market data.
- [Playground principal design specification](docs/playground-spec.md) (v0.3, portfolio-first): decision record from the scope interview, the reframe around your real portfolio (Trade Republic import, performance and attribution, scenarios, agents that draft proposals, market and ideas, preference profile), component design, APIs, data model, phased development plan with hour estimates, and the cost model.
- [Dashboard wireframes](docs/wireframes/README.md): nineteen annotated screens of the web dashboard: Home, Portfolio, Why did it perform, Scenarios (overview with lessons, build, compare), Market, Ideas, Agents, Assistant, Help, and the advanced research lab, with the built-in explanation system.
- [Phase P0 implementation plan](docs/plans/P0-implementation-plan.md): the build contract for import and value, module by module, with its test plan and its twelve work packages.
- [P0 user acceptance test script](docs/uat/P0-uat.md): the numbered steps to check phase P0 with your own Trade Republic files, on your own Mac.
- [Phase P0 report](docs/plans/P0-report.md): what was built, the review and test results, the open defects, the deviations from the plan, and the recommended scope for phase P1.

## Develop

Set up the environment and run tests:

```bash
uv sync --locked
./scripts/check.sh
PG_TODAY=2024-12-31 uv run pg --version
```

This project uses Python 3.13, ruff for formatting and linting, mypy for type checking, pytest with socket disabled for testing, and the `pg` CLI entry point.

The PDF test fixtures are written from their text files, which are the source of truth. After adding or changing a text fixture under `tests/fixtures/tr/text/`, write the PDFs again and list the new files in `tests/fixtures/MANIFEST.yaml`:

```bash
uv run python tests/fixtures/tr/make_pdfs.py
```

`pytest --update-goldens` rewrites the expected JSON files from the current output. Check every rewrite in the git diff before you commit it.

## Running the app

The app is a command line tool, `pg`, with a small import page in your browser. It reads the
transaction exports and PDF documents you download from Trade Republic. It shows your holdings and
the daily value of your portfolio in EUR. It never places, routes or automates an order, and it
holds no broker credentials. Nothing leaves your machine except requests for public prices and
exchange rates.

You need [uv](https://docs.astral.sh/uv/), which sets up Python 3.13 for you. Node 20 or newer
(tested with Node 22) is needed only for the import page. To see the app work with no further setup
and no network, run the invented sample portfolio from the repository root. Its data goes to
`.e2e/data`, which git ignores:

```bash
uv sync --locked
uv run pg --data-dir .e2e/data init
uv run pg --data-dir .e2e/data import tests/fixtures/golden/inputs/tr_transactions_2024.csv \
    tests/fixtures/golden/inputs/pdf
uv run pg --data-dir .e2e/data accept latest
uv run pg --data-dir .e2e/data holdings
```

**To use your own Trade Republic files on your Mac, follow the
[P0 user acceptance test script](docs/uat/P0-uat.md).** It is the full walk-through in numbered
steps: install, export your files from Trade Republic, import and check them against the Trade
Republic app, fetch prices, see your value and the benchmarks in EUR, use the import page, and
report a problem without sharing personal data.

The import page has a red **NO REAL ORDERS** badge, the import diff, an accept button, a holdings
table and a value chart. `pg serve` serves it, together with the API, on `127.0.0.1` only. Build
the page once, then serve both:

```bash
(cd web && npm ci && npm run build)
uv run pg --data-dir .e2e/data serve
```

Open `http://127.0.0.1:8765`. `web/` also has its own checks: `npm run typecheck`, `npm test` for
the component tests (Vitest) and `npx playwright test` for the browser test (Playwright), which
`scripts/e2e_web_server.sh` seeds a fresh data directory for.

`scripts/e2e.sh` runs the full end-to-end scenario phase P0 is accepted against (implementation
plan section 7.6): 32 steps, from a clean environment through the CLI, the API and the page, ending
with a check that the run left the working tree exactly as git already has it.
