# Phase 1 setup (owner checklist)

Phase 1 adds news collection, entity mapping, the relevance gate, the AI tagger (stage 1),
macro data and calendars. **No new SQL or migration is needed** (Phase 0 tables are enough).
Everything below is done at deploy time; nothing blocks further development.

## 1. Anthropic (the only new account)
1. console.anthropic.com → create an account → Billing → add prepaid credits (start with ~$10).
2. API Keys → create a key. This is `ANTHROPIC_API_KEY`.
3. Set the model ids (names are env keys; values chosen by you):
   - `AI_MODEL_TAGGER`: a Haiku-tier model id. Suggested: `claude-haiku-5-5`
   - `AI_MODEL_ANALYST`: a Sonnet-tier id. Suggested: `claude-sonnet-5-5` (used from Phase 2)
   - `AI_MODEL_REVIEWER`: optional, Opus-tier. Suggested: `claude-opus-5-5` (Phase 5)
4. `AI_DAILY_BUDGET_INR` / `AI_MONTHLY_BUDGET_INR`: leave blank for defaults (100 / 2000).
5. Prices: `worker/config/ai.yaml` holds per-tier USD prices and the USD→INR rate. They are set for
   the suggested models. If you pick other models, update that file or the budget caps will be off.
   The `tagger.thinking: {type: disabled}` line is valid for Haiku 5.5; if your tagger model rejects
   it (error mentioning thinking), change it to `thinking: null`.

## 2. Env keys used in Phase 1 (names only; set values at deploy)
`DATABASE_URL`, `ANTHROPIC_API_KEY`, `AI_MODEL_TAGGER`, `AI_MODEL_ANALYST`, `AI_MODEL_REVIEWER`,
`AI_DAILY_BUDGET_INR`, `AI_MONTHLY_BUDGET_INR`, `NEWS_POLL_MIN`, `MACRO_PROVIDER` (blank = default).
Blank values fall back to code defaults.

## 3. After first deploy: verify the data sources (they could not be tested from my build sandbox)
Run in the Supabase SQL Editor after the worker has run for ~30 minutes:
```sql
-- which jobs work / fail (look at 'error')
select job, status, error, started_at from job_runs order by id desc limit 30;

-- news by source (a missing source = broken feed URL in worker/config/news_sources.yaml)
select source, count(*) from news_items group by 1 order by 2 desc;

-- pipeline status counts: pending -> relevant/dropped -> tagged/not_actionable/tag_failed
select status, count(*) from news_items group by 1;

-- AI tags and spend
select symbol, direction, sentiment, event_type, left(theme, 40) as theme from news_tags order by id desc limit 20;
select ts::date as day, count(*) as calls, round(sum(cost_inr)::numeric, 2) as inr from ai_usage group by 1 order by 1 desc;

-- macro and calendar
select * from macro_snapshots order by ts desc limit 3;
select * from events_calendar order by event_date limit 20;
```
What to expect / fix:
- A feed failing is logged as a warning only; send me the failing source names and I'll fix URLs.
- `nse_announcements`, `fii_dii`, `event_calendar` use unofficial NSE JSON endpoints. If they error
  with 401/403, NSE is blocking the VM's IP or headers; tell me the error text.
- `macro_snapshots.gift_nifty` stays empty: no reliable free source yet (tracked for Phase 3/4).

## 4. Things only you can do
- Fill `worker/config/calendar_events.yaml` with RBI MPC, FOMC, US CPI, holidays and expiry dates
  from the official sources listed in the file. I left it empty on purpose: I will not guess dates.
- Review `worker/config/news_sources.yaml` and remove feeds you don't want.

## 5. Not built in Phase 1 (decisions for you)
- BSE announcements: NSE announcements already cover all F&O stocks (all are dual-listed); BSE adds
  little. Say if you still want it.
- Full-article extraction (trafilatura): RSS summaries are used to keep AI cost low. Say if you want
  full article text for deep analysis in Phase 2.
