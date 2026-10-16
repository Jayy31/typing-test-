"""Event study (DESIGN_V2 section 1): how stocks actually moved after each labelled event.

Uses only labels (event type, direction from the text's sentiment) and actual prices: no AI
predictions (R1). For each event x stock:
  - reaction day from the announcement time (`reaction_day`), timing stored and weighted;
  - prices back-adjusted for splits / bonus / special dividends / known demerger factors; windows
    within +-N trading days of any corporate action are dropped (their returns are not clean);
  - only stocks in F&O on the reaction day (point-in-time universe, incl. later removed stocks);
  - raw return over windows 0/1/3/5/10 vs the close before the reaction day;
  - abnormal return vs Nifty 50 AND vs the stock's sector index (if any); the sector one is
    primary except for market-wide event types; beta from 250 prior days (min 60 obs, else 1);
  - confounded = another major event (different type) for the same stock or a sector-wide event
    inside the window: excluded or down-weighted (config);
  - pre-event drift (window -5), max favourable / adverse move over 10 days, ATM CE/PE returns.
Then analogue_stats: weighted statistics per event type x sector x stock (optional) x regime
(India VIX bucket) x window, counting duplicate coverage of one event once.

    python -m app.history.event_study            # recompute everything (idempotent)
"""

import bisect
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import duckdb
import numpy as np

from app.history import universe
from app.history.corp_actions import Action, adjust, contract_factor, load_actions
from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


@dataclass
class Series:
    dates: list[date]
    close: list[float]
    high: list[float]
    low: list[float]
    breaks: set[int] = field(default_factory=set)  # ex-dates with unknown factor: no return across

    def idx(self, d: date) -> int | None:
        i = bisect.bisect_left(self.dates, d)
        return i if i < len(self.dates) and self.dates[i] == d else None


def _cfg() -> dict[str, Any]:
    return dict(load_yaml("history.yaml")["event_study"])


def load_series(
    con: duckdb.DuckDBPyConnection, actions: dict[str, list[Action]] | None = None
) -> tuple[dict[str, Series], dict[str, Series], dict[date, float]]:
    """Adjusted stock prices, index series by lower-case name, India VIX by date."""
    actions = load_actions(con) if actions is None else actions
    raw: dict[str, tuple[list[date], list[float], list[float], list[float]]] = {}
    for sym, d, c, h, lo in con.execute(
        "SELECT symbol, date, close, coalesce(high, close), coalesce(low, close) FROM hist_prices ORDER BY symbol, date"
    ).fetchall():
        s = raw.setdefault(sym, ([], [], [], []))
        s[0].append(d)
        s[1].append(c)
        s[2].append(h)
        s[3].append(lo)
    prices: dict[str, Series] = {}
    for sym, (dates, c, h, lo) in raw.items():
        (ac, ah, al), breaks = adjust(dates, [c, h, lo], actions.get(sym, []))
        prices[sym] = Series(dates, ac, ah, al, breaks)
    indices: dict[str, Series] = {}
    for name, d, c in con.execute(
        "SELECT lower(index_name), date, close FROM hist_index ORDER BY 1, 2"
    ).fetchall():
        ix = indices.setdefault(name, Series([], [], [], []))
        ix.dates.append(d)
        ix.close.append(c)
    for ix in indices.values():
        ix.high, ix.low = ix.close, ix.close
    v = indices.get("india vix")
    vix = dict(zip(v.dates, v.close, strict=True)) if v else {}
    return prices, indices, vix


def reaction_day(calendar: list[date], d: date, t: time | None) -> tuple[int | None, str]:
    """Index into the trading calendar of the first day that can react, and the timing label:
    pre_open (same day, gap at the open) | intraday (same day, flagged) | post_close (next day) |
    non_trading_day (next trading day) | unknown (no time: same day, down-weighted)."""
    cfg = _cfg()
    i = bisect.bisect_left(calendar, d)
    if i >= len(calendar):
        return None, "unknown"
    if calendar[i] != d:
        return i, "non_trading_day"
    if t is None:
        return i, "unknown"
    if t < time.fromisoformat(cfg["market_open"]):
        return i, "pre_open"
    if t <= time.fromisoformat(cfg["market_close"]):
        return i, "intraday"
    return (i + 1, "post_close") if i + 1 < len(calendar) else (None, "post_close")


