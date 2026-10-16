# News-Driven Stock Options Research & Paper Trading System — Design

> Research and paper trading only. No real orders. Not financial advice. Verify third-party prices and free-tier limits before signing up.


## 1. Goal and scope

- **Goal:** automatically detect news and events that can move Indian F&O stocks or sectors, suggest option trades (call/put), and paper-trade them to measure whether a news-based edge exists.
- **Universe:** stocks in the NSE F&O segment (large and mid caps). Index options (Nifty, Sensex) are **not** traded; index data is used only as market context.
- **Instrument:** monthly stock options. Current month by default; next month when the expected trigger is further out.
- **No technical analysis:** no indicators, no candlestick patterns. Decisions use news, events, macro, sector linkage, fundamentals and market positioning data only.
- **Fully automated:** data collection, analysis, filtering, ranking, paper entry, tracking, exit and reporting run without manual research.
- **Mode:** research and paper trading only. No broker order placement.


## 2. Trading rules (paper trading)

| Rule | Value |
|---|---|
| Stop-loss | Premium falls 50% from entry |
| Target 1 | Premium +25% |
| Target 2 | Premium +50% |
| Target 3 | Premium +100% and above |
| Position size | 1 lot per signal (budget not a constraint in paper mode) |
| Max open trades | No limit (all qualifying signals are tracked) |
| Holding period | Any: same day, a few days, or till near expiry |
| Strike | Nearest liquid ATM; slightly ITM/OTM allowed if liquidity is better |
| Expiry | Current month; next month if trigger date is after current expiry or fewer than ~7 trading days remain |


### Target handling (proposed default, configurable)

- Track all three targets on the same trade and record the time each was hit, so results can be compared later.
- Default exit logic: book 1/3 at T1, 1/3 at T2, final 1/3 at T3 or trailing stop.
- After T1 hit, stop-loss moves to entry price (no-loss trade). After T2, stop trails at T1 level.
- Additional exits: thesis invalidated by new news, event passed with no move (after 2 sessions), time stop 5 trading days before expiry.
- All exit rules live in config, so alternatives can be back-tested on the same signals.


## 3. What you will see (end result)


### Telegram alerts (only for filtered stocks or sectors)

No alerts for raw news. You get an alert only when the system shortlists a stock or sector, and when a paper trade changes state.

```
NEW SIGNAL  #S-1042   Score 82/100   Confidence: High
BHARTIARTL  |  CALL  |  Strike 2000  |  Expiry 24-Nov-2026
Entry premium: 48.50   SL: 24.25   T1: 60.60  T2: 72.75  T3: 97.00+
Trigger: Tariff hike announced by peers; ARPU upside (Sector: Telecom)
Sentiment: Strongly positive (4 sources)   Event window: 1-5 days
[View full analysis]  [Why filtered]  [Dismiss]
```

- **Signal alerts:** new stock or sector shortlisted, with one-line reason.
- **Trade alerts:** paper entry, T1/T2/T3 hit, SL hit, thesis-broken exit, time exit.
- **Signal updates:** score upgraded, downgraded or cancelled due to new information.
- **Drill-down:** tap "View full analysis" or reply /why S-1042 to get the complete reasoning.
- **Morning brief (8:45 AM, optional):** one market summary: global cues, GIFT Nifty, crude, USD/INR, FII/DII flows, key events and results today, active signals to watch.
- **Evening summary (4 PM, optional):** open trades, day's P&L, signals raised or cancelled today.
- **Monthly report:** what worked, what didn't, best and worst trigger types, monthly P&L, suggested rule changes (sent as PDF/link at month end).


### Full analysis for each filtered stock (drill-down)

