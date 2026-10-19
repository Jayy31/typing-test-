"""Options stage of the signal pipeline (Phase 3a).

For an analysed stock: live quote and option chains -> pre-move (priced-in) check -> implied
move and edge ratio -> liquidity filter -> Monte Carlo EV per strike/expiry -> best candidate.
Any gate that fails returns a rejection (stage, reason) instead of a choice.
Network reads happen here, outside any write transaction.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Engine, select

from app import flags
from app.ai.schemas import Scenario
from app.calibration.service import Policy, weight_for
from app.config import get_settings
from app.db.models import EventCalendar, OptionQuote
from app.db.session import session_scope
from app.market.implied import edge_ratio, event_iv_premium, n_day_move_pct
from app.market.live import UpstoxOptionData, atm_iv, iv_percentile
from app.market.options import OptionChain, OptionQuoteRow
from app.signals.expiry import trading_days_between
from app.signals.filters import load_holidays
from app.sim.simulator import (
    CandidateInput,
    SimResult,
    blend_results,
    evaluate,
    seed_for,
    simulate_stock,
)
from app.timeutil import as_ist
from app.trading import core_rules
from app.trading.exits import ExitRules
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


@dataclass
class EvaluatedCandidate:
    row: OptionQuoteRow
    input: CandidateInput | None
    result: SimResult | None  # what gates/ranking use (AI paths at `ai_weight`, rest market)
    rejected_reason: str | None
    raw: SimResult | None = None  # AI scenario paths only (calibrator input)
    market: SimResult | None = None  # market paths only
    ai_weight: float | None = None
    p_t1_calibrated: float | None = None


@dataclass
class OptionPlan:
    status: str  # priced / pending (no data source) / rejected
    spot: float | None = None
    prev_close: float | None = None
    quote_ts: datetime | None = None
    pre_news_spot: float | None = None
    premove_pct: float | None = None
    premove_fraction: float | None = None
    atm_iv_pct: float | None = None
    iv_percentile: float | None = None
    event_iv_premium: float | None = None
    implied_move_pct: float | None = None
    edge_ratio: float | None = None  # shrunk prediction / implied (used for the gate)
    edge_ratio_raw: float | None = None  # raw AI prediction / implied (reported)
    ai_weight: float = 1.0  # prediction-level weight (move, edge ratio, pre-move check)
    calibration_method: str = "shrink"
    calibrator_id: int | None = None
    predicted_move_raw: float | None = None
    predicted_move: float | None = None  # shrunk toward the market until calibration
    raw_result: SimResult | None = None  # chosen candidate simulated with AI-only paths
    event_in_window: bool = False
    candidates: list[EvaluatedCandidate] = field(default_factory=list)
    chosen: EvaluatedCandidate | None = None
    liquidity_score: float | None = None
    rejection: tuple[str, str] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def best_reference(self) -> EvaluatedCandidate | None:
        """Chosen candidate, or the best-looking one for a rejected plan (counterfactuals)."""
        if self.chosen:
            return self.chosen
        priced = [c for c in self.candidates if c.result]
        if priced:
            return max(priced, key=lambda c: c.result.ev_pct if c.result else -1e9)
        return self.candidates[0] if self.candidates else None


def liquidity_check(row: OptionQuoteRow, now: datetime) -> str | None:
    c = load_yaml("options.yaml")["liquidity"]
    if not (row.bid and row.ask):
        return "no two-sided quote"
    if row.ask < float(c["min_premium"]):
        return f"premium {row.ask:.2f} below {c['min_premium']}"
    if (row.spread_pct or 999) > float(c["max_spread_pct"]):
        return f"spread {row.spread_pct:.1f}% above {c['max_spread_pct']}%"
    if (row.oi or 0) < int(c["min_oi"]):
        return f"OI {row.oi or 0} below {c['min_oi']}"
    if (row.volume or 0) < int(c["min_volume"]):
        return f"volume {row.volume or 0} below {c['min_volume']}"
    if (now - as_ist(row.ts)).total_seconds() / 60 > float(c["max_quote_age_min"]):
        return "stale quote"
    return None


def liquidity_score(row: OptionQuoteRow) -> float:
    c = load_yaml("options.yaml")["liquidity"]
    w = c["score_weights"]
    spread = max(0.0, 1 - (row.spread_pct or 99) / float(c["max_spread_pct"]))
    oi = min(1.0, (row.oi or 0) / (4 * float(c["min_oi"])))
    vol = min(1.0, (row.volume or 0) / (4 * float(c["min_volume"])))
    return round(float(100 * (w["spread"] * spread + w["oi"] * oi + w["volume"] * vol)), 1)


def candidate_rows(chain: OptionChain, option_type: str) -> list[OptionQuoteRow]:
    """~2 ITM to ~3 OTM strikes around ATM (config) for this option type."""
    c = load_yaml("options.yaml")["candidates"]
    strikes = chain.strikes()
    i = strikes.index(chain.atm_strike())
    itm, otm = int(c["itm_strikes"]), int(c["otm_strikes"])
    if option_type == "CE":  # ITM calls are below spot
        chosen = strikes[max(0, i - itm) : i + otm + 1]
    else:
        chosen = strikes[max(0, i - otm) : i + itm + 1]
    rows = [chain.get(k, option_type) for k in chosen]
    return [r for r in rows if r is not None]


def pre_news_reference(
    symbol: str,
    news_time: datetime,
    prev_close: float | None,
    today: date,
    engine: Engine | None = None,
) -> float | None:
    """Stock price before the news: our own stored spot just before the news if it broke during
    market hours today, otherwise the previous close."""
    nt = as_ist(news_time)
    if nt.date() == today and nt.time() >= time(9, 15):
        with session_scope(engine) as s:
            row = s.scalars(
                select(OptionQuote)
                .where(
                    OptionQuote.symbol == symbol,
                    OptionQuote.ts <= nt,
                    OptionQuote.spot.is_not(None),
                )
                .order_by(OptionQuote.ts.desc())
                .limit(1)
            ).first()
            if row and row.spot and (nt - as_ist(row.ts)).total_seconds() < 3600:
                return float(row.spot)
    return prev_close


def plan_options(
    source: UpstoxOptionData,
    symbol: str,
    direction: str,
    scenarios: list[Scenario],
    predicted_move_pct: float,
    window_days: int,
    news_time: datetime,
    lot_size: int,
    now: datetime,
    engine: Engine | None = None,
    policy: Policy | None = None,
) -> OptionPlan:
    settings = get_settings()
    # Gates and ranking use the prediction tempered toward the market: by the configured shrink
    # until a calibrator exists, then by the calibrated weight (Phase 5). Raw AI values are kept.
    shrink = min(max(float(settings.scenario_shrink_weight), 0.0), 1.0)
    ai_w = policy.move_weight if policy else shrink
    predicted_raw = predicted_move_pct
    predicted_move_pct = predicted_raw * ai_w
    opt_type = "CE" if direction == "bullish" else "PE"
    sign = 1 if direction == "bullish" else -1
    today = now.date()
    entry_cfg = load_yaml("entry.yaml")
    plan = OptionPlan(
        status="priced",
        ai_weight=ai_w,
        calibration_method=policy.method if policy else "shrink",
        calibrator_id=policy.calibrator_id if policy else None,
        predicted_move_raw=predicted_raw,
        predicted_move=round(predicted_move_pct, 3),
    )

    # Core rule: current monthly expiry, or next month's when the current one is too close
    # (signals.yaml expiry.min_trading_days); never later than next month.
    listed = source.expiries(symbol)
    with session_scope(engine) as s:
        hol = load_holidays(s)
    min_td = int(load_yaml("signals.yaml")["expiry"]["min_trading_days"])
    allowed = core_rules.monthly_expiries(listed)[
        : int(load_yaml("core_rules.yaml")["max_months_ahead"]) + 1
    ]
    expiries = [e for e in allowed if e >= today and trading_days_between(today, e, hol) >= min_td]
    expiries = expiries[: int(load_yaml("options.yaml")["candidates"]["expiries"])]
    quote = source.quote(symbol)
    if not expiries or quote is None:
        return OptionPlan(status="pending", notes=["no live option data (pricing pending)"])
    plan.spot, plan.prev_close, plan.quote_ts = quote.last_price, quote.prev_close, quote.ts
    gates = live_gates(engine, today)

    # (A4 b) priced-in / pre-move check
    plan.pre_news_spot = pre_news_reference(symbol, news_time, quote.prev_close, today, engine)
    if plan.pre_news_spot:
        plan.premove_pct = round(sign * (quote.last_price / plan.pre_news_spot - 1) * 100, 3)
        if predicted_move_pct > 0:
            plan.premove_fraction = round(plan.premove_pct / predicted_move_pct, 3)
            if plan.premove_fraction > float(entry_cfg["max_premove_fraction"]):
                plan.status, plan.rejection = (
                    "rejected",
                    (
                        "entry",
                        f"stock already moved {plan.premove_pct:+.2f}% = "
                        f"{plan.premove_fraction:.0%} of the predicted {predicted_move_pct:.2f}% "
                        f"(limit {entry_cfg['max_premove_fraction']:.0%})",
                    ),
                )

    chains = [c for c in (source.chain(symbol, e) for e in expiries) if c]
    if not chains:
        plan.status = "pending"
        plan.notes.append("option chain unavailable (pricing pending)")
        return plan
    plan.atm_iv_pct = atm_iv(chains[0])
    with session_scope(engine) as s:
        holidays = load_holidays(s)
        if plan.atm_iv_pct:
            plan.iv_percentile = iv_percentile(s, symbol, plan.atm_iv_pct, now)
        events = list(
            s.scalars(
                select(EventCalendar.event_date).where(
                    EventCalendar.symbol == symbol,
                    EventCalendar.event_date >= today,
                    EventCalendar.event_date <= chains[-1].expiry,
                )
            )
        )
    if len(chains) > 1 and events and min(events) <= chains[0].expiry:
        plan.event_iv_premium = event_iv_premium(plan.atm_iv_pct, atm_iv(chains[1]))

    # implied move over the predicted window and the edge ratio (DESIGN_V2 section 2)
    if plan.atm_iv_pct:
        plan.implied_move_pct = round(n_day_move_pct(plan.atm_iv_pct, window_days * 7 / 5), 3)
        er = edge_ratio(predicted_move_pct, plan.implied_move_pct)
        er_raw = edge_ratio(predicted_raw, plan.implied_move_pct)
        plan.edge_ratio_raw = round(er_raw, 3) if er_raw is not None else None
        plan.edge_ratio = round(er, 3) if er is not None else None
        with session_scope(engine) as s:
            gate = flags.active(s, "feat.implied_move_gate")
        if gate and plan.rejection is None and (plan.edge_ratio or 0) < gates["edge_ratio_min"]:
            plan.status, plan.rejection = (
                "rejected",
                (
                    "edge",
                    f"edge ratio {plan.edge_ratio} below {gates['edge_ratio_min']:g}: predicted "
                    f"{predicted_move_pct:.2f}% vs implied {plan.implied_move_pct:.2f}% "
                    f"over {window_days}d",
                ),
            )

    # candidates and simulation
    rules = ExitRules.from_config()
    sim_cfg = load_yaml("options.yaml")["simulator"]
    max_td = max(trading_days_between(today, c.expiry, holidays) for c in chains)
    horizon = max(1, min(int(sim_cfg["horizon_cap_days"]), max_td - rules.time_stop_trading_days))
    daily_vol = (plan.atm_iv_pct or 30.0) / 100 / (252**0.5)
    seed = seed_for(f"{symbol}:{now:%Y%m%d}")
    # AI-only and market-only path sets: any mix is a linear blend of the two results
    stock_ai = simulate_stock(
        quote.last_price, scenarios, daily_vol, horizon, settings.mc_paths, seed, ai_weight=1.0
    )
    stock_mkt = simulate_stock(
        quote.last_price, scenarios, daily_vol, horizon, settings.mc_paths, seed, ai_weight=0.0
    )
    event_day = None
    if events:
        event_day = max(trading_days_between(today, min(events), holidays) - 1, 0)
        plan.event_in_window = event_day < horizon
    for chain in chains:
        td = trading_days_between(today, chain.expiry, holidays)
        for row in candidate_rows(chain, opt_type):
            reason = liquidity_check(row, now)
            if reason is None and td <= rules.time_stop_trading_days:
                reason = "too close to expiry for the time stop"
            inp = res = r_ai = r_mkt = None
            w_c = p_cal = None
            if reason is None and row.bid and row.ask:
                inp = CandidateInput(
                    chain.expiry,
                    row.strike,
                    opt_type,
                    row.bid,
                    row.ask,
                    row.iv or plan.atm_iv_pct or 30.0,
                    (chain.expiry - today).days,
                    td,
                    lot_size,
                )
                args = (
                    rules,
                    sign,
                    predicted_move_pct,
                    window_days,
                    settings.risk_free_rate,
                    event_day,
                    plan.pre_news_spot,
                )
                r_ai = evaluate(inp, stock_ai, *args)
                r_mkt = evaluate(inp, stock_mkt, *args)
                w_c = ai_w
                if policy and policy.t1_cal:
                    p_cal = policy.t1_cal.apply(r_ai.p_t1_before_sl)
                    w_c = weight_for(r_ai.p_t1_before_sl, p_cal, r_mkt.p_t1_before_sl, ai_w)
                res = blend_results(r_ai, r_mkt, w_c)
            plan.candidates.append(
                EvaluatedCandidate(row, inp, res, reason, r_ai, r_mkt, w_c, p_cal)
            )

    priced = [c for c in plan.candidates if c.result]
    if not priced:
        if plan.rejection is None:
            plan.status, plan.rejection = (
                "rejected",
                ("liquidity", "no candidate strike passed liquidity rules"),
            )
        return plan
    best = max(priced, key=lambda c: c.result.ev_pct if c.result else -1e9)
    plan.liquidity_score = liquidity_score(best.row)

    # (A4 c) high IV before a known event: require positive EV after the expected IV drop
    if (
        plan.rejection is None
        and plan.event_in_window
        and entry_cfg["require_post_crush_ev"]
        and (plan.iv_percentile or 0) >= float(entry_cfg["event_iv_percentile_min"])
        and best.result is not None
        and best.result.ev_pct <= 0
    ):
        plan.status, plan.rejection = (
            "rejected",
            (
                "entry",
                f"IV percentile {plan.iv_percentile:.0f} before an event and EV after IV drop "
                f"is {best.result.ev_pct:+.1f}%",
            ),
        )
    if plan.rejection is None and best.result is not None:
        plan.rejection = selection_rejection(best.result, gates)
        if plan.rejection:
            plan.status = "rejected"
    if plan.rejection is None:
        bad = core_rules.violation(
            symbol, direction, opt_type, best.row.expiry, today, known_expiries=listed
        )
        if bad:
            plan.status, plan.rejection = "rejected", (core_rules.STAGE, bad)
    if plan.rejection is None:
        plan.chosen = best
    plan.raw_result = best.raw  # what the AI alone would have implied
    return plan


def live_gates(engine: Engine | None, today: date) -> dict[str, float]:
    """Gate values for today's market regime (feat.regime); the base values otherwise."""
    from app.regime import gates as regime_gates
    from app.regime.service import current

    with session_scope(engine) as s:
        on = flags.active(s, "feat.regime")
    reg = current(engine, today) if on else None
    return regime_gates.for_regime(reg.vol if reg else None)


