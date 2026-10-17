"""Nightly data-quality checks (Phase 10). Results are stored in the config table
(`data_quality`, read by the dashboard) and alerted only when something fails."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select

from app.config import IST
from app.db.config_store import get_config, set_config
from app.db.models import EventCalendar, Instrument, NewsItem, OptionQuote, PaperTrade, Signal
from app.db.session import session_scope
from app.market.live import market_open
from app.notify.outbox import enqueue_alert
from app.signals.lifecycle import LIVE
from app.timeutil import as_ist
from app.yaml_config import load_yaml

CA_TYPES = ("split", "bonus", "demerger", "special_dividend")


def run_checks(engine: Engine | None = None, now: datetime | None = None) -> dict[str, list[str]]:
    now = now or datetime.now(IST)
    c = load_yaml("hardening.yaml")["quality"]
    out: dict[str, list[str]] = defaultdict(list)
    since = now - timedelta(hours=24)
    with session_scope(engine) as s:
        quotes = [
            q
            for q in s.scalars(
                select(OptionQuote).where(OptionQuote.ts >= since).order_by(OptionQuote.ts)
            )
        ]
        for q in quotes:
            name = f"{q.symbol} {q.strike:g}{q.type} {as_ist(q.ts):%d-%b %H:%M}"
            if q.bid is not None and q.ask is not None and q.bid > q.ask:
                out["bid_above_ask"].append(f"{name}: bid {q.bid} > ask {q.ask}")
            if (q.ltp is not None and q.ltp <= 0) or (q.ask is not None and q.ask <= 0):
                out["non_positive_premium"].append(f"{name}: ltp {q.ltp} ask {q.ask}")
            if q.iv is not None and not (float(c["iv_min"]) <= q.iv <= float(c["iv_max"])):
                out["iv_out_of_range"].append(f"{name}: IV {q.iv}")
        actions = {
            (e.symbol, e.event_date)
            for e in s.scalars(select(EventCalendar).where(EventCalendar.event_type.in_(CA_TYPES)))
        }
        last_spot: dict[str, tuple[datetime, float]] = {}
        for q in quotes:
            if q.spot is None:
                continue
            prev = last_spot.get(q.symbol)
            if (
                prev
                and prev[1]
                and abs(q.spot / prev[1] - 1) * 100 > float(c["spot_jump_pct"])
                and (q.symbol, as_ist(q.ts).date()) not in actions
            ):
                out["spot_jump"].append(
                    f"{q.symbol}: {prev[1]} -> {q.spot} at {as_ist(q.ts):%d-%b %H:%M} "
                    "without a corporate action"
                )
            last_spot[q.symbol] = (as_ist(q.ts), q.spot)
        watched = {g.symbol for g in s.scalars(select(Signal).where(Signal.status.in_(LIVE)))}
        watched |= {
            str(t.symbol) for t in s.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN"))
        }
        if market_open(now):
            fresh = now - timedelta(minutes=int(c["stale_chain_minutes"]))
            latest = {q.symbol: as_ist(q.ts) for q in quotes}
            for sym in sorted(watched):
                if latest.get(sym) is None or latest[sym] < fresh:
                    out["stale_chain"].append(
                        f"{sym}: last quote {latest[sym]:%H:%M}"
                        if sym in latest
                        else f"{sym}: no quote in 24 h"
                    )
        titles: dict[str, set[int | None]] = defaultdict(set)
        for n in s.scalars(
            select(NewsItem).where(
                NewsItem.fetched_at >= now - timedelta(hours=int(c["duplicate_title_hours"]))
            )
        ):
            if not n.source.startswith(("deep:", "kg:")):
                titles[n.title.strip().lower()].add(n.cluster_id)
        out["duplicate_clusters"] += [
            f"'{t[:80]}' in {len(cl)} clusters" for t, cl in titles.items() if len(cl) > 1
        ]
        lots = {i.symbol: i.lot_size for i in s.scalars(select(Instrument)) if i.is_fno}
        before = get_config(s, "quality.lot_sizes", None) or {}
        out["lot_size_change"] += [
            f"{k}: {before[k]} -> {v}" for k, v in lots.items() if k in before and before[k] != v
        ]
        set_config(s, "quality.lot_sizes", lots)
        out["expired_open_trades"] += [
            f"{t.symbol} {t.strike:g}{t.option_type} expiry {t.expiry}"
            for t in s.scalars(select(PaperTrade).where(PaperTrade.status == "OPEN"))
            if t.expiry and t.expiry < now.date()
        ]
        for g in s.scalars(select(Signal).where(Signal.created_at >= since)):
            age = (g.data_age_json or {}).get("quote_s")
            if age is not None and float(age) > float(c["max_signal_quote_age_s"]):
                out["stale_signal_quote"].append(
                    f"S-{g.id} {g.symbol}: quote {float(age):.0f}s old"
                )
        result: dict[str, Any] = {
            "checked_at": now.isoformat(),
            "issues": {k: v[:20] for k, v in out.items() if v},
            "counts": {k: len(v) for k, v in out.items() if v},
        }
        set_config(s, "data_quality", result)
    return dict(out)


def quality_job(engine: Engine | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now(IST)
    issues = {k: v for k, v in run_checks(engine, now).items() if v}
    if not issues:
        return 0
    text = "DATA QUALITY\n" + "\n".join(
        f"- {k}: {len(v)} (e.g. {v[0]})" for k, v in sorted(issues.items())
    )
    with session_scope(engine) as s:
        enqueue_alert(s, "health", text, f"quality:{now.date()}")
    return sum(len(v) for v in issues.values())
