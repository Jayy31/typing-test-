"""Strategy Lab real-fill signals (Phase 14). Runs only when a broker data source is available.

For every active strategy book (and every strategy promoted into the main engine through a merged
learning-loop PR, config/promoted_strategies.yaml), new tagged events that match the spec trigger
and filters become LAB signals: the spec's strike rule on the live chain, the monthly-expiry core
rule, the spec's exit rules. The paper engine trades them in book `lab:<spec id>` (or `main` for a
promoted strategy) with the same core-rules guard. Portfolio level: an opposite-direction request
on a stock already held in any book is blocked and logged; same-direction overlaps are allowed per
book and counted once in the portfolio view (app/lab/books.py). Scheduled pre-event entries run in
EOD incubation only (documented gap).
"""

import logging
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Instrument, LabBook, LabSpec, NewsTag, PaperTrade, Signal
from app.lab.backtest import matches
from app.lab.spec import StrategySpec, parse
from app.market.options import OptionChain, OptionDataSource, OptionQuoteRow
from app.signals.expiry import trading_days_between
from app.signals.filters import load_holidays
from app.trading import core_rules, watchlist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
NEWS_CORE = "news_core"


def promoted() -> set[int]:
    return {int(v) for v in load_yaml("promoted_strategies.yaml")["slots"].values() if v}


def _direction(spec: StrategySpec, tag: NewsTag) -> str | None:
    if spec.trigger.direction in ("bullish", "bearish"):
        return spec.trigger.direction
    d = (tag.direction or "").lower()
    if d in ("bullish", "bearish"):
        return d
    if tag.sentiment:
        return "bullish" if tag.sentiment > 0 else "bearish"
    return None


def _filters_ok(
    s: Session, spec: StrategySpec, sym: str, sector: str | None, now: datetime
) -> str | None:
    f = spec.filters
    if f.symbols and sym not in f.symbols:
        return "symbol"
    if f.sectors and sector not in f.sectors:
        return "sector"
    if sector in f.exclude_sectors:
        return "excluded sector"
    if f.regimes:
        from app.regime.service import current_in

        reg = current_in(s, now.date())
        label = reg.label if reg else "unknown/unknown"
        if not (set(label.split("/")) | {label}) & set(f.regimes):
            return f"regime {label}"
    if f.positioning:
        from app.positioning.service import latest

        snap = latest(s, sym)
        states = (snap.states_json if snap else {}) or {}
        for feat, allowed in f.positioning.items():
            st = states.get(feat)
            if not st or st[0] not in allowed:
                return f"positioning {feat}"
    return None


def _expiry(source: OptionDataSource, sym: str, today: Any, holidays: set[Any]) -> Any:
    monthly = [e for e in core_rules.monthly_expiries(source.expiries(sym)) if e >= today]
    if not monthly:
        return None
    min_td = int(load_yaml("signals.yaml")["expiry"]["min_trading_days"])
    if trading_days_between(today, monthly[0], holidays) < min_td:
        return monthly[1] if len(monthly) > 1 else None
    return monthly[0]


def _strike(spec: StrategySpec, chain: OptionChain, opt: str) -> OptionQuoteRow | None:
    rows = sorted(
        (r for r in chain.rows if r.option_type == opt and r.ask and r.bid), key=lambda r: r.strike
    )
    if not rows:
        return None
    i = min(range(len(rows)), key=lambda j: abs(rows[j].strike - chain.spot))
    if spec.option.strike == "delta":
        from app.config import get_settings
        from app.pricing.bs import greeks

        t = max((chain.expiry - chain.ts.date()).days, 1) / 365
        target = float(spec.option.target_delta or 0.5)
        return min(
            rows,
            key=lambda r: abs(
                abs(
                    greeks(
                        chain.spot,
                        r.strike,
                        t,
                        get_settings().risk_free_rate,
                        (r.iv or 30.0) / 100,
                        opt,
                    ).delta
                )
                - target
            ),
        )
    step = {"atm": 0, "itm1": 1, "itm2": 2, "otm1": -1}[spec.option.strike]
    j = i - step if opt == "CE" else i + step
    return rows[j] if 0 <= j < len(rows) else None


def _opposite_open(s: Session, sym: str, opt: str) -> str | None:
    row = s.execute(
        select(PaperTrade.book, PaperTrade.option_type).where(
            PaperTrade.symbol == sym, PaperTrade.status == "OPEN", PaperTrade.option_type != opt
        )
    ).first()
    return row[0] if row else None


