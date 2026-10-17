# Deploy: zero to running

Everything needed to go from nothing to a running system: the worker on a VM, Supabase for the
database, the dashboard on Vercel, and Telegram. Work through the sections in order. Secrets go
into `/opt/desk/.env` on the VM (bootstrap asks for them with input hidden) and into Vercel's
environment settings. They never go into git.

**Start on a trading-day morning before 09:00 IST.** The 2-hour quick check then covers market
hours (option polling, trading cycle). The full first-run report arrives 24 hours later.

## 1. Accounts (create in this order)

1. **GitHub**: this repo. Actions builds the worker image and pushes it to GHCR as
   `ghcr.io/<owner>/<repo>-worker`. Either make the package public (Packages > package settings), or
   run `docker login ghcr.io` on the VM with a classic token that has only `read:packages`.
2. **Supabase**: create a project in region Mumbai (`ap-south-1`) on the free tier. Note:
   - the project URL;
   - the anon key and the service_role key;
   - the database connection string (session pooler).

   Under Auth > URL configuration, set the Site URL to your Vercel URL and add
   `<vercel-url>/auth/callback` as a redirect URL.
3. **Anthropic**: create an API key. Set a monthly spend limit in the console as well as
   `AI_MONTHLY_BUDGET_INR`.
4. **Upstox**: open the developer console and generate the **Analytics Token**. It is read-only and
   valid for 1 year. Record the date you generated it in `BROKER_TOKEN_ISSUED_ON`.
5. **Telegram**: create a bot with @BotFather (`/newbot`) to get the token. Send the bot a message,
   then open `https://api.telegram.org/bot<token>/getUpdates` to read your chat id.
6. **healthchecks.io** (free): create a check with period 10 min and grace 15 min. Copy its ping URL
   into `HEALTHCHECK_PING_URL`. It emails you if the VM itself stops (the worker pings it every 5 min).
7. **Vercel**: import the repo with root directory `dashboard/`.
8. **Oracle Cloud** (the VM): see section 2.
9. Optional:
   - **Cloudflare R2** (history backup): a bucket plus an API token; set the `R2_*` values.
   - **GitHub fine-grained token** for learning-loop PRs (`GITHUB_TOKEN_CONFIG_PR`): this repo only,
     with Contents and Pull requests write access.

## 2. The VM

**Recommended: Oracle Cloud Always Free, Ampere A1 (ARM) in Mumbai or Hyderabad.**
- Shape `VM.Standard.A1.Flex`, 2 OCPU / 12 GB.
- Ubuntu 24.04 (aarch64).
- Boot volume 50 GB, plus a **separate block volume of 100 GB** attached (paravirtualized). This
  becomes `/data`, which holds the history store and backups, and it survives replacing the VM.

**Idle reclaim:** Oracle can reclaim Always Free instances it considers idle (low CPU, network and
memory over 7 days). This worker is light, so it can look idle.
- **Upgrade the account to Pay-As-You-Go.** Always Free resources stay free on PAYG and are not
  reclaimed for idleness.
- Then create a **budget of ₹0 with an alert at ₹1** (Billing > Budgets), so any accidental paid
  resource emails you at once.
- *Oracle changes these rules. Check the current Always Free and idle policy on the Oracle site
  when you set this up.*

**If A1 capacity is unavailable** ("out of host capacity"), retry later or try the other region.
**Paid x86 fallback:** any 2 vCPU / 4 GB+ x86 VM in Mumbai works unchanged (e.g. Oracle
`VM.Standard.E4.Flex` 1 OCPU / 8 GB, AWS `t3.medium` in ap-south-1, or a Hetzner/DigitalOcean
droplet). The image is multi-arch (amd64 and arm64).

**Firewall: outbound only.**
- Oracle: in the VCN security list keep only *egress all*. Remove the ingress rule for port 22
  once you use the Oracle Cloud Shell / bastion, or limit 22 to your own IP. No ports need to be
  open: the worker only makes outbound calls, and the dashboard runs on Vercel.
- On Ubuntu, Oracle images also ship iptables rules. Leave them as they are; nothing listens.

## 3. Database

The worker runs Alembic migrations itself on every deploy (`alembic upgrade head`), so the first
bootstrap creates every table, row-level security policy and the owner-only access rules. There is
no SQL to paste.

## 4. Bootstrap the VM

