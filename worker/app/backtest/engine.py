"""Backtest engine: signals -> entry at the reaction day's close -> day-by-day exits with the
same pure exit rules as live paper trading (app/trading/exits.py).

No look-ahead: a signal on day d uses only data up to d (analogue statistics from events whose
outcome windows ended before the walk-forward test fold, that day's chain and prices, the F&O
universe and lot size on d). Later option prices are read only by the trade simulator, one day
at a time, after entry.
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.ai.schemas import Scenario
from app.backtest import fills
from app.backtest.data import HistoryData, OptRow
from app.config import get_settings
from app.history.analogues import AnalogueResult
from app.history.corp_actions import contract_factor
from app.history.event_study import bootstrap_mean_ci, regime
from app.history.universe import value_report
from app.market.implied import n_day_move_pct
from app.positioning.evidence import assess as assess_positioning
from app.positioning.evidence import tilt as tilt_positioning
from app.pricing.bs import implied_vol
from app.regime import gates as regime_gates
from app.regime.analogues import blend as blend_regime
from app.regime.analogues import key as regime_key
from app.regime.classifier import cfg as regime_cfg
from app.signals.options_stage import selection_rejection
from app.signals.prediction import analogue_scenarios, from_scenarios
from app.sim.simulator import CandidateInput, SimResult, evaluate, seed_for, simulate_stock
from app.trading import core_rules
from app.trading.costs import trade_costs_inr
from app.trading.exits import Bar, ExitRules, PositionState, step
from app.trading.targets import targets_for
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def _cfg() -> dict[str, Any]:
    return dict(load_yaml("backtest.yaml"))


@dataclass(frozen=True)
class Variant:
    name: str
    mode: str = (
        "rules"  # rules (labels + analogues, all history) | replay (stored live AI signals, R1)
    )
    rule_set: str = "v21"  # fixed | v21
    booking: str = "per_lot"  # per_lot | single_exit
    spread_mult: float = 1.0
    direction_mode: str = "signal"  # signal | random (benchmark B6)
    threshold: str = "strict"  # rules mode direction rule (config rules_mode.thresholds)
    selection: str = "ev"  # ev (live gates) | atm (benchmark: ATM, nearest expiry, no gates)
    kg_depth: int = 0  # Phase 8: 0 = no graph; 2 = live default; 3 = comparison variant
    regime_aware: bool = False  # Phase 12: analogues conditioned on the market regime
    regime_gates: str | None = None  # Phase 12: regime.yaml backtest_variants key (gate overrides)
    positioning: bool = False  # Phase 12: positioning base rates tilt the prediction (as live)

    def rules(self) -> ExitRules:
        over = dict(_cfg()["rule_sets"][self.rule_set])
        over["booking_mode"] = self.booking
        return ExitRules.from_config(over)


@dataclass
class Signal:
    key: str
    symbol: str
    sector: str | None
    event_type: str | None
    direction: str
    day: date  # reaction day: entry at its close
    timing: str
    predicted_move_pct: float  # raw, in our direction
    window_days: int
    ai_weight: float  # share of signal paths (rest market); the gating move is predicted x weight
    scenarios: list[Scenario] | None = None
    source: str = "rules"  # rules / panel / single
    analogue: dict[str, Any] | None = None
    contract: tuple[date, float, str] | None = None  # replay: the live signal's contract
    pre_news_spot: float | None = None
    title: str | None = None


@dataclass
class Skip:
    key: str
    symbol: str
    day: date
    stage: str
    reason: str


@dataclass
class Trade:
    signal: Signal
    expiry: date
    strike: float
    option_type: str
    lots: int
    lot_size: int
    lot_source: str | None
    entry_day: date
    entry_mid: float
    entry_ask: float
    spot_entry: float
    tier: str
    entry_stale: bool
    entry_fill: str
    sim: SimResult | None
    fills: list[dict[str, Any]] = field(default_factory=list)
    exit_day: date | None = None
    exit_reason: str | None = None
    exit_fill: str = "fillable"
    spot_exit: float | None = None
    pnl_inr: float = 0.0
    pnl_pct: float = 0.0
    costs_inr: float = 0.0
    slippage_inr: float = 0.0
    t1_hit: bool = False
    marks: list[tuple[date, float]] = field(default_factory=list)  # cumulative P&L (Rs) per day
    stale_exit: bool = False

    @property
    def outlay(self) -> float:
        return self.entry_ask * self.lot_size * self.lots

    @property
    def settlement_used(self) -> bool:
        return self.entry_stale or self.stale_exit

    @property
    def fillability(self) -> str:
        order = ["fillable", "partial", "unfillable"]
        return max(self.entry_fill, self.exit_fill, key=order.index)

    @property
    def stock_pct(self) -> float | None:
        if not self.spot_exit:
            return None
        sign = 1 if self.option_type == "CE" else -1
        return sign * (self.spot_exit / self.spot_entry - 1) * 100

    def report(self) -> dict[str, Any]:
        r = value_report(self.pnl_pct, self.pnl_inr, self.lot_source)
        return {"primary": r.primary, "text": r.text(), "note": r.note}


def _prev_day(data: HistoryData, day: date) -> date:
    """The regime known before the reaction day's close: the previous trading day's."""
    i = data.idx(day)
    return data.calendar[i - 1] if i and i > 0 else day


# ---- signals ------------------------------------------------------------------------------
def lookup_analogue(
    stats: dict[tuple[Any, ...], tuple[Any, ...]],
    data: HistoryData,
    etype: str | None,
    sector: str | None,
    symbol: str,
    day: date,
    window: int,
    regime_label: str | None = None,
) -> tuple[AnalogueResult, tuple[Any, ...]] | None:
    """Most specific usable analogue group (stock -> sector -> event type; regime -> all) and its key.
    With `regime_label` (Phase 12, regime-aware variant) the all-regime stats are mixed toward that
    regime's stats at n/(n+k) instead of the VIX-regime lookup."""
    if regime_label is not None:
        return _regime_lookup(stats, etype, sector, symbol, window, regime_label)
    reg = regime(data.vix, data.calendar[data.idx(day) - 1]) if data.idx(day) > 0 else "unknown"
    best = None
    for level, sec, sym in (
        ("stock", sector, symbol),
        ("sector", sector, None),
        ("event_type", None, None),
    ):
        for r in dict.fromkeys((reg, "all")):
            v = stats.get((etype, sec, sym, r, window))
            if v is None:
                continue
            res = AnalogueResult(
                level,
                r,
                window,
                int(v[0]),
                float(v[1]),
                float(v[2]),
                float(v[3]),
                float(v[4]),
                float(v[5]),
                float(v[6]),
                float(v[7]),
            )
            key = (etype, sec, sym, r, window)
            if res.usable:
                return res, key
            best = best or (res, key)
    return best


