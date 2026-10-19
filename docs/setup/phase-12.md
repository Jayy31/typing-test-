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
2. Once, on the VM after the history backfill: parse stock futures from the F&O files already downloaded
   (served from the cache, no new downloads), then build the states and base rates:
   ```
   docker exec desk-worker uv run --no-dev python -m app.history.backfill futures --years 5
   docker exec desk-worker uv run --no-dev python -c "from app.positioning.history import nightly; print(nightly())"
   ```
   After that a nightly job (23:40) adds each new day.
3. Until a broker data source is connected, signals use the previous session's end-of-day states
   ("end of day" on the dashboard). Live intraday states switch on automatically with a broker.
4. Thresholds: `worker/config/positioning.yaml`. Switch: dashboard Settings `feat.positioning`.
