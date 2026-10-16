# Phase 3c setup (owner checklist)

Whole-lot booking and stop slippage visibility.

## 1. Database change (pick ONE)
- `make migrate` (migration 0007), or
- Supabase SQL Editor: paste `docs/setup/phase-3c-migration.sql` (database at Phase 3b), or
- brand-new database: `docs/setup/schema-all.sql`.
Check: `select version_num from alembic_version;` -> `0007`.

## 2. Env keys (names only; blank = default)
- `LOTS_PER_SIGNAL` (default 3)
- `PARTIAL_BOOKING_MODE` (`per_lot` default, or `single_exit`). For `single_exit`, set
  `single_exit_target` in `worker/config/exits.yaml` (default 2 = T2).

## 3. Check it works
```sql
-- lots and slippage per exit event
select trade_id, ts, event, lots, trigger_level, premium as fill, slippage_inr, gap, note
from trade_events order by ts desc limit 20;

-- total slippage per trade and overall
select id, symbol, lots, booking_mode, pnl, slippage_inr from paper_trades where status = 'CLOSED' order by id desc limit 20;
select round(sum(slippage_inr)::numeric, 0) total_slippage, count(*) filter (where slippage_inr < 0) trades_with_gaps
from paper_trades where status = 'CLOSED';
```
Telegram: `/stats` now shows total gap/slippage; trade alerts say "Sold 1 of 3 lots at ...".

## 4. Note on gaps
Fills are always at the bid actually observed. With polling every `OPTION_POLL_ACTIVE_MIN` minutes,
a stop can be crossed between two polls; the fill is then the next observed bid and is labelled
gap/slippage. A shorter poll interval reduces this (more API calls).
