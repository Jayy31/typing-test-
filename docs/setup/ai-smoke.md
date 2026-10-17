# Live AI smoke test (owner checklist)

Runs one full panel (researcher, novelty, bull, bear, judge), one single-analyst call and one
post-mortem on a fixed sample (a Bharti Airtel tariff-hike cluster and a sample losing trade)
with your real models. Hard limit Rs 50 per run (`worker/config/ai.yaml` `smoke.limit_inr`):
each call reserves its worst-case cost before it is sent and is skipped if the limit would be
crossed.

## 1. Database (pick ONE)
- `make migrate` (migration 0009), or
- Supabase SQL Editor: `docs/setup/ai-smoke-migration.sql` (database at 0008), or
- new database: `docs/setup/schema-all.sql`.

## 2. Automatic run on the VM
Nothing to do: about 2 minutes after the worker starts it runs the smoke test if
`ANTHROPIC_API_KEY` and all `AI_MODEL_*` (researcher, novelty, bull, bear, judge, analyst) are set
and no run has passed yet. It stops trying after 3 failed startup runs. Results are stored in
`ai_smoke_runs` and sent to you on Telegram.

## 3. Manual run (any time)
```bash
docker run --rm --env-file worker/.env news-options-worker python -m app.ai.smoke
# or, from a checkout: make smoke-ai
```

## 4. What it reports per call
Valid on the first try (or attempts used), input / output / estimated thinking / cached tokens,
actual Rs cost vs the estimate, seconds; plus the full panel's wall time (limit 180 s).

## 5. Auto-fixes it may apply (and report)
- A role that fails its schema with thinking on -> effort `low` for that role (config table key
  `ai.effort.<role>`), then that part is re-run once.
- A panel over 180 s -> effort `low` for its two slowest roles among bull / bear / judge, re-run once.
Bull and bear always run in parallel on the VM. To undo a fix:
`delete from config where key like 'ai.effort.%';`

```sql
select started_at, trigger, passed, total_cost_inr, panel_seconds, fixes_json from ai_smoke_runs order by id desc;
```
