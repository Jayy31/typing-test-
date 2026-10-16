"""Paper trading engine (Phase 3b). Paper only: nothing here can reach a broker order API.

`trading_cycle` runs every OPTION_POLL_ACTIVE_MIN in market hours:
  1. fetch option chains for live priced signals and open trades (stored in option_quotes)
  2. enter eligible signals at the live ask (1 lot), with entry rules and a fill check
  3. step every open trade through the shared exit rules with the live bid and spot
Every state change is a trade_events row and (for trade events) a Telegram alert.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import IST, get_settings
from app.db.models import (
    Analysis,
    EventCalendar,
    Instrument,
    PaperTrade,
    Signal,
    SignalVersion,
    TradeEvent,
)
from app.db.session import session_scope
from app.market.live import UpstoxOptionData, market_open, store_chain
from app.market.options import OptionChain, OptionQuoteRow
from app.notify import templates
from app.notify.outbox import enqueue_alert
from app.signals.expiry import trading_days_between
from app.signals.filters import load_holidays
from app.signals.lifecycle import LIVE
from app.signals.rejections import record_rejection
from app.timeutil import as_ist
from app.trading import fills
from app.trading.attribution import Attribution, Obs
from app.trading.costs import trade_costs_inr
from app.trading.exits import (
    Bar,
    ExitRules,
    PositionState,
    rules_from_json,
    rules_hash,
    rules_to_json,
    state_from_json,
    state_to_json,
    step,
)
from app.trading.targets import targets_for
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
OPEN = "OPEN"


# ---- data ---------------------------------------------------------------------------
def fetch_chains(
    source: UpstoxOptionData, wanted: set[tuple[str, date]], engine: Engine | None
) -> dict[tuple[str, date], OptionChain]:
    chains: dict[tuple[str, date], OptionChain] = {}
    for sym, exp in sorted(wanted):
        try:
            ch = source.chain(sym, exp)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the cycle
            log.warning("chain %s %s failed: %s", sym, exp, exc)
            continue
        if ch:
            chains[(sym, exp)] = ch
            with session_scope(engine) as s:
                store_chain(s, ch)
    return chains


def _side(option_type: str) -> int:
    return 1 if option_type == "CE" else -1


def _entry_window_open(now: datetime) -> bool:
    """No entries in the first N minutes after the open (v2.1 A4 a)."""
    mins = int(load_yaml("entry.yaml")["no_entry_first_minutes"])
    hours = load_yaml("options.yaml")["market_hours"]
    h, m = (int(x) for x in hours["open"].split(":"))
    start = datetime.combine(now.date(), time(h, m), IST) + timedelta(minutes=mins)
    return now >= start


def _options_json(s: Session, sig: Signal) -> dict[str, Any]:
    an = s.get(Analysis, sig.analysis_id) if sig.analysis_id else None
    return ((an.scores_json or {}).get("options") or {}) if an else {}


# ---- entries --------------------------------------------------------------------------
DepthFn = Callable[[OptionQuoteRow], OptionQuoteRow]


def _same(row: OptionQuoteRow) -> OptionQuoteRow:
    return row


def enter_signals(
    s: Session,
    chains: dict[tuple[str, date], OptionChain],
    now: datetime,
    depth: DepthFn = _same,
) -> list[int]:
    """Open a paper trade for each live, priced signal whose entry rules pass."""
    entry_cfg = load_yaml("entry.yaml")
    ttl = timedelta(minutes=int(entry_cfg["signal_entry_ttl_minutes"]))
    opened: list[int] = []
    sigs = s.scalars(
        select(Signal).where(Signal.status.in_(LIVE), Signal.pricing_status == "priced")
    ).all()
    for sig in sigs:
        assert sig.option_type and sig.strike and sig.expiry
        if now - as_ist(sig.created_at) > ttl:
            _cancel(s, sig, now, "entry_ttl", f"not entered within {ttl.seconds // 60} minutes")
            continue
        if not _entry_window_open(now):
            continue
        chain = chains.get((sig.symbol, sig.expiry))
        row = chain.get(sig.strike, sig.option_type) if chain else None
        if chain is None or row is None or not row.ask or not row.bid:
            continue
        opt = _options_json(s, sig)
        pre_news = opt.get("pre_news_spot")
        sign = _side(sig.option_type)
        pred = sig.predicted_move_pct or 0.0
        if pre_news and pred > 0:
            premove = sign * (chain.spot / pre_news - 1) * 100
            limit = float(entry_cfg["max_premove_fraction"])
            if premove / pred > limit:
                _cancel(
                    s,
                    sig,
                    now,
                    "entry",
                    f"by entry time the stock moved {premove:+.2f}% = {premove / pred:.0%} of "
                    f"the predicted {pred:.2f}% (limit {limit:.0%})",
                )
                continue
        opened.append(open_trade(s, sig, chain, depth(row), now, pre_news).id)
    return opened


def _cancel(s: Session, sig: Signal, now: datetime, stage: str, reason: str) -> None:
    sig.status, sig.updated_at = "CANCELLED", now
    s.add(
        SignalVersion(
            signal_id=sig.id,
            version=_next_version(s, sig.id),
            score=sig.score,
            status="CANCELLED",
            reason=reason,
            ts=now,
        )
    )
    record_rejection(
        s,
        dedupe_key=f"{stage}:{sig.symbol}:signal{sig.id}",
        ts=now,
        symbol=sig.symbol,
        stage=stage,
        reason=reason,
        direction="bullish" if sig.option_type == "CE" else "bearish",
        score=sig.score,
        signal_id=sig.id,
        analysis_id=sig.analysis_id,
        predicted_move_pct=sig.predicted_move_pct,
        ref_expiry=sig.expiry,
        ref_strike=sig.strike,
        ref_option_type=sig.option_type,
    )


def _next_version(s: Session, signal_id: int) -> int:
    from sqlalchemy import func

    return (
        int(
            s.scalar(
                select(func.max(SignalVersion.version)).where(SignalVersion.signal_id == signal_id)
            )
            or 0
        )
        + 1
    )


def open_trade(
    s: Session,
    sig: Signal,
    chain: OptionChain,
    row: OptionQuoteRow,
    now: datetime,
    pre_news_spot: float | None,
) -> PaperTrade:
    assert row.ask and sig.option_type and sig.strike and sig.expiry
    rules = ExitRules.from_config()
    inst = s.get(Instrument, sig.symbol)
    lot = int(inst.lot_size or 0) if inst else 0
    lots = int(load_yaml("portfolio.yaml")["lots_per_signal"])
    an = s.get(Analysis, sig.analysis_id) if sig.analysis_id else None
    window = int(an.window_days or 3) if an else 3
    targets = targets_for(
        row.ask,
        rules,
        spot=chain.spot,
        strike=sig.strike,
        option_type=sig.option_type,
        iv=(row.iv or 30.0) / 100,
        days_to_expiry=float((sig.expiry - now.date()).days),
        predicted_move_pct=float(sig.predicted_move_pct or 0.0),
        window_days=float(window),
        r=get_settings().risk_free_rate,
    )
    state = PositionState.open(
        row.ask,
        rules,
        targets,
        _side(sig.option_type),
        chain.spot,
        pre_news_spot,
        sig.predicted_move_pct,
        window,
    )
    entry_fill = fills.classify(row.ask_qty, lot * lots)
    attr = Attribution(spread=-(row.ask - (row.bid or row.ask)) * lot * lots)
    attr.add(_obs(row, chain, now), sig.strike, sig.option_type, get_settings().risk_free_rate, 0)
    trade = PaperTrade(
        signal_id=sig.id,
        entry_ts=now,
        entry_premium=row.ask,
        lots=lots,
        sl=state.stop,
        t1=targets[0],
        t2=targets[1] if len(targets) > 1 else targets[0],
        t3=targets[-1],
        status=OPEN,
        symbol=sig.symbol,
        option_type=sig.option_type,
        strike=sig.strike,
        expiry=sig.expiry,
        lot_size=lot,
        theme_key=sig.theme_key,
        rules_hash=rules_hash(rules),
        rules_json=rules_to_json(rules),
        state_json=state_to_json(state),
        last_premium=row.bid,
        last_spot=chain.spot,
        last_ts=now,
        entry_bid=row.bid,
        entry_ask=row.ask,
        entry_bid_qty=row.bid_qty,
        entry_ask_qty=row.ask_qty,
        entry_fill=entry_fill,
        fillability=entry_fill,
        spot_entry=chain.spot,
        pre_news_spot=pre_news_spot,
        mfe_pct=0.0,
        mae_pct=0.0,
        attribution_json=attr.to_json(),
    )
    s.add(trade)
    s.flush()
    sig.status, sig.updated_at = "CONVERTED_TO_TRADE", now
    s.add(
        SignalVersion(
            signal_id=sig.id,
            version=_next_version(s, sig.id),
            score=sig.score,
            status="CONVERTED_TO_TRADE",
            reason=f"paper trade T{trade.id} opened",
            ts=now,
        )
    )
    s.add(
        TradeEvent(
            trade_id=trade.id,
            ts=now,
            event="ENTRY",
            premium=row.ask,
            fraction=1.0,
            spot=chain.spot,
            bid_qty=row.bid_qty,
            ask_qty=row.ask_qty,
            note=f"entry at ask; fill check: {entry_fill}",
        )
    )
    enqueue_alert(
        s,
        "trade",
        templates.trade_entry(trade, sig, state.spot_stop),
        f"trade:{trade.id}:ENTRY",
        sig.id,
    )
    return trade


def _obs(row: OptionQuoteRow, chain: OptionChain, now: datetime) -> Obs:
    t = max((chain.expiry - now.date()).days, 0) / 365
    return Obs(now.isoformat(), chain.spot, row.bid or row.ltp or 0.0, row.iv or 30.0, t)


# ---- monitoring -----------------------------------------------------------------------
def monitor_trades(
    s: Session,
    chains: dict[tuple[str, date], OptionChain],
    now: datetime,
    depth: DepthFn = _same,
) -> int:
    """Step every open trade with the latest bid/spot. Returns number of trades that changed."""
    holidays = load_holidays(s)
    action = str(load_yaml("portfolio.yaml")["opposite_signal_action"])
    changed = 0
    for tr in s.scalars(select(PaperTrade).where(PaperTrade.status == OPEN)).all():
        assert tr.expiry and tr.strike and tr.option_type and tr.state_json is not None
        chain = chains.get((str(tr.symbol), tr.expiry))
        row = chain.get(tr.strike, tr.option_type) if chain else None
        if chain is None or row is None or not row.bid:
            continue
        thesis_broken = _opposite_signal(s, tr, now, action)
        rules = rules_from_json(tr.rules_json) if tr.rules_json else ExitRules.from_config()
        state = state_from_json(tr.state_json)
        bar = Bar(
            row.bid,
            row.bid,
            row.bid,
            row.bid,
            trading_days_to_expiry=trading_days_between(now.date(), tr.expiry, holidays),
            sessions_since_event=_sessions_since_event(s, tr, now.date(), holidays),
            thesis_broken=thesis_broken,
            spot=chain.spot,
            sessions_since_entry=trading_days_between(
                as_ist(tr.entry_ts).date(), now.date(), holidays
            ),
        )
        new = step(state, bar, rules)
        units = (tr.lot_size or 0) * tr.lots * state.remaining
        attr = Attribution.from_json(tr.attribution_json)
        attr.add(
            _obs(row, chain, now), tr.strike, tr.option_type, get_settings().risk_free_rate, units
        )
        tr.attribution_json = attr.to_json()
        tr.last_premium, tr.last_spot, tr.last_ts = row.bid, chain.spot, now
        move = (row.bid / tr.entry_premium - 1) * 100
        tr.mfe_pct = max(tr.mfe_pct or 0.0, move)
        tr.mae_pct = min(tr.mae_pct or 0.0, move)
        for f in new.fills[len(state.fills) :]:
            s.add(
                TradeEvent(
                    trade_id=tr.id,
                    ts=now,
                    event=f.event,
                    premium=f.price,
                    fraction=f.fraction,
                    spot=chain.spot,
                    bid_qty=row.bid_qty,
                    ask_qty=row.ask_qty,
                )
            )
            sig = s.get(Signal, tr.signal_id)
            enqueue_alert(
                s,
                "trade",
                templates.trade_event(tr, f.event, f.price, f.fraction, new),
                f"trade:{tr.id}:{f.event}:{len(new.fills)}",
                sig.id if sig else None,
            )
        if new.closed:
            close_trade(s, tr, new, depth(row), chain, now)
        tr.state_json = state_to_json(new)
        changed += int(new != state)
    return changed


def _sessions_since_event(
    s: Session, tr: PaperTrade, today: date, holidays: set[date]
) -> int | None:
    """Trading sessions since the first scheduled event for this stock on/after entry, if passed."""
    entry_day = as_ist(tr.entry_ts).date()
    ev = s.scalar(
        select(EventCalendar.event_date)
        .where(
            EventCalendar.symbol == tr.symbol,
            EventCalendar.event_date >= entry_day,
            EventCalendar.event_date <= today,
        )
        .order_by(EventCalendar.event_date)
    )
    return None if ev is None else trading_days_between(ev, today, holidays)


def _opposite_signal(s: Session, tr: PaperTrade, now: datetime, action: str) -> bool:
    """A live opposite signal on this stock (v2.1 A5): flag for review or close (config)."""
    opposite = "PE" if tr.option_type == "CE" else "CE"
    other = s.scalars(
        select(Signal).where(
            Signal.symbol == tr.symbol,
            Signal.status.in_(LIVE),
            Signal.option_type == opposite,
            Signal.created_at > tr.entry_ts,
        )
    ).first()
    if other is None:
        return False
    if action == "close":
        return True
    if not tr.review_flag:
        tr.review_flag = (
            f"opposite signal S-{other.id} ({other.option_type}) raised {now:%d-%b %H:%M}"
        )
        enqueue_alert(
            s,
            "trade",
            templates.trade_review_flag(tr, other.id),
            f"trade:{tr.id}:FLAG:{other.id}",
            other.id,
        )
    return False


def close_trade(
    s: Session,
    tr: PaperTrade,
    state: PositionState,
    row: OptionQuoteRow,
    chain: OptionChain,
    now: datetime,
) -> None:
    """Final P&L after costs, fill check, stock benchmark (v2.1 B6) and attribution residual."""
    lot, lots = tr.lot_size or 0, tr.lots
    gross_pct = state.realised_pct()
    orders = 1 + len(state.fills)
    costs = trade_costs_inr(tr.entry_premium, lot, lots, orders)
    gross_inr = gross_pct / 100 * tr.entry_premium * lot * lots
    tr.pnl = round(gross_inr - costs, 2)
    tr.pnl_pct = (
        round(tr.pnl / (tr.entry_premium * lot * lots) * 100, 3) if lot else round(gross_pct, 3)
    )
    weights = sum(f.fraction for f in state.fills) or 1.0
    tr.exit_premium = round(sum(f.price * f.fraction for f in state.fills) / weights, 2)
    tr.exit_ts, tr.exit_reason, tr.status = now, state.fills[-1].event, "CLOSED"
    tr.exit_bid_qty = row.bid_qty
    tr.exit_fill = fills.classify(row.bid_qty, lot * lots)
    tr.fillability = fills.worse(tr.entry_fill, tr.exit_fill)
    tr.spot_exit = chain.spot
    if tr.spot_entry and tr.option_type:
        bench = _side(tr.option_type) * (chain.spot / tr.spot_entry - 1) * 100
        tr.benchmark_stock_pct = round(bench, 3)
        tr.benchmark_stock_inr = round(bench / 100 * tr.spot_entry * lot * lots, 2)
    sig = s.get(Signal, tr.signal_id)
    enqueue_alert(
        s, "trade", templates.trade_closed(tr), f"trade:{tr.id}:CLOSED", sig.id if sig else None
    )


# ---- job ------------------------------------------------------------------------------
def trading_cycle(
    engine: Engine | None = None,
    source: UpstoxOptionData | None = None,
    now: datetime | None = None,
) -> int:
    """Job entrypoint (market hours only). Returns entries + trades changed."""
    now = now or datetime.now(IST)
    source = source or UpstoxOptionData(engine)
    if not source.available or not market_open(now):
        return 0
    with session_scope(engine) as s:
        wanted = {
            (g.symbol, g.expiry)
            for g in s.scalars(select(Signal).where(Signal.status.in_(LIVE)))
            if g.expiry
        }
        wanted |= {
            (str(t.symbol), t.expiry)
            for t in s.scalars(select(PaperTrade).where(PaperTrade.status == OPEN))
            if t.expiry
        }
    chains = fetch_chains(source, wanted, engine)
    with session_scope(engine) as s:
        depth: DepthFn = getattr(source, "with_depth", _same)
        opened = enter_signals(s, chains, now, depth)
        changed = monitor_trades(s, chains, now, depth)
    return len(opened) + changed


__all__ = ["replace", "trading_cycle"]
