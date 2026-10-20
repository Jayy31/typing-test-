"""Strategy paper books (Phase 14): Stage 2 incubation, Stage 3 validated, retirement, the
combined portfolio view and the correlation report.

- EOD incubation (no broker needed): each evening the spec runs on the window since its book
  opened, with end-of-day bhavcopy prices and the backtester's spread model and fill checks
  (a single test window, no look-ahead). Trades are kept on the book's `eod` trial.
- Real fills: once a broker data source exists, app/lab/live.py makes LAB signals that the paper
  engine trades in book `lab:<spec id>`; those are reported separately.
- Validated needs the EOD incubation (40 trades) AND a real-fill confirmation (15 trades).
- Retire when live results fall below the Stage-0 90% interval after enough trades, or when a
  CUSUM decay detector fires. Validated strategies may be proposed into the main engine only as a
  learning-loop suggestion (config/promoted_strategies.yaml slot), never written directly.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.backtest.data import HistoryData
from app.backtest.engine import Trade
from app.config import IST
from app.db.models import (
    LabBook,
    LabHypothesis,
    LabSpec,
    LabTrial,
    LabVerdict,
    PaperTrade,
    Suggestion,
)
from app.db.session import session_scope
from app.lab import validate as V
from app.lab.backtest import RunOptions, run_spec
from app.lab.spec import StrategySpec, cfg, parse

log = logging.getLogger(__name__)
NEWS_CORE = "news_core"
EOD, REAL = "eod_incubation", "real_fills"


def bcfg() -> dict[str, Any]:
    return dict(cfg()["books"])


def book_key(spec_id: int) -> str:
    return f"lab:{spec_id}"


def open_books(engine: Engine | None = None, now: datetime | None = None) -> int:
    """A book for every spec that passed its holdout (stage incubating), plus news_core."""
    now = now or datetime.now(IST)
    made = 0
    with session_scope(engine) as s:
        have = {b.spec_id for b in s.scalars(select(LabBook))}
        for sp in s.scalars(select(LabSpec).where(LabSpec.stage == "incubating")):
            if sp.id not in have:
                s.add(
                    LabBook(
                        spec_id=sp.id, book=book_key(sp.id), status="incubating", started_at=now
                    )
                )
                made += 1
        if bcfg().get("news_core_book", True):
            made += _news_core_book(s, have, now)
    return made


def _news_core_book(s: Session, have: set[int], now: datetime) -> int:
    from app.lab import registry as R

    h = s.scalars(select(LabHypothesis).where(LabHypothesis.name == NEWS_CORE)).first()
    if h is None:
        h = R.register(
            s,
            NEWS_CORE,
            "news_core",
            "The main engine's news signals, as a strategy, "
            "so every lab strategy is compared with it on equal terms.",
            "Baseline: whatever the main engine earns.",
        )
        sp = R.add_spec(
            s,
            h,
            StrategySpec.model_validate(
                {"name": NEWS_CORE, "source": "news_core", "trigger": {"event_types": ["results"]}}
            ),
        )
        sp.stage = "incubating"
        h.status = "incubating"
    sp_id = s.scalar(select(LabSpec.id).where(LabSpec.hypothesis_id == h.id).order_by(LabSpec.id))
    if sp_id is None or sp_id in have:
        return 0
    s.add(LabBook(spec_id=sp_id, book=NEWS_CORE, status="incubating", started_at=now))
    return 1


def trade_dict(t: Trade) -> dict[str, Any]:
    return {
        "key": t.signal.key,
        "symbol": t.signal.symbol,
        "sector": t.signal.sector,
        "direction": t.signal.direction,
        "option_type": t.option_type,
        "expiry": t.expiry.isoformat(),
        "strike": t.strike,
        "entry_day": t.entry_day.isoformat(),
        "exit_day": t.exit_day.isoformat() if t.exit_day else None,
        "exit_reason": t.exit_reason,
        "entry_ask": t.entry_ask,
        "pnl_pct": t.pnl_pct,
        "pnl_inr": t.pnl_inr,
        "fillability": t.fillability,
        "outlay": t.outlay,
        "marks": [(d.isoformat(), round(v, 2)) for d, v in t.marks],
        "mode": EOD,
    }


def eod_update(
    con: duckdb.DuckDBPyConnection,
    engine: Engine | None = None,
    data: HistoryData | None = None,
    today: date | None = None,
) -> int:
    """Re-run every active book's spec on [book start, today] with EOD prices; store on its
    `eod` trial (one per book, replaced each evening)."""
    data = data or HistoryData(con)
    today = today or (data.calendar[-1] if data.calendar else datetime.now(IST).date())
    with session_scope(engine) as s:
        books = [
            (b.id, b.spec_id, b.started_at)
            for b in s.scalars(
                select(LabBook).where(LabBook.status.in_(("incubating", "validated")))
            )
        ]
    n = 0
    for bid, spec_id, started in books:
        with session_scope(engine) as s:
            sp = s.get(LabSpec, spec_id)
            assert sp is not None
            spec = parse(sp.spec_yaml)
        start = started.astimezone(IST).date()
        res = run_spec(
            con, spec, start, today + timedelta(days=1), data, RunOptions(walk_forward=False)
        )
        trades = [trade_dict(t) for t in res.trades]
        closed = [t for t in res.trades if t.exit_day is not None and t.exit_reason not in ("END",)]
        st = V.stats(closed)
        with session_scope(engine) as s:
            trial = s.scalars(
                select(LabTrial).where(LabTrial.spec_id == spec_id, LabTrial.kind == "eod")
            ).first()
            if trial is None:
                trial = LabTrial(spec_id=spec_id, kind="eod", label=EOD)
                s.add(trial)
            trial.period_start, trial.period_end = start, today
            trial.n_trades, trial.n_events = st["n"], st["events"]
            trial.expectancy_pct, trial.ci_lo, trial.ci_hi = (
                st["expectancy_pct"],
                st["ci_lo"],
                st["ci_hi"],
            )
            trial.win_rate, trial.sharpe = st["win_rate"], st["sharpe"]
            trial.metrics_json = {
                "mode": EOD,
                "trades": trades,
                "open": len(res.trades) - len(closed),
            }
            trial.created_at = datetime.now(IST)
            b = s.get(LabBook, bid)
            assert b is not None
            b.stats_json = {
                **(b.stats_json or {}),
                EOD: {k: st[k] for k in ("n", "events", "expectancy_pct", "win_rate")},
            }
        n += 1
    return n


def real_trades(s: Session, book: str) -> list[PaperTrade]:
    """Paper-engine trades of a book (real fills from a broker data source). news_core = main."""
    key = "main" if book == NEWS_CORE else book
    return list(s.scalars(select(PaperTrade).where(PaperTrade.book == key)))


def cusum_alarm(pcts: list[float], mean0: float, sd0: float) -> bool:
    """One-sided CUSUM for a drop of the per-trade mean below the backtest's."""
    c = bcfg()["cusum"]
    if sd0 <= 0:
        return False
    k, h, s_ = float(c["k"]), float(c["h"]), 0.0
    for x in pcts:
        s_ = max(0.0, s_ + (mean0 - x) / sd0 - k)
        if s_ > h:
            return True
    return False


