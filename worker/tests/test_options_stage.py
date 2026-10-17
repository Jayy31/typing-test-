"""Phase 3a end to end: analysis -> options stage (fake source) -> priced signal or rejection."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app.ai.client import AiClient
from app.config import IST, Settings
from app.db.models import Alert, Analysis, RejectedCandidate, Signal, SignalCandidate
from app.db.session import session_scope
from app.market.options import OptionChain, OptionQuoteRow
from app.market.upstox import Quote
from app.pricing.bs import price
from app.signals.service import run_signal_cycle
from tests.helpers import FakeMessages, analyst_output, seed_instruments, seed_news
from tests.test_notify import Recorder, bot


class FakeSource:
    """Live-like source: chains priced with Black-Scholes at a flat IV."""

    def __init__(
        self,
        iv: float = 8.0,
        spot: float = 4000.0,
        prev_close: float = 3990.0,
        oi: int = 200_000,
        spread: float = 1.0,
        qty: int | None = 10_000,
    ) -> None:
        self.qty = qty
        self.iv, self.spot, self.prev, self.oi, self.spread = iv, spot, prev_close, oi, spread
        today = datetime.now(IST).date()
        self.exp = [today + timedelta(days=24), today + timedelta(days=52)]
        self.available = True

    def expiries(self, symbol: str, as_of: date | None = None) -> list[date]:
        return self.exp

    def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> OptionChain:
        now = datetime.now(IST)
        t = (expiry - now.date()).days / 365
        rows = []
        for k in range(3700, 4350, 50):
            for opt in ("CE", "PE"):
                mid = float(price(self.spot, k, t, 0.065, self.iv / 100, opt))
                rows.append(
                    OptionQuoteRow(
                        symbol,
                        expiry,
                        float(k),
                        opt,
                        max(mid - self.spread / 2, 0.05),
                        mid + self.spread / 2,
                        mid,
                        self.iv,
                        self.oi,
                        5000,
                        self.spot,
                        now,
                        self.qty,
                        self.qty,
                    )
                )
        return OptionChain(symbol, expiry, self.spot, now, tuple(rows))

    def quote(self, symbol: str) -> Quote:
        return Quote("k", self.spot, self.prev, None, None, datetime.now(IST))

    def spot(self, symbol: str, as_of: date | None = None) -> float:  # type: ignore[override]
        return self.spot


def run(engine: Engine, source: Any) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175})
    seed_news(engine)
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m", mc_paths=3000),
        engine,  # type: ignore[call-arg]
        FakeMessages(analyst_output()),
    )
    from app.config import get_settings

    get_settings.cache_clear()
    run_signal_cycle(engine, ai, source)


@pytest.fixture(autouse=True)
def _fewer_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MC_PATHS", "3000")
    from app.config import get_settings

    get_settings.cache_clear()


def test_priced_signal_with_strike_ev_and_candidates(engine: Engine) -> None:
    src = FakeSource()
    run(engine, src)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.pricing_status == "priced" and sig.option_type == "CE" and sig.strike
        assert (
            sig.expiry in src.exp
            and sig.expected_value is not None
            and sig.expected_value_inr is not None
        )
        assert 0 <= (sig.p_t1_before_sl or -1) <= 1 and sig.p_t3 is not None
        assert sig.implied_move_pct == pytest.approx(8 * (4.2 / 365) ** 0.5, abs=0.01)
        assert sig.edge_ratio == pytest.approx(1.425 / sig.implied_move_pct, abs=0.01)  # shrunk
        assert sig.edge_ratio_raw == pytest.approx(2.85 / sig.implied_move_pct, abs=0.01)
        assert sig.predicted_move_pct == pytest.approx(
            1.425
        ) and sig.predicted_move_raw_pct == pytest.approx(2.85)
        assert sig.shrink_weight == 0.5 and sig.expected_value_raw is not None
        assert (
            sig.expected_value_raw > sig.expected_value
        )  # AI alone is more optimistic than the blend
        assert sig.premove_pct == pytest.approx((4000 / 3990 - 1) * 100, abs=0.01)
        assert sig.news_to_signal_s is not None and "quote_s" in (sig.data_age_json or {})
        cands = s.scalars(select(SignalCandidate).where(SignalCandidate.signal_id == sig.id)).all()
        assert len(cands) == 12 and sum(c.chosen for c in cands) == 1  # 6 strikes x 2 expiries
        chosen = next(c for c in cands if c.chosen)
        assert chosen.ev_pct == max(c.ev_pct for c in cands if c.ev_pct is not None)
        an = s.scalars(select(Analysis)).one()
        comps = an.scores_json["components"]
        assert comps["liquidity"] != 70.0 and an.scores_json["priced_in_penalty"] > 0
        text = s.scalars(select(Alert)).one().text
    assert "Entry premium:" in text and "T1:" in text and "Edge ratio:" in text
    assert "P(T1 before SL)" in text and "not yet calibrated" in text
    why = bot(engine, Recorder()).reply_for("/why S-1")
    assert "Chosen: BHARTIARTL" in why and "Implied move" in why and "Latency:" in why


def test_edge_ratio_gate_rejects_and_records(engine: Engine) -> None:
    run(engine, FakeSource(iv=40.0))  # implied 4.3% > predicted 2.85% -> edge 0.66
    with session_scope(engine) as s:
        assert s.scalars(select(Signal)).all() == []
        rej = s.scalars(select(RejectedCandidate).where(RejectedCandidate.stage == "edge")).one()
        assert (
            "edge ratio" in rej.reason
            and rej.ref_strike
            and rej.ref_premium
            and rej.ref_option_type == "CE"
        )
        assert rej.predicted_move_pct == pytest.approx(1.425) and rej.outcome_status == "open"
        assert rej.details_json and rej.details_json["predicted_move_raw_pct"] == pytest.approx(
            2.85
        )


def test_premove_rule_rejects(engine: Engine) -> None:
    run(engine, FakeSource(prev_close=3850.0))  # already +3.9% vs predicted 2.85%
    with session_scope(engine) as s:
        assert s.scalars(select(Signal)).all() == []
        rej = s.scalars(select(RejectedCandidate)).one()
        assert rej.stage == "entry" and "already moved" in rej.reason


def test_illiquid_options_reject(engine: Engine) -> None:
    run(engine, FakeSource(oi=1000))
    with session_scope(engine) as s:
        rej = s.scalars(select(RejectedCandidate)).one()
        assert rej.stage == "liquidity"


def test_no_live_data_keeps_signal_pricing_pending(engine: Engine) -> None:
    class Down(FakeSource):
        available = False

    src = Down()
    src.available = False
    run(engine, src)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.pricing_status == "pending" and sig.strike is None


def test_weak_candidates_are_recorded_once(engine: Engine) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175})
    seed_news(engine, "TCS", 0.3, "livemint_markets", title="TCS small update")
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"), engine, FakeMessages(analyst_output())
    )  # type: ignore[call-arg]
    run_signal_cycle(engine, ai, FakeSource())
    run_signal_cycle(engine, ai, FakeSource())
    with session_scope(engine) as s:
        rows = s.scalars(
            select(RejectedCandidate).where(RejectedCandidate.stage == "candidate")
        ).all()
        assert len(rows) == 1 and rows[0].symbol == "TCS"


@pytest.fixture(autouse=True)
def _pricing_mechanics(no_selection_gates):
    """These tests cover pricing/trading mechanics; selection gates are tested in test_phase5."""
