# Phase 5 setup (owner checklist): multi-agent panel, probabilities, calibration

## 1. Database change (pick ONE)
- `make migrate` (migration 0008), or
- Supabase SQL Editor: paste `docs/setup/phase-5-migration.sql` (database at Phase 4 = 0007), or
- brand-new database: `docs/setup/schema-all.sql`.
Check: `select version_num from alembic_version;` -> `0008`.
Both files were run on Postgres 16 (fresh install and upgrade from 0007). New tables
`post_mortems` and `digests` have row-level security on, like every other table.

## 2. Env keys (names only; blank = default)
Panel models (the panel runs only when ALL five are set; otherwise every candidate gets the
single analyst). Model ids must match an entry in `worker/config/ai.yaml` `models` to be
priced exactly (otherwise the role's tier price is used):
- `AI_MODEL_RESEARCHER`, `AI_MODEL_NOVELTY`: cheap tier (e.g. `claude-haiku-5-5`)
- `AI_MODEL_BULL`, `AI_MODEL_BEAR`: mid tier (e.g. `claude-sonnet-5-5`)
- `AI_MODEL_JUDGE`: strongest tier (e.g. `claude-opus-5-5`)
- `AI_MODEL_ANALYST` (already set): single analyst for overflow, and post-mortems

Caps and budget:
- `AI_MULTI_AGENT_MAX_PER_DAY` (default 4 full panels per day)
- `AI_SINGLE_ANALYST_MAX_PER_DAY` (default 5)
- `AI_TAGGER_RESERVE_INR` (default 15): kept back from the daily/monthly caps for live tagging
- `AI_DAILY_BUDGET_INR` / `AI_MONTHLY_BUDGET_INR` (unchanged: 100 / 2000)

Calibration:
- `AI_MODEL_KNOWLEDGE_CUTOFF` (YYYY-MM-DD) **must be set**: only predictions dated after it count
  for calibration and evaluation (R1). If it is empty nothing counts and the digest says so.
- `CALIBRATION_MIN_RECORDS` (default 50) and `CALIBRATION_ISOTONIC_MIN` (default 200): Platt
  scaling from 50, isotonic from 200. Both count **independent news events** (records from one
  news event weigh one in total), so the switch happens later than the raw record count.

## 3. Prices
`worker/config/ai.yaml` holds per-model prices (checked against Anthropic's pricing page on
2026-10-17: Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10, Opus 5.5 $4/$20 per million input/output
tokens; cache reads $0.01 / $0.10 / $0.20; Haiku 5.5 long-context prices above 100k prompt
tokens). Edit that file if prices change; no code change or redeploy of code is needed.
Also keep `usd_inr` current.

## 4. Expected AI cost (defaults)
About Rs 16 per full panel, Rs 3.6 per single-analyst call, Rs 1.8 per post-mortem, Rs 7/day
for live tagging: typically ~Rs 76 per trading day, worst case ~Rs 95, ~Rs 1,750 per month.
When a cap is near, panel, single analyst and post-mortems pause first; tagging and the rules
engine continue until the Rs 15 reserve is used. You get one health alert saying what paused.
The weekly digest has no AI cost (rule-based).

## 5. New scheduled jobs (automatic)
- Every 30 min: post-mortems for newly closed trades (max 5 per run).
- Mon-Fri 16:10 IST: resolve calibration outcomes (trades and counterfactual replays).
- Mon-Fri 20:10 IST: refit calibrators (all predictions, panel, single).
- Sun 08:50 IST: weekly digest on Telegram (stored in `digests`).

## 6. Owner switches (config table, default on)
- `feat.multi_agent`: off = single analyst for everything.
- `feat.probabilities`: off = ignore calibrators and keep the fixed shrink toward the market.
Selection gates are in `worker/config/options.yaml` `selection` (EV > 0, EV >= 10% of premium,
P(T1 before SL) >= 0.55); the edge-ratio gate stays `EDGE_RATIO_MIN`.

## 7. Check it works
```sql
-- which analyses used the panel, and what each agent cost
select a.id, a.symbol, a.prediction_source, a.prompt_ver, round(sum(r.cost_inr)::numeric, 2) cost
from analyses a left join agent_runs r on r.analysis_id = a.id
group by a.id order by a.id desc limit 20;

-- calibration data: records vs independent events per target and source
select target, source, count(*) records, count(distinct cluster_key) events,
       count(*) filter (where outcome is not null) resolved
from calibration_records group by 1, 2 order by 1, 2;

select target, source, method, version, n_samples, n_events, brier, active from calibrators order by fitted_at desc;
select primary_cause, count(*) from post_mortems group by 1 order by 2 desc;
```
Telegram: `/why S-<id>` shows the panel debate, who made the prediction and the calibration
method; `/stats` splits panel vs single; `/calibration` shows records and independent events by
source; the Sunday digest lists everything with suggestions (never applied automatically).
