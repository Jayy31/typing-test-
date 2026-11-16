"""Accuracy audit 2026-11 (docs/AUDIT_FIXES.md), group A: live trading and trade selection."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from sqlalchemy import Engine, select

from app.config import IST, get_settings
from app.db.models import PaperTrade, Signal
from app.db.session import session_scope
from app.history.analogues import AnalogueResult
from app.market.options import OptionChain, OptionQuoteRow
from app.signals.candidates import Candidate, EvidenceItem
from app.signals.deferred import next_open
from app.signals.expiry import trading_days_between
from app.signals.prediction import combine
from app.sim.simulator import simulate_stock
from app.trading.paper_engine import _quote_moved, trading_cycle
from app.yaml_config import load_yaml
from tests.helpers import analyst_output
from tests.test_options_stage import FakeSource, run
from tests.test_paper_trading import _paths, make_signal, trade, weekday_at  # noqa: F401


class NoDepth(FakeSource):
    """Like Kotak outside the depth window: chain rows carry no bid/ask; with_depth supplies them."""

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain:
        ch = super().chain(symbol, expiry, as_of)
        rows = tuple(replace(r, bid=None, ask=None, bid_qty=None, ask_qty=None) for r in ch.rows)
        return OptionChain(ch.symbol, ch.expiry, ch.spot, ch.ts, rows)

    def with_depth(self, row: OptionQuoteRow) -> OptionQuoteRow:
        assert row.ltp
        return replace(
            row, bid=max(row.ltp - 0.5, 0.05), ask=row.ltp + 0.5, bid_qty=9000, ask_qty=9000
        )


def test_a1_trade_outside_depth_window_is_still_monitored(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    assert trading_cycle(engine, src, now) == 1
    far = NoDepth(spot=3700.0)  # stock collapses; the chain has no bid for our strike any more
    trading_cycle(engine, far, now + timedelta(minutes=10))
    tr = trade(engine)
    assert tr.status == "CLOSED" and tr.exit_reason in ("SL", "SPOT_SL", "STOP")


class Expired:
    available = True

    def __init__(self, spot: float) -> None:
        self._spot = spot

    def expiries(self, symbol: str, as_of: date | None = None) -> list[date]:
        return []

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> None:
        return None

    def quote(self, symbol: str) -> None:
        return None

    def spot(self, symbol: str, as_of: date | None = None) -> float:
        return self._spot


def test_a1_expired_trade_settles_at_intrinsic(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    tr = trade(engine)
    after = datetime.combine(tr.expiry + timedelta(days=1), now.timetz())  # type: ignore[operator]
    while after.weekday() >= 5:
        after += timedelta(days=1)
    trading_cycle(engine, Expired(tr.strike + 37.0), after)  # type: ignore[arg-type, operator]
    tr = trade(engine)
    assert tr.status == "CLOSED" and tr.exit_reason == "EXPIRY"
    last = (tr.state_json or {})["fills"][-1]
    assert last["price"] == pytest.approx(37.0 if tr.option_type == "CE" else 0.0)


class NoIv(FakeSource):
    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain:
        ch = super().chain(symbol, expiry, as_of)
        rows = tuple(replace(r, iv=None) for r in ch.rows)
        return OptionChain(ch.symbol, ch.expiry, ch.spot, ch.ts, rows)

    def with_depth(self, row: OptionQuoteRow) -> OptionQuoteRow:
        return row


def test_a2_quote_without_iv_is_never_priced(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.signals import options_stage

    monkeypatch.setattr(options_stage, "atm_iv", lambda chain: 8.0)  # ATM IV known, rows not
    run(engine, NoIv())
    with session_scope(engine) as s:
        assert s.scalars(select(Signal).where(Signal.pricing_status == "priced")).first() is None


def test_a3_market_paths_drift_at_the_risk_free_rate() -> None:
    get_settings.cache_clear()
    scen = analyst_output().scenarios
    days, vol = 20, 0.30 / 252**0.5
    p = simulate_stock(1000.0, scen, vol, days, 40000, 7, ai_weight=0.0)
    r = float(get_settings().risk_free_rate)
    assert float(np.mean(p.spot[:, -1])) == pytest.approx(1000 * np.exp(r * days / 252), rel=0.004)


def _an(median: float, hit: float) -> AnalogueResult:
    return AnalogueResult("event_type", "all", 3, 80, 80.0, median, median, hit, 2.0, -1.5, 1.8)


def test_a4_p_move_uses_the_measured_hit_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALOGUE_MIN_N", "5")
    monkeypatch.setenv("ANALOGUE_PRIOR_N", "20")
    get_settings.cache_clear()
    scen = analyst_output().scenarios
    up, _ = combine(scen, "bullish", _an(0.1, 0.52), True)
    dn, _ = combine(scen, "bullish", _an(-0.1, 0.48), True)
    assert up and dn and up.p_move is not None and dn.p_move is not None
    assert abs(up.p_move - dn.p_move) < 0.05  # was a ~0.3 jump on the sign of the median
    against, _ = combine(scen, "bullish", _an(0.1, 0.52), False)
    assert against and against.p_move == pytest.approx(up.p_move - 0.8 * 0.04, abs=1e-3)
    get_settings.cache_clear()


def test_a5_flipped_graph_stats() -> None:
    f = replace(_an(-1.2, 0.35), p25=-3.0, p75=0.5).flipped()
    assert (f.mean, f.median, f.p25, f.p75) == (1.2, 1.2, -0.5, 3.0)
    assert f.hit_rate == pytest.approx(0.65)


def _item(source: str, etype: str, sentiment: float, weight: float) -> EvidenceItem:
    return EvidenceItem(
        1, 1, source, "news", "t", None, datetime.now(IST), sentiment, 0.8, etype, (), weight
    )


def test_a6_a7_event_type_order_and_support_rows() -> None:
    c = Candidate(
        "TCS",
        [
            _item("moneycontrol", "results", 0.8, 1.0),
            _item("moneycontrol", "block_deal", 0.2, 0.3),
            _item("kg:1:customer_of:base_rate:support", "kg:customer_of:results", 0.9, 1.0),
        ],
    )
    types = c.ranked_event_types()
    assert types[0] == "results" and types[-1].startswith("kg:")
    assert c.sources == {"moneycontrol"}  # the support row is not a second source


def test_a8_current_month_is_traded_when_valid(engine: Engine) -> None:
    src = FakeSource()
    run(engine, src)
    min_td = int(load_yaml("signals.yaml")["expiry"]["min_trading_days"])
    today = datetime.now(IST).date()
    want = next(e for e in src.exp if trading_days_between(today, e, set()) >= min_td)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.pricing_status == "priced" and sig.expiry == want


def test_a10_entry_recheck() -> None:
    row = OptionQuoteRow(
        "X", date(2026, 11, 24), 100.0, "CE", 10.0, 10.4, 10.2, 20.0, 1, 1, 100.0, datetime.now(IST)
    )
    cfg: dict[str, Any] = {"recheck": {"enabled": True, "max_premium_drift_pct": 15}}
    assert not _quote_moved(row, {"best": {"ask": 10.0}}, cfg)
    assert _quote_moved(row, {"best": {"ask": 8.0}}, cfg)  # ask ran up 30% since pricing
    assert _quote_moved(replace(row, bid=6.0), {"best": {"ask": 10.0}}, cfg)  # spread blew out
    assert not _quote_moved(replace(row, bid=6.0), {}, {"recheck": {"enabled": False}})


def test_a11a_next_open_skips_the_weekend() -> None:
    friday = datetime(2026, 11, 20, 23, 59, tzinfo=IST)
    assert next_open(friday, set()).date() == date(2026, 11, 23)


def test_a11f_day_rules_wait_for_the_late_session(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    with session_scope(engine) as s:
        tr = s.scalars(select(PaperTrade)).one()
        rules = dict(tr.rules_json or {})
        rules["time_stop_trading_days"] = trading_days_between(now.date(), tr.expiry, set())  # type: ignore[arg-type]
        tr.rules_json = rules
    trading_cycle(engine, src, now + timedelta(minutes=5))
    assert trade(engine).status == "OPEN"  # 11:05: today is not a completed session yet
    trading_cycle(engine, src, now.replace(hour=15, minute=5))
    tr2 = trade(engine)
    assert tr2.status == "CLOSED" and tr2.exit_reason == "TIME"
