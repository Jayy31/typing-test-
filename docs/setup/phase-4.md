# Phase 4 setup (owner checklist): 5-year history

No Postgres changes in this phase (history lives in DuckDB/Parquet on the VM). Nothing to run in Supabase.

## 1. Env keys (names only; blank = default)
- `HISTORY_DATA_DIR` (default `/data/history`), `DUCKDB_PATH` (default `/data/history/history.duckdb`)
- `GDELT_ENABLED` (default true)
- `ANALOGUE_PRIOR_N` (default 20) and `ANALOGUE_MIN_N` (default 5): analogue weight = n / (n + prior), none below the minimum
- `AI_MODEL_KNOWLEDGE_CUTOFF` (YYYY-MM-DD, the training cutoff of the models you set). Empty = every
  historical date is treated as "AI knows the outcome": AI labels only, never predictions (R1).
- Optional backup (off by default): `R2_BACKUP_ENABLED`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET`

## 2. Optional: Cloudflare R2 (only if you want backups)
1. Cloudflare dashboard -> R2 -> create bucket (e.g. `options-history`).
2. R2 -> Manage API tokens -> create token with Object Read & Write on that bucket.
3. Set the five `R2_*` keys above (`R2_BACKUP_ENABLED=true`). Backups run Sundays 06:30 IST.

## 3. One-time backfill on the VM (NSE blocks the build sandbox, so this runs only on the VM)
Run these in order; each is resumable and idempotent (re-run after any failure).
```bash
mkdir -p /data/history
IMG=news-options-worker
RUN="docker run --rm --env-file worker/.env -v /data/history:/data/history $IMG"

$RUN python -m app.history.backfill all            # equity, F&O, index bhavcopies, 5 years (~1 req/s, a few hours)
$RUN python -m app.history.corp_actions             # splits, bonus, demergers, special dividends (5 years)
$RUN python -m app.history.verify                   # STOP and check if it says CHECK THE ITEMS ABOVE (section 5)
$RUN python -m app.history.news_history announcements   # NSE announcements, monthly chunks
$RUN python -m app.history.tagging estimate         # prints item count + cost estimate in INR
```
**Send me the estimate output and wait for approval.** Then:
```bash
$RUN python -m app.history.tagging submit --yes     # Batch API (half price), labels only
# batches finish within 24h; run until "events written" stops growing:
$RUN python -m app.history.tagging collect
$RUN python -m app.history.event_study              # reactions + analogue stats
$RUN python -m app.history.iv_backfill              # daily IV from bhavcopy (IV percentile from day one)
$RUN python -m app.history.verify                   # final check
```
Do not run these while the worker container is running a history job (single writer lock:
you get "being written by another process" — just retry later).

## 4. Scheduled jobs (automatic after deploy)
- Mon-Fri 18:45 IST `history_nightly`: today's bhavcopies, live tagged news -> history, GDELT, IV.
- Sun 06:30 IST `history_weekly`: curated events (`data/historical_events.yaml`), your corporate-action
  factors (`data/corporate_actions.yaml`), event study, R2 backup.
- Optional: known historical lot sizes in `data/lot_size_overrides.yaml` replace inferred ones
  (before July 2024). Without them, backtest results on those dates lead with % of premium and
  mark rupee values "approximate (inferred lot size)".
- The nightly job also fetches corporate actions for the last and next 30 days.

## 5. Verification (`python -m app.history.verify`)
Prints, per table: row counts, first/last date, missing weekdays (NSE holidays excluded where the
file was a 404), symbols with gaps, and a final line: `OK` or `CHECK THE ITEMS ABOVE` (exit code 1).
It also lists:
- corporate actions by kind, and any with no factor (usually demergers). Add the factor in
  `data/corporate_actions.yaml` (it overrides the NSE text), then re-run `event_study`;
- one-day moves above 25% with no corporate action recorded. Each one is either a missing
  split/bonus/demerger (add it to the YAML) or a real move (leave it);
- the point-in-time F&O universe: stocks ever in F&O, how many have since been removed (kept in
  history), and lot sizes from the bhavcopy vs inferred (before July 2024, approximate);
- the event study: events by timing (pre_open / intraday / post_close / non_trading_day /
  unknown), how many were confounded or excluded, and how many used a sector benchmark. If a source fails with "missing column",
NSE renamed a column: fix the mapping in `worker/config/history.yaml` and re-run the same command.

## 6. Check it works
- `/why <id>` now has "HISTORICAL ANALOGUES" with n, average, hit rate and examples.
- Predicted move source shows `blended` when at least 5 analogue events exist; their weight is
  n / (n + 20), shown in `/why` with n and effective n. Otherwise `scenario_tree`.
- Still "not yet calibrated" until Phase 5.
