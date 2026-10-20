"""Validation ladder and protection against luck (Phase 14). Deterministic, no AI.

Stage 0 runs on pre-cutoff data only (hard guard). Stage 1 (holdout) runs on post-cutoff data
and is evaluated exactly once per spec: until it has the configured minimum trades and events it
only reports counts ("holdout pending"); the first evaluation is stored as the spec's single
holdout verdict (unique index). Every run is stored as a trial, failures included.
"""

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import NormalDist
from typing import Any

import numpy as np

from app.backtest.engine import Trade
from app.backtest.metrics import bootstrap_ci
from app.backtest.runner import RunResult
from app.history.integrity import knowledge_cutoff
from app.lab.spec import StrategySpec, cfg

N = NormalDist()
GAMMA = 0.5772156649


class LookAheadError(RuntimeError):
    """A screening run touched data after the AI knowledge cutoff."""


def cutoff() -> date:
    c = knowledge_cutoff()
    if c is None:
        raise LookAheadError("AI_MODEL_KNOWLEDGE_CUTOFF is not set: no pre/post split")
    return c


def screening_end(requested: date) -> date:
    """Exclusive end of a Stage-0 run: never after the cutoff (data mining uses this too)."""
    return min(requested, cutoff() + timedelta(days=1))


# ---- statistics ----------------------------------------------------------------------------
def events_of(trades: list[Trade]) -> int:
    return len({f"{t.signal.day}:{t.signal.event_type}" for t in trades})


def sharpe(pcts: list[float]) -> float | None:
    if len(pcts) < 3:
        return None
    sd = float(np.std(pcts, ddof=1))
    return float(np.mean(pcts)) / sd if sd > 0 else None


def stats(trades: list[Trade]) -> dict[str, Any]:
    pct = [t.pnl_pct for t in trades]
    ci = bootstrap_ci(trades)
    return {
        "n": len(trades),
        "events": events_of(trades),
        "expectancy_pct": round(float(np.mean(pct)), 3) if pct else None,
        "win_rate": round(float(np.mean([p > 0 for p in pct])), 3) if pct else None,
        "sharpe": sharpe(pct),
        "ci_lo": ci["expectancy_p5"] if ci else None,
        "ci_hi": ci["expectancy_p95"] if ci else None,
    }


def deflated_sharpe(
    sr: float, n_obs: int, n_trials: int, var_trials: float, skew: float = 0.0, kurt: float = 3.0
) -> float:
    """Bailey & Lopez de Prado: probability that the observed (per-trade) Sharpe beats the
    expected maximum Sharpe of `n_trials` null strategies."""
    n_trials = max(n_trials, 1)
    sd = math.sqrt(max(var_trials, 0.0))
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = sd * (
            (1 - GAMMA) * N.inv_cdf(1 - 1 / n_trials)
            + GAMMA * N.inv_cdf(1 - 1 / (n_trials * math.e))
        )
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-9))
    return float(N.cdf((sr - sr0) * math.sqrt(max(n_obs - 1, 1)) / denom))


def moments(pcts: list[float]) -> tuple[float, float]:
    a = np.asarray(pcts, dtype=float)
    if len(a) < 4 or a.std() == 0:
        return 0.0, 3.0
    z = (a - a.mean()) / a.std()
    return float((z**3).mean()), float((z**4).mean())


def concentration(trades: list[Trade]) -> dict[str, float]:
    """Largest share of total positive profit from one stock / one entry month."""

    def share(key: Any) -> float:
        by: dict[str, float] = defaultdict(float)
        for t in trades:
            by[key(t)] += t.pnl_inr
        total = sum(v for v in by.values() if v > 0)
        return max((v for v in by.values() if v > 0), default=0.0) / total if total > 0 else 1.0

    return {
        "stock": round(share(lambda t: t.signal.symbol), 3),
        "month": round(share(lambda t: t.entry_day.strftime("%Y-%m")), 3),
    }


