# Phase 12 setup checklist

## PR 0: core rules
Nothing to set up. The rules are always on (no switch) and live in `worker/config/core_rules.yaml`
(index symbols, monthly-expiry window, never later than next month). If a signal is ever blocked by
them you will see stage `core_rule` on the dashboard's Rejected page.

## PR 1: market regime (`feat.regime`)
1. Nothing to sign up for. The deploy applies migration 0015 (`market_regime`) itself.
2. The regime is computed daily at 08:35 and shown on the Today page and in the morning brief.
   History days are filled nightly at 23:50 from the history store (VIX + Nifty; FII marked unknown).
   To fill history right away after the backfill has run:
   `docker exec desk-worker uv run --no-dev python -c "from app.regime.service import backfill; print(backfill())"`
3. Gate values per regime are in `worker/config/regime.yaml` (`gates`), shipped equal for every regime.
4. Switch off with the dashboard Settings switch `feat.regime`.

## PR 2: option-market positioning (`feat.positioning`)
1. The deploy applies migration 0016 (`positioning_snapshots`) and upgrades the history store to v6 itself.
2. Nothing to run. The nightly positioning job (23:40) waits until the 5-year history backfill is
   complete (same check as the automatic backtests). Then, once, it parses stock futures from the F&O
   files already in the download cache, builds the positioning states and base rates, and reports
   "POSITIONING HISTORY READY" on Telegram and on the dashboard Go-live page. After that it adds each
   new day automatically.
   Optional re-run (only if you change `config/positioning.yaml` thresholds or repair history):
   ```
   docker exec desk-worker uv run --no-dev python -m app.history.backfill futures --years 5
   docker exec desk-worker uv run --no-dev python -c "from app.positioning.history import nightly; print(nightly())"
   ```
3. Until a broker data source is connected, signals use the previous session's closing states. They are
   always labelled "EOD, previous session (<date>)" in /why, on the signal and Stock pages and in the
   evening summary, never as live data. Live intraday states switch on automatically with a broker.
4. Thresholds: `worker/config/positioning.yaml`. Switch: dashboard Settings `feat.positioning`.

## PR 3: watchlist paper book (`feat.watchlist_book`)
1. Nothing to set up; the deploy applies migration 0017 (a `book` column, default `main`).
2. Near-misses appear on the dashboard **Watchlist** page and in the evening summary / weekly digest.
   They never send per-trade alerts and never count in main P&L, /stats, portfolio or the monthly report.
3. Margins and switches: `worker/config/watchlist.yaml` (`calibration.include_in_fit`, `learning.include`).
   Turn the book off with the dashboard Settings switch `feat.watchlist_book`.

## Added AI cost for Phase 12
About zero: regime ~40 and positioning ~200 extra input tokens per analysis (under Rs 1 a month at
Haiku prices); the watchlist book makes no AI calls. No one-off AI job is needed.
