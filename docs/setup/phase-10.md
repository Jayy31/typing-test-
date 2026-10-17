# Phase 10 setup (owner checklist): analytics, monthly report, learning loop, hardening

Not deploying yet (owner): these steps are collected again in the Go-live guide.

## 1. Database change (pick ONE)
- `make migrate` (migration 0013), or
- Supabase SQL Editor: paste `docs/setup/phase-10-migration.sql` (database at 0012), or
- brand-new database: `docs/setup/schema-all.sql`.
Check: `select version_num from alembic_version;` -> `0013`.
Tested on Postgres 16 (fresh install and upgrade; CI re-tests the access rules on every PR). New
tables `monthly_reports`, `suggestions`, `ai_vs_rules` (dashboard read policy on all);
`alerts.file_path`. The dashboard may change a suggestion only from pending to accepted /
rejected, nothing else; who and when are stamped by the database.

## 2. Accounts and keys (all optional)
1. **GitHub fine-grained token** for learning-loop PRs: github.com -> Settings -> Developer
   settings -> Fine-grained tokens -> "Only select repositories" = this repo; Repository
   permissions: Contents = Read and write, Pull requests = Read and write; nothing else.
   Worker env: `GITHUB_TOKEN_CONFIG_PR`, `GITHUB_CONFIG_REPO` (owner/repo). Without them
   Accept/Reject are recorded only.
2. **healthchecks.io** (free): create a check with period 5 min, grace 15 min, Telegram or email
   integration; worker env `HEALTHCHECK_PING_URL` = the check's ping URL. It alerts you if the VM
   or the worker stops (the worker cannot report its own death). Listed again in the Go-live guide.

Moved defaults: `SIGNAL_SCORE_THRESHOLD` and `EDGE_RATIO_MIN` now default to YAML
(`config/signals.yaml thresholds.signal_score`, `config/options.yaml edge_ratio_min`) so learning-loop
PRs can change them; leave the env keys blank unless you want to pin a value.

## 3. Automatic once deployed
- 1st of each month: 07:50 merge ONE accepted learning-loop PR (reverts first, then the largest
  expected effect); 08:00 review changes applied 2 months ago (revert suggested if they did not
  help) and new suggestions (at most 2, 97.5% interval, both halves, 2x spread); 08:30 monthly
  report as a PDF on Telegram + dashboard page, with a cheap-model summary paragraph kept only if
  every figure it cites is in the report.
- Every 30 min: accepted suggestions become PRs (title, evidence, expected effect in the body).
- Daily: AI vs rules resolution (16:40), data-quality checks (19:30, alert only on failures),
  live-vs-backtest drift check (22:50, alert if live expectancy leaves the backtest's 90%
  interval after 30 trades), database size (warn at 70% of 500 MB) and AI cost report (03:45).
- Every heartbeat (5 min): stuck job runs detected; healthchecks ping.
- Sources failing 5 times in a row back off 5, 10, 20 ... up to 60 minutes (circuit breaker).
- Secrets are redacted from logs and stored errors.

## 4. Dashboard
New pages: Monthly reports, Suggestions (Accept opens a PR / Reject), AI vs rules, Data quality.
Pending suggestions appear under "Needs review" on Today.

## 5. AI cost
About Rs 1-2 per month (summary paragraph, cheap model). Analytics, learning loop and quality checks
make no AI calls.
