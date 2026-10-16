# Phase 2 setup (owner checklist)

Phase 2 adds the rules engine, the AI analyst, scoring, signals, and Telegram alerts with `/why`.
New in the database: one table, `alerts` (the Telegram outbox).

## 1. Database change (pick ONE)
- **Normal route:** `make migrate` (applies migration 0003).
- **SQL Editor route, if you already ran the Phase 0 SQL:** Supabase → SQL Editor → paste all of
  `docs/setup/phase-2-migration.sql` → Run. (Tested: it takes a Phase 0/1 database to the latest version.)
- **Brand-new database:** paste `docs/setup/schema-all.sql` instead (full schema, all phases so far).

## 2. Telegram bot (new account step, free)
1. In Telegram, open **@BotFather** → send `/newbot` → pick a name and a username ending in `bot`.
   BotFather replies with a token like `123456:ABC...`. This is `TELEGRAM_BOT_TOKEN`. Keep it secret.
2. Open a chat with your new bot and send it any message (e.g. `hi`).
3. Get your chat id: in a browser open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and find `"chat":{"id":123456789,...}`.
   That number is `TELEGRAM_CHAT_ID`. (Do this once, before the worker starts: while the worker runs it
   consumes the updates.)
4. The bot only obeys that chat id. Messages from anyone else are ignored.

## 3. Env keys used in Phase 2 (names only; set values at deploy)
New: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `AI_MODEL_ANALYST` (now required, suggested `claude-sonnet-5-5`).
Optional: `DASHBOARD_BASE_URL` (adds a "View full analysis" button once the dashboard exists, Phase 4),
`SIGNAL_SCORE_THRESHOLD` (blank = 65), `JOB_FAILURE_ALERT_THRESHOLD` (blank = 3).
Earlier keys stay as before. Blank values use defaults.

## 4. Check it works (Supabase SQL Editor, after the worker has run for a while)
```sql
-- signals and their lifecycle
select id, symbol, direction, status, score, expiry, created_at from signals order by id desc limit 20;
select signal_id, version, status, score, reason, ts from signal_versions order by ts desc limit 20;

-- full reasoning stored for a signal (replace 1)
select thesis, direction, window_days, confidence, risks, counter_args, scores_json from analyses where id = (select analysis_id from signals where id = 1);

-- Telegram outbox (status: pending -> sent; failed shows the error)
select id, kind, status, attempts, error, left(text, 60) as text from alerts order by id desc limit 20;

-- AI spend today (analyst + tagger)
select model, count(*) calls, round(sum(cost_inr)::numeric, 2) inr from ai_usage where ts::date = current_date group by 1;

-- failed analyses (thesis starts with "Analysis failed")
select symbol, thesis, created_at from analyses where scores_json->>'failed' = 'true' order by id desc limit 10;
```
In Telegram: send `/help`. When a signal appears, tap "Why filtered" or send `/why S-1`.

## 5. Things you should review
- `worker/config/linkage_map.yaml` and `worker/config/playbooks.yaml`: sector effects (e.g. crude up ->
  OMCs and airlines negative) are **starting defaults from general market logic, not validated**.
  Edit weights or add rules. Phase 5 will test them against results.
- `worker/config/signals.yaml`: scoring weights, thresholds, expiry weekday (Tuesday), cancel/upgrade rules.
- **Holidays:** expiry and trading-day maths ignore holidays until you add `market_holiday` events to
  `worker/config/calendar_events.yaml`. Without them an expiry could land on a holiday.
- **Expiry weekday:** NSE monthly stock expiry is set to Tuesday in `signals.yaml`. Confirm against NSE's
  current circular once; Phase 3 reads real expiries from the broker.

## 6. Known limits in Phase 2 (by design, finished in Phase 3)
- Signals have **no strike or premium yet**; alerts say "pending live option data". The liquidity
  score uses a fixed placeholder (70) and the priced-in penalty is 0 until live prices exist.
- Expiry is computed (last Tuesday, next month if fewer than 8 trading days remain: the design says ~7,
  8 = 5-day time stop + 3 holding days).
- `/open`, `/closed`, `/stats` answer "none yet" until paper trading arrives in Phase 3.
- Sector alerts are sentiment-based ("SECTOR WATCH"), at most one per sector per direction per day.
