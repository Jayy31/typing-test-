# Phase 2.5 setup (owner checklist)

Refactor for DESIGN_V2. Nothing new to sign up for. Values are set at deploy; only key names are listed.

## 1. Database change (pick ONE)
- **Normal route:** `make migrate` (applies migration 0004).
- **SQL Editor route** (database already at Phase 2): paste all of `docs/setup/phase-2.5-migration.sql`
  into Supabase → SQL Editor → Run. Tested on a Phase 2 database that already had a signal row.
- **Brand-new database:** paste `docs/setup/schema-all.sql` (everything up to Phase 2.5).

Check:
```sql
select version_num from alembic_version;                                         -- 0004
select count(*) from pg_tables where schemaname='public' and rowsecurity;        -- 26
select column_name from information_schema.columns
 where table_name='signals' and column_name in ('engine_version','edge_ratio','predicted_move_source');  -- 3 rows
```

## 2. New env keys (names only; leave blank for defaults until the phase that needs them)
- AI roles: `AI_MODEL_RESEARCHER`, `AI_MODEL_NOVELTY` (cheap, e.g. `claude-haiku-5-5`),
  `AI_MODEL_BULL`, `AI_MODEL_BEAR` (mid, e.g. `claude-sonnet-5-5`), `AI_MODEL_JUDGE` (strongest,
  e.g. `claude-opus-5-5`), `AI_MULTI_AGENT_MAX_PER_DAY` (default 30). Used from Phase 5.
  `AI_MODEL_REVIEWER` is gone (replaced by the judge).
- Engine: `RISK_FREE_RATE` (0.065), `EDGE_RATIO_MIN` (1.2), `MC_PATHS` (10000). Used from Phase 3.
- History: `HISTORY_DATA_DIR` (`/data/history`), `DUCKDB_PATH` (`/data/history/history.duckdb`),
  `GDELT_ENABLED` (true). Used from Phase 4.
- R2 backup (Phase 4, off by default): `R2_BACKUP_ENABLED`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`,
  `R2_SECRET_KEY`, `R2_BUCKET`.

## 3. Worker VM (when you deploy)
The worker now needs a persistent folder for the history store:
```
sudo mkdir -p /data/history && sudo chown 1000:1000 /data/history
docker run --env-file worker/.env -v /data/history:/data/history news-options-worker
```

## 4. Things to review
- `worker/config/taxonomy.yaml`: the ONE list of event types and themes for live and historical tagging
  (version `tx1`). Edit before Phase 4's historical tagging; after that, any change needs a version bump.
- `worker/config/exits.yaml`: exit rules (SL -50%, T1/T2/T3 25/50/100, thirds, time stop 5 days,
  no-move after 2 sessions with <10% premium change). Same rules for live, simulator and backtester.
- Feature flags: all default ON. To switch a stage off, set a config row, e.g.
  ```sql
  insert into config(key, value_json) values ('feat.multi_agent', 'false')
  on conflict (key) do update set value_json = excluded.value_json;
  ```
  Stages that are not built yet never run, whatever the flag says; each signal stores what actually ran.

## 5. What you will see
New signals now include a predicted move with its source and an explicit "not yet calibrated" note:
```
Predicted move: +2.9% (source: scenario tree)
P(move our way): 90% (not yet calibrated)
```
`/why` adds PREDICTION, SCENARIO TREE and ENGINE sections.