def beta(stock: Series, bench: Series, end_day: date) -> float:
    cfg = _cfg()
    bi = {d: k for k, d in enumerate(bench.dates)}
    xs, ys = [], []
    j = bisect.bisect_left(stock.dates, end_day)
    for k in range(max(1, j - int(cfg["beta_lookback_days"])), j):
        if k in stock.breaks:
            continue
        d0, d1 = stock.dates[k - 1], stock.dates[k]
        if d0 in bi and d1 in bi and bi[d1] - bi[d0] == 1:
            xs.append(bench.close[bi[d1]] / bench.close[bi[d0]] - 1)
            ys.append(stock.close[k] / stock.close[k - 1] - 1)
    if len(xs) < int(cfg["beta_min_obs"]):
        return 1.0
    x, y = np.array(xs), np.array(ys)
    var = float(np.var(x))
    return float(np.cov(x, y, bias=True)[0, 1] / var) if var > 0 else 1.0


def regime(vix: dict[date, float], d: date) -> str:
    cfg = _cfg()["regimes"]
    v = vix.get(d)
    if v is None:
        return "unknown"
    return "low_vol" if v < cfg["vix_low"] else "high_vol" if v > cfg["vix_high"] else "normal"


def bench_return(bench: Series, d_ref: date, d_end: date) -> float | None:
    a, b = bench.idx(d_ref), bench.idx(d_end)
    return None if a is None or b is None else bench.close[b] / bench.close[a] - 1


def atm_option_returns(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    ref_day: date,
    ref_close: float,
    later: dict[int, date],
    actions: list[Action] | None = None,
) -> dict[int, tuple[float | None, float | None]]:
    """ATM CE/PE close-to-close returns from the day before day 0, nearest expiry after the window.
    `ref_close` is the RAW close of ref_day. If a split/bonus falls in between, the contract is
    followed through NSE's adjustment (strike x factor; per-unit price / factor)."""
    rows = con.execute(
        "SELECT expiry, strike, type, close FROM hist_options WHERE symbol = ? AND date = ? AND close > 0 ORDER BY expiry",
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
        f = contract_factor(actions or [], ref_day, d)
        px = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT type, close FROM hist_options WHERE symbol = ? AND date = ? AND expiry = ? AND abs(strike - ?) < 0.01 * ?",
                [symbol, d, exp, k * f, max(k * f, 1.0)],
            ).fetchall()
        }
        ce = (px["CE"] / f) / base["CE"] - 1 if "CE" in base and px.get("CE") else None
        pe = (px["PE"] / f) / base["PE"] - 1 if "PE" in base and px.get("PE") else None
        out[w] = (ce, pe)
    return out


@dataclass(frozen=True)
class Ev:
    event_id: str
    day: int  # index in the trading calendar
    timing: str
    symbol: str | None
    sector: str | None
    event_type: str | None


def _confounded(
    ev: Ev,
    lo: int,
    hi: int,
    by_sym: dict[str, list[Ev]],
    by_sector: dict[str, list[Ev]],
    major: set[str],
) -> bool:
    others = by_sym.get(ev.symbol or "", []) + (
        by_sector.get(ev.sector or "", []) if ev.sector else []
    )
    return any(
        o.event_id != ev.event_id
        and o.event_type in major
        and o.event_type != ev.event_type
        and lo <= o.day <= hi
        for o in others
    )


def _pct(x: float | None) -> float | None:
    return None if x is None else x * 100


@dataclass(frozen=True)
class Bench:
    market: Series
    beta_market: float
    sector: Series | None
    beta_sector: float | None
    sector_name: str | None
    use_market: bool  # market-wide event type, or no sector index
    market_name: str

    def abnormal(
        self, raw: float, d_from: date, d_to: date
    ) -> tuple[float, float, float, float | None, str] | None:
        """-> (raw %, primary abnormal %, vs market %, vs sector % or None, benchmark used)."""
        m = bench_return(self.market, d_from, d_to)
        if m is None:
            return None
        abn_m = raw - self.beta_market * m
        s = bench_return(self.sector, d_from, d_to) if self.sector else None
        abn_s = raw - (self.beta_sector or 1.0) * s if s is not None else None
        if self.use_market or abn_s is None:
            return raw * 100, abn_m * 100, abn_m * 100, _pct(abn_s), self.market_name
        return raw * 100, abn_s * 100, abn_m * 100, abn_s * 100, str(self.sector_name)