def _res(level: str, reg: str, window: int, v: tuple[Any, ...]) -> AnalogueResult:
    f = [float(x) for x in v[1:8]]
    return AnalogueResult(level, reg, window, int(v[0]), f[0], f[1], f[2], f[3], f[4], f[5], f[6])


def _regime_lookup(
    stats: dict[tuple[Any, ...], tuple[Any, ...]],
    etype: str | None,
    sector: str | None,
    symbol: str,
    window: int,
    label: str,
) -> tuple[AnalogueResult, tuple[Any, ...]] | None:
    rk = regime_key(label)
    best = None
    for level, sec, sym in (
        ("stock", sector, symbol),
        ("sector", sector, None),
        ("event_type", None, None),
    ):
        v = stats.get((etype, sec, sym, "all", window))
        if v is None:
            continue
        rv = stats.get((etype, sec, sym, rk, window)) if rk else None
        res = blend_regime(
            _res(level, "all", window, v), _res(level, rk or "", window, rv) if rv else None, label
        )
        key = (etype, sec, sym, "all", window)  # direction test uses the all-regime events
        if res.usable:
            return res, key
        best = best or (res, key)
    return best


def direction_supported(
    an: AnalogueResult, values: list[tuple[float, float]], threshold: str
) -> tuple[bool, str]:
    """Owner rule: strict = hit rate >= 55% AND the lower bound of the 90% bootstrap CI of the mean
    abnormal move our way > 0; legacy = hit rate >= 50% and positive mean (comparison variant)."""
    t = _cfg()["rules_mode"]["thresholds"][threshold]
    if an.hit_rate < float(t["min_hit_rate"]):
        return False, f"analogue hit rate {an.hit_rate:.0%} below {float(t['min_hit_rate']):.0%}"
    if "min_mean_pct" in t and an.mean <= float(t["min_mean_pct"]):
        return False, f"analogue mean {an.mean:+.2f}% not above {t['min_mean_pct']}%"
    if "ci_level" in t:
        lo, hi = bootstrap_mean_ci(values, float(t["ci_level"]))
        if lo <= float(t["require_ci_lower_above"]):
            return (
                False,
                f"{float(t['ci_level']):.0%} CI of the analogue mean {lo:+.2f}..{hi:+.2f}% does not exclude {t['require_ci_lower_above']}",
            )
    return True, "supported"


