# Phase 13 setup: research agent

Nothing new to create: it uses the Anthropic key you already have. The agent is **off** until you switch it on.

1. **Budget values** (VM `/opt/desk/.env`). The defaults are now Rs 150/day and Rs 2,500/month. If your `.env` has
   `AI_DAILY_BUDGET_INR` / `AI_MONTHLY_BUDGET_INR` set, those win: set them to `150` / `2500` (or blank them), then
   `docker restart desk-worker`.
2. **Preset** (optional): `RESEARCH_AGENT_PRESET=` blank = `budget` (Haiku files without web, Sonnet live and review,
   no Opus). `balanced` / `full` are in `worker/config/research_agent.yaml`. Single fields can be overridden with
   `RESEARCH_<ROLE>_<FIELD>`, e.g. `RESEARCH_LIVE_MAX_INR=20`.
3. **Migration 0018** runs automatically on update (add-only).
4. **Smoke test**: `docker exec desk-worker python -m app.ai.smoke` now also runs one agent investigation
   (own limit Rs 30). Check the "research agent" line on Telegram.
5. **Switch on**: dashboard → Settings → "Research agent". From then on:
   - 2 investigations a day (top candidates), then the single analyst; failures fall back to the single analyst;
   - research files nightly at 22:10 (max 5, Batch API), collected overnight;
   - weekly self-review Sunday 08:20 (lessons appear in the 08:50 digest and on the Research page).
6. **Caps** (config `research_agent.yaml` `budgets`): files Rs 20/day, Rs 400/month; live Rs 60/day, Rs 1,300/month;
   review Rs 40/day, Rs 200/month. All inside the overall budget; the tagger reserve is kept.
7. **Company domains**: check a few in `data/symbol_master.csv` (`website`); correct any in `data/ir_domains.yaml`.