def _ca_near(ca_idx: list[int], lo: int, hi: int, buf: int) -> bool:
    i = bisect.bisect_left(ca_idx, lo - buf)
    return i < len(ca_idx) and ca_idx[i] <= hi + buf


def compute_reactions(con: duckdb.DuckDBPyConnection) -> int:
    cfg = _cfg()
    windows = [int(w) for w in cfg["windows"]]
    pre = int(cfg["pre_drift_days"])
    buf = int(cfg["corporate_action_buffer_days"])
    conf = cfg["confounding"]
    major = set(conf["major_event_types"])
    tw = {k: float(v) for k, v in cfg["timing_weights"].items()}
    actions = load_actions(con)
    prices, indices, _ = load_series(con, actions)
    market = indices.get(str(cfg["market_index"]).lower())
    if market is None or not market.dates:
        log.warning("event study: no %s history loaded", cfg["market_index"])
        return 0
    cal = market.dates
    sector_index = {k: str(v).lower() for k, v in (cfg.get("sector_index") or {}).items()}
    market_types = set(cfg.get("market_benchmark_event_types") or [])
    members = universe.members(con) if cfg.get("point_in_time_universe", True) else set()

    events: list[Ev] = []
    for eid, d, t, sym, sector, etype in con.execute(
        "SELECT event_id, date, time, symbol, sector, event_type FROM hist_events ORDER BY date"
    ).fetchall():
        i0, timing = reaction_day(cal, d, t)
        if i0 is not None:
            events.append(Ev(eid, i0, timing, sym, sector, etype))
    by_sym: dict[str, list[Ev]] = defaultdict(list)
    by_sector: dict[str, list[Ev]] = defaultdict(list)
    for e in events:
        if e.symbol:
            by_sym[e.symbol].append(e)
        elif e.sector:
            by_sector[e.sector].append(e)

    con.execute("DELETE FROM event_reactions")
    rows: list[tuple[Any, ...]] = []
    skipped: dict[str, int] = defaultdict(int)
    for ev in events:
        sym = ev.symbol
        if sym is None:
            continue
        st = prices.get(sym)
        d0 = cal[ev.day]
        if members and (d0, sym) not in members:
            skipped["not_in_fno"] += 1
            continue
        j0 = st.idx(d0) if st else None
        if st is None or j0 is None or j0 < pre + 1:
            skipped["no_prices"] += 1
            continue
        ca_idx = sorted(
            i
            for a in actions.get(sym, [])
            if (i := bisect.bisect_left(st.dates, a.ex_date)) < len(st.dates)
        )
        ref_day = st.dates[j0 - 1]
        ref = st.close[j0 - 1]
        sec_name = sector_index.get(ev.sector or "")
        sec = indices.get(sec_name) if sec_name else None
        b_m = beta(st, market, st.dates[j0 - pre])
        b_s = beta(st, sec, st.dates[j0 - pre]) if sec else None
        use_market = ev.event_type in market_types or sec is None
        base_w = tw.get(ev.timing, 1.0)
        later = {w: st.dates[j0 + w] for w in windows if j0 + w < len(st.dates)}
        clean = {w: dw for w, dw in later.items() if not _ca_near(ca_idx, j0 - 1, j0 + w, buf)}
        skipped["corporate_action_windows"] += len(later) - len(clean)
        raw_px = con.execute(
            "SELECT close FROM hist_prices WHERE symbol = ? AND date = ?", [sym, ref_day]
        ).fetchone()
        opts = (
            atm_option_returns(
                con, sym, ref_day, raw_px[0] if raw_px else ref, clean, actions.get(sym)
            )
            if clean
            else {}
        )
        horizon = min(j0 + max(windows), len(st.dates) - 1)
        excursions = not _ca_near(ca_idx, j0 - 1, horizon, buf)
        mfe = max(st.high[j0 : horizon + 1]) / ref - 1 if excursions else None
        mae = min(st.low[j0 : horizon + 1]) / ref - 1 if excursions else None

        bench = Bench(market, b_m, sec, b_s, sec_name, use_market, str(cfg["market_index"]))
        spans = [  # (window, from day, to day, from price, to price, calendar span, option returns)
            (w, ref_day, dw, ref, st.close[j0 + w], (ev.day, ev.day + w), opts.get(w, (None, None)))
            for w, dw in clean.items()
        ]
        if not _ca_near(ca_idx, j0 - pre - 1, j0 - 1, buf):  # pre-event drift, window -pre
            spans.append(
                (
                    -pre,
                    st.dates[j0 - pre - 1],
                    ref_day,
                    st.close[j0 - pre - 1],
                    ref,
                    (ev.day - pre, ev.day - 1),
                    (None, None),
                )
            )
        for w, d_from, d_to, p_from, p_to, (lo, hi), (ce, pe) in spans:
            ab = bench.abnormal(p_to / p_from - 1, d_from, d_to)
            if ab is None:
                continue
            raw, primary, abn_m, abn_s, bname = ab
            confounded = _confounded(ev, lo, hi, by_sym, by_sector, major)
            weight = base_w
            if confounded:
                weight = 0.0 if conf["mode"] == "exclude" else weight * float(conf["weight"])
            ex = w >= 0
            rows.append(
                (
                    ev.event_id,
                    sym,
                    w,
                    d0,
                    ev.timing,
                    raw,
                    primary,
                    abn_m,
                    abn_s,
                    bname,
                    _pct(mfe) if ex else None,
                    _pct(mae) if ex else None,
                    _pct(ce),
                    _pct(pe),
                    confounded,
                    weight,
                )
            )
    con.executemany(
        "INSERT OR REPLACE INTO event_reactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    if skipped:
        log.info("event study skipped: %s", dict(skipped))
    return len(rows)


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w) - 0.5 * w
    return float(np.interp(q * w.sum(), cum, v))