def rules_signal(
    ev: Any,
    stats: dict[tuple[Any, ...], tuple[Any, ...]],
    data: HistoryData,
    variant: Variant,
    groups: dict[tuple[Any, ...], list[tuple[float, float]]] | None = None,
    pos_stats: dict[tuple[str, str, int], tuple[int, float, float, float]] | None = None,
) -> Signal | Skip:
    cfg = _cfg()["rules_mode"]
    w = int(cfg["analogue_window"])
    if not data.in_fno(ev.symbol, ev.day):
        return Skip(ev.key, ev.symbol, ev.day, "universe", "not in F&O on the reaction day")
    label = data.regime_label(_prev_day(data, ev.day)) if variant.regime_aware else None
    found = lookup_analogue(stats, data, ev.event_type, ev.sector, ev.symbol, ev.day, w, label)
    an, key = found if found else (None, None)
    if an is None or not an.usable:
        return Skip(
            ev.key,
            ev.symbol,
            ev.day,
            "analogue",
            f"too few earlier analogues (n={an.n if an else 0})",
        )
    if variant.direction_mode == "signal" and key is not None:
        ok, why = direction_supported(an, (groups or {}).get(key, []), variant.threshold)
        if not ok:
            return Skip(ev.key, ev.symbol, ev.day, "direction", why)
    direction = ev.direction
    if variant.direction_mode == "random":
        direction = random.Random(f"{ev.key}:dir").choice(("bullish", "bearish"))
    scen = analogue_scenarios(an, direction, direction == ev.direction, 1.0, w)
    if variant.positioning and pos_stats is not None:
        states = data.positioning(ev.symbol, _prev_day(data, ev.day))
        evid = assess_positioning(states, lambda f, s, win: pos_stats.get((f, s, win)))
        scen = tilt_positioning(scen, evid, w)
    pred = from_scenarios(scen, direction)
    assert pred is not None
    a = {
        "level": an.level,
        "regime": an.regime,
        "n": an.n,
        "n_eff": an.n_eff,
        "mean": an.mean,
        "hit_rate": an.hit_rate,
        "weight": an.weight,
    }
    return Signal(
        ev.key,
        ev.symbol,
        ev.sector,
        ev.event_type,
        direction,
        ev.day,
        ev.timing,
        pred.move_pct,
        w,
        an.weight,
        scen,
        "rules",
        a,
        None,
        data.prev_close(ev.symbol, ev.day),
        ev.title,
    )


# ---- entry --------------------------------------------------------------------------------
@dataclass
class Cand:
    row: OptRow
    price: fills.DayPrice
    tier: fills.Tier
    ask: float
    bid: float
    iv: float
    td: int
    result: SimResult | None = None


def _iv(
    data: HistoryData, sym: str, day: date, row: OptRow, price: float, spot: float
) -> float | None:
    v = data.iv(sym, day, row.expiry, row.strike, row.type)
    if v:
        return v
    t = (row.expiry - day).days / 365
    sigma = implied_vol(price, spot, row.strike, t, get_settings().risk_free_rate, row.type)
    return sigma * 100 if sigma else None


