# Phase 6 setup (owner checklist): backtester

No database change, no new env keys, no AI cost. Runs on the VM against the Phase 4 history
store (do the Phase 4 backfill, event study and IV backfill first).

## 1. Check the bhavcopy units once
`worker/config/backtest.yaml` `units` says how volume and OI are reported in each F&O file
format (contracts vs shares). They decide liquidity tiers and R2 fill checks. Check one day:
```bash
docker run --rm --env-file worker/.env -v /data/history:/data/history news-options-worker python -c "
from app.history.store import connect
with connect() as c:
    print(c.execute(\"select date, symbol, strike, volume, oi from hist_options where symbol='RELIANCE' order by date desc limit 3\").fetchall())
    print(c.execute(\"select date, symbol, lot_size from hist_fno_universe where symbol='RELIANCE' order by date desc limit 1\").fetchall())"
```
If volume is many times the lot size, it is in shares; if it is a small count, it is contracts.
Edit `units` if needed (no code change).

## 2. Run the comparison (rules mode, all history)
```bash
RUN="docker run --rm --env-file worker/.env -v /data/history:/data/history news-options-worker"
$RUN python -m app.backtest compare                       # 5 years, walk-forward 12m train / 3m test
$RUN python -m app.backtest compare --from 2022-01-01 --to 2026-10-16
```
It runs side by side: v2.1 rules vs the original fixed rules, per_lot vs single_exit, spread x1.5
and x2 (sensitivity), and two benchmarks (random direction on the same events; ATM option on
every event without gates). Takes minutes, not hours (2,000 simulated paths per candidate).

## 3. Replay of live AI signals (after some weeks of live running)
```bash
$RUN python -m app.backtest compare --mode replay
```
Only signals made after `AI_MODEL_KNOWLEDGE_CUTOFF` are used (R1); earlier ones are refused and
counted in the report notes.

## 4. Single run
```bash
$RUN python -m app.backtest run --rules fixed --booking single_exit --spread 1.5
```

## 5. Where results go
- Postgres `backtest_runs` (config snapshot + hash, metrics) and `backtest_trades` (every trade,
  fills, fill class, settlement-price use, % and approximate Rs).
- `/data/history/backtests/<run_id>/metrics.json` and `equity.parquet`;
  `/data/history/backtests/report_<time>.md` (the side-by-side table). A short summary on Telegram.

## 6. How to read it
- % of premium is the primary number. Rs on trades before July 2024 (inferred lot sizes) is
  approximate; add known lots to `data/lot_size_overrides.yaml` to make them exact.
- "excl. unfillable" drops trades whose size exceeded 10% of the day's volume or that used a
  settlement price (zero-volume day); "settlement-price trades" counts the latter.
- Compare against the benchmarks rows and the spread x2 row before trusting an edge.

```sql
select id, metrics_json->'variant'->>'name' as variant, metrics_json->'all'->>'n' as trades,
       metrics_json->'all'->>'expectancy_pct' as exp_pct, metrics_json->'risk'->>'max_drawdown_pct' as dd
from backtest_runs order by id desc limit 20;
```