def selection_rejection(
    r: SimResult, gates: dict[str, float] | None = None
) -> tuple[str, str] | None:
    """Owner selection gates (config/options.yaml `selection`); `gates` = regime-specific values
    (app/regime/gates.py), same as options.yaml unless config/regime.yaml overrides them."""
    c = load_yaml("options.yaml").get("selection") or {}
    if not c.get("enabled", True):
        return None
    g = {
        "min_ev_pct": float(c["min_ev_pct"]),
        "min_ev_pct_of_premium": float(c["min_ev_pct_of_premium"]),
        "min_p_t1_before_sl": float(c["min_p_t1_before_sl"]),
    } | {k: v for k, v in (gates or {}).items() if k != "edge_ratio_min"}
    if r.ev_pct <= g["min_ev_pct"]:
        return ("ev", f"EV {r.ev_pct:+.1f}% after costs is not above {g['min_ev_pct']:g}%")
    if r.ev_pct < g["min_ev_pct_of_premium"]:
        return (
            "ev",
            f"EV {r.ev_pct:+.1f}% is below the minimum {g['min_ev_pct_of_premium']:g}% of premium",
        )
    if r.p_t1_before_sl < g["min_p_t1_before_sl"]:
        return (
            "probability",
            f"P(T1 before SL) {r.p_t1_before_sl:.0%} below {g['min_p_t1_before_sl']:.0%}",
        )
    return None


def candidate_rows_json(plan: OptionPlan) -> list[dict[str, Any]]:
    return [
        {
            "expiry": c.row.expiry.isoformat(),
            "strike": c.row.strike,
            "rejected": c.rejected_reason,
            "ev_pct": c.result.ev_pct if c.result else None,
            "ai_weight": c.ai_weight,
            "p_t1_raw": c.raw.p_t1_before_sl if c.raw else None,
            "p_t1_calibrated": c.p_t1_calibrated,
        }
        for c in plan.candidates
    ]