SSH in (or use Cloud Shell), then run:

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/deploy/bootstrap.sh -o bootstrap.sh
sudo bash bootstrap.sh <owner>/<repo>
```

Bootstrap is idempotent, so it is safe to re-run. It:
1. installs Docker;
2. mounts the block volume at `/data`, asking before it formats anything (type `YES`);
3. writes `/opt/desk/.env` (mode 600):
   - one prompt per key in `.env.example`, with secrets hidden;
   - keys you already set are kept;
   - an empty answer means the default;
4. pulls the image, runs migrations, and starts the `desk-worker` container (`restart=always`,
   `/data/history` mounted), then waits for the first heartbeat;
5. installs `desk-update.timer`;
6. runs the preflight self-check (env completeness, migrations at head, Telegram and Supabase).

Run `make preflight` locally (or `docker exec desk-worker uv run --no-dev python -m app.preflight`)
at any time.

## 5. Environment values

| Key | Required | Where | Where to get it |
|---|---|---|---|
| `APP_ENV` | no | VM .env | App |
| `TZ` | no | VM .env | App |
| `LOG_LEVEL` | no | VM .env | App |
| `DATABASE_URL` | yes | VM .env | Supabase > Project Settings > Database > Connection string (URI, session pooler) |
| `SUPABASE_URL` | yes | VM .env | Supabase > Project Settings > API > Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | VM .env | Supabase > Project Settings > API > service_role key (worker only, never Vercel) |
| `ANTHROPIC_API_KEY` | yes | VM .env | console.anthropic.com > API keys |
| `AI_MODEL_TAGGER` | yes | VM .env | AI (Anthropic) |
| `AI_MODEL_ANALYST` | yes | VM .env | AI (Anthropic) |
| `AI_MODEL_RESEARCHER` | yes | VM .env | AI (Anthropic) |
| `AI_MODEL_NOVELTY` | yes | VM .env | AI (Anthropic) |
| `AI_MODEL_BULL` | yes | VM .env | AI (Anthropic) |
| `AI_MODEL_BEAR` | yes | VM .env | AI (Anthropic) |
| `AI_MODEL_JUDGE` | yes | VM .env | AI (Anthropic) |
| `AI_MULTI_AGENT_MAX_PER_DAY` | no | VM .env | AI (Anthropic) |
| `AI_SINGLE_ANALYST_MAX_PER_DAY` | no | VM .env | AI (Anthropic) |
| `KG_MAX_PANEL_SLOTS` | no | VM .env | AI (Anthropic) |
| `AI_TAGGER_RESERVE_INR` | no | VM .env | AI (Anthropic) |
| `AI_MODEL_KNOWLEDGE_CUTOFF` | yes | VM .env | training cutoff date of the models you set (R1 look-ahead protection) |
| `AI_DAILY_BUDGET_INR` | no | VM .env | AI (Anthropic) |
| `AI_MONTHLY_BUDGET_INR` | no | VM .env | AI (Anthropic) |
| `BROKER` | no | VM .env | Broker (market data only; no order placement) |
| `BROKER_API_KEY` | no | VM .env | Broker (market data only; no order placement) |
| `BROKER_API_SECRET` | no | VM .env | Broker (market data only; no order placement) |
| `BROKER_CLIENT_ID` | no | VM .env | Broker (market data only; no order placement) |
| `BROKER_ACCESS_TOKEN` | yes | VM .env | Upstox developer console > Analytics Token (1 year) |
| `BROKER_TOTP_SECRET` | no | VM .env | Broker (market data only; no order placement) |
| `BROKER_TOKEN_ISSUED_ON` | yes | VM .env | date you generated the Analytics Token (YYYY-MM-DD) |
| `MACRO_PROVIDER` | no | VM .env | Market / macro data |
| `TWELVEDATA_API_KEY` | no | VM .env | Market / macro data |
| `ALPHAVANTAGE_API_KEY` | no | VM .env | Market / macro data |
| `TELEGRAM_BOT_TOKEN` | yes | VM .env | @BotFather > /newbot |
| `TELEGRAM_CHAT_ID` | yes | VM .env | message the bot, then open https://api.telegram.org/bot<token>/getUpdates |
| `DASHBOARD_BASE_URL` | yes | VM .env | your Vercel URL |
| `NEWS_POLL_MIN` | no | VM .env (optional) | Schedules and thresholds |
| `OPTION_POLL_ACTIVE_MIN` | no | VM .env (optional) | Schedules and thresholds |
| `SIGNAL_SCORE_THRESHOLD` | no | VM .env (optional) | Schedules and thresholds |
| `JOB_FAILURE_ALERT_THRESHOLD` | no | VM .env (optional) | Schedules and thresholds |
| `RISK_FREE_RATE` | no | VM .env (optional) | Engine (DESIGN_V2) |
| `EDGE_RATIO_MIN` | no | VM .env (optional) | Engine (DESIGN_V2) |
| `MC_PATHS` | no | VM .env (optional) | Engine (DESIGN_V2) |
| `SCENARIO_SHRINK_WEIGHT` | no | VM .env (optional) | Engine (DESIGN_V2) |
| `LOTS_PER_SIGNAL` | no | VM .env (optional) | Engine (DESIGN_V2) |
| `PARTIAL_BOOKING_MODE` | no | VM .env (optional) | Engine (DESIGN_V2) |
| `HISTORY_DATA_DIR` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `DUCKDB_PATH` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `GDELT_ENABLED` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `ANALOGUE_PRIOR_N` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `ANALOGUE_MIN_N` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `CALIBRATION_MIN_RECORDS` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `CALIBRATION_ISOTONIC_MIN` | no | VM .env (bootstrap sets paths) | History store (DuckDB + Parquet on the worker VM) |
| `R2_BACKUP_ENABLED` | no | VM .env (optional) | Optional Cloudflare R2 backup of history (off unless R2_BACKUP_ENABLED=true) |
| `R2_ACCOUNT_ID` | no | VM .env (optional) | Optional Cloudflare R2 backup of history (off unless R2_BACKUP_ENABLED=true) |
| `R2_ACCESS_KEY` | no | VM .env (optional) | Optional Cloudflare R2 backup of history (off unless R2_BACKUP_ENABLED=true) |
| `R2_SECRET_KEY` | no | VM .env (optional) | Optional Cloudflare R2 backup of history (off unless R2_BACKUP_ENABLED=true) |
| `R2_BUCKET` | no | VM .env (optional) | Optional Cloudflare R2 backup of history (off unless R2_BACKUP_ENABLED=true) |
| `NEXT_PUBLIC_SUPABASE_URL` | no | Vercel env (NEXT_PUBLIC_*) and VM .env | same as SUPABASE_URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | no | Vercel env (NEXT_PUBLIC_*) and VM .env | Supabase > Project Settings > API > anon key |
| `DASHBOARD_ALLOWED_EMAIL` | yes | Vercel env (NEXT_PUBLIC_*) and VM .env | the one email allowed to sign in |
| `MORNING_BRIEF_ENABLED` | no | Vercel env (NEXT_PUBLIC_*) and VM .env | Dashboard (Vercel, Phase 4) |
| `EVENING_SUMMARY_ENABLED` | no | Vercel env (NEXT_PUBLIC_*) and VM .env | Dashboard (Vercel, Phase 4) |
| `GITHUB_TOKEN_CONFIG_PR` | no | VM .env (optional) | GitHub > fine-grained token, this repo, contents + pull requests write |
| `GITHUB_CONFIG_REPO` | no | VM .env (optional) | Phase 10: learning-loop PRs, external heartbeat |
| `HEALTHCHECK_PING_URL` | no | VM .env (optional) | healthchecks.io check ping URL |
| `NSE_HTTP_PROXY` | no | VM .env (optional) | only if the 2-hour check shows NSE BLOCKED (e.g. http://user:pass@host:port) |

`DESK_IMAGE`, `HISTORY_DATA_DIR=/data/history` and `DUCKDB_PATH=/data/history/history.duckdb`
are added by bootstrap.

**Vercel** environment variables:
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `DASHBOARD_ALLOWED_EMAIL`

Never put the service_role key in Vercel.

## 6. Automatic redeploy

- **Worker:** every push to `main` that touches `worker/` or `data/` builds a multi-arch image
  (`.github/workflows/image.yml`), tagged with the commit SHA and `main`. On the VM,
  `desk-update.timer` runs `/opt/desk/update.sh` every 5 minutes. When it sees a new image, it:
  1. runs `alembic upgrade head`;
  2. restarts the worker;
  3. waits up to 10 minutes for a successful heartbeat;
  4. keeps the new image, or **rolls back to the previous image** and sends a Telegram alert.

  A failed image is not retried until a newer one appears. GitHub holds no SSH key and no server
  secret.
- **Rollback is always safe because migrations are add-only.** No drops, renames or type changes go
  into the same release as the code change. A removal ships in a later release, once no running
  code uses the column, and its line carries the comment `# safe-removal: <why>`.
  `tests/test_migrations_safe.py` fails CI otherwise.
