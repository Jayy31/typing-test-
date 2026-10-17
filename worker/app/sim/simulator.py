"""Option scenario simulator (DESIGN_V2 section 4): best strike/expiry by expected value.

Stock paths come from the predicted move distribution (the scenario tree for now; analogues /
blended later): each path picks a scenario by probability, draws its final move around the
scenario mean (fat-ish tails via the mixture), reaches it linearly over the scenario timing and
adds daily noise from the option's IV. Options are repriced with Black-Scholes along each path
(with an IV drop after a known event inside the horizon) and run through the SAME exit rules
as live trading (vectorised copy, tested against the pure module). Entry at ask, exits at bid.
"""

import zlib
from dataclasses import dataclass
from datetime import date

import numpy as np
from numpy.typing import NDArray

from app.ai.schemas import Scenario
from app.pricing.bs import greeks, price
from app.sim.exits_vec import run_paths
from app.trading.exits import ExitRules, PositionState
from app.trading.targets import targets_for
from app.yaml_config import load_yaml

F = NDArray[np.float64]
TRADING_DAYS = 252


@dataclass(frozen=True)
class CandidateInput:
    expiry: date
    strike: float
    option_type: str  # CE / PE
    bid: float
    ask: float
    iv_pct: float  # annualised IV in %
    calendar_days_to_expiry: int
    trading_days_to_expiry: int
    lot_size: int


@dataclass(frozen=True)
class SimResult:
    ev_pct: float  # % of entry premium, after costs
    ev_inr: float  # per position (all lots)
    p_t1_before_sl: float
    p_t2: float
    p_t3: float
    max_loss_inr: float
    exp_hold_days: float
    theta_per_day_inr: float
    breakeven_move_pct: float | None
    iv_sensitivity_pct: float  # premium change % per 1 vol point
    targets: tuple[float, ...]
    exit_mix: dict[str, float]


@dataclass(frozen=True)
class StockPaths:
    spot: F  # (paths, days)
    spot0: float
    days: int


def simulate_stock(
    spot0: float,
    scenarios: list[Scenario],
    daily_vol: float,
    days: int,
    paths: int,
    seed: int,
    ai_weight: float = 1.0,
) -> StockPaths:
    """Paths from the scenario mixture. daily_vol is a decimal (e.g. IV / sqrt(252)).

    ai_weight < 1 shrinks toward the market: that share of paths follows the AI scenario tree;
    the rest follow the option-implied distribution (no drift, IV noise only)."""
    cfg = load_yaml("options.yaml")["simulator"]
    rng = np.random.default_rng(seed)
    probs = np.array([s.probability for s in scenarios])
    pick = rng.choice(len(scenarios), size=paths, p=probs / probs.sum())
    means = np.array([s.stock_move_pct for s in scenarios])[pick]
    timing = np.maximum(np.array([s.timing_days for s in scenarios])[pick], 1)
    sd = np.maximum(
        float(cfg["min_scenario_sd_pct"]), float(cfg["scenario_sd_fraction"]) * np.abs(means)
    )
    final = means + sd * rng.standard_normal(paths)
    market = rng.random(paths) >= ai_weight
    final[market] = 0.0
    t = np.arange(1, days + 1)[None, :]
    drift = final[:, None] * np.minimum(t / timing[:, None], 1.0) / 100  # fraction of spot
    noise = np.zeros((paths, days))
    if cfg["use_iv_noise"] and daily_vol > 0:
        noise = np.cumsum(rng.standard_normal((paths, days)) * daily_vol, axis=1)
        noise -= 0.5 * (daily_vol**2) * t  # keep the noise mean-neutral
    spot = spot0 * (1 + drift) * np.exp(noise)
    return StockPaths(spot, spot0, days)


