"""Upstox market-data client (READ-ONLY). See docs/notes/upstox-api.md.

Uses the 1-year Analytics Token (GET APIs only, no trading). This module only issues GET
requests; there is deliberately no method that could place, modify or cancel an order.
Field names were taken from docs/forum samples and are UNVERIFIED against live responses:
the first raw response of each endpoint per day is saved for checking (see `save_sample`).
Parsers are pure functions so they can be fixed quickly from a saved sample.
"""

import gzip
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import IST, get_settings
from app.market.options import OptionChain, OptionQuoteRow

log = logging.getLogger(__name__)
BASE = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


class UpstoxError(RuntimeError):
    """Upstox call failed. Messages never contain the token."""


@dataclass(frozen=True)
class Quote:
    instrument_key: str
    last_price: float
    prev_close: float | None
    bid: float | None
    ask: float | None
    ts: datetime
    bid_qty: int | None = None  # best level
    ask_qty: int | None = None
    bid_qty_depth: int | None = None  # all depth levels shown (up to 5)
    ask_qty_depth: int | None = None


@dataclass(frozen=True)
class FnoInstrument:
    symbol: str
    lot_size: int
    expiries: tuple[date, ...]  # monthly stock-option expiries, ascending


# ---- pure parsers ---------------------------------------------------------------------
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # drop NaN


def _int(x: Any) -> int | None:
    v = _f(x)
    return int(v) if v is not None else None


def _pos(x: Any) -> float | None:
    v = _f(x)
    return v if v and v > 0 else None


def parse_option_chain(raw: dict[str, Any], symbol: str, expiry: date, ts: datetime) -> OptionChain:
    """GET /v2/option/chain -> OptionChain. IV of 0 is treated as missing (deep ITM samples)."""
    data = raw.get("data") or []
    rows: list[OptionQuoteRow] = []
    spot = 0.0
    for strike_row in data:
        strike = _f(strike_row.get("strike_price"))
        spot = _f(strike_row.get("underlying_spot_price")) or spot
        if strike is None:
            continue
        for side, opt in (("call_options", "CE"), ("put_options", "PE")):
            leg = strike_row.get(side) or {}
            md = leg.get("market_data") or {}
            greeks = leg.get("option_greeks") or {}
            iv = _pos(greeks.get("iv"))
            oi, vol = _f(md.get("oi")), _f(md.get("volume"))
            rows.append(
                OptionQuoteRow(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    option_type=opt,
                    bid=_pos(md.get("bid_price")),
                    ask=_pos(md.get("ask_price")),
                    ltp=_pos(md.get("ltp")),
                    iv=iv,
                    oi=int(oi) if oi is not None else None,
                    volume=int(vol) if vol is not None else None,
                    spot=None,
                    ts=ts,
                    bid_qty=_int(md.get("bid_qty")),
                    ask_qty=_int(md.get("ask_qty")),
                    instrument_key=leg.get("instrument_key"),
                )
            )
    rows = [OptionQuoteRow(**{**r.__dict__, "spot": spot}) for r in rows]
    return OptionChain(symbol, expiry, spot, ts, tuple(rows))


def parse_quotes(raw: dict[str, Any], ts: datetime) -> dict[str, Quote]:
    """GET /v2/market-quote/quotes -> {instrument_key: Quote}. Data is keyed 'NSE_EQ:SYM', so we
    re-key by each entry's instrument_token. prev_close = last_price - net_change."""
    out: dict[str, Quote] = {}
    for entry in (raw.get("data") or {}).values():
        key = entry.get("instrument_token")
        last = _pos(entry.get("last_price"))
        if not key or last is None:
            continue
        change = _f(entry.get("net_change"))
        prev = last - change if change is not None else _pos((entry.get("ohlc") or {}).get("close"))
        depth = entry.get("depth") or {}
        buy, sell = depth.get("buy") or [{}], depth.get("sell") or [{}]
        out[key] = Quote(
            key,
            last,
            prev,
            _pos(buy[0].get("price")),
            _pos(sell[0].get("price")),
            ts,
            bid_qty=_int(buy[0].get("quantity")),
            ask_qty=_int(sell[0].get("quantity")),
            bid_qty_depth=sum(_int(x.get("quantity")) or 0 for x in buy) or None,
            ask_qty_depth=sum(_int(x.get("quantity")) or 0 for x in sell) or None,
        )
    return out