- Why it was filtered: each trigger and the rule it matched.
- All linked news items with source, time, sentiment, and AI summary.
- Sentiment breakdown: per source and combined, plus trend over the last days.
- Sector context: sector sentiment, peer moves, first- and second-order effects.
- Macro context at signal time: crude, USD/INR, US yields, FII/DII flows, India VIX, GIFT Nifty, related ADR moves.
- Company data: upcoming events (results date, dividend, corporate actions), last quarter results vs expectations.
- Option data: chosen strike and why, premium, IV vs its own history, OI, volume, bid-ask spread, days to expiry.
- Score breakdown: impact, surprise, clarity, timing, liquidity, priced-in penalty.
- Risks and counter-arguments: what could make the trade fail.
- Historical analogues: similar past events and how the stock or sector reacted.
- Live status: current premium, P&L, targets hit, timeline of updates.


### Web dashboard

Next.js dashboard with: Live Signals, Open Trades, Closed Trades, News Feed, Stock Analysis page (same drill-down as above), Sector Heatmap, Event Calendar, Macro Panel, Performance Analytics, System Health. Details in section 11.


## 4. System architecture

```
 +-------------------------- DATA COLLECTORS (scheduled) ---------------------------+
 | News (RSS, NSE/BSE filings, PIB, RBI, SEBI) | Market data (broker API)          |
 | Global macro (crude, DXY, yields, ADRs)    | Calendars (results, expiry, bans) |
 | FII/DII flows | F&O universe + lot sizes | Corporate actions                     |
 +------------------------------------+---------------------------------------------+
                                      |  raw rows -> Postgres (Supabase)
                                      v
 +------------------------------ PROCESSING --------------------------------------+
 | 1. Dedup + clean  ->  2. Entity mapping (company/sector)  ->  3. Relevance gate |
 | 4. AI analysis (cheap model: tag; strong model: deep analysis)                  |
 | 5. Rules engine (sector linkage map, event playbooks)                           |
 +------------------------------------+---------------------------------------------+
                                      v
 +------------------------------ SIGNAL ENGINE -----------------------------------+
 | Eligibility filters -> Scoring -> Strike/expiry selection -> Signal lifecycle   |
 | (new / upgraded / downgraded / cancelled)                                       |
 +-------------------+--------------------------------------+-----------------------+
                     v                                      v
 +------------ PAPER TRADING ENGINE ----------+   +-------- NOTIFIER ------------+
 | Entry at live premium, tracking every N min|   | Telegram: signals + trades   |
 | Targets / SL / trailing / time / thesis    |   | /why, /open, /stats commands |
 +-------------------+------------------------+   +------------------------------+
                     v
 +------------ ANALYTICS + LEARNING ----------+   +-------- DASHBOARD (Next.js) --+
 | Win rate by trigger, sector, score band    |   | Reads Supabase directly       |
 | Monthly report, rule-weight suggestions    |   | Signals, trades, analysis     |
 +--------------------------------------------+   +-------------------------------+
```


### Key design decisions

- **No Kafka, no Redis.** Volume is small (hundreds to a few thousand items/day). Postgres tables act as the queue (status columns + row locking). Fewer moving parts, zero cost, easier AI-assisted coding.
- **Single Python service** with scheduled jobs (APScheduler) instead of microservices.
- **Postgres is the single source of truth.** Every raw input, AI output, signal version and trade event is stored, so every decision is explainable and reproducible.
- **Idempotent jobs.** Each job can re-run safely (unique keys on news URL hash, signal IDs, trade events).
- **Config-driven rules.** Sector linkage map, scoring weights and exit rules live in YAML / DB, not code.


## 5. Components in detail


### 5.1 News collector

- Polls RSS feeds and official sources every 5 min during 7:00 AM - 11:00 PM IST, every 30 min otherwise (global news matters overnight).
- Sources: Economic Times Markets, Moneycontrol, Business Standard, Livemint, Reuters India/Business, NSE corporate announcements, BSE announcements, PIB, RBI press releases, SEBI orders.
- Normalises to: title, body/summary, source, URL, published time, fetched time.
- Dedup by URL hash plus near-duplicate detection (same story on multiple sites grouped into one 'story cluster').


