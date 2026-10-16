# Phase 0 setup (owner checklist)

## 1. Create the Supabase database
1. Sign up at supabase.com and create a new project. Pick a region near you (e.g. Mumbai) and save the database password.
2. Create the tables. Choose ONE:
   - **Option A (recommended):** put `DATABASE_URL` in `worker/.env` (step 3) and run `make migrate`.
   - **Option B:** open Supabase → SQL Editor → New query, paste all of `docs/setup/schema-all.sql`, and Run. Later `make migrate` will see the tables are already at the latest version.
3. Get the connection string: Supabase → Connect → "Session pooler" (or Direct connection). Change the prefix to `postgresql+psycopg://`:
   `DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@<host>:5432/postgres`

## 2. Check it worked (SQL Editor)
```sql
select count(*) from pg_tables where schemaname = 'public';   -- expect 17
select tablename, rowsecurity from pg_tables where schemaname = 'public' order by 1;  -- rowsecurity = true on all 16 app tables
```
After the worker's first run of the `fno_meta` job (07:30 IST daily, or `make run` then trigger it manually):
```sql
select count(*) from instruments;                                  -- 148
select count(*) from instruments where is_fno;                     -- F&O stocks found in NSE's lot-size file
select job, status, error, started_at from job_runs order by id desc limit 10;
```

## 3. Local run
```
cp .env.example worker/.env     # fill in DATABASE_URL only; the rest is needed in later phases
make setup && make migrate && make test && make run
```
Needs `uv` (https://docs.astral.sh/uv/) installed.

## 4. Not needed yet
Anthropic key, broker, Telegram, Vercel: later phases.
