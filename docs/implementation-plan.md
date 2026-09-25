# Trading bot: decisions and implementation plan

Date: 25 September 2026. Companion to [trading-bot-research.md](trading-bot-research.md), which holds the general research. This document applies it to your answers and proposes concrete decisions. Nothing has been built yet.

**Your scope (from your answers):** resident in Germany; US stocks and US options; about 50,000 EUR; no strategy yet; current broker Trade Republic, open to switching; intraday trading; Python; about 100 USD per month for data and hosting; fully automatic except for very large trades; an LLM should play a role.

## 1. Summary of decisions

| Decision | Recommendation | Why, in one line |
|---|---|---|
| Live broker | **Interactive Brokers** (IBKR Pro account, served by IBKR Ireland for German residents) | Accepts German residents, US stocks and options including index options, EUR base currency with cheap conversion, the cheapest real-time data, and every framework supports it |
| Fallback broker | **tastytrade** | Accepts German residents, options-native hosted API with bot-friendly authentication, no gateway to run; but no Lumibot support, USD-only funding, and weaker stock data |
| Trade Republic | Keep it for long-term holdings if you like; **not usable for the bot** | No official API and no US exchange-traded options |
| Development and stock backtests | **Free Alpaca paper account** | Free historical consolidated minute bars back to 2016 and a free paper account for testing the plumbing, usable from Germany |
| Framework | **Lumibot** for phases 1 to 3, re-evaluated before options go live | Fastest path to a working intraday bot on IBKR and Alpaca; if it limits you, NautilusTrader (IBKR-native, production grade) is the upgrade path |
| Options history | **ThetaData** Value (40 USD) to start, Standard (80 USD) when options backtesting starts in earnest | Only affordable source of intraday options history |
| LLM | **Claude Opus 5** (`claude-opus-5`) through the official Anthropic Python SDK, in advisory roles only | Analysis, event screening, reporting and a chat interface; never a direct path to an order |
| Hosting | A small Linux VPS in a US-East region running Docker | Reliability over latency; the market is open 15:30 to 22:00 Berlin time |
| Frequency to start | Intraday capable from day one, but first strategies on 5 to 15 minute bars | Sub-minute trading is where costs and data quality kill retail bots |

The rest of this document explains each decision and ends with a revised roadmap and a checklist of what to do this week.

## 2. Broker choice for a German resident

Being in Germany removes some options and adds constraints that the research report did not cover.

### 2.1 Why Trade Republic cannot be the bot's broker

