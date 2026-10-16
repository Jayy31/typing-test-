# CLAUDE.md

Project guide for Claude Code. Read this first, then BOTH design docs:
`docs/DESIGN.md` and `docs/DESIGN_V2.md`. Together they define ONE final system (no v1/v2 split, no
shadow mode, one pipeline, one output). **Where they conflict, `docs/DESIGN_V2.md` wins.** Its section
"Rules and outputs v2.1" adds owner rules: all values in config, each rule switchable by mode so the
backtester can compare v2.1 rules with the original fixed rules. Section "Research integrity and
review" adds R1 AI look-ahead protection (pre-`AI_MODEL_KNOWLEDGE_CUTOFF` history: AI labels only, never
predictions), R2 realistic fills (fillable/partial/unfillable), R3 weekly Sunday digest (suggestions only).
Phase-specific research notes live in `docs/notes/` (e.g. `docs/notes/upstox-api.md`).

## What this is
News-driven research and **paper trading** system for Indian NSE F&O **stock options** (no index options).
Collects news, macro, calendar and option data → AI + rules analysis → ranked signals → automated paper trades → Telegram alerts + Next.js dashboard.

## Hard rules
- **Paper trading only.** Never write broker order-placement code. Broker API is used for market data only.
- **No technical analysis.** No indicators or candlestick logic.
- **No Kafka, no Redis.** Postgres (Supabase) is the single source of truth and the job queue.
- **Secrets only via env vars.** Never hard-code or log keys. Keep `.env.example` updated with blank values.
- **No per-news alerts.** Alerts only for signals, trade events, health problems, and the optional morning brief / evening summary / monthly report.
- Business rules (linkage map, playbooks, weights, thresholds, exit rules) live in YAML/DB config, not code.

## Owner decisions (binding)
- Broker: **Upstox**, market data only, via the read-only 1-year **Analytics Token** (`docs/notes/upstox-api.md`).
- No Sentry. Monitoring = `job_runs`, `source_health`, Telegram health alerts.
- AI roles, all model ids from env: `AI_MODEL_TAGGER`, `AI_MODEL_RESEARCHER`, `AI_MODEL_NOVELTY` (cheap);
  `AI_MODEL_BULL`, `AI_MODEL_BEAR` (mid); `AI_MODEL_JUDGE` (strongest). `AI_MODEL_ANALYST` (mid) is the
  single-call analyst used until multi-agent lands in Phase 5.
- Selection (from Phase 5, all in config): EV > 0 after costs, `p_t1_before_sl` >= 0.55, minimum EV of
  10% of premium, and edge_ratio >= `EDGE_RATIO_MIN` (1.2). Raw score is kept for comparison.