### 5.2 Entity mapper

- Maps text to NSE symbols using a symbol master with aliases (e.g. 'Airtel' -> BHARTIARTL, 'SBI' -> SBIN).
- Maps company -> sector -> sub-sector; tags macro themes (crude, rates, war, tariffs, monsoon).
- Cheap keyword pass first; ambiguous items go to the AI tagger.


### 5.3 Relevance gate

- Drops items with no F&O stock, sector or macro theme.
- Drops opinion pieces, recaps of old moves, and 'stocks to watch' lists unless they contain new facts.
- Keeps cost low: only relevant items reach the AI.


### 5.4 Market data collector

- Broker API: spot price, option chain (premium, OI, volume, IV, bid/ask) for F&O stocks.
- Polls option chains for (a) all stocks with active signals or open trades every 1-5 min, (b) full universe once in the morning and once mid-day.
- Global: Brent crude, USD/INR, DXY, US 10Y yield, Indian ADRs, GIFT Nifty, India VIX.
- Daily: FII/DII flows, F&O ban list, F&O universe and lot sizes, corporate actions.


### 5.5 Calendar tracker

- Results dates, board meetings, AGMs, dividend/record dates, splits/bonus.
- Monthly stock expiry (last Tuesday, NSE; verify on changes), market holidays, RBI MPC dates, US FOMC and CPI dates, GST council, Budget, index rebalancing.
- Used both as triggers (upcoming event) and as constraints (avoid holding into a known risk).


### 5.6 AI analyst

- Stage 1 (cheap model): classify each relevant item: affected symbols/sectors, direction, event type, novelty, sentiment.
- Stage 2 (strong model): for clusters that pass a threshold, deep analysis with full context (macro snapshot, sector news, company calendar, option data, historical analogues). Produces the thesis, risks and scores.
- Outputs are strict JSON validated against a schema; invalid outputs retried.


### 5.7 Rules engine

- Sector linkage map (e.g. crude up -> OMCs, paints, aviation negative; upstream oil positive).
- Event playbooks (e.g. RBI cut -> banks, NBFCs, realty, auto positive).
- Rules add or adjust scores on top of AI judgement; both are stored separately for later comparison.


### 5.8 Signal engine

- Applies eligibility filters, scoring, priced-in check, strike/expiry selection.
- Signal lifecycle: NEW -> ACTIVE -> UPGRADED / DOWNGRADED -> CANCELLED or CONVERTED_TO_TRADE.
- Re-evaluates active signals whenever new related news or market data arrives.


### 5.9 Paper trading engine

- Enters at live ask (realistic) at signal time; records spread.
- Monitors every 1-5 min; applies SL, T1/T2/T3, trailing, thesis-broken, no-move and time exits.
- Logs every state change as a trade event (full audit trail).


### 5.10 Notifier

- Telegram bot: signal alerts, trade alerts, signal updates.
- Commands: /why <id>, /open, /closed, /stats, /mute, /unmute.
- Rate-limited and grouped to avoid spam.


### 5.11 Analytics and learning

- Daily and monthly stats: win rate, average return, max drawdown, by trigger type, sector, score band, expiry choice.
- Compares AI-only vs rules-only vs combined scores.
- Monthly AI-written review with suggested weight changes (applied only after your approval).


### 5.12 Dashboard

- Next.js app reading Supabase; login-protected. See section 11.


### 5.13 Health monitor

- Heartbeat per job, last successful run, error counts, API quota usage, AI spend today/month.
- Telegram alert to you only if a job fails repeatedly or spend crosses a limit.


## 6. Signal parameters and filters


### A. Eligibility filters (hard gates)

