# Phase 4 setup (owner checklist): 5-year history

No Postgres changes in this phase (history lives in DuckDB/Parquet on the VM). Nothing to run in Supabase.

## 1. Env keys (names only; blank = default)
- `HISTORY_DATA_DIR` (default `/data/history`), `DUCKDB_PATH` (default `/data/history/history.duckdb`)
- `GDELT_ENABLED` (default true)
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
$RUN python -m app.history.verify                   # STOP if it reports FAIL (see section 5)
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
- Sun 06:30 IST `history_weekly`: curated events (`data/historical_events.yaml`), event study, R2 backup.

## 5. Verification (`python -m app.history.verify`)
Prints, per table: row counts, first/last date, missing weekdays (NSE holidays excluded where the
file was a 404), symbols with gaps, and a PASS/FAIL line. If a source fails with "missing column",
NSE renamed a column: fix the mapping in `worker/config/history.yaml` and re-run the same command.

## 6. Check it works
- `/why <id>` now has "HISTORICAL ANALOGUES" with n, average, hit rate and examples.
- Predicted move source shows `blended` when at least `min_n` (8) analogues exist; else `scenario_tree`.
- Still "not yet calibrated" until Phase 5.
