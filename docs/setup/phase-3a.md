# Phase 3a setup (owner checklist)

Phase 3a adds live option data from Upstox, implied move / edge ratio, the Monte Carlo simulator that
picks strike and expiry by expected value, entry rules, rejected-candidate records and latency fields.
Paper trades themselves arrive in Phase 3b.

## 1. Database change (pick ONE)
- **Normal route:** `make migrate` (applies migration 0005).
- **SQL Editor route** (database at Phase 2.5): paste `docs/setup/phase-3a-migration.sql` → Run.
  Tested on a 0004 database that already had a signal row.
- **Brand-new database:** paste `docs/setup/schema-all.sql` (everything up to Phase 3a).

Check:
```sql
select version_num from alembic_version;                                     -- 0005
select count(*) from pg_tables where schemaname='public' and rowsecurity;    -- 27
```

## 2. Upstox account and Analytics Token (new external step)
Read-only market data; the token cannot place orders.
1. You need an Upstox trading/demat account (free to open on upstox.com).
2. Log in to the Upstox developer portal (Developer Apps / "My Apps").
3. Open the **Analytics** tab and generate an **Analytics Token**. Per Upstox's docs it is a long-lived
   (1 year), read-only token for GET market-data APIs; only one per account at a time. Copy it.
   (Screens change; if the tab is named differently, look for "Analytics token" in their docs:
   https://upstox.com/developer/api-documentation/analytics-token/)
4. Env keys (names only, values at deploy):
   - `BROKER` = `upstox`
   - `BROKER_ACCESS_TOKEN` = the analytics token
   - `BROKER_TOKEN_ISSUED_ON` = the date you generated it (YYYY-MM-DD). The worker sends a Telegram
     reminder every day during the last 14 days before it expires.
   - `BROKER_API_KEY`, `BROKER_API_SECRET`, `BROKER_CLIENT_ID`, `BROKER_TOTP_SECRET`: not needed
     (leave blank).
5. Optional tuning keys: `OPTION_POLL_ACTIVE_MIN` (default 2), `MC_PATHS` (10000),
   `EDGE_RATIO_MIN` (1.2), `RISK_FREE_RATE` (0.065).

Without the token everything still runs: signals are created with "pricing pending" and no strike.

## 3. First run on the VM: confirm the Upstox field names (important)
I could not reach Upstox from the build sandbox, so parsers follow Upstox docs/forum samples.
The worker saves the first raw response of each endpoint per day to
`/data/history/raw_samples/upstox/`. After the first market-hours run:
```
ls /data/history/raw_samples/upstox/
head -c 1500 /data/history/raw_samples/upstox/*v2_option_chain.json; echo
head -c 1500 /data/history/raw_samples/upstox/*v2_market-quote_quotes.json; echo
```
Send me those two snippets (they contain prices only, no secrets) and I will adjust the parsers if a
field name differs.

## 4. Check it works (Supabase SQL Editor)
```sql
-- instrument keys, lot sizes and monthly expiries from the Upstox master (daily 07:35)
select symbol, instrument_key, lot_size, is_fno from instruments where is_fno order by symbol limit 10;
select value_json->'TCS' as tcs_expiries from config where key = 'fno_expiries';

-- option quotes being stored (universe 09:35 and 12:30; active signals every few minutes)
select symbol, expiry, count(*) quotes, max(ts) latest from option_quotes group by 1,2 order by latest desc limit 20;

-- priced signals: strike, EV, edge ratio, probabilities, latency
select id, symbol, option_type, strike, expiry, pricing_status, expected_value, expected_value_inr,
       edge_ratio, implied_move_pct, predicted_move_pct, p_t1_before_sl, iv_percentile, news_to_signal_s
from signals order by id desc limit 20;

-- every strike/expiry the simulator evaluated for a signal (replace 1)
select expiry, strike, option_type, ask, spread_pct, ev_pct, p_t1_before_sl, chosen, rejected_reason
from signal_candidates where signal_id = 1 order by ev_pct desc nulls last;

-- rejected candidates and why
select ts, symbol, stage, left(reason, 100) reason, ref_strike, ref_premium
from rejected_candidates order by id desc limit 30;

-- per-source health (Upstox shows up as upstox_instruments)
select value_json from config where key = 'source_health';
```

## 5. Things to review (all in `worker/config/`)
- `options.yaml`: liquidity rules (min OI 50,000, min volume 500, max spread 5%, min premium 2),
  strikes evaluated (2 ITM to 3 OTM, current + next monthly), simulator settings (IV drop after an
  event 8 vol points, costs 1.5% of premium + Rs 20 per order).
- `entry.yaml`: no entries in the first 15 minutes, skip if > 60% of the predicted move already
  happened, high-IV-before-event rule, priced-in penalty up to 30 points.
- `exits.yaml`: now has modes: `stop.mode` (underlying+premium or premium), `targets.mode` (fixed or
  predicted_move), `window_stop`. Defaults: underlying+premium stop, fixed targets, window stop on.
- IV percentile shows "n/a, building history" until about 20 trading days of quotes are stored.

## 6. Known limits
- EV and probabilities come from the analyst's scenario tree and are NOT calibrated yet (alerts say so).
  Expect them to look optimistic until Phase 4 (analogues) and Phase 5 (calibration).
- Entry, exits and paper P&L are Phase 3b; "first 15 minutes" and "signal entry expiry" apply there.
