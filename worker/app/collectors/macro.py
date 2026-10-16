"""Macro collector: crude, USD/INR, DXY, US 10Y, India VIX and Indian ADRs (Yahoo Finance)."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from app.config import IST
from app.db.models import MacroSnapshot
from app.db.session import session_scope

log = logging.getLogger(__name__)

TICKERS = {
    "brent": "BZ=F",
    "usdinr": "INR=X",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",  # quoted as yield in percent
    "india_vix": "^INDIAVIX",
}
# GIFT Nifty has no reliable free Yahoo ticker; it stays null until a source is added.
ADRS = {"INFY": "INFY", "WIT": "WIPRO", "HDB": "HDFCBANK", "IBN": "ICICIBANK", "RDY": "DRREDDY"}


def fetch_last_prices(tickers: list[str]) -> dict[str, tuple[float, float] | None]:
    """Return {ticker: (last, previous_close)} using yfinance; None when unavailable."""
    import yfinance as yf

    out: dict[str, tuple[float, float] | None] = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).fast_info
            last, prev = float(info["last_price"]), float(info["previous_close"])
            out[t] = (last, prev)
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not sink the snapshot
            log.warning("macro ticker %s failed: %s", t, exc)
            out[t] = None
    return out


def build_snapshot(prices: dict[str, tuple[float, float] | None], ts: datetime) -> MacroSnapshot:
    def last(name: str) -> float | None:
        p = prices.get(TICKERS[name])
        return p[0] if p else None

    adr: dict[str, Any] = {}
    for adr_ticker, nse_symbol in ADRS.items():
        p = prices.get(adr_ticker)
        if p and p[1]:
            adr[nse_symbol] = {
                "adr": adr_ticker,
                "last": p[0],
                "change_pct": round((p[0] / p[1] - 1) * 100, 2),
            }
    return MacroSnapshot(
        ts=ts,
        brent=last("brent"),
        usdinr=last("usdinr"),
        dxy=last("dxy"),
        us10y=last("us10y"),
        india_vix=last("india_vix"),
        adr_json=adr or None,
    )


def collect_macro(engine: Engine | None = None) -> int:
    prices = fetch_last_prices([*TICKERS.values(), *ADRS])
    if all(v is None for v in prices.values()):
        raise RuntimeError("no macro data available")
    snap = build_snapshot(prices, datetime.now(IST).replace(second=0, microsecond=0))
    with session_scope(engine) as s:
        s.merge(snap)
    return 1
