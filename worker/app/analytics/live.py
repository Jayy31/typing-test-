"""Live paper-trade analytics (B5 on live trades), breakdowns and live-vs-backtest drift.

Expectancy is % of premium after costs; intervals bootstrap over news events (trades from the same
news cluster count once, like the backtester), every metric with and without unfillable trades.
"""

import math
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import numpy as np
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import (
    Analysis,
    BacktestRun,
    MacroSnapshot,
    NewsItem,
    NewsTag,
    PaperTrade,
    PortfolioSnapshot,
    Signal,
)
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def cfg() -> dict[str, Any]:
    return dict(load_yaml("tracking.yaml")["analytics"])


def bootstrap_ci(
    groups: list[list[float]], level: float | None = None
) -> tuple[float, float] | None:
    """Percentile interval of the mean, resampling whole groups (news events) with replacement."""
    if len(groups) < 5:
        return None
    c = cfg()
    level = float(level if level is not None else c["ci_level"])
    rng = np.random.default_rng(int(c["bootstrap_seed"]))
    means = []
    for _ in range(int(c["bootstrap_samples"])):
        pick = rng.integers(0, len(groups), len(groups))
        vals = [v for i in pick for v in groups[i]]
        means.append(float(np.mean(vals)))
    tail = (1 - level) / 2 * 100
    lo, hi = np.percentile(means, [tail, 100 - tail])
    return round(float(lo), 2), round(float(hi), 2)


def perf(trades: list[PaperTrade], cluster: Callable[[PaperTrade], str]) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    pct = np.array([t.pnl_pct or 0.0 for t in trades])
    inr = np.array([t.pnl or 0.0 for t in trades])
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        groups[cluster(t)].append(t.pnl_pct or 0.0)
    ci = bootstrap_ci(list(groups.values()))
    losses = inr[inr <= 0].sum()
    t1 = [
        bool((t.state_json or {}).get("targets_hit", 0)) or (t.exit_reason or "").startswith("T")
        for t in trades
    ]
    return {
        "n": len(trades),
        "events": len(groups),
        "win_rate": round(float((pct > 0).mean()), 3),
        "expectancy_pct": round(float(pct.mean()), 2),
        "ci": list(ci) if ci else None,
        "profit_factor": round(float(inr[inr > 0].sum() / -losses), 2) if losses < 0 else None,
        "t1_hit_rate": round(float(np.mean(t1)), 3),
        "pnl_inr": round(float(inr.sum()), 0),
        "slippage_inr": round(sum(t.slippage_inr or 0.0 for t in trades), 0),
        "exit_mix": dict(sorted(_count(t.exit_reason or "?" for t in trades).items())),
    }


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return dict(out)