def plan_entry(
    sig: Signal, data: HistoryData, variant: Variant, rules: ExitRules, watch: bool = False
) -> Trade | Skip:
    """Entry for a signal. `watch` (Phase 12 watchlist book): accept a near-miss that failed
    exactly one soft gate (edge ratio, P(T1 before SL), min EV %) within config/watchlist.yaml margins."""
    st = get_settings()
    bar = data.prices.get(sig.symbol, {}).get(sig.day)
    if bar is None:
        return Skip(sig.key, sig.symbol, sig.day, "data", "no stock price on the reaction day")
    spot, sign = bar.close, (1 if sig.direction == "bullish" else -1)
    opt = "CE" if sign > 0 else "PE"
    lot, lot_src = data.lot_on(sig.symbol, sig.day)
    if not lot:
        return Skip(sig.key, sig.symbol, sig.day, "lot", "lot size unknown on the day")
    gating_move = sig.predicted_move_pct * sig.ai_weight
    if sig.contract is None and variant.selection == "ev" and sig.pre_news_spot and gating_move > 0:
        premove = sign * (spot / sig.pre_news_spot - 1) * 100
        limit = float(load_yaml("entry.yaml")["max_premove_fraction"])
        if premove / gating_move > limit:
            return Skip(
                sig.key,
                sig.symbol,
                sig.day,
                "entry",
                f"stock already moved {premove:+.2f}% = {premove / gating_move:.0%} of the predicted {gating_move:.2f}%",
            )
    min_td = int(_cfg()["entry"]["min_trading_days_to_expiry"])
    chain = [r for r in data.chain(sig.symbol, sig.day) if r.type == opt]
    if sig.contract:
        exp, k, typ = sig.contract
        chain = [
            r
            for r in data.chain(sig.symbol, sig.day)
            if r.expiry == exp and r.type == typ and abs(r.strike - k) < 0.01 * k
        ]
        expiries = [exp] if chain else []
    else:
        expiries = sorted(
            {r.expiry for r in chain if data.trading_days_between(sig.day, r.expiry) >= min_td}
        )
        expiries = expiries[: int(load_yaml("options.yaml")["candidates"]["expiries"])]
    if not expiries:
        return Skip(
            sig.key,
            sig.symbol,
            sig.day,
            "liquidity",
            "no option contract with enough days to expiry",
        )
    large = sig.symbol in data.large_caps(sig.day)
    cfg_c = load_yaml("options.yaml")["candidates"]
    cands: list[Cand] = []
    for exp in expiries:
        rows = sorted((r for r in chain if r.expiry == exp), key=lambda r: r.strike)
        if not rows:
            continue
        i = min(range(len(rows)), key=lambda j: abs(rows[j].strike - spot))
        if sig.contract:
            pick = rows
        elif variant.selection == "atm":
            pick = [rows[i]]
        elif opt == "CE":
            pick = rows[max(0, i - int(cfg_c["itm_strikes"])) : i + int(cfg_c["otm_strikes"]) + 1]
        else:
            pick = rows[max(0, i - int(cfg_c["otm_strikes"])) : i + int(cfg_c["itm_strikes"]) + 1]
        for r in pick:
            p = fills.tradable(r)
            if p is None:
                continue
            tier = fills.tier_for(
                abs(r.strike / spot - 1) * 100,
                data.to_lots(r.oi, sig.day, lot, "oi"),
                data.to_lots(r.volume, sig.day, lot, "volume"),
                large,
            )
            hs = fills.half_spread(p.close, tier, variant.spread_mult)
            iv = _iv(data, sig.symbol, sig.day, r, p.close, spot)
            if iv is None:
                continue
            cands.append(
                Cand(
                    r,
                    p,
                    tier,
                    p.close + hs,
                    fills.bid(p.close, hs),
                    iv,
                    data.trading_days_between(sig.day, exp),
                )
            )
        if variant.selection == "atm" and cands:
            break
    if not cands:
        return Skip(
            sig.key,
            sig.symbol,
            sig.day,
            "liquidity",
            "no tradable option price (zero volume and no settlement)",
        )
    chosen = cands[0]
    gates = regime_gates.base()
    margins = {k: float(v) for k, v in load_yaml("watchlist.yaml")["margins"].items()}
    if variant.regime_gates:  # Phase 12 comparison: gate values that depend on the regime
        vol = data.regime_label(_prev_day(data, sig.day)).split("/")[0]
        over = regime_cfg()["backtest_variants"][variant.regime_gates]
        gates = regime_gates.for_regime(vol, over)
    if sig.contract is None and variant.selection == "ev":
        atm_iv = min(cands, key=lambda c: abs(c.row.strike - spot)).iv
        implied = n_day_move_pct(atm_iv, sig.window_days * 7 / 5)
        edge = gating_move / implied if implied else 0.0
        soft_failed: list[str] = []
        if edge < gates["edge_ratio_min"] and watch and edge >= margins["edge_ratio_min"]:
            soft_failed.append("edge_ratio")
        elif edge < gates["edge_ratio_min"]:
            return Skip(
                sig.key,
                sig.symbol,
                sig.day,
                "edge",
                f"edge ratio {edge:.2f} below {gates['edge_ratio_min']:g} (predicted {gating_move:.2f}% vs implied {implied:.2f}%)",
            )
        horizon = max(
            1,
            min(
                int(load_yaml("options.yaml")["simulator"]["horizon_cap_days"]),
                max(c.td for c in cands) - rules.time_stop_trading_days,
            ),
        )
        paths = simulate_stock(
            spot,
            sig.scenarios or [],
            atm_iv / 100 / 252**0.5,
            horizon,
            int(_cfg()["rules_mode"]["paths"]),
            seed_for(f"bt:{sig.key}"),
            ai_weight=sig.ai_weight,
        )
        for c in cands:
            inp = CandidateInput(
                c.row.expiry,
                c.row.strike,
                opt,
                c.bid,
                c.ask,
                c.iv,
                (c.row.expiry - sig.day).days,
                c.td,
                lot,
            )
            c.result = evaluate(
                inp,
                paths,
                rules,
                sign,
                gating_move,
                sig.window_days,
                st.risk_free_rate,
                None,
                sig.pre_news_spot,
            )
        chosen = max(cands, key=lambda c: c.result.ev_pct if c.result else -1e9)
        assert chosen.result is not None
        rej = selection_rejection(chosen.result, gates)
        if watch:
            sim = chosen.result
            if sim.ev_pct <= gates["min_ev_pct"]:
                return Skip(sig.key, sig.symbol, sig.day, "ev", "EV not positive after costs")
            for name, value, need, floor in (
                (
                    "p_t1_before_sl",
                    sim.p_t1_before_sl,
                    gates["min_p_t1_before_sl"],
                    margins["min_p_t1_before_sl"],
                ),
                (
                    "ev_pct",
                    sim.ev_pct,
                    gates["min_ev_pct_of_premium"],
                    margins["min_ev_pct_of_premium"],
                ),
            ):
                if value < need:
                    if value < floor:
                        return Skip(
                            sig.key, sig.symbol, sig.day, "watchlist", f"{name} beyond the margin"
                        )
                    soft_failed.append(name)
            if len(soft_failed) != 1:
                return Skip(
                    sig.key,
                    sig.symbol,
                    sig.day,
                    "watchlist",
                    f"{len(soft_failed)} soft gates failed",
                )
        elif rej:
            return Skip(sig.key, sig.symbol, sig.day, rej[0], rej[1])
    bad = core_rules.violation(
        sig.symbol,
        sig.direction,
        chosen.row.type,
        chosen.row.expiry,
        sig.day,
        known_expiries={r.expiry for r in data.chain(sig.symbol, sig.day)},
    )
    if bad:
        return Skip(sig.key, sig.symbol, sig.day, core_rules.STAGE, bad)
    vol_lots = data.to_lots(chosen.row.volume, sig.day, lot, "volume")
    return Trade(
        sig,
        chosen.row.expiry,
        chosen.row.strike,
        chosen.row.type,
        rules.lots,
        lot,
        lot_src,
        sig.day,
        chosen.price.close,
        chosen.ask,
        spot,
        chosen.tier.name,
        chosen.price.stale,
        fills.classify(rules.lots, vol_lots, chosen.price.stale),
        chosen.result,
    )