def review_books(engine: Engine | None = None, now: datetime | None = None) -> dict[str, int]:
    """Promote (incubation passed -> validated with real fills) or retire books."""
    now = now or datetime.now(IST)
    bc = bcfg()
    out = {"validated": 0, "retired": 0, "suggested": 0}
    with session_scope(engine) as s:
        for b in s.scalars(select(LabBook).where(LabBook.status.in_(("incubating", "validated")))):
            if b.book == NEWS_CORE:
                continue
            eod = s.scalars(
                select(LabTrial).where(LabTrial.spec_id == b.spec_id, LabTrial.kind == "eod")
            ).first()
            st0 = s.scalars(
                select(LabTrial).where(LabTrial.spec_id == b.spec_id, LabTrial.kind == "stage0")
            ).first()
            eod_pcts = (
                [
                    t["pnl_pct"]
                    for t in ((eod.metrics_json or {}).get("trades") or [])
                    if t["exit_day"]
                ]
                if eod
                else []
            )
            real = [
                t for t in real_trades(s, b.book) if t.status == "CLOSED" and t.pnl_pct is not None
            ]
            real_pcts = [float(t.pnl_pct or 0.0) for t in real]
            live = real_pcts or eod_pcts
            why = _retire_reason(live, st0, bc)
            if why:
                b.status, b.retired_at, b.retire_reason = "retired", now, why
                _verdict(s, b.spec_id, "retired", False, {"reason": why, "n": len(live)})
                sp = s.get(LabSpec, b.spec_id)
                if sp:
                    sp.stage = "retired"
                out["retired"] += 1
                continue
            if b.status == "incubating":
                inc_ok = (
                    len(eod_pcts) >= int(bc["incubation_min_trades"])
                    and float(np.mean(eod_pcts)) > 0
                )
                real_ok = (
                    len(real_pcts) >= int(bc["real_fill_min_trades"])
                    and float(np.mean(real_pcts)) > 0
                )
                checks = {
                    "eod_incubation": {
                        "passed": inc_ok,
                        "n": len(eod_pcts),
                        "need": int(bc["incubation_min_trades"]),
                        "expectancy_pct": round(float(np.mean(eod_pcts)), 2) if eod_pcts else None,
                    },
                    "real_fill_confirmation": {
                        "passed": real_ok,
                        "n": len(real_pcts),
                        "need": int(bc["real_fill_min_trades"]),
                        "expectancy_pct": round(float(np.mean(real_pcts)), 2)
                        if real_pcts
                        else None,
                    },
                }
                if inc_ok and not s.scalar(
                    select(LabVerdict.id).where(
                        LabVerdict.spec_id == b.spec_id, LabVerdict.stage == "incubation"
                    )
                ):
                    _verdict(s, b.spec_id, "incubation", True, checks)
                if inc_ok and real_ok:
                    b.status, b.validated_at = "validated", now
                    sp = s.get(LabSpec, b.spec_id)
                    if sp:
                        sp.stage = "validated"
                    _verdict(s, b.spec_id, "validated", True, checks)
                    out["validated"] += 1
                    out["suggested"] += suggest_promotion(s, b, checks, now)
    return out


