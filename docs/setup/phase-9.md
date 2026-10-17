# Phase 9 setup (owner checklist): dashboard, morning brief, evening summary

Not deploying yet (owner): these steps are collected again in the Go-live guide.

## 1. Database change (pick ONE)
- `make migrate` (migration 0012), or
- Supabase SQL Editor: paste `docs/setup/phase-9-migration.sql` (database at 0011), or
- brand-new database: `docs/setup/schema-all.sql`.
Check: `select version_num from alembic_version;` -> `0012`.
Tested on Postgres 16 (upgrade, downgrade, re-upgrade; CI runs the same test on every PR):
another email sees nothing; the owner reads every table but can write only owner switches
(`feat.*`, `brief.morning_enabled`, `brief.evening_enabled`, `alerts_muted`) with true/false
values; every switch change is logged in `config_audit` (who, when, old -> new).
New tables: `dashboard_owner`, `config_audit`, `event_library` (copied from the history store weekly).

## 2. Accounts
1. Supabase (already planned): Authentication -> Providers -> Email: enable magic links;
   turn OFF "Allow new users to sign up". Authentication -> Users -> "Invite user": your email.
   Authentication -> URL configuration: Site URL = your Vercel URL; add
   `https://<your-vercel-domain>/auth/callback` to the redirect URLs.
2. Vercel: import the GitHub repo, Root Directory `dashboard`, framework Next.js.

## 3. Env keys (names only)
Worker (VM): `DASHBOARD_ALLOWED_EMAIL`, `DASHBOARD_BASE_URL` (the Vercel URL, for links in
alerts), optional `MORNING_BRIEF_ENABLED`, `EVENING_SUMMARY_ENABLED` (default on; the dashboard
switches override them).
Vercel: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `DASHBOARD_ALLOWED_EMAIL`.
Never put the service-role key in Vercel.

## 4. Automatic once deployed
- Worker start (and daily 07:10): `dashboard_owner` set to `DASHBOARD_ALLOWED_EMAIL`.
- 08:45 morning brief, 16:00 evening summary (trading days; morning brief also when results came
  in overnight). FII/DII: final figures for the previous session in the morning; provisional or
  pending in the evening.
- Every alert ends with "Open: <link>" to the exact signal or trade page.
- Sunday: event-study statistics copied to `event_library` for the Event library page.

## 5. Using the dashboard
Today is the landing page (live signals, open trades with P&L, today's events, system health
green/amber/red, AI spend, items needing review). Bottom tabs: Today, Signals, Trades, More.
Every page shows "Data as of <time>"; amber after 2x the producing job's interval, red after 6x
(market-hours jobs are not flagged when the market is closed).

## 6. Local preview without a database
```bash
cd dashboard && npm ci && DASHBOARD_FIXTURES=1 npm run build && DASHBOARD_FIXTURES=1 npm start
```