def evaluate(
    cand: CandidateInput,
    stock: StockPaths,
    rules: ExitRules,
    direction: int,
    predicted_move_pct: float,
    window_days: int,
    r: float,
    event_day: int | None,
    pre_news_spot: float | None = None,
) -> SimResult:
    """EV and probabilities for one candidate on shared stock paths."""
    cfg = load_yaml("options.yaml")["simulator"]
    entry = cand.ask
    half_spread = max((cand.ask - cand.bid) / 2, 0.0)
    iv0 = cand.iv_pct / 100
    days = min(stock.days, max(cand.trading_days_to_expiry - rules.time_stop_trading_days, 1))
    spot = stock.spot[:, :days]
    cal_per_td = cand.calendar_days_to_expiry / max(cand.trading_days_to_expiry, 1)
    t_left = (
        np.maximum(cand.calendar_days_to_expiry - np.arange(1, days + 1) * cal_per_td, 0.0) / 365
    )
    iv = np.full(days, iv0)
    if event_day is not None and event_day < days:
        iv[event_day:] = max(iv0 - float(cfg["iv_crush_points"]) / 100, 0.05)
    mid = price(spot, cand.strike, t_left[None, :], r, iv[None, :], cand.option_type)
    bid = np.maximum(mid - half_spread, 0.0)

    targets = targets_for(
        entry,
        rules,
        spot=stock.spot0,
        strike=cand.strike,
        option_type=cand.option_type,
        iv=iv0,
        days_to_expiry=float(cand.calendar_days_to_expiry),
        predicted_move_pct=predicted_move_pct,
        window_days=float(window_days),
        r=r,
    )
    pos = PositionState.open(
        entry,
        rules,
        targets,
        direction,
        stock.spot0,
        pre_news_spot,
        predicted_move_pct,
        window_days,
    )
    dte = [cand.trading_days_to_expiry - (d + 1) for d in range(days)]
    res = run_paths(
        bid,
        spot,
        entry,
        targets,
        rules,
        direction,
        stock.spot0,
        pos.spot_stop,
        predicted_move_pct,
        window_days,
        dte,
        event_day,
    )

    lot_value = entry * cand.lot_size * rules.lots  # whole position (LOTS_PER_SIGNAL)
    cost_pct = float(cfg["costs_pct_of_premium"]) + (
        # entry order + one order per target that books lots
        (1 + sum(1 for f in rules.book_fractions if f > 0))
        * float(cfg["brokerage_inr_per_order"])
        / lot_value
        * 100
        if lot_value
        else 0.0
    )
    pnl = res.realised_pct - cost_pct
    ev_pct = float(pnl.mean())
    g = greeks(
        stock.spot0, cand.strike, cand.calendar_days_to_expiry / 365, r, iv0, cand.option_type
    )
    events, counts = np.unique(res.exit_event, return_counts=True)
    return SimResult(
        ev_pct=round(ev_pct, 3),
        ev_inr=round(ev_pct / 100 * lot_value, 2),
        p_t1_before_sl=round(float((res.first_event == "T1").mean()), 4),
        p_t2=round(float((res.targets_hit >= 2).mean()), 4),
        p_t3=round(float((res.targets_hit >= 3).mean()), 4),
        max_loss_inr=round(float(pnl.min()) / 100 * lot_value, 2),
        exp_hold_days=round(float(res.hold_days.mean()), 2),
        theta_per_day_inr=round(g.theta_per_day * cand.lot_size * rules.lots, 2),
        breakeven_move_pct=_breakeven(cand, stock.spot0, iv0, r, window_days),
        iv_sensitivity_pct=round(g.vega_per_point / entry * 100, 3) if entry else 0.0,
        targets=tuple(round(x, 2) for x in targets),
        exit_mix={str(e): round(int(c) / len(pnl), 4) for e, c in zip(events, counts, strict=True)},
    )


def _breakeven(
    cand: CandidateInput, spot: float, iv: float, r: float, hold_days: int
) -> float | None:
    """Stock move (%, our direction) needed after `hold_days` for the bid to recover the ask."""
    t = max(cand.calendar_days_to_expiry - hold_days * 1.4, 0.0) / 365
    sign = 1.0 if cand.option_type == "CE" else -1.0
    half = (cand.ask - cand.bid) / 2

    def value(move: float) -> float:
        return (
            float(price(spot * (1 + sign * move / 100), cand.strike, t, r, iv, cand.option_type))
            - half
        )

    lo, hi = -50.0, 50.0
    if value(hi) < cand.ask:
        return None
    for _ in range(60):
        midm = (lo + hi) / 2
        if value(midm) < cand.ask:
            lo = midm
        else:
            hi = midm
    return round(hi, 3)


def seed_for(key: str) -> int:
    """Deterministic per-signal seed so results are reproducible."""
    return int(load_yaml("options.yaml")["simulator"]["seed"]) + zlib.crc32(key.encode())


def blend_results(ai: SimResult, market: SimResult, w: float) -> SimResult:
    """Result for a path mix with share `w` of AI-scenario paths (the rest market paths).
    Averages over paths are linear in the mix, so blending the two pure results is the same as
    simulating the mix (without extra sampling noise). Max loss is the worse of the two."""
    w = min(max(w, 0.0), 1.0)

    def lin(a: float, b: float) -> float:
        return w * a + (1 - w) * b

    keys = set(ai.exit_mix) | set(market.exit_mix)
    return SimResult(
        ev_pct=lin(ai.ev_pct, market.ev_pct),
        ev_inr=lin(ai.ev_inr, market.ev_inr),
        p_t1_before_sl=lin(ai.p_t1_before_sl, market.p_t1_before_sl),
        p_t2=lin(ai.p_t2, market.p_t2),
        p_t3=lin(ai.p_t3, market.p_t3),
        max_loss_inr=min(ai.max_loss_inr, market.max_loss_inr),
        exp_hold_days=lin(ai.exp_hold_days, market.exp_hold_days),
        theta_per_day_inr=lin(ai.theta_per_day_inr, market.theta_per_day_inr),
        breakeven_move_pct=ai.breakeven_move_pct,
        iv_sensitivity_pct=lin(ai.iv_sensitivity_pct, market.iv_sensitivity_pct),
        targets=ai.targets,
        exit_mix={k: lin(ai.exit_mix.get(k, 0.0), market.exit_mix.get(k, 0.0)) for k in keys},
    )