def _retire_reason(live: list[float], st0: LabTrial | None, bc: dict[str, Any]) -> str | None:
    if len(live) < int(bc["retire_min_trades"]) or st0 is None:
        return None
    mean = float(np.mean(live))
    if (
        bc.get("retire_below_backtest_ci", True)
        and st0.ci_lo is not None
        and mean < float(st0.ci_lo)
    ):
        return (
            f"live expectancy {mean:+.2f}% below the backtest's 90% interval "
            f"(from {st0.ci_lo:+.2f}%)"
        )
    if st0.expectancy_pct is not None and st0.sharpe:
        sd0 = abs(float(st0.expectancy_pct) / float(st0.sharpe))
        if cusum_alarm(live, float(st0.expectancy_pct), sd0):
            return "decay detected (CUSUM on per-trade results vs the backtest mean)"
    return None


def _verdict(s: Session, spec_id: int, stage: str, passed: bool, checks: dict[str, Any]) -> None:
    s.add(
        LabVerdict(
            spec_id=spec_id,
            stage=stage,
            passed=passed,
            checks_json={"checks": checks},
            summary=f"{stage}: {'passed' if passed else 'failed'}",
            created_at=datetime.now(IST),
        )
    )


def suggest_promotion(s: Session, b: LabBook, checks: dict[str, Any], now: datetime) -> int:
    """A learning-loop suggestion to put the strategy into an empty promotion slot. The owner
    accepts or rejects it on the dashboard; an accepted one becomes a GitHub PR (one merge a
    month)."""
    from app.analytics.learning import month_start
    from app.yaml_config import load_yaml

    slots = load_yaml("promoted_strategies.yaml")["slots"]
    if b.spec_id in slots.values():
        return 0
    free = next((k for k, v in slots.items() if not v), None)
    if free is None:
        return 0
    sp = s.get(LabSpec, b.spec_id)
    s.add(
        Suggestion(
            month=month_start(now.date()),
            kind="strategy",
            param=(sp.name if sp else b.book)[:64],
            file="worker/config/promoted_strategies.yaml",
            yaml_path=f"slots.{free}",
            current_value=0,
            proposed_value=float(b.spec_id),
            expected_effect=float(
                (checks.get("real_fill_confirmation") or {}).get("expectancy_pct") or 0
            ),
            status="pending",
            created_at=now,
            evidence_json={
                "reason": f"Strategy Lab: {sp.name if sp else b.book} validated",
                **checks,
                "spec_yaml": sp.spec_yaml if sp else None,
            },
        )
    )
    return 1