# ---- exits --------------------------------------------------------------------------------
def simulate(trade: Trade, data: HistoryData, variant: Variant, rules: ExitRules) -> Trade:
    st = get_settings()
    sig = trade.signal
    sign = 1 if trade.option_type == "CE" else -1
    tier = fills.Tier(
        trade.tier,
        next(
            float(t["half_spread_pct"])
            for t in _cfg()["spread"]["tiers"]
            if t["name"] == trade.tier
        ),
    )
    gating_move = sig.predicted_move_pct * sig.ai_weight
    iv = (
        _iv(
            data,
            sig.symbol,
            trade.entry_day,
            OptRow(
                trade.entry_day,
                trade.expiry,
                trade.strike,
                trade.option_type,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
            trade.entry_mid,
            trade.spot_entry,
        )
        or 30.0
    )
    targets = targets_for(
        trade.entry_ask,
        rules,
        spot=trade.spot_entry,
        strike=trade.strike,
        option_type=trade.option_type,
        iv=iv / 100,
        days_to_expiry=float((trade.expiry - trade.entry_day).days),
        predicted_move_pct=gating_move,
        window_days=float(sig.window_days),
        r=st.risk_free_rate,
    )
    state = PositionState.open(
        trade.entry_ask,
        rules,
        targets,
        sign,
        trade.spot_entry,
        sig.pre_news_spot,
        gating_move,
        sig.window_days,
    )
    actions = data.actions.get(sig.symbol, [])
    units = trade.lot_size * trade.lots
    d, i, last_bid = trade.entry_day, 0, trade.entry_mid
    trade.marks.append(
        (d, -(trade.entry_ask - trade.entry_mid) * units)
    )  # marked at mid: the spread is lost
    while not state.closed:
        nd = data.next_day(d)
        if nd is None:
            break
        d, i = nd, i + 1
        f = contract_factor(actions, trade.entry_day, d)  # split/bonus: strike x f, price / f
        sbar = data.prices.get(sig.symbol, {}).get(d)
        if d >= trade.expiry:  # expiry day: cash settlement at intrinsic value
            spot = sbar.close if sbar else None
            if spot is None:
                continue
            intrinsic = max(0.0, sign * (spot / f - trade.strike))
            state = step(
                state,
                Bar(
                    intrinsic, intrinsic, intrinsic, intrinsic, 0, i, False, spot / f, None, None, i
                ),
                rules,
            )
            if not state.closed:
                state = _force_close(state, "EXPIRY", intrinsic)
            trade.spot_exit = spot / f
            _record(trade, state, d, False, None, units)
            break
        row = data.contract(sig.symbol, d, trade.expiry, trade.strike * f, trade.option_type)
        p = fills.tradable(row)
        if p is None or sbar is None:
            continue  # no tradable price today: no exit possible

        b = _bid_fn(f, tier, variant.spread_mult)
        bar = Bar(
            b(p.open),
            b(p.high),
            b(p.low),
            b(p.close),
            data.trading_days_between(d, trade.expiry),
            i,
            False,
            sbar.close / f,
            sbar.high / f,
            sbar.low / f,
            i,
        )
        before = len(state.fills)
        state = step(state, bar, rules)
        last_bid = bar.close
        trade.spot_exit = sbar.close / f
        if len(state.fills) > before:
            lots_vol = data.to_lots(row.volume if row else None, d, trade.lot_size, "volume")
            _record(trade, state, d, p.stale, lots_vol, units, before)
        realised = sum(x.fraction * (x.price - trade.entry_ask) for x in state.fills) * units
        trade.marks.append((d, realised + state.remaining * (last_bid - trade.entry_ask) * units))
    if not state.closed:  # data ended: mark at the last bid
        state = _force_close(state, "END", last_bid)
        _record(trade, state, d, False, None, units)
    orders = 1 + len(state.fills)
    trade.costs_inr = trade_costs_inr(trade.entry_ask, trade.lot_size, trade.lots, orders)
    gross = sum(x.fraction * (x.price - trade.entry_ask) for x in state.fills) * units
    trade.pnl_inr = round(gross - trade.costs_inr, 2)
    trade.pnl_pct = round(trade.pnl_inr / trade.outlay * 100, 3) if trade.outlay else 0.0
    trade.t1_hit = state.targets_hit >= 1
    trade.slippage_inr = round(
        sum(min(0.0, (x.price - x.trigger) * x.fraction) for x in state.fills if x.trigger) * units,
        2,
    )
    if trade.marks:
        trade.marks[-1] = (trade.marks[-1][0], trade.pnl_inr)
    return trade


def _bid_fn(f: float, tier: fills.Tier, mult: float) -> Any:
    """Option price (raw contract units) -> modelled bid in entry-contract units."""

    def b(x: float) -> float:
        return fills.bid(x / f, fills.half_spread(x / f, tier, mult))

    return b


def _force_close(state: PositionState, event: str, price: float) -> PositionState:
    from dataclasses import replace

    from app.trading.exits import Fill

    return replace(
        state, remaining=0.0, closed=True, fills=(*state.fills, Fill(event, state.remaining, price))
    )


def _record(
    trade: Trade,
    state: PositionState,
    d: date,
    stale: bool,
    vol_lots: float | None,
    units: int,
    before: int = -1,
) -> None:
    new = state.fills[before:] if before >= 0 else state.fills[-1:]
    for x in new:
        lots = round(x.fraction * trade.lots)
        trade.fills.append(
            {
                "day": d.isoformat(),
                "event": x.event,
                "price": round(x.price, 2),
                "lots": lots,
                "trigger": x.trigger,
                "gap": x.gap,
                "stale": stale,
            }
        )
        if x.event not in ("EXPIRY", "END"):
            fc = fills.classify(max(lots, 1), vol_lots, stale)
            order = ["fillable", "partial", "unfillable"]
            trade.exit_fill = max(trade.exit_fill, fc, key=order.index)
    trade.stale_exit = trade.stale_exit or stale
    if state.closed:
        trade.exit_day, trade.exit_reason = d, state.fills[-1].event