- Stock is in the NSE F&O segment.
- Liquidity: minimum option volume and OI at the chosen strike, bid-ask spread below a % threshold of premium.
- Not in the F&O ban list.
- No contract-adjusting corporate action (split, bonus, demerger, special dividend) inside the holding window, or flagged clearly.
- Enough trading days left to expiry for the expected move window.


### B. Scheduled triggers

- Quarterly results in the expiry window (and results vs expectations once out).
- Analyst/investor meets, AGMs, product launches.
- RBI policy, US Fed, US CPI/jobs, GST council, Budget, cabinet decisions, PLI schemes.
- Monthly auto sales, GST collections, core sector data.
- Elections and results.
- Ex-dividend and record dates.
- Index rebalancing (Nifty, MSCI, FTSE) announcements and effective dates.
- US peer results (Accenture for IT, US banks, pharma peers).


### C. Unscheduled triggers

- War, geopolitical tension, terror events.
- Crude oil spikes or crashes.
- Tariffs, sanctions, trade deals.
- Government bills, regulations, bans, policy reversals.
- Order wins and contracts (defence, railways, infra, IT deals).
- M&A, stake sales, block and bulk deals, promoter buying/pledging.
- Regulatory action: SEBI, RBI, USFDA, CCI.
- Management changes, fraud allegations, short-seller reports.
- Credit rating changes.
- Natural disasters, monsoon, plant shutdowns.


### D. Macro and global context