- **Dashboard:** Vercel deploys `main` automatically.
- **Logs:**
  - `docker logs --tail 200 desk-worker`
  - `journalctl -u desk-update`
  - `/opt/desk/state/{current,previous,failed}`

## 7. First run

- **2 h after the first start: quick check** (Telegram and the dashboard's Go-live page). It tests
  every NSE endpoint first, then:
  - the database and Telegram;
  - the Upstox token and its expiry;
  - the AI smoke test;
  - whether market-hours jobs ran;
  - source health, job errors, and rows collected.
- **24 h after: first-run report.** Everything above, plus:
  - row counts for every table and history backfill progress;
  - Upstox response field names and the XBRL share;
  - database size;
  - each `docs/UNVERIFIED.md` item marked verified, FAILED, or *needs you* (with the manual step).
- Failed checks also appear under "Needs review" on the dashboard Today page.
- Run either check by hand: `docker exec desk-worker uv run --no-dev python -m app.golive quick`
  (or `full`, or `probe` for NSE only).
- Start the history backfill after the quick check passes (`docs/setup/phase-4.md`).

### If NSE blocks the server

NSE often blocks or throttles data-centre IPs. The quick check reports each endpoint as
OK / BLOCKED / THROTTLED / field mismatch.

**Option 1: a proxy for NSE only.** Set `NSE_HTTP_PROXY=http://user:pass@host:port` in
`/opt/desk/.env`, for example a residential/ISP proxy in India, or a small home machine running
tinyproxy. Then `docker restart desk-worker` and run `app.golive probe` again. Only NSE hosts go
through it; Upstox, Telegram, Anthropic and Supabase do not.

**Option 2: alternatives per data type.**

| NSE data | Alternative |
|---|---|
| Corporate announcements | BSE corporate announcements API (api.bseindia.com); news RSS |
| FII/DII flows | NSDL FPI daily reports (fpi.nsdl.co.in) |
| Event calendar | BSE corporate calendar; `data/manual_calendar.yaml` |
| Corporate actions | BSE corporate actions; Upstox instrument master (contract changes) |
| Insider trades (PIT) | BSE insider disclosures (SAST/PIT) |
| Bulk / block deals | BSE bulk/block deal reports |
| Shareholding | BSE shareholding pattern |
| Financial results / XBRL | BSE results XBRL; results PDFs in announcements (AI extract) |
| Equity quote (issued size) | Upstox market quote + yearly manual market caps |
| F&O ban list | none free (thin contracts are also rejected by liquidity checks) |
| Lot sizes | Upstox instrument master (already used daily) |
| Equity bhavcopy (history) | BSE bhavcopy; Upstox historical candles |
| F&O bhavcopy (history) | Upstox expired-instrument candles (paid); else none free |
| Index closes incl. VIX | Upstox index candles; Yahoo `^NSEI` / `^INDIAVIX` |

The BSE sources are not yet built as collectors. Ask for them if the proxy is not an option.

## 8. Upgrading and recovering

- **Upgrade:** merge to `main`. The VM picks up the change within about 15 minutes.
- **Pin or roll back by hand:**
  ```bash
  docker rm -f desk-worker
  docker run -d --name desk-worker --restart=always \
    --env-file /opt/desk/.env -v /data/history:/data/history <image>@<digest>
  ```
  Then write that digest to `/opt/desk/state/current`.
- **New VM:**
  1. Attach the same block volume (or restore it, see the next item).
  2. Re-run bootstrap; it keeps an existing filesystem.
  3. Copy `/opt/desk/.env` from your password manager.
- **History restore from R2:** with the `R2_*` values set,
  stop the worker, then
  `docker run --rm --env-file /opt/desk/.env -v /data/history:/data/history <image> uv run --no-dev python -m app.history.backup restore`,
  then start it again. Without R2, re-run the backfill.
- **Key rotation:**
  1. Edit `/opt/desk/.env`, then `docker restart desk-worker`.
  2. For Supabase keys, also update Vercel.
  3. Rotate Telegram with @BotFather `/revoke`, and Anthropic/GitHub in their consoles.
- **Yearly Upstox token:** the worker warns 14 days before expiry. Generate a new Analytics Token,
  set `BROKER_ACCESS_TOKEN` and `BROKER_TOKEN_ISSUED_ON`, then restart.
- **Database full:** the free tier is 500 MB. The `db_size` check warns at 80%. Quote thinning and
  cleanup run nightly.
