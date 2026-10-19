# Unverified assumptions (running list)

Everything below was built from documentation, community samples or reasoning, and could not be
checked against the live service from the build sandbox. Each phase adds to this list; the Go-live
phase checks them all in one pass (the first-run report flags mismatches).

| # | Phase | Assumption | Where | How to verify |
|---|---|---|---|---|
| 1 | 0 | NSE lot-size file format | `app/collectors/fno_meta.py` | first `fno_meta` run row count |
| 2 | 1 | RSS feed URLs in `news_sources.yaml` | `config/news_sources.yaml` | `source_health` |
| 3 | 1 | NSE announcements / FII-DII / event-calendar JSON fields | `app/collectors/nse.py` | `source_health`, row counts |
| 4 | 1 | Yahoo tickers (brent, usdinr, dxy, us10y, india_vix, ADRs) | `app/collectors/macro.py` | non-null macro snapshot columns |
| 5 | 2 | NSE ban list CSV and corporate-actions JSON | `app/collectors/nse.py` | `fno_ban` config, events_calendar rows |
| 6 | 2 | Telegram Bot API behaviour (buttons, polling) | `app/notify/telegram.py` | first-run report delivery |
| 7 | 3a | Upstox field names (instruments, option chain, quotes, depth quantities) | `app/market/upstox.py` | saved raw samples vs parsers |
| 8 | 3b | Upstox bid/ask quantity fields (fallback in place) | `app/trading/fills.py` | fill class not always "unknown" |
| 9 | 4 | NSE archive URLs and bhavcopy columns (equity, F&O old + UDiFF, indices) | `config/history.yaml` | `python -m app.history.verify` |
| 10 | 4 | NSE announcements history API, GDELT | `app/history/news_history.py` | loaded_files status |
| 11 | 5 | Real Anthropic structured outputs per agent with thinking / effort | `app/ai/panel.py` | AI smoke test (`ai_smoke_runs`) |
| 12 | 6 | Bhavcopy volume / OI units (auto-detected nightly) | `app/history/units.py` | store_meta `units:*`, alert if inconclusive |
| 13 | 7 | NSE PIT (insider) JSON fields: symbol, acqName, personCategory, secAcq, secVal, tdpTransactionType, acqfromDt, date | `app/deep/parsers.py` | `insider_trades` rows, raw_json |
| 14 | 7 | NSE bulk/block deal fields BD_* and publication after the close (18:00) | `app/deep/parsers.py`, `deep_data.yaml` | `bulk_block_deals` rows |
| 15 | 7 | NSE shareholding master fields; FII / DII / pledge % may be absent there | `app/deep/parsers.py` | non-null fii_pct / pledge_pct |
| 16 | 7 | NSE financial-results feed fields (broadCastDate, toDate, consolidated, xbrl) | `app/deep/results.py` | `results_extracts` rows, latency |
| 17 | 7 | XBRL element names (RevenueFromOperations, ProfitLossForPeriod, ...) and 3-month contexts | `app/deep/results.py` | share of extracts with method = xbrl |
| 18 | 7 | NSE quote-equity `securityInfo.issuedSize` | `app/deep/stats.py` | non-null `stock_stats.mcap_inr` |
| 19 | 7 | SEBI, USFDA, PIB, RBI feed URLs | `config/deep_data.yaml` | `source_health` |
| 20 | 7 | Yahoo commodity tickers (GC=F, SI=F, HG=F, ALI=F, NG=F, HRC=F) | `config/deep_data.yaml` | `macro_snapshots.commodities_json` keys |
| 21 | 7 | Counterparty name patterns (FII / MF-DII) classify real client names well | `config/deep_data.yaml` | share of deals classed "other" |
| 22 | 8 | Sub-sector membership is static (competitor edges have no start date: mild survivorship in backtests) | `app/kg/graph.py` | review sub-sectors yearly |
| 23 | 8 | Promoter-group links from shared promoter names in insider filings (name spelling must match across stocks) | `app/kg/graph.py` | count of same_group edges |
| 24 | 8 | Cheap model extracts stated company links reliably from news text (prompt kg_links_v1) | `app/kg/extract.py` | sample 20 AI edges after a week |
| 25 | 8 | Name matcher scores on real SEBI / USFDA / news company names (threshold 0.85) | `app/deep/matching.py` | weekly digest review section |
| 26 | 8 | Seed edges in `data/kg_seed.yaml` are correct and current | `data/kg_seed.yaml` | owner review |
| 27 | 9 | Supabase `auth.jwt() ->> 'email'` carries the signed-in email; RLS policies tested only on Postgres with a stub `auth.jwt()` | `0012_phase_9_dashboard_access.py` | sign in on the real dashboard; another email sees nothing |
| 28 | 9 | Supabase magic link + `@supabase/ssr` cookie session flow (proxy + /auth/callback) on Vercel | `dashboard/proxy.ts` | first sign-in |
| 29 | 9 | Live Supabase queries in `dashboard/lib/supabase-db.ts` (pages tested only against fixtures) | dashboard | open every page once after deploy |
| 30 | 9 | Dashboard "unverified alerts" read the config key `unverified_alerts` (filled by the Go-live checks) | `dashboard/lib/queries.ts` | Go-live phase |
| 31 | 10 | GitHub REST flow for learning-loop PRs (branch, contents PUT, PR, squash merge) with a fine-grained token | `app/analytics/github.py` | first accepted suggestion |
| 32 | 10 | Telegram `sendDocument` multipart upload of the monthly PDF | `app/notify/telegram.py` | first monthly report |
| 33 | 10 | healthchecks.io ping URL behaviour (GET, 10 s timeout) | `app/health/watchdog.py` | stop the worker once, expect an alert |
| 34 | 10 | `pg_database_size` / `pg_statio_user_tables` readable with the worker's Supabase role | `app/health/watchdog.py` | `db_size` config row |
| 35 | 10 | Cheap model keeps the monthly summary within the figures (validator rejects otherwise) | `app/analytics/monthly.py` | `monthly_reports.ai_summary_status` |
| 36 | 11 | NSE serves data-centre IPs (Oracle Mumbai/Hyderabad) without blocking or throttling; if not, `NSE_HTTP_PROXY` (HTTP proxy used only for NSE hosts) works | `app/collectors/nse_http.py`, `config/golive.yaml` | 2-hour quick check (`nse_probe`, every endpoint, with the alternative per data type) |
| 37 | 12 | Monthly expiry = the last listed expiry of each month in the broker contract list; without a list, an expiry in the last 9 days of its month (`config/core_rules.yaml`). Holds while NSE lists no weekly stock options | `app/trading/core_rules.py` | first live signals: `core_rule` rejections in Rejected (expect none) |
| 38 | 12 | Regime thresholds (VIX 13/20, percentile 40/85, Nifty 20d ±1.5%, FII 10d ±Rs 3000 cr) give sensible labels on Indian data; live FII from the NSE FII/DII collector is net Rs crore per day | `config/regime.yaml` | Today page regime card for a week; backtest "regime-aware" vs base |
