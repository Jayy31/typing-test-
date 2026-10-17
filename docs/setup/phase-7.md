# Phase 7 setup (owner checklist): deeper data sources and the results fast-path

## 1. Database change (pick ONE)
- `make migrate` (migration 0010), or
- Supabase SQL Editor: paste `docs/setup/phase-7-migration.sql` (database at 0009), or
- brand-new database: `docs/setup/schema-all.sql`.
Check: `select version_num from alembic_version;` -> `0010`.
Both files were run on Postgres 16 (fresh install and upgrade from 0009), and the migration was
also downgraded and re-applied. All nine new tables have row-level security on.

New tables: `insider_trades`, `bulk_block_deals`, `shareholding`, `ratings`, `regulatory_actions`
(each with `event_date` AND `disclosed_at`), `results_extracts`, `business_updates`,
`macro_releases`, `stock_stats`. New columns: `signals.enter_after`,
`macro_snapshots.commodities_json`.

## 2. Env keys
None new. Results and auto-sales extraction use `AI_MODEL_TAGGER` (cheap) with
`AI_MODEL_ANALYST` (mid) as the fallback; both are already set. Without them the XBRL path
still works and the AI fallback is skipped.

## 3. Redeploy the worker
Rebuild/restart the container. Everything else is automatic:
- Insider trades every 30 min (07:00-23:00), bulk and block deals at 18:05 and 20:05 on weekdays,
  shareholding three times a day, credit ratings from exchange announcements every 10 min,
  SEBI orders hourly, USFDA warning letters every 3 hours, macro releases hourly, auto sales on
  the 1st-4th of each month, commodities with every macro snapshot (15 min).
- Results fast-path: on days with results in the calendar (today or yesterday), the NSE results
  feed is polled every 45 s from 09:00 to 23:30 IST, otherwise every 15 min. New results: XBRL
  first (no AI), else the cheap model, then the mid model. A material beat/miss runs the signal
  cycle for that stock at once (the daily panel caps still apply).
- Results after the close or on a holiday: the signal is made at once with pricing "pending" and
  `enter_after` = the next open. At the open the stored prediction is priced with live quotes (same
  options gates; a rejection cancels the signal and is recorded for the counterfactual tracker).
  The first-15-minutes rule applies, and the entry TTL counts from 09:30, not from the evening.
- Nightly (18:45 weekdays): materiality stats (20-day average traded value from the history store;
  market cap from NSE issued shares, refreshed on Mondays), live structured events into the history
  store, and the next 6 months of the 5-year structured backfill (insider trades + bulk/block
  deals) until it is complete. The Sunday event study then learns base rates per subtype.

## 4. What changes in signals and /why
- Structured events become evidence at no tagger cost. Hand-set rules in
  `worker/config/deep_data.yaml` start at low weight: alone, they cannot create a candidate (they
  count as one unofficial source), except results, which come straight from the filing.
- Once the event study has `ANALOGUE_MIN_N` independent events for a subtype (e.g.
  `deal_buy_fii`), its base rate replaces the hand-set direction and strength.
- `/why` shows, for every structured item, "Direction from: hand-set rule (low weight)" or
  "event-study base rate (n, average move, hit rate)".
- Materiality thresholds (`deep_data.yaml` -> `materiality`): insider trades at least Rs 1 cr AND
  (10% of 20-day average traded value OR 0.05% of market cap); deals at least Rs 5 cr AND (25% of
  ADV OR 0.10% of market cap); shareholding stake changes of 0.5 pp (pledge 1 pp); results
  |surprise| at least 0.25; auto sales |YoY| at least 10%. Smaller items are stored with the
  reason but create no evidence.
- Counterparty subtypes (taxonomy tx2): promoter / promoter group / director-KMP / FII / MF-DII /
  other, learned separately.

## 5. Owner switch
`feat.deep_data` (config table, default on). Off = structured items are still collected but
ignored as evidence.

## 6. Checks after the first day
```sql
select regulator, count(*), count(symbol) as mapped from regulatory_actions group by 1;
select kind, count(*), sum(material::int) as material from bulk_block_deals group by 1;
select symbol, period_end, method, surprise_score, subtype, latency_s from results_extracts order by id desc limit 20;
select source, count(*) from news_items where source like 'deep:%' group by 1;
select key, value from config where key = 'source_health';
```
NSE endpoint paths and field names could not be called from the build sandbox. If a source
keeps failing you get the usual health alert after repeated failures; the raw row is stored in
`raw_json` for checking.

## 7. Expected AI cost
About Rs 1-3 per day (auto sales, macro releases, results that have no XBRL), about Rs 10 on a
peak results day, inside the existing budget. No one-off AI job: the history backfill uses
rules only.
