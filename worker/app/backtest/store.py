"""Persist backtest runs (Postgres backtest_runs / backtest_trades, Parquet detail) and render
the side-by-side comparison report."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import Engine

from app.backtest.engine import Trade
from app.backtest.runner import RunResult
from app.config import IST, get_settings
from app.db.models import BacktestRun, BacktestTrade
from app.db.session import session_scope
from app.flags import ENGINE_VERSION
from app.yaml_config import load_yaml

CONFIG_FILES = (
    "backtest.yaml",
    "exits.yaml",
    "options.yaml",
    "entry.yaml",
    "history.yaml",
    "portfolio.yaml",
)


def config_snapshot(res: RunResult) -> dict[str, Any]:
    cfg = {name: load_yaml(name) for name in CONFIG_FILES}
    st = get_settings()
    return {
        "variant": res.variant.__dict__,
        "engine_version": ENGINE_VERSION,
        "settings": {
            "lots_per_signal": st.lots_per_signal,
            "edge_ratio_min": st.edge_ratio_min,
            "analogue_prior_n": st.analogue_prior_n,
            "analogue_min_n": st.analogue_min_n,
        },
        "config": cfg,
    }


def config_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def trade_row(t: Trade) -> dict[str, Any]:
    return {
        "signal": t.signal.key,
        "option_type": t.option_type,
        "source": t.signal.source,
        "event_type": t.signal.event_type,
        "sector": t.signal.sector,
        "timing": t.signal.timing,
        "predicted_move_pct": t.signal.predicted_move_pct,
        "ai_weight": t.signal.ai_weight,
        "analogue": t.signal.analogue,
        "lots": t.lots,
        "lot_size": t.lot_size,
        "lot_source": t.lot_source,
        "tier": t.tier,
        "entry_mid": t.entry_mid,
        "entry_stale": t.entry_stale,
        "entry_fill": t.entry_fill,
        "exit_fill": t.exit_fill,
        "fillability": t.fillability,
        "settlement_used": t.settlement_used,
        "fills": t.fills,
        "costs_inr": t.costs_inr,
        "slippage_inr": t.slippage_inr,
        "t1_hit": t.t1_hit,
        "spot_entry": t.spot_entry,
        "spot_exit": t.spot_exit,
        "stock_pct": t.stock_pct,
        "ev_pct": t.sim.ev_pct if t.sim else None,
        "p_t1": t.sim.p_t1_before_sl if t.sim else None,
        "report": t.report(),
    }


def save(res: RunResult, metrics: dict[str, Any], engine: Engine | None = None) -> int:
    snap = config_snapshot(res)
    now = datetime.now(IST)
    with session_scope(engine) as s:
        run = BacktestRun(
            config_hash=config_hash(snap),
            config_json=snap,
            period_start=res.start,
            period_end=res.end,
            status="done",
            started_at=now,
            finished_at=now,
            metrics_json=metrics,
            notes="; ".join(res.notes) or None,
        )
        s.add(run)
        s.flush()
        for t in res.trades:
            s.add(
                BacktestTrade(
                    run_id=run.id,
                    symbol=t.signal.symbol,
                    event_ref=t.signal.key[:64],
                    direction=t.signal.direction,
                    expiry=t.expiry,
                    strike=t.strike,
                    entry_date=t.entry_day,
                    entry_premium=t.entry_ask,
                    exit_date=t.exit_day,
                    exit_premium=t.fills[-1]["price"] if t.fills else None,
                    exit_reason=t.exit_reason,
                    pnl_pct=t.pnl_pct,
                    pnl_inr=t.pnl_inr,
                    details_json=trade_row(t),
                )
            )
        run_id = int(run.id)
    out = Path(get_settings().history_data_dir) / "backtests" / str(run_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1, default=str), encoding="utf-8")
    return run_id


def save_equity(run_id: int, points: list[tuple[Any, float]]) -> None:
    if not points:
        return
    out = Path(get_settings().history_data_dir) / "backtests" / str(run_id)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()  # in-memory: never touches the history DB's writer lock
    try:
        con.execute("CREATE TABLE e (date DATE, equity DOUBLE)")
        con.executemany("INSERT INTO e VALUES (?, ?)", points)
        con.execute(f"COPY e TO '{out / 'equity.parquet'}' (FORMAT PARQUET)")
    finally:
        con.close()


def _f(x: Any, fmt: str) -> str:
    return "n/a" if x is None else format(x, fmt)


def render(rows: list[tuple[int, dict[str, Any]]]) -> str:
    """Side-by-side comparison, % of premium first."""
    head = (
        "| run | variant | trades | events | win | expectancy % (p5..p95) | excl. unfillable % | PF | T1 hit | "
        "max DD % | return % | settlement-price trades | slippage Rs | P&L Rs exact / approx |"
    )
    lines = ["# Backtest comparison", "", head, "|" + "---|" * 14]
    for run_id, m in rows:
        a, x, b, r = (
            m["all"],
            m["excluding_unfillable"],
            m.get("bootstrap") or {},
            m.get("risk") or {},
        )
        ci = (
            f" ({_f(b.get('expectancy_p5'), '+.1f')}..{_f(b.get('expectancy_p95'), '+.1f')})"
            if b
            else ""
        )
        lines.append(
            f"| {run_id} | {m['variant']['name']} | {a.get('n', 0)} | {m['independent_events']} | {_f(a.get('win_rate'), '.0%')} | "
            f"{_f(a.get('expectancy_pct'), '+.1f')}{ci} | {_f(x.get('expectancy_pct'), '+.1f')} | {_f(a.get('profit_factor'), '.2f')} | "
            f"{_f(a.get('t1_hit_rate'), '.0%')} | {_f(r.get('max_drawdown_pct'), '.1f')} | {_f(r.get('return_pct'), '+.1f')} | "
            f"{m['settlement_price_trades']} | {m['slippage_inr']:+,.0f} | {m['pnl_inr_exact_lots']:+,.0f} / {m['pnl_inr_approx_inferred_lots']:+,.0f}* |"
        )
    lines += [
        "",
        "* rupees on trades with inferred lot sizes (before July 2024) are approximate; % of premium is the primary metric.",
        "Spread sensitivity rows (x1.5, x2) use the same signals with wider modelled spreads.",
        "Benchmarks: 'random direction' trades the same events with a random side; 'ATM no gates' buys the ATM option on every event.",
    ]
    for run_id, m in rows:
        if m.get("notes"):
            lines.append(f"Run {run_id} notes: " + "; ".join(m["notes"]))
        bm = m.get("benchmarks") or {}
        lines.append(
            f"Run {run_id}: stock with the same capital Rs {bm.get('stock_same_capital_inr', 0):+,.0f}; "
            f"Nifty buy & hold {_f(bm.get('nifty_buy_hold_pct'), '+.1f')}%; skipped signals {m['signals_skipped']}"
        )
    return "\n".join(lines)


def is_candidate(v: dict[str, Any]) -> bool:
    """Variants that could replace the default: not a benchmark, normal spread."""
    return (
        v.get("direction_mode", "signal") == "signal"
        and v.get("selection", "ev") == "ev"
        and float(v.get("spread_mult", 1.0)) == 1.0
    )


def best_vs_default(rows: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    """Owner rule (multiple comparisons): the best candidate's advantage over the default (first
    row) counts only if its 90% bootstrap interval lies entirely above the default's."""
    default_id, default = rows[0]
    cands = [(i, m) for i, m in rows if is_candidate(m["variant"]) and m["all"].get("n")]
    if not cands or not default["all"].get("n"):
        return {"best": None, "significant": False, "reason": "not enough trades"}
    best_id, best = max(cands, key=lambda r: r[1]["all"]["expectancy_pct"])
    out: dict[str, Any] = {
        "best": best["variant"]["name"],
        "best_run": best_id,
        "default": default["variant"]["name"],
        "best_expectancy": best["all"]["expectancy_pct"],
        "default_expectancy": default["all"]["expectancy_pct"],
    }
    if best_id == default_id:
        return {**out, "significant": False, "reason": "the current default is the best variant"}
    b, d = best.get("bootstrap") or {}, default.get("bootstrap") or {}
    if not b or not d:
        return {**out, "significant": False, "reason": "too few trades for a confidence interval"}
    sig = b["expectancy_p5"] > d["expectancy_p95"]
    reason = (
        f"interval {b['expectancy_p5']:+.1f}..{b['expectancy_p95']:+.1f}% vs default "
        f"{d['expectancy_p5']:+.1f}..{d['expectancy_p95']:+.1f}%"
    )
    return {**out, "significant": sig, "reason": reason}


def verdict_text(
    v: dict[str, Any], best_x2: float | None = None, default_x2: float | None = None
) -> str:
    if not v.get("best"):
        return f"VERDICT: no conclusion ({v['reason']})."
    if v["best"] == v["default"]:
        return f"VERDICT: the current default ({v['default']}) is the best variant; no change suggested."
    head = (
        f"VERDICT: best variant '{v['best']}' {v['best_expectancy']:+.1f}% vs default {v['default_expectancy']:+.1f}% "
        f"per trade ({v['reason']})"
    )
    if not v["significant"]:
        return head + ": NOT SIGNIFICANT. No config change suggested."
    if best_x2 is None or default_x2 is None:
        return (
            head
            + ": significant, but the 2x spread check could not run. No config change suggested."
        )
    if best_x2 > default_x2:
        return (
            head
            + f": significant and still better at 2x spread ({best_x2:+.1f}% vs {default_x2:+.1f}%). Suggestion: consider switching to it."
        )
    return (
        head
        + f": significant, but not better at 2x spread ({best_x2:+.1f}% vs {default_x2:+.1f}%). No config change suggested."
    )