Trade Republic has no official API. The libraries you can find (`pytr`, `trade-republic`, `trade-republic-uapi`) reverse-engineer the private app backend, log in with your phone number and PIN, and describe themselves as unofficial and at your own risk ([pytr](https://github.com/nborrmann/pytr), [trade-republic on PyPI](https://pypi.org/project/trade-republic/)). Trade Republic also does not offer US exchange-traded options; its "derivatives" are bank-issued warrants and knock-outs, which are a different product with a different pricing model. Running a bot through an unofficial API risks your account and gives you no options market at all. You can keep the account for long-term holdings, where its automatic German tax handling is convenient.

### 2.2 Who accepts German residents and offers US options

| Broker | Accepts German residents | US stock options | Index options (SPX) | API needs a local gateway | Paper account | Costs that matter for intraday | Framework support | Funding in EUR |
|---|---|---|---|---|---|---|---|---|
| **Interactive Brokers** (IBKR Ireland) | Yes | Yes | Yes | Yes (TWS or IB Gateway; weekly two-factor login) | Yes | Stocks about 0.0035 to 0.005 USD per share with a minimum per order; options 0.65 USD per contract each way, lower on tiered pricing; data about 15 USD per month | Lumibot, LEAN, NautilusTrader, ThetaGang, ib_async | Yes, EUR base currency, cheap FX |
| **tastytrade** | Yes ([supported countries](https://support.tastytrade.com/support/s/solutions/articles/43000435355)) | Yes | Yes | No (hosted REST and WebSocket, OAuth2 with non-expiring refresh tokens) | Yes (resets daily) | Stocks 0 USD; options 1 USD per contract to open, 0 to close, capped at 10 USD per leg; data free with account | LEAN (young plugin), community Python SDK; not Lumibot | No, USD wires or Wise only |
| **Alpaca** (EU entity) | Yes, since July 2026 via its Spanish entity ([passporting](https://alpaca.markets/blog/alpaca-completes-eea-passporting-to-29-countries-expanding-access-to-regulated-investment-services-across-europe/)) | **"Coming soon"** for EEA clients ([Alpaca Europe](https://alpaca.markets/eu/broker)) | No | No | Yes (paper is global and free) | Stocks 0 USD | Lumibot, LEAN, alpaca-py | EU entity trades US stocks in EUR |
| **Tradier** | Yes ([permitted countries](https://support.tradier.com/kb/guide/en/permitted-and-blocked-countries-CLzSa0D1rf/Steps/4997916)) | Yes | Yes | No | Sandbox with delayed data | Pro plan 10 USD per month, then no commissions; data free with account | Lumibot, LEAN; no official Python SDK | USD |

Two EU-specific rules apply at any of these brokers:

- **PRIIPs**: as an EU retail client you cannot buy shares of US-domiciled ETFs such as SPY or QQQ because they have no Key Information Document. You can trade options on them, and shares delivered through an option exercise or assignment can be held and sold ([Elite Trader discussion of IBKR practice](https://www.elitetrader.com/et/threads/priips-kids-for-us-etf-long-options-in-the-uk-eu-and-interactive-brokers.345811/), [Logical Invest](https://logical-invest.com/u-s-etfs-unavailable-to-european-investors-pripps-and-kid/)). Individual US stocks are unaffected. For index exposure, use SPX options (cash settled, no shares involved) or UCITS ETFs on European exchanges.
- **Non-professional market data status** is unchanged: you qualify as a natural person trading your own money. Keep the account in your own name, not a company.

### 2.3 Recommendation: IBKR, with tastytrade as the alternative

IBKR wins on four points that matter for your case: EUR funding with base currency EUR and conversion at near-interbank rates, availability of both stock and index options, the cheapest real-time data (about 15 USD per month for consolidated US stock and options quotes), and support from every framework worth using. It is also the broker you are least likely to outgrow.

Its costs are operational. The API talks to a running IB Gateway, the gateway needs a weekly re-login with two-factor authentication, and the community tooling that automates this (`gnzsnz/ib-gateway-docker`, `ibg-controller`) needs care. Stock commissions are not zero, so a strategy that trades hundreds of times a day will pay for it; at 5 to 15 minute bars this is manageable, and the report's phase 3 measures it before you scale. Apply for an **IBKR Pro** account: Lite accounts have no API access.

tastytrade is the right choice if you want the least operations work and expect options to dominate: its hosted API with refresh tokens that never expire is the most bot-friendly authentication of any broker, and it accepts German residents. You would give up Lumibot (use LEAN or a custom stack on the community `tastyware/tastytrade` SDK), fund in USD by wire or Wise, and pay 1 USD per options contract to open.

Alpaca is no longer the first pick for you: its EU entity does not yet offer options to EEA clients. It remains the best free development environment. Open a paper account, use its free historical consolidated minute data for stock backtests, and develop the plumbing against it before the IBKR account is approved. If you would rather have Alpaca for live stock trading as well, ask Alpaca support whether a German resident can still open an account with the US entity that offers options; the answer was not verifiable from public pages.

## 3. Data plan inside 100 USD per month

| Item | Purpose | Monthly cost (approximate) |
|---|---|---|
| IBKR market data: US Securities Snapshot and Futures Value Bundle plus the US Equity and Options Add-On Streaming Bundle | Real-time consolidated stock quotes and OPRA options quotes for live trading, 100 simultaneous streaming symbols | About 15 USD; the base bundle is commonly waived above a monthly commission threshold, verify on IBKR's pricing page |
| Alpaca paper account | Free historical consolidated minute bars for stock backtests, free paper trading, free news feed | 0 USD |
| ThetaData Value, later Standard | Historical options chains for backtesting; Value gives 4 years of 1-minute data, Standard adds tick quotes and 8 years | 40 USD, later 80 USD |
| VPS (for example Hetzner or DigitalOcean, smallest instance, US-East) | Always-on host for the bot and IB Gateway in Docker | About 5 USD |
| Claude API | LLM roles in section 6 | 25 to 45 USD at the usage modelled in section 6, less with prompt caching |

Two variants:

- **Variant A, fits 100 USD including the LLM**: IBKR data 15 + ThetaData Value 40 + VPS 5 + LLM 25 to 40. Recommended to start.
- **Variant B, maximum data**: IBKR data 15 + ThetaData Standard 80 + VPS 5 = 100, with the LLM on top. Move to this when you begin options backtesting with tick-level fills.

IBKR itself cannot be your backtest source: it has no historical data for expired options and rate-limits historical requests hard. Stock backtests come from Alpaca's free history, options backtests from ThetaData.

## 4. Framework: Lumibot first, with a defined exit

You asked for a recommendation between building and adopting. Adopt for the engine, own the policy:

- **Lumibot** gives you one Python `Strategy` class that runs unchanged in backtests (Alpaca history or ThetaData), on the Alpaca paper account, and live on IBKR. It supports intraday loops (its `sleeptime` can be seconds or minutes), option chains, Greeks and multi-leg helpers, and it is released weekly (4.6.0 on 24 September 2026). It is GPL-3.0, which is irrelevant for personal use.
- **You write** the risk manager, the approval gate, the LLM module, the state store, reconciliation checks, alerts and deployment. These are the parts that make or break a live bot and they are portable to any framework.
- **Exit criterion**: at the end of phase 2 (paper trading), if Lumibot's polling loop, fill modelling or IBKR integration are limiting you, port the strategy and your own modules to **NautilusTrader**, which is IBKR-native, event-driven and built for exactly this, at the cost of a steeper learning curve. If you switch to tastytrade, the equivalent move is to **LEAN** or a custom stack on the tastytrade SDK.

Not recommended for you: Backtrader (unmaintained), the crypto bots (no stock or options connectors), the LLM "hedge fund" repositories (they do not trade), Trade Republic wrappers (unofficial).

## 5. Architecture adapted to your requirements

The reference architecture in the research report stands. Three additions come from your answers.

### 5.1 Intraday specifics

- One long-running process per trading day, started by a scheduler 30 minutes before the US open and stopped after the close, with the exchange calendar from `pandas_market_calendars` and all scheduling in the America/New_York time zone. The US and EU switch daylight saving on different dates, so for two to three weeks a year the market opens at 14:30 or 16:30 Berlin time instead of 15:30; never hard-code the offset.
- Streaming quotes over the IBKR API rather than polling, within the 100 streaming-symbol limit; a symbol universe of at most 50 to 80 liquid large caps to begin with.
- Bar-close discipline: strategies act only on completed bars, and the backtester must enforce the same.
- Hard daily limits in the risk manager: for 50,000 EUR a reasonable starting set is a maximum loss per day of 1% (500 EUR, then flatten and halt), a maximum position of 10% of equity per symbol, a maximum gross exposure of 100% (no leverage to start), a maximum of 20 orders per minute, and a symbol allowlist. Options add a maximum defined risk per position of 2% of equity and a rule to close or roll short legs before the last hour of expiration day.

### 5.2 The approval gate for very large trades

"Fully automatic unless very big" becomes a threshold in the risk manager:

- Orders below the threshold execute automatically.
- Orders above it are held and sent to you on Telegram with the reason, size, price and the LLM's one-paragraph explanation; you reply approve or reject. If you do not answer within a timeout (for intraday, 3 to 5 minutes), the order is **dropped**, not executed, and the strategy is told so it can re-plan.
- Proposed defaults, for you to confirm: stock orders above 10% of equity (about 5,000 EUR), options positions with a maximum loss above 2% of equity (about 1,000 EUR), and any order that would take gross exposure above 80%.
- The gate also serves as the manual kill switch: the same Telegram bot accepts `pause`, `flatten` and `status`.

Because the market is open in your evening, decide whether you want to be reachable 15:30 to 22:00 or whether the bot should simply skip trades above the threshold on days you are not.

### 5.3 Germany-specific operating rules

- **Taxes are your job.** IBKR and tastytrade do not withhold German tax. Gains, losses, option premiums and US dividends go into the Anlage KAP of your tax return; the flat rate is 25% plus solidarity surcharge (26.375%) plus church tax if applicable, after the 1,000 EUR saver's allowance. The 20,000 EUR annual cap on offsetting losses from derivatives (Termingeschäfte, which includes options) was abolished by the Jahressteuergesetz 2024, retroactively for all open cases, so options losses now offset other capital income in full ([Flick Gocke Schaumburg](https://www.fgs.de/en/news-and-insights/blog/detail/update-zum-jahressteuergesetz-2024-jstg-2024-rueckwirkender-entfall-der-verlustverrechnungsbeschraenkung-fuer-termingeschaefte-und-forderungsausfaelle-im-privatvermoegen), [VLH](https://www.vlh.de/kaufen-investieren/geldanlage/verluste-aus-termingeschaeften-das-aendert-sich.html), [Ecovis](https://ecovis-kso.com/blog/verlustverrechnung-termingeschaefte-2024/)). Premiums you receive for writing options are taxable when received, with the cost of closing deductible. Germany has no wash-sale rule; FIFO applies. An intraday bot produces thousands of trades a year, so plan the trade log export (IBKR Flex Queries) and a tax tool or a Steuerberater from the start; ask the adviser once whether your volume could ever be classified as commercial trading, which is rare for private individuals but worth a question.
- **US withholding**: file the W-8BEN with the broker so US dividend withholding is 15% under the treaty rather than 30%.
- **Currency**: keep EUR as base currency at IBKR and convert what you need to USD; a USD balance carries exchange-rate risk that is not part of your strategy. Track P&L in USD for the strategy and in EUR for taxes.
- **Investor protection**: IBKR Ireland is an EU-regulated firm under MiFID II with an EU compensation scheme; tastytrade is a US broker under SIPC. Both are standard; neither covers trading losses.

## 6. The LLM component

You want an LLM involved. The safe and useful way is advisory: the model produces structured analysis that deterministic code consumes, and no free text ever becomes an order.

### 6.1 Roles, in order of value

1. **Pre-market briefing and watchlist screen**: once a day, read the news feed (Alpaca's free Benzinga news API) and the earnings and macro calendar, and return a structured object per symbol: `tradeable` yes/no, event risk (earnings today, FDA decision, index rebalance), a sentiment label with a confidence, and one sentence of reasoning. The strategy only trades symbols marked tradeable.
2. **Event guard during the day**: when a headline arrives for a symbol you hold or are about to trade, classify it (material or noise, direction) so the risk manager can pause entries or tighten limits for that symbol.
3. **Trade explanations and the daily report**: every order gets a one-paragraph explanation of the deterministic signal, in plain language, sent to Telegram; at the close, a report of P&L, slippage versus backtest, limits hit and anomalies.
4. **Natural-language control**: ask the bot on Telegram "why did you buy NVDA at 16:05" or "what is my exposure" and get an answer computed from the state store, with the LLM formatting and explaining, never inventing numbers.
5. **Anomaly triage**: when reconciliation fails or errors spike, summarise the logs and propose a cause, so you are not reading stack traces on your phone.
6. **Research assistant**: offline, in Claude Code, for strategy ideation, code review and backtest analysis. This is where an LLM adds the most value and costs the least risk.

Not recommended: letting the model pick entries and exits from raw price data. Its judgement on news and context is good; its ability to beat a backtested rule on bar data is unproven, and you could not test it reproducibly.

### 6.2 Design rules

- Structured outputs with a fixed JSON schema for every call, validated before use; a schema failure means "no opinion", never a default of "trade".
- The risk manager stays deterministic and sits between every signal and the broker; LLM outputs can only make the bot more conservative (skip, pause, tighten), never larger or more aggressive, except for the explanation text.
- Every call is logged with its inputs, output and cost; a monthly spend cap stops the LLM (and only the LLM) when exceeded.
- Stable system prompt first and volatile content last, so prompt caching applies; adaptive thinking on; the SDK's refusal fallback enabled as the current API guidance recommends.

### 6.3 Model and cost

Current first-party pricing per million tokens: Claude Opus 5 (`claude-opus-5`) 5 USD in and 25 USD out; Claude Sonnet 5 (`claude-sonnet-5`) 2 and 10; Claude Haiku 4.5 (`claude-haiku-4-5`) 1 and 5. The plan uses Opus 5 by default, with adaptive thinking and effort tuned per role.

Worked example for one trading day: one pre-market briefing (30k tokens in, 3k out), 30 event checks (4k in, 500 out each), one closing report (20k in, 3k out), 20 Telegram questions (5k in, 500 out each). That is about 270k input and 31k output tokens a day. On Opus 5 that is about 2.10 USD a day, or roughly 45 USD over 21 trading days, before prompt caching, which cuts the input share substantially because the briefing and report prompts share a large stable prefix. If you want to reduce this, the high-volume event checks are the natural candidate for Haiku 4.5, which would bring the month to about 25 USD; that trade-off is yours to make once you see the quality of each role.

## 7. A realistic view of intraday trading before you start

Intraday is the hardest place to make a retail bot profitable, and the research report's evidence section is about exactly this population. Three practical consequences:

- Costs decide. At IBKR a round trip in 100 shares costs at least 2 USD in commissions plus the spread; twenty round trips a day is about 900 USD a month before slippage. The first backtests must include commissions, half the spread on every fill, and a slippage assumption, and must still show a margin over those.
- Start with 5 to 15 minute bars on liquid large caps or SPX options, not with one-minute or tick strategies. Data is cleaner, fills are closer to the backtest, and the bot's polling loop is a non-issue.
- Treat the first three months as an experiment with a fixed budget of capital at risk (for example a hard cap of 10,000 EUR deployed and 500 EUR maximum daily loss), not as an income plan. Scale only on measured live results.

## 8. Revised roadmap

| Phase | Weeks | Work | Exit criterion |
|---|---|---|---|
| **0. Accounts and setup** | 1 to 2 | Apply for IBKR Pro (Ireland); open an Alpaca paper account; create the Anthropic API key; set up a Telegram bot; provision the VPS with Docker; scaffold the repository | Quote fetched and paper order placed from a script; IB Gateway logging in inside Docker on the VPS |
| **1. Data and first backtests** | 2 to 4 | Download Alpaca minute history for 50 to 80 liquid US stocks; implement two simple intraday rules (for example an opening-range breakout and a mean-reversion rule on 5 or 15 minute bars) in Lumibot; realistic costs; walk-forward tests | At least one rule is stable across parameter neighbours and out-of-sample windows after costs |
| **2. Paper trading on Alpaca** | 3 to 4 | Risk manager, approval gate with Telegram, state store, reconciliation, alerts, LLM briefing and daily report; run unattended every US session | 20 or more sessions with no unexplained divergence between backtest logic and paper behaviour; approval gate exercised end to end |
| **3. Live on IBKR, small** | 4 to 12 | Switch the broker adapter to IBKR; capital at risk capped at about 10,000 EUR; measure slippage and commissions against the backtest; tax export in place | One month without operational incidents; live costs within the backtest's assumptions |
| **4. Options** | after 3 | ThetaData Standard; defined-risk strategies first (vertical spreads on liquid names or SPX); chain and Greeks handling; expiry rules; paper then live | Same discipline as phases 2 and 3 with options-specific limits verified |
| **5. Scale and harden** | ongoing | More capital or strategies only on measured results; consider NautilusTrader if the engine limits you; CI tests; dashboards | Each increase in capital preceded by a review of the previous period |

## 9. What to do this week

1. Apply for an **Interactive Brokers Pro** account (you will be onboarded through IBKR Ireland), select EUR as base currency, request a margin account and options trading permissions for stock and index options, and submit the W-8BEN.
2. Open a free **Alpaca paper account** and generate API keys for paper trading and market data.
3. Create an **Anthropic API key** and a **Telegram bot token**.
4. Decide the three approval-gate thresholds in section 5.2, or accept the proposed defaults.
5. Confirm the two open assumptions: that you can be reachable on Telegram during US hours on most evenings, and that the first strategies may run on 5 to 15 minute bars rather than faster.

When you confirm, the next step on my side is to scaffold the repository: a Lumibot strategy skeleton, the risk manager and approval gate, the Telegram bot, the LLM module with its schemas, configuration for paper and live kept strictly separate, Docker files for the bot and IB Gateway, and tests with a fake broker. That is a few days of work and can start on the Alpaca paper account before IBKR approves you.

## Sources for this document

- Alpaca Europe: [Alpaca Europe broker page](https://alpaca.markets/eu/broker), [expansion into Europe and WealthKernel acquisition](https://alpaca.markets/blog/alpaca-expands-into-europe-and-launches-european-equities-trading/), [EEA passporting to 29 countries](https://alpaca.markets/blog/alpaca-completes-eea-passporting-to-29-countries-expanding-access-to-regulated-investment-services-across-europe/), [countries Alpaca is available](https://alpaca.markets/support/countries-alpaca-is-available)
- tastytrade: [supported countries for international accounts](https://support.tastytrade.com/support/s/solutions/articles/43000435355), [international accounts](https://tastytrade.com/learn/accounts/account-types/international-account/), [international funding methods](https://support.tastytrade.com/support/s/solutions/articles/43000475189)
- Tradier: [permitted and blocked countries](https://support.tradier.com/kb/guide/en/permitted-and-blocked-countries-CLzSa0D1rf/Steps/4997916)
- Trade Republic unofficial libraries: [pytr](https://github.com/nborrmann/pytr), [trade-republic on PyPI](https://pypi.org/project/trade-republic/), [trade-republic-uapi](https://pypi.org/project/trade-republic-uapi/1.0.2/)
- PRIIPs and US ETFs for EU residents: [Elite Trader on options on US ETFs at IBKR](https://www.elitetrader.com/et/threads/priips-kids-for-us-etf-long-options-in-the-uk-eu-and-interactive-brokers.345811/), [Logical Invest](https://logical-invest.com/u-s-etfs-unavailable-to-european-investors-pripps-and-kid/), [Finorum](https://finorum.com/us-etfs-in-europe/)
- German taxation of derivatives: [Flick Gocke Schaumburg on the JStG 2024](https://www.fgs.de/en/news-and-insights/blog/detail/update-zum-jahressteuergesetz-2024-jstg-2024-rueckwirkender-entfall-der-verlustverrechnungsbeschraenkung-fuer-termingeschaefte-und-forderungsausfaelle-im-privatvermoegen), [VLH](https://www.vlh.de/kaufen-investieren/geldanlage/verluste-aus-termingeschaeften-das-aendert-sich.html), [Ecovis](https://ecovis-kso.com/blog/verlustverrechnung-termingeschaefte-2024/), [GTK Steuerberater](https://www.gtkp.de/aktuelles/2024-12-11-bundesrat-ermoeglicht-vollstaendige-verlustverrechnung-bei-termingeschaeften-676.html)
- Broker, framework, data and rules facts not re-cited here: see the appendix of [trading-bot-research.md](trading-bot-research.md)
