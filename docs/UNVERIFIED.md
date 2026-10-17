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
