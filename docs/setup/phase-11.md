# Phase 11 setup checklist: Go-live

Full guide: `docs/setup/deploy.md`. Do these steps in order.

1. **Accounts.** Create the GitHub GHCR package (make it public or have a `read:packages` token
   ready), then Supabase (Mumbai), Anthropic, the Upstox Analytics Token, the Telegram bot,
   healthchecks.io and Vercel. Optional: Cloudflare R2 and the GitHub fine-grained token.
2. **Oracle VM.**
   - A1 Flex ARM, 2 OCPU / 12 GB, Mumbai or Hyderabad, Ubuntu 24.04, plus a 100 GB block volume.
   - Upgrade the account to Pay-As-You-Go and add a ₹0 budget with an alert at ₹1. Check Oracle's
     current idle-reclaim policy first.
   - If A1 is out of capacity, use an x86 fallback.
   - Egress only.
3. **First image.** Merge this PR to `main`; the "worker image" workflow pushes
   `ghcr.io/<owner>/<repo>-worker:main`.
4. **Bootstrap.** On a trading-day morning before 09:00 IST, run
   `sudo bash bootstrap.sh <owner>/<repo>`. Enter the values from the env table in `deploy.md`.
   Secrets are typed hidden.
5. **Vercel.** Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` and
   `DASHBOARD_ALLOWED_EMAIL`. In Supabase Auth, set the Site URL and the redirect
   `<vercel>/auth/callback`.
6. **Preflight.** Bootstrap runs it; it must print `preflight OK`.
7. **+2 h: quick check on Telegram.**
   - If any NSE endpoint is BLOCKED or THROTTLED, set `NSE_HTTP_PROXY` and restart. Otherwise, tell
     Claude which alternatives (BSE/Upstox) to build.
   - Once NSE is reachable, start the history backfill (`docs/setup/phase-4.md`).
8. **+24 h: first-run report.**
   - Fix every FAIL.
   - Do each "needs you" step. The list is also on the dashboard Go-live page and in
     `config/golive.yaml` under `unverified`.
9. **Stop test.** Stop the worker for 20 minutes (`docker stop desk-worker`) and expect a
   healthchecks.io email, then start it again (`docker start desk-worker`).

There is no SQL to run: migration 0014 (`golive_checks`) is applied by the deploy itself.

New env key: `NSE_HTTP_PROXY` (optional).