class Context:
    """Lookups for one batch of trades: signal, analysis, event type, evidence basis, regime."""

    def __init__(self, s: Session, trades: list[PaperTrade]) -> None:
        ids = [t.signal_id for t in trades]
        self.sig = {g.id: g for g in s.scalars(select(Signal).where(Signal.id.in_(ids)))}
        an_ids = [g.analysis_id for g in self.sig.values() if g.analysis_id]
        self.an = {a.id: a for a in s.scalars(select(Analysis).where(Analysis.id.in_(an_ids)))}
        news_ids = {n for a in self.an.values() for n in self._cited(a)}
        self.news = {n.id: n for n in s.scalars(select(NewsItem).where(NewsItem.id.in_(news_ids)))}
        self.tags: dict[int, list[NewsTag]] = defaultdict(list)
        for tg in s.scalars(select(NewsTag).where(NewsTag.news_id.in_(news_ids))):
            self.tags[tg.news_id].append(tg)
        th = load_yaml("history.yaml")["event_study"]["regimes"]
        self.vix = [
            (as_ist(m.ts), m.india_vix)
            for m in s.scalars(
                select(MacroSnapshot)
                .where(MacroSnapshot.india_vix.is_not(None))
                .order_by(MacroSnapshot.ts)
            )
        ]
        self.vix_low, self.vix_high = float(th["vix_low"]), float(th["vix_high"])

    @staticmethod
    def _cited(a: Analysis) -> list[int]:
        js = a.scores_json or {}
        return list(js.get("cited_news_ids") or js.get("news_ids") or [])

    def analysis(self, t: PaperTrade) -> Analysis | None:
        g = self.sig.get(t.signal_id)
        return self.an.get(g.analysis_id) if g and g.analysis_id else None

    def cluster(self, t: PaperTrade) -> str:
        a = self.analysis(t)
        return f"c{a.cluster_id}" if a and a.cluster_id else f"s{t.signal_id}"

    def event_type(self, t: PaperTrade) -> str:
        a = self.analysis(t)
        g = self.sig.get(t.signal_id)
        for nid in self._cited(a) if a else []:
            for tg in self.tags.get(nid, []):
                if tg.event_type and (g is None or tg.symbol == g.symbol):
                    return tg.event_type
        return "unknown"

    def evidence_basis(self, t: PaperTrade) -> str:
        a = self.analysis(t)
        srcs = [self.news[n].source for n in (self._cited(a) if a else []) if n in self.news]
        structured = [x for x in srcs if x.startswith(("deep:", "kg:"))]
        if not structured:
            return "news only"
        return "base rate" if any("base_rate" in x for x in structured) else "hand-set rule"

    def market_regime(self, t: PaperTrade) -> str:
        """Phase 12 regime label stored on the analysis (calm/normal/stressed x risk-on/off)."""
        a = self.analysis(t)
        return str((a.scores_json or {}).get("regime") or "unknown") if a else "unknown"

    def positioning(self, t: PaperTrade) -> str:
        """Phase 12: the strongest positioning state behind the trade (base rate first)."""
        a = self.analysis(t)
        ev = ((a.scores_json or {}).get("positioning") or {}).get("evidence", []) if a else []
        if not ev:
            return "none"
        used = sorted((e for e in ev if e.get("weight")), key=lambda e: -float(e["weight"]))
        if used:
            return f"{used[0]['feature']}={used[0]['state']} (base rate)"
        build = next((e for e in ev if e["feature"] == "oi_buildup"), None)
        return f"oi_buildup={build['state']} (support)" if build else "support only"

    def regime(self, t: PaperTrade) -> str:
        when = as_ist(t.entry_ts)
        v = next((x for ts, x in reversed(self.vix) if ts <= when), None)
        if v is None:
            return "unknown"
        return "low_vol" if v < self.vix_low else "high_vol" if v > self.vix_high else "normal"


BREAKDOWNS: dict[str, Callable[[Context, PaperTrade], str]] = {
    "event_type": lambda c, t: c.event_type(t),
    "sector": lambda c, t: (c.sig[t.signal_id].sector if t.signal_id in c.sig else None) or "?",
    "score_band": lambda c, t: _band(c.sig[t.signal_id].score if t.signal_id in c.sig else 0),
    "impact_order": lambda c, t: str(
        (c.sig[t.signal_id].impact_order if t.signal_id in c.sig else 1) or 1
    ),
    "prediction_source": lambda c, t: (
        (c.sig[t.signal_id].prediction_source if t.signal_id in c.sig else None) or "single"
    ),
    "calibration": lambda c, t: (
        (c.sig[t.signal_id].calibration_method if t.signal_id in c.sig else None) or "shrink"
    ),
    "regime": lambda c, t: c.regime(t),
    "entry_timing": lambda c, t: (
        "after hours" if t.signal_id in c.sig and c.sig[t.signal_id].enter_after else "normal"
    ),
    "evidence_basis": lambda c, t: c.evidence_basis(t),
    "market_regime": lambda c, t: c.market_regime(t),
    "positioning": lambda c, t: c.positioning(t),
}


