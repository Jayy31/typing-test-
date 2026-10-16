# CLAUDE.md

Project guide for Claude Code. Read this first, then `docs/DESIGN.md` (full design, source of truth).

## What this is
News-driven research and **paper trading** system for Indian NSE F&O **stock options** (no index options).
Collects news, macro, calendar and option data → AI + rules analysis → ranked signals → automated paper trades → Telegram alerts + Next.js dashboard.

## Hard rules
- **Paper trading only.** Never write broker order-placement code. Broker API is used for market data only.
- **No technical analysis.** No indicators or candlestick logic.
- **No Kafka, no Redis.** Postgres (Supabase) is the single source of truth and the job queue.
- **Secrets only via env vars.** Never hard-code or log keys. Keep `.env.example` updated with blank values.
- **No per-news alerts.** Alerts only for signals, trade events, health problems, and the optional morning brief / evening summary / monthly report.
- Business rules (linkage map, playbooks, weights, thresholds, exit rules) live in YAML/DB config, not code.

## Trading rules (defaults, configurable)
- 1 lot per signal, no cap on open trades
- SL: premium −50% | T1: +25% | T2: +50% | T3: +100% and above
- Book 1/3 at each target; after T1 move SL to entry; after T2 trail SL at T1
- Other exits: thesis broken, no move 2 sessions after event, time stop 5 trading days before expiry
- Entry at live ask, exit at live bid; gaps exit at opening bid

## Stack
- `worker/`: Python 3.12, APScheduler, httpx, feedparser, trafilatura, Pydantic v2, SQLAlchemy 2 + Alembic, Anthropic SDK, python-telegram-bot, pytest
- `dashboard/`: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + Recharts + Supabase JS, deployed on Vercel
- DB: Supabase Postgres. Worker uses service-role key; dashboard uses anon key + row-level security (read-only) + Supabase Auth limited to one email
- Worker hosting: always-on free VM (Oracle Cloud Always Free) via Docker
- Timezone: `Asia/Kolkata` everywhere

## Conventions
- Typed Pydantic models at every boundary (external APIs, AI outputs, DB writes)
- AI outputs: strict JSON validated against schemas; store model id + prompt version + input refs with every output
- Model ids come from env (`AI_MODEL_TAGGER`, `AI_MODEL_ANALYST`, `AI_MODEL_REVIEWER`), never hard-coded
- Enforce `AI_DAILY_BUDGET_INR` / `AI_MONTHLY_BUDGET_INR`; log usage to `ai_usage`
- All jobs idempotent (unique keys), retries with backoff, log to `job_runs`
- Every external call has a timeout
- Tests required for parsers, entity mapping, scoring, exit rules
- Small commits; one branch per phase (`phase-0-foundation`, ...); open a PR per phase

## Commands (create these in Phase 0)
- `make setup` – install deps
- `make migrate` – run Alembic migrations
- `make test` – run pytest
- `make run` – start worker locally
- `make lint` – ruff + mypy
- `cd dashboard && npm run dev`

## Current phase
Phase 0 – Foundation (see `docs/DESIGN.md` §19). Update this line when a phase is done.

## Progress log
- (add one line per completed phase/PR)
