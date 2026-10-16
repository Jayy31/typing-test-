# Phase 3b setup (owner checklist)

Phase 3b adds paper trading: entries, exits, trade alerts, portfolio, rejected-candidate tracking,
prediction scorecard, P&L attribution, fill checks, raw quote archive and EV shrinkage.
Nothing new to sign up for.

## 1. Database change (pick ONE)
- **Normal route:** `make migrate` (applies migration 0006).
- **SQL Editor route** (database at Phase 3a): paste `docs/setup/phase-3b-migration.sql` → Run.
  Tested on a 0005 database holding a signal and option quotes.
- **Brand-new database:** paste `docs/setup/schema-all.sql`.

Check:
```sql
select version_num from alembic_version;                                     -- 0006
select count(*) from pg_tables where schemaname='public' and rowsecurity;    -- 29
```

## 2. New env key (name only; blank = default)
- `SCENARIO_SHRINK_WEIGHT` (default 0.5): share of the AI scenario tree in EV/probability/edge until
  calibration (Phase 5). 1.0 = trust the AI fully; 0.0 = market view only.

## 3. VM storage
The quote archive writes Parquet to `/data/history/parquet/option_quotes/date=YYYY-MM-DD/`
(the persistent folder from Phase 2.5). Expect roughly 5-20 MB per trading day. Check after a few days:
```
du -sh /data/history/parquet/option_quotes
ls /data/history/parquet/option_quotes | tail -3
```

## 4. Check it works (Supabase SQL Editor)
```sql
-- paper trades: entry, exit, P&L after costs, fill flag, benchmark
select id, symbol, option_type, strike, expiry, status, entry_premium, exit_premium, exit_reason,
       pnl, pnl_pct, fillability, benchmark_stock_pct, review_flag
from paper_trades order by id desc limit 20;

-- every event of a trade (replace 1)
select ts, event, premium, fraction, spot, bid_qty, ask_qty, note from trade_events where trade_id = 1 order by ts;

-- P&L attribution of closed trades (delta + theta + vega + spread + residual = gross P&L)
select id, symbol, pnl, attribution_json->'delta' delta, attribution_json->'theta' theta,
       attribution_json->'vega' vega, attribution_json->'spread' spread, attribution_json->'residual' other
from paper_trades where status = 'CLOSED' order by id desc limit 10;

-- portfolio equity and drawdown
select ts, equity, realised_inr, unrealised_inr, drawdown_pct, open_trades, premium_at_risk_inr
from portfolio_snapshots order by ts desc limit 10;

-- performance with and without unfillable trades
select count(*) trades, round(sum(pnl)::numeric, 0) pnl,
       round(sum(pnl) filter (where fillability <> 'unfillable')::numeric, 0) pnl_excl_unfillable
from paper_trades where status = 'CLOSED';

-- what rejected candidates did afterwards
select ts, symbol, stage, outcome_status, stock_return_pct, option_return_pct, exit_reason
from rejected_candidates where outcome_status <> 'open' order by id desc limit 20;

-- prediction scorecard (traded or not)
select symbol, direction, predicted_move_pct shrunk, predicted_move_raw_pct raw, realised_move_pct,
       direction_hit, move_error_pct, days_to_half_move, timing_error_days
from prediction_scores where status = 'scored' order by id desc limit 20;

-- raw vs shrunk on signals
select id, symbol, predicted_move_raw_pct, predicted_move_pct, edge_ratio_raw, edge_ratio,
       expected_value_raw, expected_value, p_t1_before_sl_raw, p_t1_before_sl
from signals order by id desc limit 10;
```
In Telegram: `/open`, `/closed`, `/stats`, `/portfolio`, `/exposure`.

## 5. Fill quantities (please check once)
After the first market-hours run, see whether quantities arrive:
```sql
select count(*) total, count(bid_qty) with_bid_qty, count(ask_qty) with_ask_qty
from option_quotes where ts > now() - interval '1 day';
```
If `with_bid_qty` is 0, Upstox's chain does not send quantities for your account. The engine then
reads the option's full depth quote at entry and exit automatically. If trades still show
`fillability = unknown`, send me one raw sample (`/data/history/raw_samples/upstox/`).

## 6. Things to review (worker/config/)
- `portfolio.yaml`: notional capital Rs 5,00,000, `opposite_signal_action` (flag or close).
- `entry.yaml`: `signal_entry_ttl_minutes` (120).
- `tracking.yaml`: counterfactual runs (12:40, 15:20; up to 60 rejections per run, 10 trading days),
  scorecard time (15:40).
- `options.yaml`: `protect_days_after_close` (2), costs (1.5% of premium + Rs 20 per order).
