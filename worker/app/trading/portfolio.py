"""Notional paper portfolio (v2.1 A6) and theme exposure (A5)."""

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import IST, get_settings
from app.db.models import PaperTrade, PortfolioSnapshot
from app.db.session import session_scope
from app.market.live import market_open
from app.pricing.bs import greeks
from app.trading.exits import state_from_json
from app.yaml_config import load_yaml


def open_value(tr: PaperTrade) -> tuple[float, float, float]:
    """(unrealised Rs incl. booked parts, premium at risk Rs, delta-equivalent notional Rs)."""
    if not tr.state_json or not tr.lot_size:
        return 0.0, 0.0, 0.0
    st = state_from_json(tr.state_json)
    units = tr.lot_size * tr.lots
    mark = tr.last_premium if tr.last_premium is not None else tr.entry_premium
    booked = st.realised_pct() / 100 * tr.entry_premium * units
    unreal = booked + st.remaining * (mark - tr.entry_premium) * units
    at_risk = st.remaining * mark * units
    delta_notional = 0.0
    attr = (tr.attribution_json or {}).get("last")
    if attr and tr.strike and tr.option_type:
        g = greeks(
            attr["spot"],
            tr.strike,
            max(attr["t_years"], 1e-6),
            get_settings().risk_free_rate,
            max(attr["iv_pct"], 1.0) / 100,
            tr.option_type,
        )
        delta_notional = g.delta * attr["spot"] * units * st.remaining
    return unreal, at_risk, delta_notional


def capital_used(tr: PaperTrade) -> float:
    """Premium paid for the lots still open (whole lots)."""
    if not tr.state_json or not tr.lot_size:
        return 0.0
    open_lots = int(round(state_from_json(tr.state_json).remaining * tr.lots))
    return tr.entry_premium * tr.lot_size * open_lots


def exposure(s: Session) -> dict[str, dict[str, Any]]:
    """Per theme: open trades, premium at risk and delta-equivalent notional."""
    out: dict[str, dict[str, Any]] = {}
    for tr in s.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN")):
        _, risk, dn = open_value(tr)
        e = out.setdefault(
            tr.theme_key or "unknown",
            {"trades": 0, "at_risk_inr": 0.0, "delta_notional_inr": 0.0, "symbols": []},
        )
        e["trades"] += 1
        e["at_risk_inr"] = round(e["at_risk_inr"] + risk, 2)
        e["delta_notional_inr"] = round(e["delta_notional_inr"] + dn, 2)
        e["symbols"] = sorted({*e["symbols"], str(tr.symbol)})
    return out


def snapshot(engine: Engine | None = None, now: datetime | None = None, force: bool = False) -> int:
    """Equity = capital + realised (after costs) + open value; drawdown from the running peak."""
    now = now or datetime.now(IST)
    if not force and not market_open(now):
        return 0
    capital = float(load_yaml("portfolio.yaml")["notional_capital_inr"])
    with session_scope(engine) as s:
        closed = select(func.coalesce(func.sum(PaperTrade.pnl), 0.0)).where(
            PaperTrade.status == "CLOSED"
        )
        realised = float(s.scalar(closed) or 0.0)
        realised_fillable = float(
            s.scalar(closed.where(PaperTrade.fillability != "unfillable")) or 0.0
        )
        opens = s.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN")).all()
        vals = [open_value(t) for t in opens]
        unreal = sum(v[0] for v in vals)
        equity = capital + realised + unreal
        peak = max(
            float(s.scalar(select(func.max(PortfolioSnapshot.peak_equity))) or capital), equity
        )
        s.merge(
            PortfolioSnapshot(
                ts=now.replace(second=0, microsecond=0),
                capital=capital,
                realised_inr=round(realised, 2),
                unrealised_inr=round(unreal, 2),
                equity=round(equity, 2),
                peak_equity=round(peak, 2),
                drawdown_pct=round((equity / peak - 1) * 100, 3) if peak else 0.0,
                open_trades=len(opens),
                premium_at_risk_inr=round(sum(v[1] for v in vals), 2),
                exposure_json=exposure(s),
                realised_fillable_inr=round(realised_fillable, 2),
                capital_used_inr=round(sum(capital_used(t) for t in opens), 2),
            )
        )
    return 1


def snapshot_job() -> int:
    return snapshot()


def eod_snapshot_job() -> int:
    return snapshot(force=True)
