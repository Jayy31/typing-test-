# Kotak Neo Trade API: what we use (market data only)

Read from Kotak's official REST docs on 2026-11-16:
- [authentication](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/authentication.md)
- [static IP whitelisting](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/static-ip-whitelisting.md)
- [troubleshooting](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/troubleshooting.md)
- [market-data APIs](https://github.com/Kotak-Neo/Kotak-Neo/tree/main/docs/market-data-apis)

The official Python SDK changelog was also checked: [kotak-neo-python](https://github.com/Kotak-Neo/kotak-neo-python), v3.0.13 (2026-10-28). We do **not** use the SDK. It contains order functions; we call the read-only REST endpoints directly (`app/market/kotak.py`).

## Credentials (Neo app → More → Trade API)
- **Access token:** create an application under "Your Applications". It goes as plain text in the `Authorization` header, with no `Bearer`. Resetting it ends all sessions.
- **TOTP:** register under "TOTP Registration" (mobile OTP plus client code, then scan the QR code). The VM needs the base32 **secret** behind the QR code, not just the authenticator app. There is a 5-minute cooldown between attempts.
- **Login details:** mobile number (+91…), UCC (client code), and the 6-digit MPIN.

## Login (daily, only to learn `baseUrl`)
1. `POST https://mis.kotaksecurities.com/login/1.0/tradeApiLogin`
   - Headers: `Authorization: <access token>`, `neo-fin-key: neotradeapi`.
   - Body: `{mobileNumber, ucc, totp}`.
   - Returns a view `token` and `sid`.
2. `POST https://mis.kotaksecurities.com/login/1.0/tradeApiValidate`
   - Headers: the same, plus `sid` and `Auth` (from step 1).
   - Body: `{mpin}`.
   - Returns `baseUrl`, `feedUrl`, and a trade-session `token`/`sid`.

Kotak says `baseUrl` is "stable for the day and even after that rarely changes". We store `baseUrl` and `feedUrl` in config `kotak.session`. **The trade-session token and sid are discarded at once**, so order, portfolio and report APIs (which need `Auth` + `sid`) are unreachable from our code.

## Market data (only `Authorization: <access token>`; "a completed 2FA session is not required")
| Use | Endpoint (`{baseUrl}` + …) | Notes |
|---|---|---|
| Scrip master file list | `GET /script-details/1.0/masterscrip/file-paths` | Returns CSV URLs on `lapi.kotaksecurities.com` (`nse_fo.csv`, `nse_cm-v1.csv`, …), updated daily |
| Quotes and depth | `GET /script-details/1.0/quotes/neosymbol/<seg>\|<pSymbol>,…/all` | Response is a JSON list: `exchange`, `exchange_token`, `ltp`, `ohlc.close` (previous close), `depth.buy/sell` top 5 `{price, quantity, orders}`. All values are strings |
| Expiries | `GET /market-data/1.0/watchlist/expiries?underlying=&exchange=nse_fo` | ISO dates; not used (the scrip master gives them) |
| Option chain | `GET /market-data/1.0/watchlist/option-chain?exchange=nse_fo&underlying=<pSymbolName>&expiry=YYYY-MM-DD&instrument_type=option&count=20` | Per strike: `instrument.{neoSymbol, strikePrice, optionType}`, `quote.{ltp, volume}`, `openInterest.{current, previous, change}`; `common_data.mktLot`. **No bid/ask, no IV** |
| Futures chain | the same with `instrument_type=fut` | `fut[].inst.expiryDt` (`31-JUL-2026`), `quote.{ltp, pc}`, `oi.{cur, chg}` |

**Scrip master CSV columns we read:**
- `pSymbol` is the token.
- `pSymbolName` is the underlying.
- `pTrdSymbol` is e.g. `TCS-EQ`.
- `pOptionType` is `CE`, `PE`, or `XX` for futures.
- `lLotSize` is the lot size.
- `lExpiryDate`: for `nse_fo`, add 315511200, then read it as a Unix time in IST.

The strike column header really is `dStrikePrice;`, with the semicolon; we don't need it.

## What we build on top
- **Bid/ask:** one batched quotes call for the strikes within `strikes_each_side` of ATM, covering bid/ask and best-level quantities for the fill check. Other rows fall back to `with_depth` at entry and exit.
- **IV:** solved in-house (`app/pricing/bs.implied_vol`), from the mid when bid/ask exist, else from the LTP.
- **Spot:** a quotes call on `nse_cm|<pSymbol>`.
- **Futures:** the futures chain, which feeds live intraday positioning states.

## Limits
- **Rate:** 10 requests per second across all APIs. We use at most 5 (`min_interval_s` 0.2).
- **Historical candles:** active contracts only, with at most 180 days of daily data. The 5-year history therefore stays on the NSE files.

## Safety (paper trading only)
- **Allowlist:** `_check_url` lets through only the two login POSTs, the four data GETs and the scrip CSV downloads; anything else raises before sending.
- **Test:** `tests/test_kotak.py::test_no_order_code_anywhere` scans the worker for order endpoints, order functions and the SDK.
- **Exchange-side block:** since 1 April 2026 (SEBI retail algo rules), Kotak accepts order APIs only from a **whitelisted static IP**, with a session created from that IP. **Never whitelist an IP** for this account: orders are then rejected by Kotak itself. Data APIs work from any IP.
- **No funds:** the owner keeps no funds in the account.

## Unverified until the first live run (docs/UNVERIFIED.md 7, 8, 51-54)
The first response of each endpoint per day is saved to `/data/history/raw_samples/kotak/`, and the 24-hour go-live report checks the expected fields. Fix the parsers in `app/market/kotak.py` if a field differs.
