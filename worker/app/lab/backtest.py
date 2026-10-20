"""Spec backtests (Phase 14): any valid StrategySpec on the history store, with the same fills,
spread model, fillability, exit engine, contract adjustments and walk-forward folds as the
backtester. `news_core` delegates to the rules backtest unchanged (regression-tested equal).

Point in time: analogue statistics per fold come only from reactions whose window ended before
the fold; filters read the previous trading day's regime / positioning / IV / traded value; a
pre-event entry uses a scheduled date only if it was public (intimated) at least
`spec.min_intimation_days` trading days before the entry day.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean
from typing import Any

import duckdb

from app.backtest import fills
from app.backtest.data import Event, HistoryData, OptRow
from app.backtest.engine import Signal, Skip, Trade, Variant, _iv, simulate
from app.backtest.runner import RunResult, folds, run_rules
from app.config import get_settings
from app.history.analogues import AnalogueResult
from app.history.event_study import grouped_values, reaction_rows, summarize, vix_series
from app.lab.spec import StrategySpec, cfg
from app.pricing.bs import greeks
from app.trading import core_rules
from app.trading.exits import ExitRules
from app.yaml_config import load_yaml


@dataclass(frozen=True)
class Trigger:
    """One potential entry produced by a spec's trigger (before filters)."""

    key: str
    symbol: str
    sector: str | None
    direction: str
    day: date
    at_open: bool
    event_day: date | None
    analogue: AnalogueResult | None
    title: str | None


@dataclass(frozen=True)
class RunOptions:
    spread_mult: float = 1.0
    # placebo: shift each trigger's entry day by this many trading days (key -> offset)
    shift: Callable[[str], int] | None = None


def matches(spec: StrategySpec, etype: str | None) -> bool:
    from app import taxonomy

    want = set(spec.trigger.event_types)
    return etype in want or (taxonomy.parent(etype) in want if etype else False)


def _prev(data: HistoryData, d: date) -> date | None:
    i = data.idx(d)
    return data.calendar[i - 1] if i > 0 else None


def _shift_day(data: HistoryData, d: date, n: int) -> date | None:
    i = data.idx(d) + n
    return data.calendar[i] if 0 <= i < len(data.calendar) else None


class Context:
    """Point-in-time helpers over one HistoryData (filters read the previous trading day)."""

    def __init__(self, con: duckdb.DuckDBPyConnection, data: HistoryData) -> None:
        self.con, self.data = con, data
        self._atm: dict[str, list[tuple[date, float]]] | None = None

    def adv_cr(self, sym: str, d: date) -> float | None:
        n = int(cfg()["spec"]["adv_window_days"])
        i = self.data.idx(d)
        vals = []
        for day in self.data.calendar[max(0, i - n) : i]:
            b = self.data.prices.get(sym, {}).get(day)
            if b and b.volume:
                vals.append(b.close * b.volume / 1e7)
        return mean(vals) if vals else None

    def iv_percentile(self, sym: str, d: date) -> float | None:
        if self._atm is None:
            self._atm = {}
            try:
                for s_, day, v in self.con.execute(
                    "SELECT symbol, date, atm_iv FROM hist_atm_iv ORDER BY date"
                ).fetchall():
                    self._atm.setdefault(s_, []).append((day, float(v)))
            except duckdb.Error:
                pass
        rows = [v for day, v in self._atm.get(sym, []) if day < d]
        rows = rows[-int(cfg()["spec"]["iv_percentile_lookback_days"]) :]
        if len(rows) < 20:
            return None
        cur = rows[-1]
        return 100 * sum(1 for v in rows if v <= cur) / len(rows)

    def scheduled(
        self, start: date, end: date, types: list[str]
    ) -> list[tuple[str, str, date, date]]:
        try:
            return [
                (s_, t, ed, io)
                for s_, t, ed, io in self.con.execute(
                    "SELECT symbol, event_type, event_date, intimated_on FROM hist_scheduled "
                    "WHERE event_date >= ? AND event_date < ? ORDER BY event_date, symbol",
                    [start, end],
                ).fetchall()
                if t in types
            ]
        except duckdb.Error:
            return []