- Brent crude, USD/INR, DXY, US 10Y yield.
- FII/DII daily flows.
- Overnight US/Asian markets, GIFT Nifty, India VIX.
- Indian ADRs (Infosys, Wipro, HDFC Bank, ICICI Bank, Dr. Reddy's, others) vs domestic close.
- Metals (copper, aluminium, steel), gold, China data.


### E. Calendar and expiry factors

- Monthly stock expiry date and days remaining.
- Weekly index expiry days: minor volatility factor for index heavyweights only.
- Holidays in the window, monthly rollover data, index rebalancing dates.


### F. Trade-level checks before entry

- Priced-in check: how much the stock already moved since the news.
- IV vs the stock's own recent IV range (avoid entering at extreme IV before an event unless the thesis is about the event move).
- OI build-up around nearby strikes.
- Market mood that day (risk-on / risk-off).


## 7. AI analysis design


### Two-stage pipeline

| Stage | Model tier | Input | Output |
|---|---|---|---|
| 1. Tagger | Cheap/fast (e.g. Claude Haiku) | One news item + symbol hints | Symbols, sectors, themes, event type, direction, sentiment (-1 to +1), novelty, is_actionable |
| 2. Analyst | Strong (e.g. Claude Sonnet / Opus for top picks) | Story cluster + macro snapshot + sector news + company calendar + option data + historical analogues | Thesis, direction, expected move size and window, scores, risks, counter-arguments, confidence |
| 3. Reviewer (optional, top signals) | Strong | Stage 2 output only | Independent critique; flags weak reasoning; can downgrade |


### Quality controls

- Strict JSON schema output, validated with Pydantic; retries on failure.
- Every claim must reference a stored news item ID or data point (no unsupported reasoning).
- Prompt caching for the fixed system prompt and sector linkage map to cut cost.
- Prompts and model versions stored with every output, so results are reproducible.
- Model names configured in env, so they can be upgraded without code changes.


### Sentiment

- Per item: score -1 to +1 with reason.
- Per stock: time-decayed weighted average across sources (official filings weighted highest).
- Per sector: aggregate of member stocks plus sector-level news.
- Trend: change over 1 day, 3 days, 7 days.


## 8. Scoring and ranking

| Component | Weight (default) | Meaning |
|---|---|---|
| Impact | 25% | Typical move size for this event type on this stock/sector |
| Surprise | 20% | How unexpected vs consensus or prior news |
| Clarity | 15% | Clear direction vs mixed effects |
| Timing | 15% | Move likely within days vs vague future |
| Source quality | 10% | Official filing / multiple reputable sources vs single rumour |
| Liquidity | 10% | Option volume, OI, spread quality |
| Macro alignment | 5% | Macro and sector backdrop supports the direction |
| Priced-in penalty | minus up to 30 | Stock already moved a lot after the news |

- Final score 0-100. Signal raised at score >= 65 (configurable). 'High conviction' at >= 80.
- Weights stored in DB; the learning loop proposes changes monthly.


## 9. Paper trading engine

- **Entry:** at the live ask price at signal time (realistic fill). Records bid, ask, spread, IV, OI, spot.
- **Monitoring:** every 1-5 min during market hours (9:15 AM - 3:30 PM IST).
- **Exit pricing:** at live bid (realistic). Slippage and spread recorded.
- **Exit triggers:** SL -50%, T1 +25%, T2 +50%, T3 +100%+, trailing after T1, thesis broken, no move after event, time stop near expiry.
- **Costs:** optional brokerage, STT and exchange charges model for realistic net P&L.
- **Gap handling:** if the market opens beyond SL or target, exit at the opening bid (not at the level).
- **Audit trail:** every state change saved as a trade event with timestamp and reason.


## 10. Alerts and stock drill-down

- Alert kinds: signal (new/upgraded/downgraded/cancelled), trade event, system health problem, plus optional morning brief, evening summary and monthly report. No per-news alerts.
- Each alert has a short ID; reply /why <id> in Telegram or open the dashboard link for full analysis.
- Drill-down content: listed in section 3.


## 11. Dashboard

| Page | Contents |
|---|---|
| Live Signals | Ranked signals, score, direction, strike, entry, status, last update |
| Stock Analysis | Full drill-down: triggers, news, sentiment, sector, macro, option data, score breakdown, risks, analogues, timeline |
| Open Trades | Entry vs current premium, P&L, targets hit, SL level |
| Closed Trades | History with outcome, holding time, exit reason |
| News Feed | All relevant news, each tagged with affected stocks/sectors, direction, sentiment and impact; filters by stock, sector, theme, impact; links to related signals |
| Sector Heatmap | Sentiment and signal count per sector, trend |
| Event Calendar | Results, policy, expiry, holidays, rebalancing |
| Macro Panel | Crude, INR, DXY, yields, FII/DII, VIX, ADRs |
| Performance | Win rate, average return, P&L curve, by trigger/sector/score band, AI vs rules comparison |
| System Health | Job status, last runs, errors, API quota, AI spend |
| Settings | Scoring weights, thresholds, exit rules, alert preferences |


## 12. Data sources (free)

| Data | Source | Cost |
|---|---|---|
| Market news | RSS: Economic Times, Moneycontrol, Business Standard, Livemint, Reuters | Free |
| Company filings | NSE and BSE corporate announcements | Free |
| Government / regulator | PIB, RBI, SEBI press releases and orders | Free |
| Option chain, prices, OI, IV | Broker API: Upstox / Angel One SmartAPI / Dhan (free with account, verify current terms); Zerodha Kite Connect (paid) | Free to ~Rs 500/month |
| Crude, DXY, yields, ADRs | yfinance (Yahoo Finance) or Twelve Data / Alpha Vantage free tier | Free |
| FII/DII flows | NSE daily reports | Free |
| F&O universe, lot sizes, ban list | NSE published files | Free |
| Results / corporate action calendar | NSE/BSE event calendars | Free |
| Global calendar (Fed, CPI) | Official Fed/BLS calendars, maintained YAML | Free |

> NSE website data should be fetched politely (low frequency, proper headers, caching). Broker API is the primary source for live market data; scraping is only for public reference files.


## 13. Technology stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Best finance, news-parsing, broker SDK and AI libraries; AI tools write it reliably |
| Scheduler | APScheduler inside one worker process | Simple, no extra infra |
| HTTP / parsing | httpx, feedparser, trafilatura, BeautifulSoup | Robust fetching and article extraction |
| Validation | Pydantic v2 | Strict schemas for AI outputs and configs |
| DB access | SQLAlchemy 2 + Alembic migrations | Typed models, safe schema changes |
| Database | Supabase Postgres (free tier) | Hosted, UI, auth, row-level security for dashboard |
| AI | Anthropic Claude API (Haiku tier for tagging, Sonnet/Opus tier for analysis) | Quality reasoning; prompt caching lowers cost |
| Alerts | Telegram Bot API (python-telegram-bot) | Free, instant, supports commands |
| Dashboard | Next.js + Tailwind + shadcn/ui + Recharts, Supabase JS client | Free hosting on Vercel |
| Worker hosting | Oracle Cloud Always Free VM or a small VPS; alternative: GitHub Actions cron (limited) | Continuous process needed for 1-5 min polling |
| Logging / errors | Structured logs + Sentry free tier | Visibility into failures |
| Tests / CI | pytest + GitHub Actions | Catch regressions from AI-written code |

> **Hosting note:** GitHub Actions cron is free but runs at best every ~5 min and can be delayed; it cannot hold a live websocket. For true near-real-time tracking, a continuously running free VM (Oracle Cloud Always Free) is the recommended choice. Verify free-tier availability at signup.


## 14. Database schema (core tables)

```
instruments        symbol, name, sector, sub_sector, aliases[], is_fno, lot_size, updated_at
news_items         id, url_hash (unique), source, title, body, url, published_at, fetched_at, cluster_id
news_clusters      id, headline, first_seen, last_seen, item_count
news_tags          news_id, symbol, sector, theme, event_type, direction, sentiment, novelty, model, prompt_ver
macro_snapshots    ts, brent, usdinr, dxy, us10y, gift_nifty, india_vix, fii_net, dii_net, adr_json
option_quotes      ts, symbol, expiry, strike, type, bid, ask, ltp, iv, oi, volume, spot
events_calendar    id, symbol/sector, event_type, event_date, source, details
analyses           id, cluster_id, symbol, thesis, direction, move_size, window_days, risks, counter_args,
                   analogues, scores_json, confidence, model, prompt_ver, created_at
signals            id, symbol, sector, direction, strike, expiry, score, status, analysis_id,
                   created_at, updated_at
signal_versions    signal_id, version, score, status, reason, ts
paper_trades       id, signal_id, entry_ts, entry_premium, lots, sl, t1, t2, t3, status,
                   exit_ts, exit_premium, pnl, exit_reason
trade_events       trade_id, ts, event (ENTRY/T1/T2/T3/SL/TRAIL/THESIS/TIME/NOMOVE), premium, note
rules              id, name, condition_json, effect_json, enabled
config             key, value_json
job_runs           job, started_at, finished_at, status, error, items_processed
ai_usage           ts, model, input_tokens, output_tokens, cached_tokens, cost_inr
```


## 15. Project structure

```
news-options-research/
  worker/                    # Python service
    app/
      config.py              # loads env + DB config
      db/ (models.py, session.py, migrations/)
      collectors/ (news_rss.py, nse_bse.py, govt.py, broker.py, macro.py, calendar.py, fno_meta.py)
      processing/ (dedup.py, entity_map.py, relevance.py)
      ai/ (client.py, prompts/, schemas.py, tagger.py, analyst.py, reviewer.py)
      rules/ (linkage_map.yaml, playbooks.yaml, engine.py)
      signals/ (filters.py, scoring.py, strike_select.py, lifecycle.py)
      trading/ (paper_engine.py, exits.py, costs.py)
      notify/ (telegram_bot.py, templates.py)
      analytics/ (stats.py, monthly_review.py)
      health/ (heartbeat.py)
      scheduler.py           # all job schedules
    tests/
    pyproject.toml
    Dockerfile
  dashboard/                 # Next.js app
  data/ (symbol_master.csv, historical_events.yaml)
  .env.example
  README.md
```


## 16. Environment variables and secrets

All keys and settings live in environment variables on the deploy target (VM: **.env** file readable only by the service user, or systemd EnvironmentFile; Vercel: Project Settings > Environment Variables; GitHub Actions: Repository Secrets). Never commit secrets. A **.env.example** with blank values is committed instead.


### Worker (.env)

```
# --- App ---
APP_ENV=production
TZ=Asia/Kolkata
LOG_LEVEL=INFO

# --- Database (Supabase) ---
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=          # worker only, never in dashboard

# --- AI (Anthropic) ---
ANTHROPIC_API_KEY=
AI_MODEL_TAGGER=                    # e.g. a Haiku-tier model id
AI_MODEL_ANALYST=                   # e.g. a Sonnet-tier model id
AI_MODEL_REVIEWER=                  # e.g. an Opus-tier model id (optional)
AI_DAILY_BUDGET_INR=100
AI_MONTHLY_BUDGET_INR=2000

# --- Broker (choose one) ---
BROKER=upstox                       # upstox | angelone | dhan | kite
BROKER_API_KEY=
BROKER_API_SECRET=
BROKER_CLIENT_ID=
BROKER_ACCESS_TOKEN=                # refreshed daily by auth job where required
BROKER_TOTP_SECRET=                 # only if broker login flow needs TOTP

# --- Market / macro data ---
MACRO_PROVIDER=yfinance             # yfinance | twelvedata | alphavantage
TWELVEDATA_API_KEY=
ALPHAVANTAGE_API_KEY=

# --- Telegram ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DASHBOARD_BASE_URL=https://your-app.vercel.app

# --- Monitoring ---
SENTRY_DSN=

# --- Schedules (minutes) ---
NEWS_POLL_MIN=5
OPTION_POLL_ACTIVE_MIN=2
SIGNAL_SCORE_THRESHOLD=65
```


### Dashboard (Vercel)

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=      # read-only via row-level security
DASHBOARD_ALLOWED_EMAIL=            # only your login can access
```


### Security practices

- Service-role key only on the worker; dashboard uses the anon key with row-level security (read-only).
- Dashboard protected by Supabase Auth (email magic link) limited to your email.
- Broker API used in read-only/data mode; no order-placement code in the project.
- Rotate keys if leaked; budget caps on AI spend enforced in code.


## 17. Setup checklist (what you provide)

| # | Item | Where | Cost |
|---|---|---|---|
| 1 | GitHub account + private repo | github.com | Free |
| 2 | Supabase project (DB URL, service key, anon key) | supabase.com | Free |
| 3 | Vercel account linked to GitHub | vercel.com | Free |
| 4 | Anthropic API key + prepaid credits | console.anthropic.com | Pay as you go |
| 5 | Broker demat account + API app (key, secret, client ID) | Upstox / Angel One / Dhan developer portals | Free to ~Rs 500/month |
| 6 | Telegram bot token (BotFather) + your chat ID | Telegram | Free |
| 7 | Free VM for the worker | Oracle Cloud Always Free (or any small VPS) | Free |
| 8 | Sentry project (optional) | sentry.io | Free |
| 9 | Twelve Data / Alpha Vantage key (optional backup) | Their sites | Free tier |


## 18. Cost breakdown (monthly estimate)

| Item | Estimate |
|---|---|
| Worker hosting (Oracle Always Free VM) | Rs 0 |
| Database (Supabase free tier) | Rs 0 |
| Dashboard (Vercel free tier) | Rs 0 |
| News and official sources | Rs 0 |
| Macro data (yfinance / free tiers) | Rs 0 |
| Telegram | Rs 0 |
| Broker data API | Rs 0 (Upstox/Angel/Dhan, verify) or ~Rs 500 (Kite) |
| Claude API | ~Rs 500 - 2,500 depending on news volume and how many deep analyses run (estimate) |
| **Total** | **~Rs 500 - 3,000 / month** |


### How AI cost stays low without hurting quality

- Free keyword/entity pass removes most irrelevant news before any AI call.
- Cheap model for tagging; strong model only for story clusters that pass the threshold.
- One deep analysis per story cluster, not per article.
- Prompt caching for large fixed context (system prompt, linkage map).
- Daily and monthly budget caps with alert; system degrades gracefully (tagging continues, deep analysis queued).

> Supabase free projects may pause after inactivity and have storage limits; with continuous jobs it stays active. Old option quote rows can be downsampled or archived to stay within free storage.


## 19. Build phases


### Phase 0 - Foundation (2-3 days)

- Repo, Python project, Docker, .env.example, Supabase schema + migrations.
- Symbol master with sectors and aliases; F&O universe and lot sizes loader.
- Job runner, job_runs logging, health heartbeat, Sentry.


### Phase 1 - News intelligence (4-6 days)

- RSS + NSE/BSE + PIB/RBI/SEBI collectors; dedup and clustering.
- Entity mapping and relevance gate.
- AI tagger (stage 1) with schemas, budget tracking, prompt caching.
- Macro collector and calendar tracker.


### Phase 2 - Signals and alerts (4-6 days)

- Rules engine with sector linkage map and playbooks.
- AI analyst (stage 2) with full context assembly.
- Eligibility filters (without live options: use NSE reference data first), scoring, signal lifecycle.
- Telegram alerts for signals + /why drill-down.


### Phase 3 - Live options and paper trading (4-6 days)

- Broker API integration, auth refresh, option chain polling.
- Liquidity filters, strike/expiry selection, priced-in and IV checks.
- Paper trading engine with targets, SL, trailing, thesis, no-move and time exits; trade alerts.


### Phase 4 - Dashboard (4-6 days)

- Next.js app with auth; Live Signals, Stock Analysis, Open/Closed Trades.
- News Feed, sector heatmap, calendar, macro panel, system health, settings.
- Morning brief and evening summary jobs.


### Phase 5 - Analytics and learning (3-4 days)

- Performance stats, AI vs rules comparison, monthly review report.
- Historical event library to validate linkage rules; optional reviewer model for top signals.


### Phase 6 - Hardening (ongoing)

- Tests for parsers, scoring, exits; retries and backoff; data quality checks.
- Source health monitoring; cost tuning; documentation.

Timelines are rough estimates for part-time AI-assisted building.


## 20. Reliability, quality and limitations

- **Retries with backoff** on every external call; circuit breaker when a source keeps failing.
- **Idempotent writes** (unique keys) so reruns never duplicate data.
- **Graceful degradation:** if the broker API is down, signals still generate and are marked 'pricing pending'.
- **Data quality checks:** stale quotes, zero bids, abnormal spreads are rejected.
- **Reproducibility:** every AI output stored with model, prompt version and input references.
- **Limitations:** RSS feeds can lag minutes behind breaking news; some sources change formats; paper fills differ from real fills; free tiers can change; past event behaviour does not guarantee future moves.


## 21. Vibe-coding guidelines

- Build one phase at a time; each phase must run end-to-end before the next.
- Keep this document and a short ARCHITECTURE.md in the repo so the AI coding tool always has context.
- Ask for tests with every module (parsers, scoring, exit rules).
- Keep business rules in YAML/DB config, not hard-coded.
- Use typed schemas (Pydantic) at every boundary: API responses, AI outputs, DB writes.
- Commit small, working steps; review diffs before accepting.
- Never paste real secrets into prompts or code; use .env only.
- **Claude Code:** commit this PDF's content as docs/DESIGN.md plus a CLAUDE.md (stack, commands, conventions, current phase) at repo root. Start each session with: 'Read CLAUDE.md and docs/DESIGN.md, then implement Phase N'. Work on a branch per phase and open a PR for review.