def make_signals(s: Session, source: OptionDataSource, now: datetime) -> int:
    """New LAB signals for active strategy books (real fills). Returns signals made."""
    if not getattr(source, "available", False):
        return 0
    prom = promoted()
    books = list(s.scalars(select(LabBook).where(LabBook.status.in_(("incubating", "validated")))))
    targets: list[tuple[LabBook | None, int, str]] = [
        (b, b.spec_id, "main" if b.spec_id in prom else b.book)
        for b in books
        if b.book != NEWS_CORE
    ]
    for sid in prom - {b.spec_id for b in books}:
        targets.append((None, sid, "main"))
    holidays = load_holidays(s)
    max_tag = int(s.scalar(select(func.max(NewsTag.id))) or 0)
    made = 0
    for b, spec_id, book in targets:
        sp = s.get(LabSpec, spec_id)
        if sp is None or sp.stage == "retired":
            continue
        spec = parse(sp.spec_yaml)
        stats = dict((b.stats_json if b else None) or {})
        cursor = int(stats.get("live_cursor", max_tag))  # a new book starts from now: no backfill
        if spec.entry.timing == "before_event":
            stats["live_cursor"] = max_tag
            if b:
                b.stats_json = stats
            continue
        for tag in s.scalars(
            select(NewsTag)
            .where(NewsTag.id > cursor, NewsTag.symbol.is_not(None))
            .order_by(NewsTag.id)
        ):
            if not matches(spec, tag.event_type):
                continue
            inst = s.get(Instrument, tag.symbol)
            if inst is None or not inst.is_fno or core_rules.is_index(inst.symbol):
                continue
            direction = _direction(spec, tag)
            if direction is None or _filters_ok(s, spec, inst.symbol, inst.sector, now):
                continue
            opt = StrategySpec.option_type(direction)
            other = _opposite_open(s, inst.symbol, opt)
            if other:
                blocked = list(stats.get("blocked", []))[-49:]
                blocked.append(
                    {
                        "ts": now.isoformat(),
                        "symbol": inst.symbol,
                        "direction": direction,
                        "reason": f"opposite of an open trade in book {other}",
                    }
                )
                stats["blocked"] = blocked
                continue
            exp = _expiry(source, inst.symbol, now.date(), holidays)
            chain = source.chain(inst.symbol, exp) if exp else None
            row = _strike(spec, chain, opt) if chain else None
            if exp is None or row is None:
                continue
            if core_rules.violation(inst.symbol, direction, opt, exp, now.date()):
                continue
            enter_after = None
            if spec.entry.timing == "next_open":
                enter_after = datetime.combine(
                    now.date() + timedelta(days=1), time(9, 15), now.tzinfo
                )
            sig = Signal(
                symbol=inst.symbol,
                sector=inst.sector,
                direction="CALL" if opt == "CE" else "PUT",
                strike=row.strike,
                expiry=exp,
                option_type=opt,
                score=0.0,
                status=watchlist.LAB_STATUS,
                book=book,
                pricing_status="priced",
                enter_after=enter_after,
                created_at=now,
                updated_at=now,
                flags_json={
                    "lab": {
                        "spec_id": spec_id,
                        "spec": spec.name,
                        "rules": spec.exits.overrides(),
                        "max_hold_days": spec.exits.max_hold_days,
                        "iv_spike_pts": spec.exits.iv_spike_exit_pts,
                        "tag_id": tag.id,
                        "mode": "real_fills",
                    }
                },
            )
            s.add(sig)
            made += 1
            if b is not None and b.status == "validated" and b.alerts_enabled:
                s.flush()
                _alert(s, sig, spec, b)
        stats["live_cursor"] = max_tag
        if b:
            b.stats_json = stats
    return made


def _alert(s: Session, sig: Signal, spec: StrategySpec, b: LabBook) -> None:
    """Validated strategies may send signal alerts (switch per strategy on the dashboard,
    default off). Incubating strategies never alert; they appear in the digests only."""
    from app.notify.outbox import enqueue_alert

    text = (
        f"STRATEGY SIGNAL ({spec.name}, validated, paper only)\n{sig.symbol} "
        f"{'CALL' if sig.option_type == 'CE' else 'PUT'} {sig.strike:g} exp {sig.expiry}\n"
        f"Book {b.book}; entry at the ask in the next paper-engine cycle."
    )
    enqueue_alert(s, "signal", text, f"lab_signal:{sig.id}", sig.id)
