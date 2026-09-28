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
- [P0 user acceptance testing guide](docs/uat/P0-macos.md): the steps to check phase P0 with your own Trade Republic files, on your own Mac.

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

## Run phase P0

Phase P0 imports your Trade Republic exports, reconciles them against your own count, and shows a
daily value of your portfolio in EUR. The app never places, routes or automates an order; it holds
no broker credentials. See `docs/uat/P0-macos.md` for the full walk-through with your own files; the
commands below use the synthetic golden portfolio (`docs/plans/P0-implementation-plan.md` section
1.2) so they work in this repository with no setup:

```bash
uv run pg --data-dir .e2e/data init
uv run pg --data-dir .e2e/data import tests/fixtures/golden/inputs/tr_transactions_2024.csv \
    tests/fixtures/golden/inputs/pdf/*.pdf
uv run pg --data-dir .e2e/data accept latest
uv run pg --data-dir .e2e/data holdings
```

The same phase also has a FastAPI backend (`pg serve`, bound to `127.0.0.1` only) and a minimal
Vite and React import page: a red **NO REAL ORDERS** badge, the import diff, an accept button, a
holdings table and a value chart. Build the page once, then serve both together:

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