def regime_consistency(trades: list[Trade], label_of: Any) -> dict[str, Any]:
    c = cfg()["luck"]["regime"]
    by: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by[label_of(t.entry_day).split("/")[0]].append(t.pnl_pct)
    big = {k: float(np.mean(v)) for k, v in by.items() if len(v) >= int(c["min_trades_per_regime"])}
    pos = sum(1 for v in big.values() if v > 0)
    share = pos / len(big) if big else 0.0
    return {
        "by_regime": {k: round(v, 2) for k, v in big.items()},
        "share_positive": round(share, 3),
        "passed": bool(big) and share >= float(c["min_share_regimes_positive"]),
    }


# ---- neighbours and placebo ----------------------------------------------------------------
def neighbours(spec: StrategySpec) -> list[tuple[str, StrategySpec]]:
    """Plateau test: strike +-1, entry/exit days +-1/+-2, thresholds +-10%."""
    pc = cfg()["luck"]["plateau"]
    out: list[tuple[str, StrategySpec]] = []
    d = spec.model_dump(mode="json")

    def add(label: str, path: list[str], value: Any) -> None:
        nd = d.copy()
        node = nd
        for p in path[:-1]:
            node[p] = dict(node[p])
            node = node[p]
        node[path[-1]] = value
        try:
            out.append((label, StrategySpec.model_validate(nd)))
        except ValueError:
            pass

    ladder = ["itm2", "itm1", "atm", "otm1"]
    if spec.option.strike in ladder:
        i = ladder.index(spec.option.strike)
        for j in (i - 1, i + 1):
            if 0 <= j < len(ladder):
                add(f"strike {ladder[j]}", ["option", "strike"], ladder[j])
    elif spec.option.target_delta is not None:
        for f in (1 - float(pc["threshold_step"]), 1 + float(pc["threshold_step"])):
            add(
                f"delta x{f:.2f}",
                ["option", "target_delta"],
                round(spec.option.target_delta * f, 3),
            )
    for step in pc["day_steps"]:
        for sgn in (-1, 1):
            if spec.entry.days_before is not None:
                add(
                    f"days_before {sgn * step:+d}",
                    ["entry", "days_before"],
                    spec.entry.days_before + sgn * step,
                )
            if spec.exits.max_hold_days is not None:
                add(
                    f"max_hold {sgn * step:+d}",
                    ["exits", "max_hold_days"],
                    spec.exits.max_hold_days + sgn * step,
                )
    f_step = float(pc["threshold_step"])
    for path, val in (
        (["filters", "iv_percentile_min"], spec.filters.iv_percentile_min),
        (["filters", "iv_percentile_max"], spec.filters.iv_percentile_max),
        (["filters", "min_adv_cr"], spec.filters.min_adv_cr),
        (["exits", "stop_loss_pct"], spec.exits.stop_loss_pct),
        (["exits", "iv_spike_exit_pts"], spec.exits.iv_spike_exit_pts),
    ):
        if val is not None:
            for f in (1 - f_step, 1 + f_step):
                add(f"{path[-1]} x{f:.1f}", path, round(float(val) * f, 3))
    if spec.exits.targets_pct:
        for f in (1 - f_step, 1 + f_step):
            add(
                f"targets x{f:.1f}",
                ["exits", "targets_pct"],
                [round(x * f, 2) for x in spec.exits.targets_pct],
            )
    return out


def placebo_shift(seed: int) -> Any:
    """Deterministic random shift (trading days) per trigger key, never within min_shift of 0."""
    pc = cfg()["luck"]["placebo"]
    lo, hi = int(pc["min_shift_days"]), int(pc["max_shift_days"])

    def f(key: str) -> int:
        r = random.Random(f"{seed}:{key}")
        return r.choice([-1, 1]) * r.randint(lo, hi)

    return f


@dataclass
class Checks:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, name: str, passed: bool, **detail: Any) -> None:
        self.items[name] = {"passed": bool(passed), **detail}

    @property
    def passed(self) -> bool:
        return all(v["passed"] for v in self.items.values())

    def failed(self) -> list[str]:
        return [k for k, v in self.items.items() if not v["passed"]]