def parse_instruments(
    rows: list[dict[str, Any]], symbols: set[str]
) -> tuple[dict[str, tuple[str, str | None]], dict[str, FnoInstrument]]:
    """Instrument master -> ({symbol: (equity instrument_key, isin)}, {symbol: FnoInstrument}).

    Expiry is epoch milliseconds (assumed; verify). Only the LAST expiry of each month is kept,
    since we trade monthly stock options only.
    """
    equities: dict[str, tuple[str, str | None]] = {}
    exp: dict[str, set[date]] = {}
    lots: dict[str, int] = {}
    for r in rows:
        seg, itype = r.get("segment"), r.get("instrument_type")
        if seg == "NSE_EQ" and itype == "EQ" and r.get("trading_symbol") in symbols:
            equities[str(r["trading_symbol"])] = (str(r["instrument_key"]), r.get("isin"))
        elif seg == "NSE_FO" and itype in ("CE", "PE"):
            sym = str(r.get("underlying_symbol") or "")
            ms = _f(r.get("expiry"))
            if sym in symbols and ms:
                exp.setdefault(sym, set()).add(
                    datetime.fromtimestamp(ms / 1000, UTC).astimezone(IST).date()
                )
                if r.get("lot_size"):
                    lots[sym] = int(r["lot_size"])
    fno: dict[str, FnoInstrument] = {}
    for sym, dates in exp.items():
        monthly: dict[tuple[int, int], date] = {}
        for d in dates:
            key = (d.year, d.month)
            monthly[key] = max(monthly.get(key, d), d)
        fno[sym] = FnoInstrument(sym, lots.get(sym, 0), tuple(sorted(monthly.values())))
    return equities, fno


# ---- client ---------------------------------------------------------------------------
class UpstoxClient:
    def __init__(
        self,
        token: str | None = None,
        http: httpx.Client | None = None,
        min_interval_s: float = 0.25,
    ) -> None:
        self._token = token if token is not None else get_settings().broker_access_token
        self._http = http or httpx.Client(timeout=20.0)
        self._min_interval = min_interval_s
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, path: str, params: dict[str, Any]) -> Any:
        """The only network method for the API: GET with the bearer token in a header."""
        if not self._token:
            raise UpstoxError("BROKER_ACCESS_TOKEN (Upstox analytics token) is not set")
        self._throttle()
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            resp = self._http.get(f"{BASE}{path}", params=params, headers=headers)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstoxError(f"GET {path} failed: {type(exc).__name__}") from None
        if resp.status_code != 200 or body.get("status") != "success":
            errors = body.get("errors") if isinstance(body, dict) else None
            raise UpstoxError(f"GET {path} failed: HTTP {resp.status_code} {str(errors)[:200]}")
        save_sample(path, body)
        return body

    def option_chain(self, instrument_key: str, expiry: date) -> dict[str, Any]:
        return dict(
            self.get(
                "/v2/option/chain",
                {"instrument_key": instrument_key, "expiry_date": expiry.isoformat()},
            )
        )

    def quotes(self, instrument_keys: list[str]) -> dict[str, Any]:
        return dict(
            self.get("/v2/market-quote/quotes", {"instrument_key": ",".join(instrument_keys)})
        )

    def instruments(self) -> list[dict[str, Any]]:
        """Public instrument master (no token needed)."""
        try:
            resp = self._http.get(INSTRUMENTS_URL, timeout=60.0)
            resp.raise_for_status()
            return list(json.loads(gzip.decompress(resp.content)))
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise UpstoxError(f"instrument master download failed: {type(exc).__name__}") from None


def save_sample(path: str, body: Any) -> None:
    """Keep the first raw response per endpoint per day, to verify field names after deploy."""
    try:
        folder = Path(get_settings().history_data_dir) / "raw_samples" / "upstox"
        name = f"{date.today().isoformat()}_{path.strip('/').replace('/', '_')}.json"
        target = folder / name
        if not target.exists():
            folder.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(body)[:2_000_000])
    except OSError:
        pass  # samples are a convenience; never fail a data call because of them
