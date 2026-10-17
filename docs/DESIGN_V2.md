# DESIGN_V2 — Level 2 Upgrades (Research-Grade Prediction)

Merged into the main build. `docs/DESIGN.md` + this file together define **one final system** (no separate v1/v2 products). Where this file conflicts with DESIGN.md, **this file wins**. Existing code may be changed as needed. Same rules apply: paper trading only, stock options only, no technical analysis, no Kafka/Redis, secrets in env, config-driven rules.

Scope: 7 upgrades.
1. Historical event study + backtesting
2. Predicted move vs option-implied move
3. Probabilities + calibration (instead of plain scores)
4. Option scenario simulator (best strike/expiry by expected value)
5. Deeper data sources
6. Company knowledge graph (second/third-order effects)
7. Multi-agent AI analysis (bull / bear / judge + scenario trees)

---

## 0. Cross-cutting changes

### Storage
- Supabase free tier (≈500 MB) is too small for multi-year history.
- **Historical data → Parquet files + DuckDB on the worker VM** (free, fast analytics, no server).
- Supabase keeps live/operational data only (signals, trades, analyses, recent quotes).
- New env: `HISTORY_DATA_DIR=/data/history`, `DUCKDB_PATH=/data/history/history.duckdb`.
- Optional backup of Parquet to object storage (Cloudflare R2 free tier / Oracle Object Storage): `HISTORY_BACKUP_BUCKET`, `HISTORY_BACKUP_KEY`, `HISTORY_BACKUP_SECRET`.

### New pipeline order for each candidate signal
```
news cluster
 → tagger (existing)
 → knowledge graph expansion (#6)        → list of directly + indirectly affected stocks
 → analogue lookup in event study (#1)   → historical move stats per stock/event type
 → multi-agent analysis (#7)             → scenarios with probabilities
 → probability model + calibration (#3)  → P(move ≥ X% in N days), P(T1 before SL)
 → implied move check (#2)               → predicted vs implied move ratio
 → scenario simulator (#4)               → best strike/expiry by expected value
 → final signal (existing lifecycle, alerts, paper trading)
```

### Feature flags (config table)
Each upgrade has a flag (`feat.event_study`, `feat.implied_move_gate`, `feat.probabilities`, `feat.simulator`, `feat.deep_data`, `feat.knowledge_graph`, `feat.multi_agent`) so it can be switched off if a source or model misbehaves. All default **on** once built. Every signal stores `engine_version` and `flags_json` so results can be analysed by configuration.

### Single system, single output
No shadow v1/v2 split. One pipeline, one set of signals and paper trades, one dashboard. Comparison of approaches is done through the **backtester** and analytics (e.g. "with vs without implied-move gate"), not by running two live systems.

---

## 1. Historical event study + backtesting

### Goal
Know how stocks/sectors actually reacted to each event type in the past, and backtest the full system on history before trusting it.

### Data (free)
| Data | Source | Notes |
|---|---|---|
| Daily stock prices | NSE equity bhavcopy (daily archive files) | 5+ years |
| Daily stock option prices, OI, volume | NSE F&O bhavcopy | Enables option-level backtests |
| Index levels | NSE index archives | For market-adjusted returns |
| Past corporate announcements | NSE/BSE announcement archives | Results, orders, M&A, etc. |
| Past news | GDELT (free global news archive) + any archived RSS | Headline + URL + time |
| Past macro | yfinance (crude, USD/INR, DXY, US10Y) | Daily |
| Past events | Curated `data/historical_events.yaml` (RBI decisions, Budgets, wars, tariffs, elections) | Hand + AI assisted |
| F&O universe history, lot sizes | NSE archives | Avoid survivorship bias |

Polite downloading: rate-limited, cached, resumable, idempotent.

### Processing
- Backfill jobs: `history_backfill_prices`, `history_backfill_options`, `history_backfill_announcements`, `history_backfill_news`.
- Classify historical news/announcements with the **same tagger** (batch mode, cheapest model, Anthropic Batch API if available for lower cost) so history and live use identical labels.
- **Abnormal return** = stock return − (beta × market/sector return), computed for windows 0, +1, +3, +5, +10 trading days.
- Store per event × stock: pre-event drift (−5 to −1 days), reaction, post-event drift, max favourable/adverse move, option premium change for ATM CE/PE.

### Tables (DuckDB)
```
hist_prices        date, symbol, open, high, low, close, volume, delivery_pct
hist_options       date, symbol, expiry, strike, type, open, high, low, close, settle, oi, chg_oi, volume
hist_events        event_id, date, time, symbol/sector/theme, event_type, direction, source, sentiment, surprise
event_reactions    event_id, symbol, window, raw_ret, abnormal_ret, mfe, mae, atm_ce_ret, atm_pe_ret
analogue_stats     event_type, sector, symbol(nullable), regime, n, mean_abn_ret, median, hit_rate, std, p25, p75
```

