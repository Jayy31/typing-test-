"""Materiality inputs, refreshed nightly: 20-day average traded value (history store prices) and
market cap (issued shares from NSE quote-equity, refreshed weekly, x latest close)."""

import logging
import time as _time
from datetime import datetime
from typing import Any

import duckdb
from sqlalchemy import Engine, select

from app.collectors.nse import nse_get
from app.config import IST
from app.db.models import Instrument, StockStats
from app.db.session import session_scope
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def adv20(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[float, float]]:
    """symbol -> (20-session average traded value in Rs, latest close). Volume is in shares."""
    rows = con.execute(
        """
        WITH r AS (
            SELECT symbol, date, close, volume,
                   row_number() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM hist_prices WHERE date >= current_date - INTERVAL 45 DAY)
        SELECT symbol, avg(close * volume), max(CASE WHEN rn = 1 THEN close END)
        FROM r WHERE rn <= 20 GROUP BY symbol"""
    ).fetchall()
    return {s: (float(v), float(c)) for s, v, c in rows if v is not None and c is not None}


def parse_issued_size(payload: Any) -> float | None:
    try:
        return float(payload["securityInfo"]["issuedSize"])
    except (KeyError, TypeError, ValueError):
        return None


def refresh(
    con: duckdb.DuckDBPyConnection, engine: Engine | None = None, fetch_issued: bool | None = None
) -> int:
    """Update stock_stats for F&O stocks. Issued shares are re-fetched weekly (or when missing)."""
    path = load_yaml("deep_data.yaml")["sources"]["quote_equity"]["path"]
    values = adv20(con)
    now = datetime.now(IST)
    n = 0
    with session_scope(engine) as s:
        symbols = [i.symbol for i in s.scalars(select(Instrument)) if i.is_fno]
        current = {st.symbol: st for st in s.scalars(select(StockStats))}
    for sym in symbols:
        st = current.get(sym)
        stale = (
            st is None or st.issued_shares is None or now.weekday() == 0
        )  # Mondays (the nightly job runs Mon-Fri)
        issued = st.issued_shares if st else None
        if fetch_issued if fetch_issued is not None else stale:
            try:
                issued = parse_issued_size(nse_get(path.format(symbol=sym), 15.0)) or issued
                _time.sleep(0.5)  # polite
            except Exception as exc:  # noqa: BLE001 - one stock must not stop the refresh
                log.info("issued size %s failed: %s", sym, exc)
        adv, close = values.get(sym, (None, None))
        with session_scope(engine) as s:
            row = s.get(StockStats, sym) or StockStats(symbol=sym)
            row.adv20_inr = adv
            row.close = close
            row.issued_shares = issued
            row.mcap_inr = issued * close if issued and close else None
            row.updated_at = now
            s.add(row)
        n += 1
    return n
