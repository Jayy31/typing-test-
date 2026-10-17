"""Backtest metrics (v2.1 B5) and benchmarks (B6).

% of premium is the primary unit for every trade (lot sizes before July 2024 are inferred);
rupee totals are split into exact-lot and approximate (inferred lot size). Uncertainty is shown
with a bootstrap over independent events (same day and event type = one event).
"""

import math
from collections import Counter, defaultdict
from datetime import date
from typing import Any

import numpy as np

from app.backtest.data import HistoryData
from app.backtest.engine import Trade
from app.backtest.runner import RunResult
from app.history.universe import EXACT
from app.yaml_config import load_yaml


def _perf(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    pct = np.array([t.pnl_pct for t in trades])
    inr = np.array([t.pnl_inr for t in trades])
    wins, losses = inr[inr > 0], inr[inr <= 0]
    return {
        "n": len(trades),
        "win_rate": round(float((pct > 0).mean()), 3),
        "expectancy_pct": round(float(pct.mean()), 2),
        "median_pct": round(float(np.median(pct)), 2),
        "avg_win_pct": round(float(pct[pct > 0].mean()), 2) if (pct > 0).any() else None,
        "avg_loss_pct": round(float(pct[pct <= 0].mean()), 2) if (pct <= 0).any() else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
        "t1_hit_rate": round(float(np.mean([t.t1_hit for t in trades])), 3),
    }


def _clusters(trades: list[Trade]) -> list[str]:
    return [f"{t.signal.day}:{t.signal.event_type}" for t in trades]


def bootstrap_ci(trades: list[Trade]) -> dict[str, float] | None:
    if len(trades) < 5:
        return None
    cfg = load_yaml("backtest.yaml")["bootstrap"]
    by: dict[str, list[float]] = defaultdict(list)
    for k, t in zip(_clusters(trades), trades, strict=True):
        by[k].append(t.pnl_pct)
    groups = list(by.values())
    rng = np.random.default_rng(int(cfg["seed"]))
    means = []
    for _ in range(int(cfg["samples"])):
        pick = rng.integers(0, len(groups), len(groups))
        vals = [v for i in pick for v in groups[i]]
        means.append(float(np.mean(vals)))
    lo, hi = np.percentile(means, [5, 95])
    return {"expectancy_p5": round(float(lo), 2), "expectancy_p95": round(float(hi), 2)}


def equity_curve(
    trades: list[Trade], capital: float, start: date, end: date, calendar: list[date]
) -> list[tuple[date, float]]:
    days = [d for d in calendar if start <= d <= end]
    per_trade = []
    for t in trades:
        marks = dict(t.marks)
        per_trade.append((t.entry_day, t.exit_day or end, marks, t.pnl_inr))
    out = []
    last: dict[int, float] = {}
    for d in days:
        total = 0.0
        for k, (a, b, marks, final) in enumerate(per_trade):
            if d < a:
                continue
            if d >= b:
                total += final
                continue
            if d in marks:
                last[k] = marks[d]
            total += last.get(k, 0.0)
        out.append((d, capital + total))
    return out


def _risk(curve: list[tuple[date, float]], capital: float) -> dict[str, Any]:
    if len(curve) < 2:
        return {}
    eq = np.array([v for _, v in curve])
    rets = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    dd = float(((eq - peak) / peak).min()) * 100
    years = max((curve[-1][0] - curve[0][0]).days / 365.25, 1 / 365)
    cagr = ((eq[-1] / capital) ** (1 / years) - 1) * 100 if eq[-1] > 0 else -100.0
    sd = float(rets.std())
    down = rets[rets < 0]
    return {
        "return_pct": round(float(eq[-1] / capital - 1) * 100, 2),
        "cagr_pct": round(float(cagr), 2),
        "max_drawdown_pct": round(dd, 2),
        "sharpe": round(float(rets.mean() / sd * math.sqrt(252)), 2) if sd > 0 else None,
        "sortino": round(float(rets.mean() / down.std() * math.sqrt(252)), 2)
        if len(down) > 1 and down.std() > 0
        else None,
    }


def _by(trades: list[Trade], key: Any) -> dict[str, Any]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        groups[str(key(t))].append(t)
    return {
        k: {
            "n": len(v),
            "expectancy_pct": round(float(np.mean([x.pnl_pct for x in v])), 2),
            "win_rate": round(float(np.mean([x.pnl_pct > 0 for x in v])), 3),
        }
        for k, v in sorted(groups.items())
    }


def compute(res: RunResult, data: HistoryData) -> dict[str, Any]:
    capital = float(load_yaml("portfolio.yaml")["notional_capital_inr"])
    trades = res.trades
    first = res.folds[0].test_start if res.folds else res.start
    curve = equity_curve(trades, capital, first, res.end, data.calendar)
    exact = [t for t in trades if t.lot_source in EXACT]
    approx = [t for t in trades if t.lot_source not in EXACT]
    with_p = [t for t in trades if t.sim is not None]
    nifty = [data.nifty[d] for d in data.calendar if first <= d <= res.end and d in data.nifty]
    stock = [t for t in trades if t.stock_pct is not None]
    m: dict[str, Any] = {
        "variant": res.variant.__dict__,
        "period": [first.isoformat(), res.end.isoformat()],
        "folds": len(res.folds),
        "signals_skipped": dict(Counter(s.stage for s in res.skips)),
        "independent_events": len(set(_clusters(trades))),
        "all": _perf(trades),
        "excluding_unfillable": _perf([t for t in trades if t.fillability != "unfillable"]),
        "fills": dict(Counter(t.fillability for t in trades)),
        "settlement_price_trades": sum(t.settlement_used for t in trades),
        "bootstrap": bootstrap_ci(trades),
        "risk": _risk(curve, capital),
        "pnl_inr_exact_lots": round(sum(t.pnl_inr for t in exact), 0),
        "pnl_inr_approx_inferred_lots": round(sum(t.pnl_inr for t in approx), 0),
        "trades_inferred_lots": len(approx),
        "slippage_inr": round(sum(t.slippage_inr for t in trades), 0),
        "exit_mix": dict(Counter(t.exit_reason for t in trades)),
        "avg_hold_days": round(
            float(
                np.mean(
                    [
                        data.trading_days_between(t.entry_day, t.exit_day)
                        for t in trades
                        if t.exit_day
                    ]
                )
            ),
            1,
        )
        if trades
        else None,
        "brier_p_t1": round(
            float(np.mean([(t.sim.p_t1_before_sl - t.t1_hit) ** 2 for t in with_p if t.sim])), 4
        )
        if with_p
        else None,
        "by_event_type": _by(trades, lambda t: t.signal.event_type),
        "by_sector": _by(trades, lambda t: t.signal.sector),
        "by_regime": _by(trades, lambda t: (t.signal.analogue or {}).get("regime")),
        "by_source": _by(trades, lambda t: t.signal.source),
        "by_spread_tier": _by(trades, lambda t: t.tier),
        "benchmarks": {
            "stock_same_capital_inr": round(
                sum((t.stock_pct or 0) * t.outlay / 100 for t in stock), 0
            ),
            "stock_avg_pct": round(float(np.mean([t.stock_pct for t in stock])), 2)
            if stock
            else None,
            "nifty_buy_hold_pct": round((nifty[-1] / nifty[0] - 1) * 100, 2)
            if len(nifty) > 1
            else None,
        },
        "counterfactual_open_trade_on_same_stock": _perf(res.counterfactuals),
        "notes": res.notes,
    }
    return m


def equity_points(res: RunResult, data: HistoryData) -> list[tuple[date, float]]:
    capital = float(load_yaml("portfolio.yaml")["notional_capital_inr"])
    first = res.folds[0].test_start if res.folds else res.start
    return equity_curve(res.trades, capital, first, res.end, data.calendar)
