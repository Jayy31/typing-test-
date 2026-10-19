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