def filter_reason(
    spec: StrategySpec, ctx: Context, sym: str, sector: str | None, d: date
) -> str | None:
    f = spec.filters
    data = ctx.data
    prev = _prev(data, d)
    if f.symbols and sym not in f.symbols:
        return "symbol filter"
    if f.sectors and sector not in f.sectors:
        return "sector filter"
    if sector in f.exclude_sectors:
        return "excluded sector"
    if f.regimes:
        label = data.regime_label(prev) if prev else "unknown/unknown"
        parts = set(label.split("/")) | {label}
        if not parts & set(f.regimes):
            return f"regime {label}"
    if f.positioning:
        states = data.positioning(sym, prev) if prev else {}
        for feat, allowed in f.positioning.items():
            if states.get(feat, ("unknown", 0.0))[0] not in allowed:
                return f"positioning {feat}"
    if f.iv_percentile_min is not None or f.iv_percentile_max is not None:
        p = ctx.iv_percentile(sym, d)
        if p is None:
            return "no IV history"
        if f.iv_percentile_min is not None and p < f.iv_percentile_min:
            return "IV percentile below min"
        if f.iv_percentile_max is not None and p > f.iv_percentile_max:
            return "IV percentile above max"
    if f.min_adv_cr is not None:
        adv = ctx.adv_cr(sym, d)
        if adv is None or adv < f.min_adv_cr:
            return "traded value below min"
    if f.large_cap_only and prev and sym not in data.large_caps(prev):
        return "not large cap"
    return None


def allowed_expiry(
    data: HistoryData, chain: list[OptRow], day: date, event_day: date | None
) -> date | None:
    """Monthly rule: current monthly; next when it has fewer than `expiry.min_trading_days`
    trading days left or the event falls after it; never later."""
    monthly = [e for e in core_rules.monthly_expiries({r.expiry for r in chain}) if e >= day]
    if not monthly:
        return None
    min_td = int(load_yaml("signals.yaml")["expiry"]["min_trading_days"])
    cur = monthly[0]
    too_close = data.trading_days_between(day, cur) < min_td
    after = event_day is not None and event_day > cur
    if too_close or after:
        return monthly[1] if len(monthly) > 1 else None
    return cur


def pick_strike(
    spec: StrategySpec,
    rows: list[OptRow],
    spot: float,
    opt: str,
    day: date,
    data: HistoryData,
    sym: str,
) -> OptRow | None:
    rows = sorted(rows, key=lambda r: r.strike)
    if not rows:
        return None
    i = min(range(len(rows)), key=lambda j: abs(rows[j].strike - spot))
    mode = spec.option.strike
    if mode == "delta":
        target = float(spec.option.target_delta or 0.5)
        best, gap = None, 9.0
        for r in rows:
            p = fills.tradable(r)
            if p is None:
                continue
            iv = _iv(data, sym, day, r, p.close, spot)
            if not iv:
                continue
            t = max((r.expiry - day).days, 1) / 365
            dl = abs(greeks(spot, r.strike, t, get_settings().risk_free_rate, iv / 100, opt).delta)
            if abs(dl - target) < gap:
                best, gap = r, abs(dl - target)
        return best
    # ITM = below spot for calls, above spot for puts
    step = {"atm": 0, "itm1": 1, "itm2": 2, "otm1": -1}[mode]
    j = i - step if opt == "CE" else i + step
    return rows[j] if 0 <= j < len(rows) else None


