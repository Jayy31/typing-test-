# Handoff (2026-10-19)

Read `CLAUDE.md` first (hard rules, owner decisions, progress log), then `docs/DESIGN.md` and
`docs/DESIGN_V2.md` (V2 wins on conflict). This file is the short version plus what the code
doesn't show.

## State
- **Phases 0-13: all built and merged to `main`.** Last: Phase 13 research agent (PRs #28-#32).
- **Open PRs: none.** `ENGINE_VERSION` 9.0.0, latest migration `0018`.
- **Not deployed yet.** The owner is doing the external setup step by step in chat:
  - done: Supabase, Vercel dashboard (`https://option-desk-tan.vercel.app`), Telegram bot, Anthropic key, healthchecks.io;
  - next: Oracle Always Free VM (`docs/setup/deploy.md`, `deploy/bootstrap.sh`).
- **Broker: not chosen.** Upstox is in the code but the owner's account wasn't recognised, so it is postponed.
  - Candidate: Kotak Neo (free API, TOTP; its key can trade, so keep a low balance).
  - Zerodha and Groww charge about ₹500/month.
  - Plug a new broker in through `app/market/options.py` (`OptionDataSource`) and `FuturesQuoteSource`.
  - Until a broker exists:
    - live option prices and paper trading don't run;
    - preflight flags `BROKER_ACCESS_TOKEN` / `BROKER_TOKEN_ISSUED_ON`;
    - the 2-hour go-live check shows the Upstox line as FAIL (expected).

## Owner decisions not obvious from code
- **Paper trading only.** NSE F&O **stock** options only, never index options.
  - Buy calls/puts only; one leg per trade.
  - Monthly expiries only (current month, or next when the current one is under 8 trading days).
  - Guarded by `app/trading/core_rules.py` plus a session-wide test guard.
- **AI models:**
  - Haiku 5.5 and Sonnet 5.5 only; defaults in code and `.env.example` (blank VM value = default):
    - Haiku: tagger (also results/auto-sales extraction, company links, monthly summary), panel researcher, novelty, bull, bear, `AI_MODEL_POSTMORTEM`, `AI_MODEL_RESULTS_FALLBACK`;
    - Sonnet: `AI_MODEL_ANALYST` (single analyst, also the research-agent fallback), `AI_MODEL_JUDGE`;
    - research agent (budget preset): files Haiku, live + review Sonnet, top = live (Sonnet);
  - `AI_ALLOWED_MODELS` (default Haiku + Sonnet): any role or preset outside it fails `make preflight` (which lists the model per role) and raises a startup health alert. balanced/full presets need Opus added there;
  - `AI_MODEL_KNOWLEDGE_CUTOFF=2026-06-30`: before it, the AI gives labels only (R1).
- **Research agent:**
  - `feat.research_agent` defaults to **off**; the owner switches it on in Settings.
  - Budget preset only: files on Haiku (our own data, no web), live and review on Sonnet, `research_top` blank. No Opus anywhere.
  - balanced/full presets are kept for later (`RESEARCH_AGENT_PRESET`).
  - Agent failure falls back to the **single analyst**, never the panel. With the agent on, the panel is not used.
  - 2 agent runs/day (top candidates), then the single analyst within its cap.
  - IR domains come from the `website` column in `data/symbol_master.csv` (filled from general knowledge; needs a check). Overrides go in `data/ir_domains.yaml`.
  - The smoke test's agent run has its own ₹30 limit, separate from the ₹50 smoke cap.
- **No manual steps:** jobs build themselves once their inputs exist (backtests, positioning history). Manual commands are kept only as optional re-runs.
- **Labels:** previous-session data is labelled "EOD, previous session (<date>)".
- **No custom domain.** Free hosted links only.
- **Cost estimates:** show the owner the cost before any one-off AI job and wait for approval.

## Config that matters (env wins over code defaults; blank env = default)
| Item | Value |
|---|---|
| `AI_DAILY_BUDGET_INR` / `AI_MONTHLY_BUDGET_INR` | default **150 / 2500** (owner chose these). During setup the owner was earlier told to type 50/350 into the VM `.env`; make sure it's 150/2500 or blank |
| `AI_TAGGER_RESERVE_INR` | 15 (other AI pauses first) |
| Research sub-caps (`config/research_agent.yaml` `budgets`) | files ₹20/day, ₹400/month · live ₹60/day, ₹1,300/month · review ₹40/day, ₹200/month |
| Per-run estimates (₹, for the +30% overrun alert) | files 0.5, live 26, review 25 |
| Live research | `live.max_per_day` 2; `files.max_per_night` 5; horizon 10 trading days; batch deadline 07:30 |
| Panel / single caps | `AI_MULTI_AGENT_MAX_PER_DAY` 4, `AI_SINGLE_ANALYST_MAX_PER_DAY` 5 |
| Selection | EV > 0 and ≥ 10% of premium, `p_t1_before_sl` ≥ 0.55, `EDGE_RATIO_MIN` 1.2; `SCENARIO_SHRINK_WEIGHT` 0.5 until calibrated |
| Trading | `LOTS_PER_SIGNAL` 3, `per_lot` booking; SL −50%, T1 +25%, T2 +50%, T3 +100% |
| Prices | `worker/config/ai.yaml` (checked 2026-10-19; web search $10 per 1,000 searches) |

## Known gaps / half-done
- **Everything external is unverified live.** See `docs/UNVERIFIED.md` items 1-45 (NSE endpoints, Upstox fields, real Anthropic tool and web calls, Batch API with `output_config`, company domains).
- **Broker data source:** not built (see above).
- **5-year history:** the backfill runs on the VM only; the sandbox can't reach NSE.
- **Calibration:** none yet. Everything shows "not yet calibrated" until enough post-cutoff events exist.
- **Research tools on the old panel path:** the panel and single analyst don't use the research tools; only the agent does.

## Conventions
- **One branch per piece of work.** In this cloud session the designated branch is reset to `origin/main` after each merge.
  - Each PR gets a squash merge once CI (worker + dashboard) is green; Claude merges its own PRs.
  - Commits carry the attribution lines; no model ids in commits or PRs.
- **Migrations are add-only** (`test_migrations_safe`).
  - Every new table gets RLS plus an `owner_read` policy (`test_rls_postgres`).
  - Owner writes from the dashboard are limited to:
    - `feat.*` / `brief.*` / `alerts_muted` switches;
    - suggestion decisions;
    - lesson deletes.

    All of these are logged to `config_audit`.
- **Rules live in YAML/DB config, not code.**
  - Secrets come only from env.
  - `.env.example` holds keys only.
  - Model ids come from env/config.
- **New stages:**
  - add a flag in `app/flags.py` (`DEFAULTS` + `BUILT`) and bump `ENGINE_VERSION`;
  - extend the fixtures and e2e checks for any dashboard page.
- **End of each phase:**
  - write `docs/setup/phase-N.md` (owner checklist);
  - update `docs/UNVERIFIED.md` and the CLAUDE.md progress log.
- **Checks before pushing:**
  - worker (in `worker/`): `ruff check`, `mypy app`, `pytest`;
  - dashboard (in `dashboard/`): `tsc`, `eslint`, `next build`, then `playwright test` (fixtures, with `PW_CHROMIUM=/opt/pw-browsers/chromium`).
- **Owner communication:** the owner prefers short, direct step-by-step instructions in chat over reading docs, and should never paste keys into chat.
