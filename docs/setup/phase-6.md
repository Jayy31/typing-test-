# Phase 6 setup (owner checklist): backtester

No database change, no new env keys, no AI cost. Runs on the VM against the Phase 4 history
store (do the Phase 4 backfill, event study and IV backfill first).

## 1. Nothing to run by hand
- **Bhavcopy units** are detected on the worker every night (volume and OI in shares are exact
  multiples of the lot size; contract counts are not), per file format, stored and used by the
  backtester. You get a Telegram alert only if detection is inconclusive.
- **Rules-mode comparison** runs automatically once the Phase 4 backfill, the event study and the
  IV backfill are complete (checked daily at 22:40 IST), and sends you the Telegram summary.
- **Replay comparison** runs automatically once there are at least 30 stored live predictions made
  after `AI_MODEL_KNOWLEDGE_CUTOFF`, then every 30 days.

## 2. What the comparison contains
v2.1 vs the original fixed rules, per_lot vs single_exit, spread x1.5 and x2, the legacy
direction threshold (hit >= 50%, mean > 0) against the default strict one (hit >= 55% and the 90%
bootstrap interval of the mean move above 0), and the benchmarks. The report ends with a VERDICT:
the best variant's advantage over the default counts only if its 90% interval lies entirely above
the default's (else "NOT SIGNIFICANT"), and a config change is suggested only if it also beats
the default at 2x spread. Signals skipped because a trade on the same stock was open are
simulated as counterfactuals and reported separately (not in P&L).

## 3. Optional manual runs
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