def compute_stats(con: duckdb.DuckDBPyConnection) -> int:
    """Direction-adjusted, weighted abnormal-return statistics for analogue lookup. Several
    articles about the same event (same stock, type, direction, reaction day) count once."""
    vix = dict(
        con.execute(
            "SELECT date, close FROM hist_index WHERE lower(index_name) = 'india vix'"
        ).fetchall()
    )
    data = con.execute("""
        SELECT e.event_type, e.sector, r.symbol, r."window", e.direction, r.reaction_day,
               max(r.weight) AS w, arg_max(r.abnormal_ret, r.weight) AS abn
        FROM event_reactions r JOIN hist_events e USING (event_id)
        WHERE e.direction IN ('bullish', 'bearish') AND r."window" >= 0 AND r.weight > 0
        GROUP BY ALL""").fetchall()
    groups: dict[tuple[Any, ...], list[tuple[float, float]]] = defaultdict(list)
    for etype, sector, sym, w, direction, d, wt, abn in data:
        signed = abn if direction == "bullish" else -abn
        for reg in dict.fromkeys((regime(vix, d), "all")):
            for key in (
                (etype, sector, sym, reg, w),
                (etype, sector, None, reg, w),
                (etype, None, None, reg, w),
            ):
                groups[key].append((signed, wt))
    con.execute("DELETE FROM analogue_stats")
    now = datetime.now().replace(microsecond=0)
    out = []
    for (etype, sector, sym, reg, w), vals in groups.items():
        a = np.array([v for v, _ in vals])
        wt = np.array([x for _, x in vals])
        mean = float(np.average(a, weights=wt))
        out.append(
            (
                etype,
                sector,
                sym,
                reg,
                w,
                len(a),
                float(wt.sum()),
                mean,
                weighted_quantile(a, wt, 0.5),
                float(np.average(a > 0, weights=wt)),
                float(np.sqrt(np.average((a - mean) ** 2, weights=wt))),
                weighted_quantile(a, wt, 0.25),
                weighted_quantile(a, wt, 0.75),
                now,
            )
        )
    con.executemany(
        "INSERT INTO analogue_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", out
    )
    return len(out)


def run(con: duckdb.DuckDBPyConnection | None = None) -> tuple[int, int]:
    def body(c: duckdb.DuckDBPyConnection) -> tuple[int, int]:
        universe.fill_lots(c)
        return compute_reactions(c), compute_stats(c)

    if con is not None:
        return body(con)
    with connect(write=True) as c:
        return body(c)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    reactions, stats = run()
    print(f"event_reactions: {reactions} rows, analogue_stats: {stats} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