def stage0_checks(
    main: RunResult,
    stress: RunResult,
    plateau: list[tuple[str, RunResult]],
    placebo_exp: list[float],
    n_trials: int,
    var_trials: float,
    label_of: Any,
) -> tuple[Checks, dict[str, Any]]:
    c0, lk = cfg()["stage0"], cfg()["luck"]
    s = stats(main.trades)
    ch = Checks()
    ch.add("min_trades", s["n"] >= int(c0["min_trades"]), value=s["n"], need=int(c0["min_trades"]))
    ch.add(
        "min_events",
        s["events"] >= int(c0["min_events"]),
        value=s["events"],
        need=int(c0["min_events"]),
    )
    lo = s["ci_lo"]
    ch.add(
        "ci_excludes_zero", lo is not None and lo > float(c0["require_ci_lower_above"]), value=lo
    )
    sr = s["sharpe"] or 0.0
    skew, kurt = moments([t.pnl_pct for t in main.trades])
    dsr = deflated_sharpe(
        sr, s["n"], n_trials, max(var_trials, float(c0["min_sharpe_variance"])), skew, kurt
    )
    ch.add(
        "deflated_sharpe",
        dsr >= float(c0["deflated_sharpe_min"]),
        value=round(dsr, 3),
        trials=n_trials,
        need=float(c0["deflated_sharpe_min"]),
    )
    prof = [(lbl, stats(r.trades)["expectancy_pct"]) for lbl, r in plateau]
    ok = [e for _, e in prof if e is not None and e > 0]
    share = len(ok) / len(prof) if prof else 0.0
    ch.add(
        "plateau",
        bool(prof) and share >= float(lk["plateau"]["min_share_profitable"]),
        share=round(share, 3),
        neighbours={lbl: e for lbl, e in prof},
    )
    if placebo_exp:
        pct = float(np.percentile(placebo_exp, float(lk["placebo"]["real_must_beat_percentile"])))
        real = s["expectancy_pct"] or 0.0
        ch.add("placebo", real > pct, real=real, placebo_p=round(pct, 3), runs=len(placebo_exp))
    else:
        ch.add("placebo", False, reason="no placebo runs")
    conc = concentration(main.trades)
    ch.add(
        "concentration",
        conc["stock"] <= float(lk["max_profit_share_per_stock"])
        and conc["month"] <= float(lk["max_profit_share_per_month"]),
        **conc,
    )
    se = stats(stress.trades)["expectancy_pct"]
    ch.add(
        "cost_stress",
        se is not None and se > 0,
        expectancy_pct=se,
        spread_mult=float(lk["cost_stress_spread_mult"]),
    )
    rc = regime_consistency(main.trades, label_of)
    ch.add("regime", rc["passed"], **{k: v for k, v in rc.items() if k != "passed"})
    return ch, s


def holdout_checks(main: RunResult, stress: RunResult) -> tuple[Checks | None, dict[str, Any]]:
    """None while pending (only counts are reported, never results)."""
    h = cfg()["holdout"]
    n, ev = len(main.trades), events_of(main.trades)
    if n < int(h["min_trades"]) or ev < int(h["min_events"]):
        return None, {
            "n": n,
            "events": ev,
            "need_trades": int(h["min_trades"]),
            "need_events": int(h["min_events"]),
        }
    s = stats(main.trades)
    ch = Checks()
    ch.add(
        "expectancy",
        (s["expectancy_pct"] or 0) > float(h["require_expectancy_above"]),
        value=s["expectancy_pct"],
    )
    if h.get("require_cost_stress", True):
        se = stats(stress.trades)["expectancy_pct"]
        ch.add("cost_stress", se is not None and se > 0, expectancy_pct=se)
    return ch, s


def diagnose(trades: list[Trade], label_of: Any) -> dict[str, Any]:
    """Where a spec makes and loses money: by sector, regime, event type, top stocks."""

    def by(key: Any) -> dict[str, dict[str, float]]:
        g: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            g[str(key(t))].append(t.pnl_pct)
        return {
            k: {"n": len(v), "avg_pct": round(float(np.mean(v)), 2)}
            for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))[:8]
        }

    return {
        "sector": by(lambda t: t.signal.sector or "?"),
        "regime": by(lambda t: label_of(t.entry_day).split("/")[0]),
        "event_type": by(lambda t: t.signal.event_type or "?"),
        "exit": by(lambda t: t.exit_reason or "?"),
        "concentration": concentration(trades) if trades else {},
        "costs_pct": round(
            float(np.mean([t.costs_inr / t.outlay * 100 for t in trades if t.outlay])), 2
        )
        if trades
        else None,
    }