# ---- portfolio view and correlation --------------------------------------------------------
def portfolio(engine: Engine | None = None) -> dict[str, Any]:
    """All books combined on notional capital: same-direction overlaps count once (attributed
    to every book that wanted them); an opposite-direction trade on a stock already held is
    blocked and logged; one trade per stock at portfolio level."""
    with session_scope(engine) as s:
        books = {
            b.spec_id: b for b in s.scalars(select(LabBook).where(LabBook.status != "retired"))
        }
        rows: list[dict[str, Any]] = []
        for spec_id, b in books.items():
            eod = s.scalars(
                select(LabTrial).where(LabTrial.spec_id == spec_id, LabTrial.kind == "eod")
            ).first()
            for t in ((eod.metrics_json or {}).get("trades") or []) if eod else []:
                rows.append({**t, "book": b.book})
    rows.sort(key=lambda r: (r["entry_day"], r["book"]))
    held: dict[str, dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for r in rows:
        cur = held.get(r["symbol"])
        if cur and (cur["exit_day"] is None or r["entry_day"] <= cur["exit_day"]):
            if cur["direction"] == r["direction"]:
                cur["books"].append(r["book"])  # same trade: counted once, attributed to all
            else:
                blocked.append(
                    {
                        "book": r["book"],
                        "symbol": r["symbol"],
                        "day": r["entry_day"],
                        "reason": f"opposite of open {cur['direction']} trade "
                        f"({', '.join(cur['books'])})",
                    }
                )
            continue
        r2 = {**r, "books": [r["book"]]}
        held[r["symbol"]] = r2
        kept.append(r2)
    daily: dict[str, float] = defaultdict(float)
    for r in kept:
        prev = 0.0
        for d, v in r["marks"]:
            daily[d] += v - prev
            prev = v
    eq, curve = float(bcfg()["capital_inr"]), []
    for d in sorted(daily):
        eq += daily[d]
        curve.append((d, round(eq, 2)))
    return {
        "mode": EOD,
        "trades": len(kept),
        "blocked": blocked[-50:],
        "pnl_inr": round(sum(r["pnl_inr"] for r in kept if r["exit_day"]), 2),
        "equity": curve[-250:],
        "attribution": _attribution(kept),
        "correlation": correlation(rows),
    }


def _attribution(kept: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"trades": 0, "pnl_inr": 0.0})
    for r in kept:
        for b in r["books"]:  # an overlapping trade is attributed to every book that wanted it
            out[b]["trades"] += 1
            out[b]["pnl_inr"] = round(
                out[b]["pnl_inr"] + (r["pnl_inr"] if r["exit_day"] else 0.0), 2
            )
    return dict(out)


def correlation(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    """Pearson correlation of daily P&L between books (each book's own trades)."""
    series: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        prev = 0.0
        for d, v in r["marks"]:
            series[r["book"]][d] += v - prev
            prev = v
    names = sorted(series)
    days = sorted({d for s in series.values() for d in s})
    mat = {b: np.array([series[b].get(d, 0.0) for d in days]) for b in names}
    out: dict[str, dict[str, float | None]] = {}
    for a in names:
        out[a] = {}
        for b in names:
            x, y = mat[a], mat[b]
            ok = len(days) > 2 and x.std() > 0 and y.std() > 0
            out[a][b] = round(float(np.corrcoef(x, y)[0, 1]), 3) if ok else None
    return out
