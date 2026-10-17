# Phase 8 setup (owner checklist): knowledge graph and 2nd/3rd-order signals

Not deploying yet (owner): these steps are collected again in the Go-live guide.

## 1. Database change (pick ONE)
- `make migrate` (migration 0011), or
- Supabase SQL Editor: paste `docs/setup/phase-8-migration.sql` (database at 0010), or
- brand-new database: `docs/setup/schema-all.sql`.
Check: `select version_num from alembic_version;` -> `0011`.
Both files ran on Postgres 16 (fresh install and upgrade from 0010); the migration was also
downgraded and re-applied. New table `kg_paths` has row-level security on.

Changes: `kg_edges` gets exposure (kind, %), `valid_from` / `valid_to`, origin and the source news
item; `analyses.impact_order`; `regulatory_actions.match_confidence` / `match_candidates`.

## 2. Env keys (names only; blank = default)
- `KG_MAX_PANEL_SLOTS` (default 1): how many of the daily panels graph candidates may use.
Link extraction uses `AI_MODEL_TAGGER` (cheap), which is already set.

## 3. Automatic once deployed
- 07:20 daily and in the nightly history job: graph built from `data/kg_seed.yaml`, competitors
  within each sub-sector and promoter groups (same promoter entity filing for several stocks).
- Hourly at :50 (07:00-23:00): cheap-model link extraction from tagged news, at most 20 items per
  run and 80 per day, one call per item. Links need both company names matched with
  confidence >= 0.85.
- Every signal cycle: direct evidence propagates along edges (max 2 hops, x0.5 per extra hop),
  at most 10 new graph-only candidates per day; graph candidates use at most 1 panel slot.
- Nightly: edges copied into the history store; the Sunday event study learns linked-stock
  reactions per (edge type, event type), which replace the default sign table at
  `ANALOGUE_MIN_N` events.

## 4. One-off history extraction (needs your approval, after Go-live)
```bash
$RUN python -m app.kg.extract estimate        # prints the cost estimate only
$RUN python -m app.kg.extract submit --yes    # only after you approved the estimate
$RUN python -m app.kg.extract collect         # store finished batches (re-run until done)
```
Rough guess before the real estimate: Rs 150-400 for 5 years (Batch API, half price).

## 5. What you will see
- `/why`: impact order (direct / via graph 1 hop / 2 hops), each hop with its weight and where
  the sign came from (default table, low weight / event-study base rate), plus graph paths not
  used (sign unknown = support only, or the daily cap).
- `/stats`: order 1 / 2 / 3 lines.
- Weekly digest: "Regulator items to review" (name match below 0.85: stored, no evidence).
- Backtest compare: two more rows, knowledge graph at 2 hops (live default) and 3 hops (variant).

## 6. Owner-editable config
- `worker/config/kg.yaml`: edge base weights, exposure references, default sign table
  (same / opposite / unknown per edge type x event type), propagation limits.
- `data/kg_seed.yaml`: links that need a human (with exposure when known).
- `worker/config/deep_data.yaml` `regulators.min_match_confidence` (0.85).

## 7. AI cost
About Rs 1-2 per day for live link extraction once deployed, plus the one-off history batch
above (only with your approval).