def _band(score: float) -> str:
    return "80+" if score >= 80 else "70-79" if score >= 70 else "65-69" if score >= 65 else "<65"


def closed_trades(
    s: Session, start: datetime | None = None, end: datetime | None = None, book: str = "main"
) -> list[PaperTrade]:
    q = select(PaperTrade).where(PaperTrade.status == "CLOSED", PaperTrade.book == book)
    out = []
    for t in s.scalars(q):
        x = as_ist(t.exit_ts) if t.exit_ts else None
        if x is None or (start and x < start) or (end and x >= end):
            continue
        out.append(t)
    return out


def summary(
    s: Session, start: datetime | None = None, end: datetime | None = None
) -> dict[str, Any]:
    trades = closed_trades(s, start, end)
    ctx = Context(s, trades)
    fillable = [t for t in trades if t.fillability != "unfillable"]
    out: dict[str, Any] = {
        "all": perf(trades, ctx.cluster),
        "excluding_unfillable": perf(fillable, ctx.cluster),
        "breakdowns": {},
    }
    for name, key in BREAKDOWNS.items():
        groups: dict[str, list[PaperTrade]] = defaultdict(list)
        for t in trades:
            groups[key(ctx, t)].append(t)
        out["breakdowns"][name] = {k: perf(v, ctx.cluster) for k, v in sorted(groups.items())}
    out["risk"] = risk(s, start, end)
    return out


def risk(s: Session, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """Daily mark-to-market from portfolio snapshots (last per day): drawdown, Sharpe, Sortino."""
    daily: dict[date, float] = {}
    for p in s.scalars(select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts)):
        ts = as_ist(p.ts)
        if (start and ts < start) or (end and ts >= end):
            continue
        daily[ts.date()] = p.equity
    eq = np.array(list(daily.values()))
    if len(eq) < 3:
        return {"days": len(eq)}
    ret = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    down = ret[ret < 0]
    sd = float(ret.std(ddof=1))
    return {
        "days": len(eq),
        "return_pct": round(float((eq[-1] / eq[0] - 1) * 100), 2),
        "max_drawdown_pct": round(float(((peak - eq) / peak).max() * 100), 2),
        "sharpe": round(float(ret.mean() / sd * math.sqrt(252)), 2) if sd > 0 else None,
        "sortino": round(float(ret.mean() / down.std(ddof=1) * math.sqrt(252)), 2)
        if len(down) > 1 and down.std(ddof=1) > 0
        else None,
    }


def drift_check(engine: Engine | None = None, now: datetime | None = None) -> int:
    """Alert when live expectancy falls outside the latest backtest's 90% interval (>= N trades)."""
    c = cfg()
    with session_scope(engine) as s:
        live = summary(s)["all"]
        if live.get("n", 0) < int(c["drift_min_trades"]):
            return 0
        runs = s.scalars(select(BacktestRun).order_by(BacktestRun.id.desc())).all()
        bt = next(
            (
                r.metrics_json
                for r in runs
                if (r.metrics_json or {}).get("variant", {}).get("name")
                == c["drift_backtest_variant"]
            ),
            None,
        )
        b = (bt or {}).get("bootstrap") or {}
        if not b:
            return 0
        lo, hi = float(b["expectancy_p5"]), float(b["expectancy_p95"])
        e = float(live["expectancy_pct"])
        if lo <= e <= hi:
            return 0
        side = "below" if e < lo else "above"
        text = (
            f"LIVE vs BACKTEST DRIFT\nLive expectancy {e:+.1f}% of premium over "
            f"{live['n']} trades is "
            f"{side} the backtest's 90% interval ({lo:+.1f}% .. {hi:+.1f}%, "
            f"{c['drift_backtest_variant']}).\n"
            "Check fills, spreads and data quality before trusting either number."
        )
        month = (now or datetime.now()).strftime("%Y-%m")
        return int(enqueue_alert(s, "health", text, f"drift:{month}:{side}"))
