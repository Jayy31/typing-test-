"""Event study (DESIGN_V2 section 1): how stocks actually moved after each labelled event.

Uses only labels (event type, direction from the text's sentiment) and actual prices: no AI
predictions (R1). For each event x stock:
  day 0 = first trading day on/after the event (after 15:30 -> next trading day);
  raw return over windows 0/1/3/5/10 vs the close before day 0;
  abnormal return = raw - beta x market (Nifty 50), beta from 250 prior days (min 60 obs, else 1);
  pre-event drift (window -5), max favourable / adverse move over 10 days, ATM CE/PE returns.
Then analogue_stats: per event type x sector x stock (optional) x regime (India VIX bucket) x window.

    python -m app.history.event_study            # recompute everything (idempotent)
"""

import bisect
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

import duckdb
import numpy as np

from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


@dataclass
class Series:
    dates: list[date]
    close: list[float]
    high: list[float]
    low: list[float]

    def idx(self, d: date) -> int | None:
        i = bisect.bisect_left(self.dates, d)
        return i if i < len(self.dates) and self.dates[i] == d else None


def load_series(
    con: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, Series], Series, dict[date, float]]:
    cfg = load_yaml("history.yaml")["event_study"]
    prices: dict[str, Series] = {}
    for sym, d, c, h, lo in con.execute(
        "SELECT symbol, date, close, coalesce(high, close), coalesce(low, close) FROM hist_prices ORDER BY symbol, date"
    ).fetchall():
        s = prices.setdefault(sym, Series([], [], [], []))
        s.dates.append(d)
        s.close.append(c)
        s.high.append(h)
        s.low.append(lo)
    mrows = con.execute(
        "SELECT date, close FROM hist_index WHERE lower(index_name) = lower(?) ORDER BY date",
        [cfg["market_index"]],
    ).fetchall()
    market = Series(
        [r[0] for r in mrows], [r[1] for r in mrows], [r[1] for r in mrows], [r[1] for r in mrows]
    )
    vix = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT date, close FROM hist_index WHERE lower(index_name) = 'india vix'"
        ).fetchall()
    }
    return prices, market, vix


def day_zero(market: Series, d: date, t: time | None) -> int | None:
    cut = time.fromisoformat(load_yaml("history.yaml")["event_study"]["after_close_cutoff"])
    i = bisect.bisect_left(market.dates, d)
    if i < len(market.dates) and market.dates[i] == d and t is not None and t > cut:
        i += 1
    return i if i < len(market.dates) else None


def beta(stock: Series, market: Series, end_day: date) -> float:
    cfg = load_yaml("history.yaml")["event_study"]
    mi = {d: k for k, d in enumerate(market.dates)}
    xs, ys = [], []
    j = bisect.bisect_left(stock.dates, end_day)
    for k in range(max(1, j - int(cfg["beta_lookback_days"])), j):
        d0, d1 = stock.dates[k - 1], stock.dates[k]
        if d0 in mi and d1 in mi and mi[d1] - mi[d0] == 1:
            xs.append(market.close[mi[d1]] / market.close[mi[d0]] - 1)
            ys.append(stock.close[k] / stock.close[k - 1] - 1)
    if len(xs) < int(cfg["beta_min_obs"]):
        return 1.0
    x, y = np.array(xs), np.array(ys)
    var = float(np.var(x))
    return float(np.cov(x, y, bias=True)[0, 1] / var) if var > 0 else 1.0


def regime(vix: dict[date, float], d: date) -> str:
    cfg = load_yaml("history.yaml")["event_study"]["regimes"]
    v = vix.get(d)
    if v is None:
        return "unknown"
    return "low_vol" if v < cfg["vix_low"] else "high_vol" if v > cfg["vix_high"] else "normal"


def atm_option_returns(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    ref_day: date,
    ref_close: float,
    later: dict[int, date],
) -> dict[int, tuple[float | None, float | None]]:
    """ATM CE/PE close-to-close returns from the day before day 0, nearest expiry after the window."""
    rows = con.execute(
        """
        SELECT expiry, strike, type, close FROM hist_options
        WHERE symbol = ? AND date = ? AND close > 0 ORDER BY expiry""",
        [symbol, ref_day],
    ).fetchall()
    if not rows:
        return {}
    last_day = max(later.values())
    expiries = sorted({r[0] for r in rows if r[0] > last_day}) or sorted({r[0] for r in rows})
    exp = expiries[0]
    strikes = sorted({r[1] for r in rows if r[0] == exp})
    k = min(strikes, key=lambda x: abs(x - ref_close))
    base = {r[2]: r[3] for r in rows if r[0] == exp and r[1] == k}
    out: dict[int, tuple[float | None, float | None]] = {}
    for w, d in later.items():
        px = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT type, close FROM hist_options WHERE symbol = ? AND date = ? AND expiry = ? AND strike = ?",
                [symbol, d, exp, k],
            ).fetchall()
        }
        ce = px["CE"] / base["CE"] - 1 if "CE" in base and px.get("CE") else None
        pe = px["PE"] / base["PE"] - 1 if "PE" in base and px.get("PE") else None
        out[w] = (ce, pe)
    return out