def plan(
    spec: StrategySpec,
    ctx: Context,
    key: str,
    sym: str,
    sector: str | None,
    direction: str,
    entry_day: date,
    at_open: bool,
    event_day: date | None,
    rules: ExitRules,
    opts: RunOptions,
    analogue: AnalogueResult | None,
    title: str | None,
) -> Trade | Skip:
    data = ctx.data
    bar = data.prices.get(sym, {}).get(entry_day)
    if bar is None:
        return Skip(key, sym, entry_day, "data", "no stock price on the entry day")
    if not data.in_fno(sym, entry_day):
        return Skip(key, sym, entry_day, "universe", "not in F&O on the entry day")
    lot, lot_src = data.lot_on(sym, entry_day)
    if not lot:
        return Skip(key, sym, entry_day, "lot", "lot size unknown on the day")
    spot = bar.open if at_open else bar.close
    opt = StrategySpec.option_type(direction)
    chain = data.chain(sym, entry_day)
    exp = allowed_expiry(data, chain, entry_day, event_day)
    if exp is None:
        return Skip(key, sym, entry_day, "expiry", "no allowed monthly expiry")
    row = pick_strike(
        spec,
        [r for r in chain if r.expiry == exp and r.type == opt],
        spot,
        opt,
        entry_day,
        data,
        sym,
    )
    p = fills.tradable(row)
    if row is None or p is None:
        return Skip(key, sym, entry_day, "liquidity", "no tradable option price for the strike")
    ref = p.open if at_open else p.close
    tier = fills.tier_for(
        abs(row.strike / spot - 1) * 100,
        data.to_lots(row.oi, entry_day, lot, "oi"),
        data.to_lots(row.volume, entry_day, lot, "volume"),
        sym in data.large_caps(entry_day),
    )
    hs = fills.half_spread(ref, tier, opts.spread_mult)
    bad = core_rules.violation(
        sym, direction, opt, exp, entry_day, known_expiries={r.expiry for r in chain}
    )
    if bad:
        return Skip(key, sym, entry_day, core_rules.STAGE, bad)
    pred = abs(analogue.mean) if analogue is not None and analogue.usable else 0.0
    exit_by = None
    if spec.exits.exit_before_event and event_day is not None:
        exit_by = _shift_day(data, event_day, -1)
    sig = Signal(
        key,
        sym,
        sector,
        spec.trigger.event_types[0],
        direction,
        entry_day,
        "close",
        pred,
        3,
        1.0,
        None,
        f"lab:{spec.name}",
        None,
        None,
        data.prev_close(sym, entry_day),
        title,
        exit_by=exit_by,
        max_hold_days=spec.exits.max_hold_days,
        iv_spike_pts=spec.exits.iv_spike_exit_pts,
    )
    vol_lots = data.to_lots(row.volume, entry_day, lot, "volume")
    return Trade(
        sig,
        exp,
        row.strike,
        opt,
        rules.lots,
        lot,
        lot_src,
        entry_day,
        ref,
        ref + hs,
        spot,
        tier.name,
        p.stale,
        fills.classify(rules.lots, vol_lots, p.stale),
        None,
    )


def _rules(spec: StrategySpec, has_prediction: bool) -> ExitRules:
    over = spec.exits.overrides()
    if not has_prediction:  # no predicted move to scale targets / window stop: fixed rules
        over.update(targets_mode="fixed", window_stop=False)
    if spec.entry.timing == "before_event":  # the event has not happened yet: no "no move" exit
        over["no_move_sessions"] = 10_000
    return ExitRules.from_config(over)


def _analogue(
    stats: dict[Any, Any], etype: str | None, sector: str | None, sym: str, w: int
) -> AnalogueResult | None:
    for sec, s_ in ((sector, sym), (sector, None), (None, None)):
        v = stats.get((etype, sec, s_, "all", w))
        if v is not None:
            f = [float(x) for x in v[1:8]]
            return AnalogueResult(
                "x", "all", w, int(v[0]), f[0], f[1], f[2], f[3], f[4], f[5], f[6]
            )
    return None