- History: 5 years. Optional Cloudflare R2 backup of Parquet (Phase 4), off by default
  (`R2_BACKUP_ENABLED`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET`).
- **Before any one-off AI job (e.g. historical batch tagging) show the owner the cost estimate
  (`app.ai.client.estimate_cost` / `tagger.estimate`) and wait for approval.**
- NSE files cannot be downloaded from the build sandbox: history setup docs must give exact VM
  commands plus a verification script (row counts, date ranges, gaps).
- Dashboard and `/why` must say "not yet calibrated" until a calibrator exists, and always show the
  source of each predicted move (scenario tree / analogues / blended).
- `.env.example` holds keys only (blank values); the owner sets values at deploy.
- Until calibration (Phase 5): EV, probabilities, pre-move and edge gates use the prediction SHRUNK toward the
  option-implied market distribution (`SCENARIO_SHRINK_WEIGHT`, default 0.5); raw AI values are stored and shown.
- Option quotes: full resolution archived to Parquet before thinning; never thin stocks with open trades or
  live signals until 2 days after they close.

## Trading rules (defaults, configurable)
- 1 lot per signal, no cap on open trades
- SL: premium −50% | T1: +25% | T2: +50% | T3: +100% and above
- Book 1/3 at each target; after T1 move SL to entry; after T2 trail SL at T1
- Other exits: thesis broken, no move 2 sessions after event, time stop 5 trading days before expiry
- Entry at live ask, exit at live bid; gaps exit at opening bid

## Stack
- `worker/`: Python 3.12, APScheduler, httpx, feedparser, trafilatura, Pydantic v2, SQLAlchemy 2 + Alembic, Anthropic SDK, python-telegram-bot, pytest
- `dashboard/`: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + Recharts + Supabase JS, deployed on Vercel
- DB: Supabase Postgres. Worker uses service-role key; dashboard uses anon key + row-level security (read-only) + Supabase Auth limited to one email
- Worker hosting: always-on free VM (Oracle Cloud Always Free) via Docker
- Timezone: `Asia/Kolkata` everywhere

## Conventions
- Typed Pydantic models at every boundary (external APIs, AI outputs, DB writes)
- AI outputs: strict JSON validated against schemas; store model id + prompt version + input refs with every output
- Model ids come from env (`AI_MODEL_<ROLE>`), never hard-coded
- Enforce `AI_DAILY_BUDGET_INR` / `AI_MONTHLY_BUDGET_INR`; log usage to `ai_usage`
- All jobs idempotent (unique keys), retries with backoff, log to `job_runs`
- Every external call has a timeout
- Tests required for parsers, entity mapping, scoring, exit rules
- **End of every phase:** give the owner a setup checklist in the final reply and save it as `docs/setup/phase-N.md`: accounts to create, API keys/env values to set (names only, never values), any SQL to run (also saved under `docs/setup/`, tested on a real Postgres), and any other external steps, in the order to do them
- Shared building blocks (reuse, never duplicate): taxonomy `config/taxonomy.yaml` (`app/taxonomy.py`);
  tagger core with live + batch modes (`app/ai/tagger.py`); collector base (`app/collectors/base.py`);
  option data interface (`app/market/options.py`); pure exit rules (`app/trading/exits.py`, `config/exits.yaml`);
  history store (`app/history/store.py`, DuckDB single writer); feature flags (`app/flags.py`: flip
  `BUILT[...]` when a stage ships, bump `ENGINE_VERSION`)
- Small commits; one branch per phase (`phase-0-foundation`, ...); open a PR per phase

## Commands
- `make setup` – install deps
- `make migrate` – run Alembic migrations
- `make test` – run pytest
- `make run` – start worker locally
- `make lint` – ruff + mypy
- `cd dashboard && npm run dev`

## Build order (merged, DESIGN_V2)
| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation | done |
| 1 | News intelligence | done |
| 2 | Signals and alerts | done |
| 2.5 | Refactor 0-2 for DESIGN_V2 (schema, taxonomy, tagger batch, collector base, option interface, DuckDB, flags, scenario tree, V2 signal fields/templates) | done |
| 3a | Upstox live data, option polling, implied move / edge ratio, Monte Carlo simulator (strike/expiry by EV), entry rules A4 b/c, priced-in + liquidity scoring, rejected candidates (B1 store), latency (B7) | done |
| 3b | Paper engine with v2.1 exits A1-A3, entry timing A4a, overlap A5, notional capital A6, counterfactual tracker (B1), P&L attribution (B2), prediction scorecard (B3), live stock benchmark (B6), trade alerts, R2 fills, R4 quote archive, R5 EV shrinkage | done |
| 4 | History: 5y backfill (bhavcopy, announcements, GDELT, events YAML), batch tagging (R1: labels only before cutoff), event study + analogue lookup, R6 IV history backfill from bhavcopy, Cloudflare R2 backup | next |
| 5 | Multi-agent analysis + probability blending + calibration + EV ranking + AI post-mortems (B4) + R1 post-cutoff-only evaluation + R3 weekly digest (Telegram) | |
| 6 | Backtester (walk-forward, costs, no look-ahead / survivorship) + fixed vs v2.1 rule comparison, metrics module (B5), benchmarks (B6), R1 enforcement, R2 with/without unfillable metrics | |
| 7 | Deeper data sources (+ their tables) incl. results fast-path | |
| 8 | Knowledge graph + 2nd/3rd-order signals | |
| 9 | Dashboard (all pages from both docs + Rejected page, portfolio views) + morning brief / evening summary incl. top rejected (B8), R1 backtest note, R2 toggle, R3 digest page | |
| 10 | Analytics, monthly report, learning loop, hardening | |

## Current phase
Phase 4 – History: 5-year backfill, batch tagging (labels only before the AI cutoff), event study + analogues, IV history backfill, optional R2 backup. Update this line when a phase is done.

## Progress log
- Phase 0 (PR `phase-0-foundation`): worker skeleton, full §14 schema + Alembic migrations (RLS enabled on all tables), settings, job runner with `job_runs` + retries, health heartbeat, symbol master (148 stocks) + NSE lot-size loader, APScheduler, Dockerfile, Makefile, CI. No Sentry (owner decision). NSE lot-size fetch untested against live NSE (blocked in build sandbox); Dockerfile untested (no Docker daemon).
- Phase 1 (PR `phase-1-news-intelligence`): RSS + NSE announcement collectors (feeds in `worker/config/news_sources.yaml`), URL-hash dedup + title-similarity clustering, entity mapper (case-sensitive alias match, themes in YAML), relevance gate (YAML rules), budget-capped Anthropic client with prompt caching + `ai_usage` logging, stage-1 tagger (structured outputs, prompt `tagger_v1`), Yahoo macro collector, NSE FII/DII + event calendar parsers, manual calendar YAML (empty: owner fills), scheduler wiring. `.env.example` is keys-only (blank values fall back to defaults). Unverified against live sources (blocked in build sandbox): feed URLs, NSE JSON endpoints, Yahoo tickers, real Anthropic calls. Not built: BSE announcements, GIFT Nifty, full-article extraction.
- Phase 2 (PR `phase-2-signals-alerts`): rules engine (`config/linkage_map.yaml` macro moves, `config/playbooks.yaml` news playbooks; adjusts score, never creates signals), stage-2 analyst (`analyst_v1`, citations validated against provided news ids), candidates with time-decayed source-weighted sentiment, scoring per §8 (AI score and rules adjustment stored separately), eligibility gates (F&O, ban list, contract-adjusting actions, days to expiry), provisional expiry, signal lifecycle with versions, `alerts` outbox + Telegram bot (httpx; alerts, `/why`, `/signals`, `/open`, `/closed`, `/stats`, `/mute`, buttons; token never in logs/errors; owner chat only), health alerts from heartbeat, sector-watch alerts, cleanup job. Deviation: expiry rolls to next month under 8 trading days (design ~7) so the 5-day time stop leaves 3 holding days. Pending for Phase 3: strike/premium, real liquidity score, priced-in penalty, real expiry from broker. Unverified live: NSE ban/corporate-action endpoints, real Anthropic analyst call, real Telegram API.
- Phase 2.5 (PR `phase-2.5-refactor`): migration 0004 (V2 signal fields incl. `predicted_move_source`, `calibrator_id`, `expected_value_inr`; tables scenarios, signal_candidates, agent_runs, calibrators, calibration_records, kg_nodes, kg_edges, backtest_runs, backtest_trades; `news_tags.taxonomy_ver`; RLS on all), shared taxonomy `tx1`, tagger `tagger_v2` with live + Batch API modes and cost estimates, AI roles per owner, collector base + per-source health (all collectors ported), `OptionDataSource` + static impl, pure exit rules, DuckDB/Parquet store with single-writer lock, feature flags + `ENGINE_VERSION` 2.5.0 stamped on signals, analyst `analyst_v2` with validated scenario tree, predicted move from scenario tree, alert and `/why` V2 sections with explicit calibration status. V2 collector tables deferred to Phase 7 (owner).
- Phase 3a (PR `phase-3-live-options`): migration 0005 (`rejected_candidates`; instrument_key/isin; signal option_type, pricing_status, latency fields), read-only Upstox client (analytics token in header, GET only, scrubbed errors, raw samples saved), instrument master sync (keys, lots, monthly expiries), option polling + storage + quote thinning, IV percentile, implied move / edge-ratio gate, Black-Scholes + greeks + IV solver, Monte Carlo simulator over the scenario tree with a vectorised exit engine tested against the pure one, exit modes (underlying stop, predicted-move targets, window stop), entry rules (pre-move, high-IV event EV), real liquidity score + priced-in penalty, signal_candidates table, rejection records at every stage, latency/freshness, alert entry line, token-expiry reminder. `ENGINE_VERSION` 3.0.0; flags simulator + implied_move_gate built. Unverified live: Upstox field names (parsers from docs/forum samples; first responses saved for checking).
- Phase 3b (PR `phase-3b-paper-trading`): migration 0006 (paper_trades contract/state/fills/benchmark/attribution columns, trade_events quantities, option_quotes bid/ask qty, signal theme_key + raw/shrunk fields, tables portfolio_snapshots, prediction_scores), paper engine (`trading_cycle`: chain polling for live signals + open trades, entry at ask after the first 15 min with entry TTL and pre-move recheck, exits via the shared rules incl. underlying stop / window stop, opposite-signal flag|close, close with costs, fills, stock benchmark), fill checks (chain best-level qty, depth-quote fallback, fillable/partial/unfillable/unknown), Greeks P&L attribution (delta/theta/vega/spread/residual, sums to gross P&L), notional portfolio snapshots + theme exposure, counterfactual tracker for rejections, prediction scorecard, trade alerts and /open /closed /stats (with/without unfillable) /portfolio /exposure, raw quote Parquet archive + protected thinning, EV shrinkage toward the market (`SCENARIO_SHRINK_WEIGHT`). Unverified live: Upstox quantities fields (fallback in place).
