# Upstox API notes for Phase 3 (research done before coding)

Status: **nothing here was verified against the live API.** The build sandbox could not reach
upstox.com, so this comes from web-search summaries of the official docs and forum posts. Every
field name below must be confirmed against a real response (log one raw response per endpoint in
the first run and adjust the parsers). Parsers must be pure functions with tests built from samples.

## Auth: use the Analytics Token (decision)
- Source: https://upstox.com/developer/api-documentation/analytics-token/
- Long-lived access token, **1 year validity**, **read-only, GET APIs only, no trading**.
- Created in the Upstox Developer Apps page, Analytics tab. No OAuth redirect. One token per account.
- Market Data and Realtime/Streaming APIs work from any server (no static IP). Portfolio/Funds
  endpoints need a registered static IP, and we do not use them.
- Standard access tokens expire daily at 3:30 AM and need a manual login, so they are not used.
- Fits the hard rule "paper trading only, never write order code". Env: `BROKER_ACCESS_TOKEN`
  holds the analytics token (key names in `.env.example` stay; `BROKER_API_KEY/SECRET` unused).
- Add an expiry reminder: alert the owner ~14 days before the token's 1-year expiry. Store the
  generation date in `config` (key `broker_token_issued`) or env.
- Header: `Authorization: Bearer <token>`, `Accept: application/json`.

## Endpoints (base `https://api.upstox.com`)
1. **Option chain** `GET /v2/option/chain?instrument_key=<underlying>&expiry_date=YYYY-MM-DD`
   - Docs page: https://upstox.com/developer/api-documentation/get-pc-option-chain
   - Per strike in `data[]`: `expiry`, `pcr`, `strike_price`, `underlying_key`, `underlying_spot_price`,
     `call_options` and `put_options`. Each side has `instrument_key` (e.g. `NSE_FO|12345`),
     `market_data` {`ltp`, `volume`, `oi`, `prev_oi`, `close_price`, `bid_price`, `bid_qty`,
     `ask_price`, `ask_qty`} and `option_greeks` {`delta`, `gamma`, `theta`, `vega`, `iv`, `pop`}.
   - Forum caveats: a different layout (`call`/`put`, nested bid/ask) has been reported; greeks can be
     all zero for deep ITM contracts (treat IV 0 as missing); empty `data` reported even with valid auth;
     error `UDAPI100060` = wrong path/params. No historical option chain is offered, so we must store our
     own IV history in `option_quotes`.
   - Whether it accepts a **stock** underlying (`NSE_EQ|<ISIN>`) is likely but unconfirmed: test first.
2. **Option contracts** `GET /v2/option/contract?instrument_key=<underlying>[&expiry_date=...]` (from
   memory, unconfirmed): lists contracts with expiry, strike, lot size, CE/PE. Use to get the real
   monthly expiry dates and replace the provisional "last Tuesday" rule in `signals/expiry.py`.
3. **Instruments master** `https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`
   (also `complete.json.gz`), see https://upstox.com/developer/api-documentation/instruments
   - Equity: `instrument_key` like `NSE_EQ|INE839G01010`, `trading_symbol`, `isin`, `lot_size`.
   - Derivatives (`NSE_FO`): `instrument_key` like `NSE_FO|36365`, `underlying_symbol`, `lot_size`,
     `strike_price` (0.0 for futures), `expiry` (integer, probably epoch **milliseconds**),
     `instrument_type`. Staff advise using `instrument_key` as the identifier, not `trading_symbol`
     (one user reported `trading_symbol` missing from the NSE file). Check fields after download.
   - Needed to map our NSE symbol (e.g. `TCS`) to `NSE_EQ|<ISIN>`. Add `isin`/`instrument_key` handling
     in `fno_meta` (new column or a `config` map; needs a migration if a column).
4. **Full market quote** `GET /v2/market-quote/quotes?instrument_key=a,b,...` (v3 exists:
   `/v3/market-quote/quotes`). Docs: https://upstox.com/developer/api-documentation/get-full-market-quote
   - `data` keyed `EXCHANGE:SYMBOL` (not by instrument_key): `last_price`, `net_change`, `volume`,
     `ohlc` {open, high, low, close}, `depth` {`buy`[], `sell`[]} each level {quantity, price, orders},
     `timestamp`, circuit limits. Depth is padded with zero levels; `total_buy_quantity` was once buggy.
   - Prior close for the **priced-in check** = `ohlc.close` (no top-level close_price). Spot move since
     news = `last_price` vs price at news time (store spot in `option_quotes.spot` and use prior close
     when news broke overnight).
   - Use for option premiums too (bid/ask from depth level 0) when the chain is too heavy.
5. Older OHLC quote endpoint is deprecated; do not use.

## Limits and risks to check
- Rate limits not confirmed (believed ~50 req/s, 500/min, 2000 per 30 min for standard APIs). Poll
  only stocks with live signals/open trades often; full universe at most twice a day (design 5.4).
- Option chain can return empty data outside market hours or on schema changes: reject stale quotes,
  zero bids and abnormal spreads (design section 20).
- Weekly/other expiries: only monthly stock options are traded; pass the monthly `expiry_date`.

## Phase 3 plan (agreed scope, from docs/DESIGN.md section 19 and the owner)
1. `collectors/upstox.py`: read-only client (GET only; a test already forbids order-placement names),
   instrument sync (symbol -> instrument_key/ISIN), option chain + quote parsers, raw-response logging.
2. Real expiry from option contracts (replace provisional expiry) and real lot sizes.
3. Option polling jobs: active signals/open trades every `OPTION_POLL_ACTIVE_MIN` (1-5 min, market
   hours 9:15-15:30 IST), full universe morning + mid-day; store in `option_quotes`; downsample later.
4. Strike/expiry selection (`signals/strike_select.py`): nearest liquid ATM, slightly ITM/OTM if more
   liquid; liquidity thresholds in YAML (min volume, OI, max spread % of premium).
5. Checks before entry: priced-in penalty (replaces 0 in scoring), IV vs own history, OI build-up,
   market mood; real liquidity score replaces the fixed 70.
6. `trading/paper_engine.py`, `exits.py`, `costs.py`: entry at ask, exit at bid, SL -50%, T1 +25%,
   T2 +50%, T3 +100%; book 1/3 each; SL to entry after T1, trail at T1 after T2; thesis-broken, no-move
   after 2 sessions, time stop 5 trading days before expiry; gap exits at opening bid; every change a
   `trade_events` row; signal -> CONVERTED_TO_TRADE.
7. Trade alerts through the existing `alerts` outbox; update signal alert with strike/premium lines.
8. Tests for parsers, strike selection, every exit rule and gap handling; setup doc `docs/setup/phase-3.md`
   (Upstox developer app + Analytics Token steps, env keys, SQL checks).

## Sources
- https://upstox.com/developer/api-documentation/analytics-token/
- https://upstox.com/developer/api-documentation/api-overview
- https://upstox.com/developer/api-documentation/get-pc-option-chain
- https://upstox.com/developer/api-documentation/instruments
- https://upstox.com/developer/api-documentation/get-full-market-quote
- https://upstox.com/developer/api-documentation/get-full-market-quote-v3
- https://community.upstox.com (forum threads on option chain responses, instrument files, quotes)