def candidates(
    spec: StrategySpec,
    ctx: Context,
    start: date,
    end: date,
    stats: dict[Any, Any],
    res: RunResult,
    opts: RunOptions,
) -> list[Trigger]:
    data = ctx.data
    w = int(load_yaml("backtest.yaml")["rules_mode"]["analogue_window"])
    out: list[Trigger] = []
    if spec.entry.timing == "before_event":
        min_int = int(cfg()["spec"]["min_intimation_days"])
        # events up to ~6 weeks after the fold: their entry may fall inside it (kept by entry day)
        ahead = end + timedelta(days=45)
        for sym, etype, ed, intimated in ctx.scheduled(start, ahead, spec.trigger.event_types):
            key = f"sched:{sym}:{etype}:{ed}"
            i = data.idx(ed)
            if i >= len(data.calendar) or i - spec.entry.days_before < 0:  # type: ignore[operator]
                continue
            entry: date = data.calendar[i - spec.entry.days_before]  # type: ignore[operator]
            if opts.shift:
                entry = _shift_day(data, entry, opts.shift(key)) or entry
            if intimated is None or data.trading_days_between(intimated, entry) < min_int:
                res.skips.append(
                    Skip(key, sym, entry, "lookahead", "event date not public early enough")
                )
                continue
            sector = None
            an = _analogue(stats, etype, sector, sym, w)
            direction = _direction(spec, None, an)
            if direction is None:
                res.skips.append(Skip(key, sym, entry, "direction", "base rate too thin"))
                continue
            out.append(
                Trigger(key, sym, sector, direction, entry, False, ed, an, f"{etype} on {ed}")
            )
        return out
    evs: list[Event] = data.events(start, end)
    if spec.trigger.graph_hops:
        evs = evs + data.linked_events(evs, spec.trigger.graph_hops)
    for ev in evs:
        if not matches(spec, ev.event_type):
            continue
        an = _analogue(stats, ev.event_type, ev.sector, ev.symbol, w)
        if spec.trigger.min_analogue_n and (an is None or an.n < spec.trigger.min_analogue_n):
            res.skips.append(
                Skip(ev.key, ev.symbol, ev.day, "analogue", "too few earlier analogues")
            )
            continue
        direction = _direction(spec, ev.direction, an)
        if direction is None:
            res.skips.append(Skip(ev.key, ev.symbol, ev.day, "direction", "no direction"))
            continue
        at_open = spec.entry.timing == "next_open"
        day: date | None = _shift_day(data, ev.day, 1) if at_open else ev.day
        if day is not None and opts.shift:
            day = _shift_day(data, day, opts.shift(ev.key))
        if day is None:
            continue
        out.append(
            Trigger(ev.key, ev.symbol, ev.sector, direction, day, at_open, None, an, ev.title)
        )
    return out


def _direction(spec: StrategySpec, event_dir: str | None, an: AnalogueResult | None) -> str | None:
    mode = spec.trigger.direction
    if mode in ("bullish", "bearish"):
        return mode
    if mode == "from_event":
        return event_dir
    if an is None or an.n < spec.trigger.min_analogue_n or an.mean == 0:
        return None
    if event_dir is None:  # scheduled events: the base rate's own sign
        return "bullish" if an.mean > 0 else "bearish"
    # stats are direction-adjusted: a negative mean means the market did the opposite
    flip = {"bullish": "bearish", "bearish": "bullish"}
    return event_dir if an.mean > 0 else flip[event_dir]


def run_spec(
    con: duckdb.DuckDBPyConnection,
    spec: StrategySpec,
    start: date,
    end: date,
    data: HistoryData | None = None,
    opts: RunOptions | None = None,
) -> RunResult:
    data = data or HistoryData(con)
    opts = opts or RunOptions()
    variant = Variant(f"lab:{spec.name}", spread_mult=opts.spread_mult)
    if spec.source == "news_core":
        return run_rules(con, Variant("news_core", spread_mult=opts.spread_mult), start, end, data)
    ctx = Context(con, data)
    res = RunResult(variant, start, end, folds=folds(start, end))
    vix = vix_series(con)
    one = bool(load_yaml("backtest.yaml")["entry"]["one_trade_per_symbol"])
    busy: dict[str, date] = {}
    for f in res.folds:
        stats = summarize(grouped_values(reaction_rows(con, before=f.test_start), vix))
        for c in sorted(
            candidates(spec, ctx, f.test_start, f.test_end, stats, res, opts),
            key=lambda c: (c.day, c.key),
        ):
            key, sym, sector, direction, day = c.key, c.symbol, c.sector, c.direction, c.day
            if not (f.test_start <= day < f.test_end):
                continue
            why = filter_reason(spec, ctx, sym, sector, day)
            if why:
                res.skips.append(Skip(key, sym, day, "filter", why))
                continue
            if one and sym in busy and day <= busy[sym]:
                res.skips.append(Skip(key, sym, day, "overlap", "open trade on same stock"))
                continue
            rules = _rules(spec, c.analogue is not None and c.analogue.usable)
            planned = plan(
                spec,
                ctx,
                key,
                sym,
                sector,
                direction,
                day,
                c.at_open,
                c.event_day,
                rules,
                opts,
                c.analogue,
                c.title,
            )
            if isinstance(planned, Skip):
                res.skips.append(planned)
                continue
            trade = simulate(planned, data, variant, rules)
            res.trades.append(trade)
            busy[sym] = trade.exit_day or date.max
    if not res.folds:
        res.notes.append("period shorter than the training window: no test fold")
    return res