### Analogue lookup (used live)
For a new signal: find past events with same event_type (+ sector, + similar surprise/sentiment, + similar market regime). Return n, average and median move, hit rate, distribution, and 3–5 concrete examples (date, stock, move). Shown in `/why` and Stock Analysis page.

### Backtester
- Replays history day by day: news known at that time only (**no look-ahead**), F&O universe as of that date (**no survivorship bias**).
- Uses EOD option prices from bhavcopy; entry next available price after the event, exits by the same rules as live (SL −50%, T1 +25%, T2 +50%, T3 +100%+, time/thesis/no-move exits), using daily high/low with conservative fill assumptions (if SL and target both inside one day's range, assume SL first).
- Costs: brokerage, STT, exchange charges, and a spread/slippage assumption.
- **Walk-forward**: tune on period A, test on later period B, roll forward.
- Outputs: win rate, average return, expectancy, max drawdown, by event type / sector / regime / score band / config (flags).
- Results stored and shown on a **Backtest** dashboard page; each run saved with config hash.

### Limitation to state in UI
Daily data cannot capture intraday sequence exactly; backtests are approximate. Live paper trading remains the final judge.

---

## 2. Predicted move vs option-implied move

### Concept
Option premiums already contain the move the market expects. Edge exists only if our expected move is larger than the implied move (for direction trades: larger in our direction).

### Calculation
- **Implied move to expiry** ≈ ATM straddle price ÷ spot (live from option chain).
- **Implied move for N days** ≈ ATM IV × spot × √(N/365).
- **Event implied move** (if results/event before expiry): compare IV of the expiry containing the event vs the next expiry to isolate the event premium.
- **Predicted move**: from analogue stats (#1) blended with multi-agent scenarios (#7) and probability model (#3).

### Gate
- `edge_ratio = predicted_move_in_direction / implied_move`.
- Default rule: trade only if `edge_ratio ≥ 1.2` (config). Show the ratio on every signal.
- Also flag **IV crush risk**: event before expiry + IV percentile high → simulator (#4) must show positive EV after expected IV drop.

### New fields
`signals.implied_move_pct`, `signals.predicted_move_pct`, `signals.edge_ratio`, `signals.iv_percentile`, `signals.event_iv_premium`.

---

## 3. Probabilities + calibration

### Outputs per signal
- `p_move`: P(stock moves ≥ X% in our direction within N days) — X, N per signal.
- `p_t1_before_sl`: P(premium hits +25% before −50%).
- `p_t2`, `p_t3`: P(reaching +50%, +100%).
- `expected_value`: Σ(probability × payoff) − costs, in premium % and ₹ per lot.

### Sources of probability
1. **Base rate** from analogue stats (#1).
2. **Adjustment** from multi-agent judge (#7) — must state reasons for moving away from base rate.
3. **Monte Carlo** from scenario simulator (#4) for target/SL probabilities.
Blend weights in config; start with base-rate-heavy.

### Calibration
- After each closed trade/expired signal, record predicted probability vs outcome.
- **Brier score** and **reliability curve** (predicted bucket vs actual hit rate) per model and config.
- **Recalibration**: isotonic or Platt scaling fitted on history + closed trades, refit weekly; stored as versioned calibrator.
- Dashboard **Calibration** page: reliability chart, Brier score trend, over/under-confidence warning.

### Selection rule
Rank and filter signals by **expected value** and `p_t1_before_sl`, not by raw score. Raw score kept for comparison.

---

## 4. Option scenario simulator

### Goal
Pick the strike and expiry with the best expected value, accounting for move size, timing, time decay and IV changes.

### Method
- Pricing: Black-Scholes (European approximation; fine for Indian stock options) with live spot, IV per strike, days to expiry, risk-free rate (`RISK_FREE_RATE` env, e.g. T-bill yield), dividends if ex-date in window.
- Candidates: strikes from ~2 ITM to ~3 OTM, current and next expiry, both filtered by liquidity rules.
- Scenarios grid: stock move (from predicted distribution), holding days (1, 3, 5, 10, to expiry−5), IV change (event IV crush, vol expansion, unchanged).
- **Monte Carlo** (e.g. 10,000 paths) using the predicted move distribution from #1/#3 (fat tails allowed), path-dependent so T1/T2/T3/SL order is respected.
- Output per candidate: EV, P(T1 before SL), P(T3), max loss, expected holding days, theta cost per day, breakeven move, sensitivity to IV.
- Choose best candidate by EV subject to liquidity and spread limits.
- Store the full candidate table for the drill-down (`signal_candidates` table).

### Display
Stock Analysis page: candidate table + payoff chart by scenario + "why this strike/expiry".

---

## 5. Deeper data sources

All new collectors follow existing conventions (idempotent, retries, timeouts, `job_runs`, source health).

| Category | Data | Source (free unless noted) | Use |
|---|---|---|---|
| Results | Quarterly results filings (PDF/XBRL) | NSE/BSE announcements | AI extracts revenue, EBITDA, PAT, margins, guidance within minutes; compares to last quarter, last year and (if available) consensus |
| Management | Concall transcripts, investor presentations | Company filings on NSE/BSE | Tone, guidance changes, risk mentions |
| Insider / smart money | Promoter and insider trades (SAST / PIT disclosures) | NSE/BSE | Buying/selling signals |
| | Bulk and block deals | NSE/BSE daily | Large investor activity |
| | Shareholding pattern (quarterly) | NSE/BSE | FII/DII/promoter changes, pledges |
| | Mutual fund monthly portfolios | AMFI / AMC disclosures | Fund buying/selling trends |
| Business activity | Monthly auto sales | Company filings (1st of month) | Auto stocks |
| | GST collections, core sector, IIP, CPI/WPI | Govt releases | Macro |
| | Power demand | Grid operator data | Power/utilities |
| | Government tenders and orders | GeM, defence/railway ministry releases, company filings | Order-win detection |
| | Commodity and freight prices | yfinance / public sources | Input costs, metals, shipping |
| Credit / regulatory | Rating changes | Rating agency press releases | Credit events |
| | SEBI/RBI orders, USFDA inspection outcomes | Regulator sites | Regulatory risk |
| Global peers | US/global peer results and guidance | Company IR pages / news | Map to Indian peers (via #6) |
| Attention | Google Trends (pytrends) | Free, unofficial | Retail attention spikes |
| Optional paid | Consensus estimates, social sentiment (X), premium news APIs | Paid | Only if free data proves insufficient |

### Results fast-path
New job polls results filings every 30–60 s during results season; AI extraction uses a strict schema (`ResultsExtract`); surprise = actual vs expectation proxy (last quarter, last year, consensus if available, or management guidance).

---

## 6. Company knowledge graph

### Content
Nodes: company, sector, sub-sector, product, raw material, customer segment, country/export market, commodity, macro factor, competitor, supplier, customer, promoter group.

Edges (with weight 0–1 and source):
- `supplies_to`, `buys_from`, `competes_with`, `belongs_to_sector`
- `exposed_to_commodity` (input cost / revenue)
- `revenue_from_country` (% exports to US, EU, etc.)
- `exposed_to_currency`, `exposed_to_rates` (debt levels)
- `regulated_by`, `same_group_as`

### Building it
- Seed with AI extraction from annual reports, investor presentations and filings (strict schema, every edge stores source + confidence).
- Manual overrides in `data/kg_overrides.yaml`.
- Refresh quarterly; flag low-confidence edges.

### Storage
Postgres tables (no graph DB needed): `kg_nodes`, `kg_edges(src, dst, type, weight, source, confidence, updated_at)`. Use recursive SQL or NetworkX in Python for traversal.

### Use
- For each event: propagate impact through edges up to 2–3 hops with decaying weight → list of indirectly affected F&O stocks with explanation path (e.g. "US pharma tariff → revenue_from_country US 45% → SUNPHARMA").
- Indirect candidates go through the same pipeline; marked `impact_order = 2/3` in signals.
- Dashboard: graph view of the impact path on Stock Analysis page.

---

## 7. Multi-agent AI analysis

### Agents (each with strict JSON output)
1. **Researcher**: assembles facts only — news, filings, analogue stats, macro, KG paths, option data. No opinions.
2. **Bull agent**: strongest case for the trade direction.
3. **Bear agent**: strongest case against (priced in, weak source, regime, IV crush, counter-news).
4. **Judge**: weighs both, starts from the analogue base rate, outputs scenario tree with probabilities and a final view.
5. **Novelty / priced-in checker**: is the news genuinely new; how much has the stock/option already moved.

### Scenario tree
```
scenario: name, probability, stock_move_pct, timing_days, triggers, invalidation
e.g. RBI cuts (0.6) → banks +3% in 3d | RBI holds (0.4) → banks −1.5% in 2d
```
Probabilities must sum to 1; fed into #3 and #4.

### Cost control
- Run multi-agent only for clusters passing the first-stage threshold.
- Researcher output shared across agents; prompt caching on fixed context.
- Bull/Bear on mid-tier model, Judge on strongest model.
- Daily budget caps already exist; add `AI_MULTI_AGENT_MAX_PER_DAY`.

### Stored
`agent_runs(analysis_id, agent, model, prompt_ver, input_refs, output_json, tokens, cost)`. Full debate visible in drill-down ("Bull case / Bear case / Judge verdict").

---

## New / changed dashboard pages
- **Backtest**: run list, results by event type/sector/regime, equity curve, config used.
- **Event Library**: browse historical events and reactions; analogue search.
- **Calibration**: reliability chart, Brier score, per model and config.
- **Stock Analysis** (extended): analogues, implied vs predicted move, probabilities, EV, candidate strikes table, payoff chart, KG impact path, bull/bear/judge debate.

## Telegram
Signal alert adds: `P(T1 before SL)`, `EV`, `Edge ratio`, `Analogues: n=42, avg +4.8%, hit 71%`. `/why` includes all new sections.

## New env variables
```
HISTORY_DATA_DIR=
DUCKDB_PATH=
HISTORY_BACKUP_BUCKET=
HISTORY_BACKUP_KEY=
HISTORY_BACKUP_SECRET=
RISK_FREE_RATE=0.065
EDGE_RATIO_MIN=1.2
MC_PATHS=10000
AI_MODEL_BULL=
AI_MODEL_BEAR=
AI_MODEL_JUDGE=
AI_MULTI_AGENT_MAX_PER_DAY=30
GDELT_ENABLED=true
```

## Changes required to already-built parts (Phases 0–2)
- **Schema:** add to `signals`: engine_version, flags_json, impact_order, p_move, p_t1_before_sl, p_t2, p_t3, expected_value, implied_move_pct, predicted_move_pct, edge_ratio, iv_percentile, event_iv_premium. New tables: signal_candidates, agent_runs, scenarios, calibration_records, calibrators, kg_nodes, kg_edges, backtest_runs, backtest_trades, plus V2 collector tables (insider_trades, bulk_block_deals, shareholding, mf_holdings, results_extracts, ratings, regulatory_actions).
- **Storage:** add DuckDB + Parquet history store on the VM (`HISTORY_DATA_DIR`, `DUCKDB_PATH`).
- **Event taxonomy:** one fixed, versioned list of event types and themes shared by live tagging and historical tagging (YAML).
- **Tagger:** must support batch mode (historical) with identical output schema; record prompt version.
- **Collectors:** common base class so new sources (filings, insider, bulk/block, MF, tenders, ratings, regulators, peers, trends) plug in easily; results fast-path polling.
- **Option data access:** one interface with two implementations: live broker and historical bhavcopy (used by backtester and simulator).
- **Exit rules:** pure, reusable module (used by live paper trading, simulator and backtester).
- **Analyst output schema:** includes scenario tree (scenario, probability, move, timing, invalidation) from day one.
- **Signals (Phase 2):** analyst output gains scenario tree; scoring keeps current score but adds slots for probabilities/EV/edge ratio (filled in later phases); `/why` and alert templates extended to show new sections when available; ranking switches to EV once Phase 5 lands.
- **Config:** feature flags and new thresholds (EDGE_RATIO_MIN, MC_PATHS, RISK_FREE_RATE, AI_MULTI_AGENT_MAX_PER_DAY).

## Merged build order (Phases 0–2 already built)
| Phase | Scope |
|---|---|
| 2.5 | Refactor already-built Phases 0–2 for the changes above (schema migration, taxonomy, tagger batch mode, collector base, option data interface, DuckDB store, flags, scenario tree in analyst, extended signal fields/templates) |
| 3 | Live options + paper trading + **implied move / edge ratio** + **scenario simulator** (best strike/expiry by EV) |
| 4 | **History**: backfill (prices, options bhavcopy, announcements, news, events YAML), batch tagging, **event study + analogue lookup** wired into live signals |
| 5 | **Multi-agent analysis** (researcher, bull, bear, judge, novelty) + **probability blending + calibration** + EV-based ranking |
| 6 | **Backtester** (walk-forward, costs, no look-ahead/survivorship) |
| 7 | **Deeper data sources** incl. results fast-path |
| 8 | **Knowledge graph** + indirect (2nd/3rd order) signals |
| 9 | Dashboard: all pages from DESIGN.md + Backtest, Event Library, Calibration, extended Stock Analysis; morning brief, evening summary |
| 10 | Analytics + monthly report + learning loop + hardening |

Each phase: branch, tests, PR, update CLAUDE.md.

---

## Rules and outputs v2.1

Added by the owner after Phase 2.5. Same principles: **every value lives in config** (`config/exits.yaml`,
`config/entry.yaml`, `config/portfolio.yaml`, `config/signals.yaml`), and **every rule has a mode switch
so the backtester (Phase 6) can run the v2.1 rules and the original fixed rules on the same signals**
and compare them. The live system runs one configured set at a time (single pipeline, single output);
each trade stores the rule config hash it ran under.

### A. Trading rules

| # | Rule | Detail | Config (examples) | Phase |
|---|---|---|---|---|
| A1 | Underlying-based stop | Primary stop on the **stock**: thesis invalidation = price back beyond the pre-news level, or adverse stock move of X% from entry. Premium -50% stays as a hard backstop (whichever hits first). | `exits.stop.mode: underlying+premium \| premium`, `exits.stop.underlying_adverse_pct`, `exits.stop.use_pre_news_level`, `exits.stop.premium_backstop_pct: -50` | 3 (live), 6 (backtest) |
| A2 | Target modes | `fixed`: premium +25/+50/+100%. `predicted_move`: T1/T2/T3 = option premium (Black-Scholes repriced at entry IV, expected holding time) when the stock has moved 50% / 100% / 150% of the predicted move. Booking (1/3 each), SL-to-entry and trailing stay the same. | `exits.targets.mode: fixed \| predicted_move`, `exits.targets.fixed_pct`, `exits.targets.predicted_fractions: [0.5, 1.0, 1.5]` | 3 (live), 6 (compare) |
| A3 | Scenario time stop | Exit if the move has not started within the scenario tree's expected window (probability-weighted timing of the winning scenarios) plus a buffer. "Started" = stock moved at least N% of the predicted move in our direction. Near-expiry time stop (5 trading days) stays as backstop. | `exits.window_stop.enabled`, `exits.window_stop.buffer_days`, `exits.window_stop.started_fraction` | 3 |
| A4 | Entry rules | (a) no entries in the first 15 minutes after the open; (b) skip if the stock already moved > 60% of the predicted move since the pre-news reference; (c) before a known event with high IV (IV percentile above threshold), enter only if the simulator shows positive EV **after** the expected IV drop. | `entry.no_entry_first_minutes: 15`, `entry.max_premove_fraction: 0.6`, `entry.event_iv_percentile_min`, `entry.require_post_crush_ev: true` | 3 |
| A5 | Overlap and expiry of signals | Signals are grouped by thesis/theme (cluster + taxonomy theme + sector); exposure per theme is reported (open premium at risk, delta-equivalent notional). Opposite signal on a stock with an open trade: `flag` for review (alert) or `close` the trade (config). A signal not entered within a set time expires. | `portfolio.theme_keys`, `portfolio.opposite_signal_action: flag \| close`, `entry.signal_entry_ttl_minutes` | 3 |
| A6 | Notional capital | Paper portfolio with notional capital (default Rs 5,00,000) for portfolio-level P&L, equity curve and drawdown. Position sizing stays 1 lot per signal; capital is for measurement. | `portfolio.notional_capital_inr: 500000` | 3 (track), 9 (dashboard) |

### B. Outputs

| # | Output | Detail | Phase |
|---|---|---|---|
| B1 | Rejected candidates + counterfactual | Every candidate rejected anywhere in the pipeline (relevance aside: from candidate stage onward: score, eligibility, liquidity, edge ratio, entry rules, EV, probability gates) is stored with stage and reason. A tracker records what the stock and the would-be option did afterwards (same exit rules applied hypothetically). Dashboard "Rejected" page. | 3 (store + track), 9 (page) |
| B2 | P&L attribution | Per trade, split P&L into delta, theta, vega and residual, from Black-Scholes greeks at each observation (sum over intervals). | 3 |
| B3 | Prediction scorecard | For every prediction, traded or not: direction hit, move-size error (predicted vs realised), timing error. Feeds calibration and analytics. | 3 (record + score), 5 (calibration), 10 (reports) |
| B4 | AI post-mortem | Automatic AI review of every closed trade (what the thesis said, what happened, which rule exited, lesson). Cheap model, budget-capped. Monthly report summarises them. | 5 (post-mortem), 10 (monthly summary) |
| B5 | Metrics | Expectancy, profit factor, average win/loss, max drawdown, MFE/MAE, time to target; broken down by event type, sector, regime, score band and entry time. One metrics module shared by live analytics and the backtester. | 6 (module + backtest), 9 (dashboard), 10 (reports) |
| B6 | Benchmarks | Same signals traded as stock/future instead of option; random entries on the same days; fixed-rule (pre-v2.1) version. | 3 (record stock benchmark per live trade), 6 (all benchmarks in backtests) |
| B7 | Latency and freshness | Per signal: news publish → signal time, stock move between news and signal, age of each data input used (news, quotes, macro). | 3 |
| B8 | Evening summary | Includes the day's top rejected candidates and why (with counterfactual so far). | 9 (with the summary jobs) |

### C. Phase placement summary
- **Phase 3:** A1-A6, B1 (store + counterfactual tracker), B2, B3 (record + score), B6 (live stock benchmark), B7.
- **Phase 5:** B3 into calibration, B4 post-mortems.
- **Phase 6:** A1-A3 backtest comparisons (fixed vs v2.1), B5 metrics module, B6 random/fixed-rule benchmarks.
- **Phase 9:** "Rejected" page, portfolio equity/drawdown views, B5 dashboards, B8 evening summary.
- **Phase 10:** B3/B4/B5 in the monthly report.

---

## Research integrity and review

Added by the owner after Phase 3a. Values in config; nothing here is auto-applied.

### R1. AI look-ahead protection in backtests
- The AI models know market outcomes up to their training cutoff, so AI predictions on older news are contaminated.
- `AI_MODEL_KNOWLEDGE_CUTOFF` (env, YYYY-MM-DD; the latest cutoff of any model used) splits history:
  - **Before the cutoff:** AI is used only for fact/label extraction (event type, entities, sentiment, surprise) via the batch tagger. It is never asked for direction, move size, probabilities or outcomes.
  - The event study and the backtester use only these labels plus actual price data. Rule-based and analogue-based predictions are backtestable; AI-predicted ones are not.
  - **After the cutoff:** multi-agent predictions, probabilities and calibration are evaluated only on live (post-cutoff) data.
- Every backtest run stores which components used AI and how (`labels_only` / `predictions`). Runs that would use AI predictions on pre-cutoff dates are refused.
- The Backtest UI shows a clear note: which parts used AI labels, which used AI predictions, and the cutoff date.

### R2. Realistic fills for illiquid options
- At entry and exit, record bid/ask prices **and quantities** and check whether 1 lot could have filled at that price.
- Each trade is flagged `fillable` / `partial` / `unfillable` (entry and exit checked separately; the worse one wins).
- All performance metrics are reported with and without unfillable trades (dashboard toggle; reports show both).
- Backtester: the same check where quantity data exists; otherwise a conservative filter from daily volume/OI (config thresholds), with excluded trades counted and reported.

### R3. Weekly review digest (Sunday, Telegram + dashboard page)
- Week's P&L (incl. notional-capital portfolio view) and open trades.
- Top 3 winning and top 3 losing trades, each with a short AI post-mortem.
- Rejected signals that would have won (from the counterfactual tracker).
- Unfillable trades flagged this week.
- Data source errors and job failures (source health, `job_runs`).
- AI spend for the week vs budget.
- Rules or thresholds the system suggests reviewing, with reasons and evidence. **Suggestions only, never auto-applied.**

### Phase placement
| Item | Phase |
|---|---|
| R1 config key `AI_MODEL_KNOWLEDGE_CUTOFF` | added now (Phase 3a PR) |
| R1 batch tagging = labels only, pre-cutoff enforcement in the event study | 4 |
| R1 calibration and probability evaluation on post-cutoff live data only | 5 |
| R1 backtester enforcement, run metadata, refusal of contaminated runs | 6 |
| R1 Backtest UI note | 9 |
| R2 bid/ask quantities captured, fillability check and flags on paper trades and rejected-candidate counterfactuals | 3b |
| R2 metrics with/without unfillable trades (metrics module), backtester volume/OI filter | 6 |
| R2 dashboard toggle | 9 |
| R3 weekly digest on Telegram (needs post-mortems + counterfactuals) | 5 |
| R3 dashboard page | 9 |
| R3 rule-review suggestions: basic evidence-based suggestions in 5, full learning-loop suggestions | 10 |

### Owner additions during Phase 3b
| # | Item | Detail | Phase |
|---|---|---|---|
| R4 | Raw quote archive | Before Supabase option quotes are thinned, every full-resolution row (all fields incl. bid/ask quantities) is written to Parquet on the VM via the history store, partitioned by IST date (`parquet/option_quotes/date=YYYY-MM-DD/`). Idempotent (last archived id in config). Quotes of stocks with open paper trades or live signals are never thinned until 2 days after they close (`protect_days_after_close`). | 3b |
| R5 | EV shrinkage until calibration | Simulated paths blend the AI scenario distribution with the option-implied market distribution (no drift, ATM IV): share `SCENARIO_SHRINK_WEIGHT` (default 0.5) from the AI, the rest from the market. Predicted move is shrunk the same way (market expectation = 0) and P(move) toward 0.5. Raw and shrunk values are both stored; **shrunk values drive gating (pre-move, edge ratio) and ranking (EV)**. Phase 5 calibration replaces the fixed weight with fitted weights. | 3b (replaced in 5) |
| R6 | IV history backfill | From 5 years of NSE F&O bhavcopy EOD option prices, invert Black-Scholes per strike per day (underlying close, days to expiry, `RISK_FREE_RATE`) and load daily IV into the IV history so IV percentile works from day one (up to 1 year lookback). | 4 |
| R2 detail | Quantities source | Upstox's option chain gives bid/ask quantity at the best price for each strike (stored per poll). If the live feed omits them, the engine reads the option's own full market quote (5-level depth) at entry and exit only. If neither has quantities, the fill flag is `unknown` and reports count it separately. | 3b |

### Owner fixes after Phase 3b (Phase 3c)
| # | Item | Detail | Phase |
|---|---|---|---|
| L1 | Whole-lot booking | An option lot cannot be split. Default `LOTS_PER_SIGNAL=3`, `PARTIAL_BOOKING_MODE=per_lot`: 1 lot at T1, 1 at T2, 1 at T3 or the trailing stop. `single_exit`: the whole position exits at `single_exit_target` (default T2); earlier targets only move the stop. If lots don't divide by the number of targets, the remainder goes to the last target and is logged; with fewer lots than targets the position closes at target number `lots`. Fractional lots are never recorded. Alerts say "Sold 1 of 3 lots at ...". Portfolio tracks capital used. EV in Rs is per position. | 3c (backtester compares both modes in 6) |
| L2 | Stop slippage visibility | Every stop/target fill stores its trigger level, the fill price, slippage in Rs (fill - level, x units) and a `gap` flag when filled worse than the level (price gapped between observations; fills are at the observed bid, never at an unseen level). Totals per trade, in `/stats`, and in the weekly digest (Phase 5). | 3c (digest in 5) |

### Owner fixes to the history (Phase 4, before merge)
- **H1 Corporate actions.** NSE corporate actions for 5 years (splits, bonus, demergers, special
  dividends) in `hist_corp_actions`; owner-verified factors in `data/corporate_actions.yaml` win.
  Prices are back-adjusted for the event study; any window within ±3 trading days of an action
  is dropped; option contracts are followed through NSE's adjustment (strike × factor, lot ÷
  factor). `verify` flags one-day moves > 25% with no action recorded.
- **H2 Event timing.** Reaction day from the IST announcement time: before 09:15 → same day
  (`pre_open`); 09:15–15:30 → same day, flagged `intraday`; after 15:30 → next trading day
  (`post_close`); weekend/holiday → next trading day; no time → same day, `unknown`, weight 0.5.
- **H3 Point-in-time universe.** F&O list and lot size as of each date from the bhavcopies,
  including later removed or delisted stocks; the history tagger accepts those symbols too.
  Lot sizes before July 2024 are inferred (marked `inferred`); owner-known values in
  `data/lot_size_overrides.yaml` replace inferred ones (never exact bhavcopy lots). For trades or
  events on inferred lots the backtester (Phase 6) reports **% of premium as the primary metric**
  and marks rupee values "approximate (inferred lot size)" (`universe.value_report`).
- **H4 Confounding.** Another major event of a different type for the same stock, or a
  sector-wide event, inside the window → `confounded`; down-weighted (0.5) or excluded (config).
  Several articles on one event count once.
- **H5 Analogue weight.** weight = n / (n + `ANALOGUE_PRIOR_N`) (default 20), using the weighted
  n, and 0 below `ANALOGUE_MIN_N` (5) events. `/why` shows n, effective n and the weight.
- **H6 Sector benchmark.** Abnormal return vs the stock's sector index (map in `history.yaml`),
  falling back to Nifty 50; both stored. Market-wide event types (policy, macro, geopolitical,
  commodity) always use Nifty 50.

### Owner decisions for Phase 5 (multi-agent, probabilities, calibration)
- **Panel**: researcher + novelty (cheap) -> bull + bear in parallel (mid) -> judge (strongest),
  all citations checked against the provided news ids. At most `AI_MULTI_AGENT_MAX_PER_DAY` (4)
  panels per day for the top candidates by pre-AI rank, then the single analyst up to
  `AI_SINGLE_ANALYST_MAX_PER_DAY` (5), then rejected as `ai_cap`. Every prediction is tagged
  `panel` or `single`; scorecard, calibration, /stats and the digest report them separately
  and combined.
- **Budget reserve**: `AI_TAGGER_RESERVE_INR` (15/day) is kept back from both caps for the live
  tagger. Panel, single analyst and post-mortems pause first; the health alert names what paused.
- **Calibration**: Platt scaling from `CALIBRATION_MIN_RECORDS` (50), isotonic from
  `CALIBRATION_ISOTONIC_MIN` (200); the method in use is stored on each signal and shown in /why
  and the digest. Records are grouped by originating news event (cluster): each event weighs one
  when fitting and in the Brier score; reports show records and independent events. Only
  predictions after `AI_MODEL_KNOWLEDGE_CUTOFF` count (R1). Targets: `p_move` (scorecard) and
  `p_t1_before_sl` (paper trade or counterfactual replay).
- **How calibration reaches EV**: the simulated probability and EV are linear in the share of AI
  scenario paths (the rest follow the market distribution). Before calibration that share is
  `SCENARIO_SHRINK_WEIGHT`; after, it is the share that reproduces the calibrated probability
  (`p_move` calibrator for the predicted move / edge ratio, `p_t1` calibrator per candidate for
  EV and the probability gate).
- **Selection gates** (config `options.yaml` `selection`): EV > 0 and >= 10% of premium,
  P(T1 before SL) >= 0.55, plus the edge-ratio gate.
- **Prices** live in `config/ai.yaml` per model id (checked 2026-10-17), tier fallback.
- **Post-mortems** (B4) use the mid model with a fixed cause list; the **weekly digest** (R3) is
  rule-based (no AI cost) and only suggests.

### Owner decisions for Phase 6 (backtester)
- End-of-day data only. **Entry at the close of the event's reaction day**; exits checked from the
  next day with the same pure exit rules as live; **stop first** when a day touches both.
- **Spread model** (until fitted from the live quote archive): half-spread by tier in
  `config/backtest.yaml` (liquid large-cap ATM 2%, liquid OTM or mid-cap ATM 4%, illiquid/deep OTM
  9%), minimum one tick (Rs 0.05); every comparison also runs at x1.5 and x2.
- **Stale prices**: a zero-volume close is not tradable; the settlement price is used and
  flagged; with neither there is no entry or exit that day. Reports count settlement-price trades.
- **Walk-forward** 12 months train / 3 months test; analogue statistics per fold only from events
  whose 10-day outcome window ended before the fold. Point-in-time F&O universe and lot sizes.
- **AI mode** replays stored live predictions only, after `AI_MODEL_KNOWLEDGE_CUTOFF` (R1).
- Rules mode (no AI): direction = event label when its analogues moved that way (hit >= 50%, mean
  > 0); predicted distribution = analogue quantiles mixed with the market at weight n / (n + 20);
  live gates (pre-move, edge ratio, EV / probability) apply.
- Compared side by side: v2.1 vs fixed rules, per_lot vs single_exit, spread x1.5 / x2, random
  direction and ATM-without-gates benchmarks, stock with the same capital, Nifty buy and hold.

- (after Phase 6) No manual VM steps: bhavcopy volume/OI units detected nightly (alert only if
  inconclusive); rules `compare` runs automatically once the history is complete, replay
  `compare` once >= 30 post-cutoff live predictions exist, then monthly.
- Rules-mode direction rule: hit rate >= 55% AND the lower bound of the 90% bootstrap interval of
  the mean abnormal move > 0 (ANALOGUE_MIN_N applies); the old rule (>= 50%, positive mean) is a
  comparison variant. Verdict: advantage over the default only if outside the bootstrap intervals,
  change suggested only if it also wins at 2x spread.
- One trade per stock, live and in backtests: a skipped signal is recorded as rejected ("open
  trade on same stock") so the counterfactual tracker follows it.

### Owner decisions for Phase 7 (deeper data sources)
- Results extraction: XBRL first, then the cheap model with the mid model as fallback (strict
  `ResultsExtract` schema). Surprise without consensus: revenue and profit growth vs the
  company's own trend (median of its last four YoY growths, else 10%), margin change, stated
  guidance.
- Fast-path polling on results days 09:00-23:30 IST. Results after the close or on holidays run the
  signal cycle at once; the signal enters at the next open (`enter_after`), re-priced with live
  quotes, after the first-15-minutes rule; entry TTL counts from then.
- Concall transcripts deferred; mutual-fund portfolios deferred to Phase 10.
- Disclosure dates: insider trades, bulk/block deals, shareholding, ratings and regulatory actions
  store the event date AND the public disclosure timestamp; the event study, rules mode and the
  backtester use only the disclosure timestamp (`hist_events.date/time`; `event_date` is
  reference only, enforced by tests/test_deep_disclosure.py). Deal reports count as public at
  18:00 on the deal date.
- Evidence: hand-set rules in `deep_data.yaml` start at low weight (strength x0.6, kind weight 0.3,
  not an official source); once the event study has `ANALOGUE_MIN_N` events for a subtype its
  base rate replaces the rule's direction and strength (kind weight 1.0, counts as official).
  `/why` shows which one was used.
- Materiality thresholds in config (Rs minimum, % of 20-day average traded value, % of market cap)
  so that small items create no candidates.
- Counterparty (promoter, promoter group, director/KMP, FII, MF/DII, other) is part of the subtype
  in taxonomy tx2, so base rates are learned per counterparty.

### Owner decisions for Phase 8 (knowledge graph)
- Depth: max 2 hops live (x0.5 per extra hop); 3 hops only as a backtest comparison variant.
- At most 10 new graph-propagated candidates per day; they may use at most `KG_MAX_PANEL_SLOTS`
  (1) of the 4 daily panels, the rest go to the single analyst within its cap.
- Sign by edge type x event type (default table in `config/kg.yaml`: same / opposite / unknown,
  unknown = support only); the event study learns sign and strength per (edge type, event type)
  from linked-stock reactions (vs the market, at the source's disclosure timing) and replaces
  the default at `ANALOGUE_MIN_N` events. `/why` shows which was used.
- Exposure (revenue share, % of input costs, stake %) stored on the edge scales its weight;
  missing exposure -> low default weight (x0.3).
- No double counting: a stock already covered by the same news event (directly or via a
  sector-level tag) gets the graph path as supporting evidence, not a new graph candidate.
- Regulator items: confidence-scored name match on the alias list; below 0.85 stored for review
  (weekly digest), no candidates. AI-extracted company links use the same matcher.

### Owner decisions for Phase 9 (dashboard)
- Read-only dashboard except owner switches: `feat.*`, `brief.morning_enabled`,
  `brief.evening_enabled`, `alerts_muted`; write policy only for the owner email, every change
  logged (who, when, old -> new) and shown on Settings. Thresholds, weights, rules stay in YAML.
- Morning brief 08:45 (previous session's final FII/DII), evening summary 16:00 (FII/DII
  provisional or pending). Mobile-first; "Today" landing page; deep links from every alert;
  "data as of" on every page with amber/red staleness.