def compute_reactions(con: duckdb.DuckDBPyConnection) -> int:
    cfg = load_yaml("history.yaml")["event_study"]
    windows = [int(w) for w in cfg["windows"]]
    prices, market, _ = load_series(con)
    events = con.execute("""
        SELECT event_id, date, time, symbol FROM hist_events WHERE symbol IS NOT NULL ORDER BY date""").fetchall()
    con.execute("DELETE FROM event_reactions")
    rows: list[tuple[Any, ...]] = []
    for eid, d, t, sym in events:
        st = prices.get(sym)
        i0 = day_zero(market, d, t)
        if st is None or i0 is None or i0 == 0:
            continue
        d0 = market.dates[i0]
        j0 = bisect.bisect_left(st.dates, d0)
        if j0 >= len(st.dates) or st.dates[j0] != d0 or j0 < 6:
            continue
        ref = st.close[j0 - 1]
        b = beta(st, market, st.dates[j0 - 5])
        m_ref = market.close[i0 - 1]
        later = {w: st.dates[j0 + w] for w in windows if j0 + w < len(st.dates)}
        opts = atm_option_returns(con, sym, st.dates[j0 - 1], ref, later) if later else {}
        horizon = min(j0 + 10, len(st.dates) - 1)
        mfe = max(st.high[j0 : horizon + 1]) / ref - 1
        mae = min(st.low[j0 : horizon + 1]) / ref - 1
        for w, dw in later.items():
            mi = market.idx(dw)
            if mi is None:
                continue
            raw = st.close[j0 + w] / ref - 1
            abn = raw - b * (market.close[mi] / m_ref - 1)
            ce, pe = opts.get(w, (None, None))
            rows.append(
                (
                    eid,
                    sym,
                    w,
                    raw * 100,
                    abn * 100,
                    mfe * 100,
                    mae * 100,
                    None if ce is None else ce * 100,
                    None if pe is None else pe * 100,
                )
            )
        # pre-event drift: window -5 (close 6 days before -> close before day 0)
        pre_raw = ref / st.close[j0 - 6] - 1
        pm = market.idx(st.dates[j0 - 6])
        pre_abn = pre_raw - b * (m_ref / market.close[pm] - 1) if pm is not None else pre_raw
        rows.append(
            (
                eid,
                sym,
                -int(cfg["pre_drift_days"]),
                pre_raw * 100,
                pre_abn * 100,
                None,
                None,
                None,
                None,
            )
        )
    con.executemany(
        "INSERT OR REPLACE INTO event_reactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    return len(rows)


def compute_stats(con: duckdb.DuckDBPyConnection) -> int:
    """Direction-adjusted abnormal-return statistics for analogue lookup."""
    _, _, vix = load_series(con)
    data = con.execute("""
        SELECT e.event_type, e.sector, r.symbol, r."window", e.direction, e.date, r.abnormal_ret
        FROM event_reactions r JOIN hist_events e USING (event_id)
        WHERE e.direction IN ('bullish', 'bearish') AND r."window" >= 0""").fetchall()
    groups: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for etype, sector, sym, w, direction, d, abn in data:
        signed = abn if direction == "bullish" else -abn
        for reg in (regime(vix, d), "all"):
            groups[(etype, sector, sym, reg, w)].append(signed)
            groups[(etype, sector, None, reg, w)].append(signed)
            groups[(etype, None, None, reg, w)].append(signed)
    con.execute("DELETE FROM analogue_stats")
    now = datetime.now().replace(microsecond=0)
    out = []
    for (etype, sector, sym, reg, w), vals in groups.items():
        a = np.array(vals)
        out.append(
            (
                etype,
                sector,
                sym,
                reg,
                w,
                len(a),
                float(a.mean()),
                float(np.median(a)),
                float((a > 0).mean()),
                float(a.std()),
                float(np.percentile(a, 25)),
                float(np.percentile(a, 75)),
                now,
            )
        )
    con.executemany(
        "INSERT INTO analogue_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", out
    )
    return len(out)


def run(con: duckdb.DuckDBPyConnection | None = None) -> tuple[int, int]:
    if con is not None:
        return compute_reactions(con), compute_stats(con)
    with connect(write=True) as c:
        return compute_reactions(c), compute_stats(c)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    reactions, stats = run()
    print(f"event_reactions: {reactions} rows, analogue_stats: {stats} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
