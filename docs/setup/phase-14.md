# Phase 14 setup: Strategy Lab

Nothing to install or configure. Migrations 0019 and 0020 run automatically on deploy.

## Steps (in order)
1. **Deploy as usual.** The update timer pulls the new image, and migrations 0019 and 0020 run on start.
2. **Nothing else is needed for the history.** The lab waits for the same 5-year history check the automatic backtests use. Mining and screening only use data **before** `AI_MODEL_KNOWLEDGE_CUTOFF` (2026-06-30). Post-cutoff data is kept for the one-time holdout.
3. **Switch it on when you want it:** Dashboard → Settings → "Strategy Lab" (`feat.strategy_lab`, off by default).
   - Nightly at 23:10: seed patterns → mining → evidence → AI proposals (at most 3 a week) → screening → holdout → refinement → paper books.
   - Sunday at 09:05: a weekly Telegram summary. `/lab` in Telegram shows the status at any time.
4. **Optional env overrides** (names only; blank = default): `LAB_HYPOTHESIS_MODEL`, `LAB_SUMMARY_MODEL` and other `LAB_<ROLE>_<FIELD>` fields. Models must be in `AI_ALLOWED_MODELS`.
5. **Index event dates are checked automatically.** Each night at 22:55, while the lab is on, every entry in `data/index_events.yaml` is checked against NSE Indices press releases.
   - Only verified entries are used by Stage 0/1.
   - Mismatches (other date, other action, not found) show on the Go-live page and on Telegram. Nothing is needed from you unless one appears.
6. **Telegram alerts for a strategy book:** use the "Telegram alerts" button on the Lab page. It only appears once a book is **Validated**, which needs:
   - EOD incubation;
   - plus 15 real-fill trades, which need a broker.
   Each change is logged in the Settings change log as `lab.alerts.<book>`.
7. **Promotion into the main engine** goes only through Suggestions (accept → GitHub PR into `config/promoted_strategies.yaml`). The lab never edits live config.

## Costs
- Lab AI caps: ₹30/day and ₹600/month, inside the overall budget.
- The lab pauses first: it stops ₹20/day and ₹150/month below the overall caps, so other AI keeps running.
- Expected spend: about ₹250–450/month.
  - AI proposals: 3/week at about ₹8 each ≈ ₹105.
  - AI refinements: up to 2/night at about ₹8, only for AI-proposed and system-evidence ideas after the free deterministic grid ≈ ₹100–250.
  - Trial write-ups: about ₹0.2 each, up to 10/night ≈ ₹30–60.
- The ₹600/month cap always keeps room for the AI proposals still due that month. Refinements and write-ups pause first.
