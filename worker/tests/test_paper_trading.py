"""Phase 3b: paper engine, exits live, fills, attribution, portfolio, counterfactuals, scorecard."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app.ai.client import AiClient
from app.config import IST, Settings, get_settings
from app.db.models import (
    Alert,
    OptionQuote,
    PaperTrade,
    PortfolioSnapshot,
    PredictionScore,
    RejectedCandidate,
    Signal,
    TradeEvent,
)
from app.db.session import session_scope
from app.signals.prediction import Prediction, shrink
from app.signals.service import run_signal_cycle
from app.tracking.counterfactual import track_rejections
from app.tracking.scorecard import update_scores
from app.trading import fills
from app.trading.attribution import Attribution, Obs
from app.trading.paper_engine import trading_cycle
from app.trading.portfolio import snapshot
from app.yaml_config import load_yaml
from tests.helpers import FakeMessages, analyst_output, seed_instruments, seed_news
from tests.test_notify import Recorder, bot
from tests.test_options_stage import FakeSource


def weekday_at(h: int, m: int) -> datetime:
    d = datetime.now(IST).date()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=IST)


@pytest.fixture(autouse=True)
def _paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setenv("MC_PATHS", "2000")
    monkeypatch.setenv("HISTORY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_signal(engine: Engine, src: FakeSource, at: datetime) -> Signal:
    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175})
    seed_news(engine)
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"), engine, FakeMessages(analyst_output())
    )  # type: ignore[call-arg]
    run_signal_cycle(engine, ai, src)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        sig.created_at = at - timedelta(minutes=5)
        assert sig.pricing_status == "priced"
        return sig


def trade(engine: Engine) -> PaperTrade:
    with session_scope(engine) as s:
        return s.scalars(select(PaperTrade)).one()


def test_entry_then_targets_then_trailing_exit(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    sig = make_signal(engine, src, now)
    assert trading_cycle(engine, src, now) == 1
    tr = trade(engine)
    assert (
        tr.status == "OPEN"
        and tr.entry_premium > 0
        and tr.strike == sig.strike
        and tr.lot_size == 475
    )
    assert tr.entry_fill == "fillable" and tr.rules_hash and tr.spot_entry == 4000.0
    with session_scope(engine) as s:
        assert s.get(Signal, sig.id).status == "CONVERTED_TO_TRADE"  # type: ignore[union-attr]
        assert [e.event for e in s.scalars(select(TradeEvent))] == ["ENTRY"]
    # stock rallies a little: premium crosses T1 (the stop moves to entry)
    src.spot = 4030.0
    trading_cycle(engine, src, now + timedelta(minutes=30))
    tr = trade(engine)
    assert tr.status == "OPEN" and tr.state_json and tr.state_json["targets_hit"] == 1
    assert tr.state_json["stop"] == pytest.approx(tr.entry_premium)
    # stock slips back (still above the pre-news stock stop at 3990): breakeven stop closes the rest
    src.spot = 3995.0
    trading_cycle(engine, src, now + timedelta(minutes=60))
    tr = trade(engine)
    assert tr.status == "CLOSED" and tr.exit_reason == "TRAIL"
    assert tr.pnl is not None and tr.pnl_pct is not None and tr.fillability == "fillable"
    assert tr.benchmark_stock_pct == pytest.approx((3995 / 4000 - 1) * 100, abs=1e-3)
    a = tr.attribution_json or {}
    assert set(a) >= {"delta", "theta", "vega", "spread", "residual"} and a["spread"] < 0
    parts = a["delta"] + a["theta"] + a["vega"] + a["spread"] + a["residual"]
    gross = tr.state_json and sum(
        f["fraction"] * (f["price"] - tr.entry_premium) for f in tr.state_json["fills"]
    )
    assert parts == pytest.approx(
        float(gross or 0) * tr.lot_size * tr.lots, abs=1.0
    )  # parts add up
    assert (tr.mfe_pct or 0) > 0
    assert tr.lots == 3 and tr.booking_mode == "per_lot"
    with session_scope(engine) as s:
        evs = s.scalars(select(TradeEvent).order_by(TradeEvent.id)).all()
        assert [(e.event, e.lots) for e in evs] == [("ENTRY", 3), ("T1", 1), ("TRAIL", 2)]
        trail = evs[-1]
        # the BHARTIARTL case: stop at entry, next poll's bid was lower -> gap fill at the bid
        assert trail.gap and trail.trigger_level == pytest.approx(tr.entry_premium)
        assert trail.slippage_inr == pytest.approx(
            (trail.premium - tr.entry_premium) * 475 * 2, abs=0.05
        )
        assert trail.note and trail.note.startswith("gap/slippage")
    assert tr.slippage_inr == pytest.approx(trail.slippage_inr, abs=0.05) and tr.slippage_inr < 0
    with session_scope(engine) as s:
        texts = [
            a.text.split("\n")[0] for a in s.scalars(select(Alert).where(Alert.kind == "trade"))
        ]
    assert texts[0].startswith("PAPER ENTRY") and any(t.startswith("TARGET 1 HIT") for t in texts)
    with session_scope(engine) as s:
        bodies = [a.text for a in s.scalars(select(Alert).where(Alert.kind == "trade"))]
    assert any("Sold 1 of 3 lots at" in b for b in bodies) and any(
        "Sold 2 of 3 lots at" in b for b in bodies
    )
    assert any("Gap/slippage: level" in b for b in bodies)
    assert "Gap/slippage vs trigger levels" in bot(engine, Recorder()).reply_for("/stats")
    assert texts[-1].startswith("TRADE CLOSED")


def test_no_entry_in_first_15_minutes_and_ttl_expiry(engine: Engine) -> None:
    src = FakeSource()
    early = weekday_at(9, 20)
    make_signal(engine, src, early)
    assert trading_cycle(engine, src, early) == 0
    with session_scope(engine) as s:
        assert s.scalars(select(PaperTrade)).all() == []
    late = early + timedelta(hours=3)  # beyond the 120-minute entry TTL
    trading_cycle(engine, src, late)
    with session_scope(engine) as s:
        assert s.scalars(select(Signal)).one().status == "CANCELLED"
        rej = s.scalars(
            select(RejectedCandidate).where(RejectedCandidate.stage == "entry_ttl")
        ).one()
        assert rej.ref_strike is not None


def test_underlying_stop_and_unfillable_flag(engine: Engine) -> None:
    src = FakeSource(qty=0)
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    src.spot = 3985.0  # back below the pre-news level (prev close 3990): thesis invalidated
    trading_cycle(engine, src, now + timedelta(minutes=10))
    tr = trade(engine)
    assert tr.status == "CLOSED" and tr.exit_reason == "USTOP"
    assert tr.entry_fill == "unfillable" and tr.fillability == "unfillable"
    text = bot(engine, Recorder()).reply_for("/stats")
    assert "All: 1 trades" in text and "Excluding unfillable: no closed trades" in text


def test_opposite_signal_flag_and_close_modes(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    with session_scope(engine) as s:
        s.add(
            Signal(
                symbol="BHARTIARTL",
                direction="PUT",
                option_type="PE",
                score=80,
                status="NEW",
                created_at=now + timedelta(minutes=1),
                updated_at=now,
            )
        )
    trading_cycle(engine, src, now + timedelta(minutes=5))
    tr = trade(engine)
    assert tr.status == "OPEN" and tr.review_flag and "opposite signal" in tr.review_flag
    monkeypatch.setitem(load_yaml("portfolio.yaml"), "opposite_signal_action", "close")
    trading_cycle(engine, src, now + timedelta(minutes=10))
    assert trade(engine).exit_reason == "THESIS"


def test_portfolio_snapshot_and_exposure(engine: Engine) -> None:
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    trading_cycle(engine, src, now)
    src.spot = 4020.0
    trading_cycle(engine, src, now + timedelta(minutes=5))
    assert snapshot(engine, now + timedelta(minutes=6)) == 1
    with session_scope(engine) as s:
        snap = s.scalars(select(PortfolioSnapshot)).one()
        assert snap.capital == 500000 and snap.open_trades == 1 and snap.equity != snap.capital
        assert snap.capital_used_inr and snap.capital_used_inr > 0
        assert snap.exposure_json and "theme:tariffs" in snap.exposure_json
    b = bot(engine, Recorder())
    assert "Equity Rs" in b.reply_for("/portfolio")
    assert "theme:tariffs: 1 trade(s) BHARTIARTL" in b.reply_for("/exposure")
    assert "BHARTIARTL" in b.reply_for("/open")


def test_counterfactual_tracks_rejected_option(engine: Engine) -> None:
    src = FakeSource(iv=40.0)  # edge gate rejects
    seed_instruments(engine, {"BHARTIARTL": 475})
    seed_news(engine)
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"), engine, FakeMessages(analyst_output())
    )  # type: ignore[call-arg]
    run_signal_cycle(engine, ai, src)
    now = weekday_at(12, 40)
    track_rejections(engine, src, now)
    src.spot = 4300.0
    track_rejections(engine, src, now + timedelta(hours=1))
    with session_scope(engine) as s:
        rej = s.scalars(select(RejectedCandidate).where(RejectedCandidate.stage == "edge")).one()
        assert rej.stock_return_pct and rej.stock_return_pct > 0
        assert rej.option_return_pct is not None and rej.option_return_pct > 0
        assert rej.details_json and "state" in rej.details_json["cf"]
    track_rejections(engine, src, now + timedelta(days=20))  # past max_days -> resolved
    with session_scope(engine) as s:
        rej = s.scalars(select(RejectedCandidate).where(RejectedCandidate.stage == "edge")).one()
        assert rej.outcome_status == "resolved" and rej.exit_reason


def test_scorecard_scores_after_the_window(engine: Engine) -> None:
    src = FakeSource()
    make_signal(engine, src, weekday_at(11, 0))
    with session_scope(engine) as s:
        ps = s.scalars(select(PredictionScore)).one()
        assert ps.predicted_move_pct == pytest.approx(
            1.425
        ) and ps.predicted_move_raw_pct == pytest.approx(2.85)
        ps.created_at = datetime.now(IST) - timedelta(days=10)
    src.spot = 4120.0  # +3%
    update_scores(engine, src, weekday_at(15, 40))
    with session_scope(engine) as s:
        ps = s.scalars(select(PredictionScore)).one()
        assert ps.status == "scored" and ps.direction_hit is True
        assert ps.realised_move_pct == pytest.approx(3.0) and ps.move_error_pct == pytest.approx(
            3.0 - 1.425
        )
        assert ps.days_to_half_move is not None


def test_quote_archive_and_protected_thinning(engine: Engine, tmp_path: Path) -> None:
    from app.health.cleanup import cleanup

    old = datetime.now(IST) - timedelta(days=20)
    with session_scope(engine) as s:
        for sym in ("TCS", "INFY"):
            for i in range(3):
                s.add(
                    OptionQuote(
                        ts=old + timedelta(minutes=i),
                        symbol=sym,
                        expiry=date(2026, 10, 27),
                        strike=4000.0,
                        type="CE",
                        bid=10.0,
                        ask=11.0,
                        bid_qty=500,
                        ask_qty=700,
                    )
                )
        s.add(
            Signal(
                symbol="TCS",
                direction="CALL",
                score=80,
                status="NEW",
                created_at=old,
                updated_at=old,
            )
        )
    cleanup(engine)
    files = list((tmp_path / "parquet" / "option_quotes").rglob("*.parquet"))
    assert files and all("date=" in str(f.parent) for f in files)
    import duckdb

    n, qty = (
        duckdb.connect()
        .execute(
            f"select count(*), sum(ask_qty) from read_parquet('{tmp_path}/parquet/option_quotes/*/*.parquet')"
        )
        .fetchone()
    )  # type: ignore[misc]
    assert n == 6 and qty == 4200
    with session_scope(engine) as s:
        left = {
            sym: len(s.scalars(select(OptionQuote).where(OptionQuote.symbol == sym)).all())
            for sym in ("TCS", "INFY")
        }
    assert left == {"TCS": 3, "INFY": 1}  # TCS has a live signal: protected; INFY thinned to 1/day
    cleanup(engine)  # idempotent: nothing archived twice
    assert len(list((tmp_path / "parquet" / "option_quotes").rglob("*.parquet"))) == len(files)


def test_fill_classification_and_attribution() -> None:
    assert fills.classify(500, 475) == "fillable" and fills.classify(100, 475) == "partial"
    assert fills.classify(0, 475) == "unfillable" and fills.classify(None, 475) == "unknown"
    assert (
        fills.worse("fillable", "partial") == "partial"
        and fills.worse("unfillable", "unknown") == "unfillable"
    )
    a = Attribution()
    t0 = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    a.add(Obs(t0.isoformat(), 1000.0, 30.0, 30.0, 30 / 365), 1000.0, "CE", 0.065, 0)
    a.add(
        Obs((t0 + timedelta(minutes=5)).isoformat(), 1002.0, 31.1, 30.0, 30 / 365),
        1000.0,
        "CE",
        0.065,
        100,
    )
    assert a.delta == pytest.approx(0.55 * 2 * 100, rel=0.1)  # ~delta x dS x units
    assert abs(a.residual) < abs(a.delta) and a.vega == 0.0


def test_shrink_toward_market() -> None:
    p = shrink(Prediction(4.0, "scenario_tree", 0.8), 0.5)
    assert p.move_pct == 2.0 and p.p_move == pytest.approx(0.65) and p.source == "scenario_tree"
    assert shrink(Prediction(4.0, "scenario_tree", 0.8), 1.0).move_pct == 4.0


def test_depth_fallback_fills_missing_quantities() -> None:
    from app.market.live import UpstoxOptionData
    from app.market.options import OptionQuoteRow

    class Client:
        configured = True

        def quotes(self, keys: list[str]) -> dict[str, Any]:
            return {
                "status": "success",
                "data": {
                    "NSE_FO:X": {
                        "instrument_token": keys[0],
                        "last_price": 10.0,
                        "net_change": 0.0,
                        "depth": {
                            "buy": [
                                {"quantity": 350, "price": 9.9},
                                {"quantity": 700, "price": 9.8},
                            ],
                            "sell": [{"quantity": 1050, "price": 10.1}],
                        },
                    }
                },
            }

    src = UpstoxOptionData(None, Client())  # type: ignore[arg-type]
    row = OptionQuoteRow(
        "TCS",
        date(2026, 10, 27),
        4000.0,
        "CE",
        9.9,
        10.1,
        10.0,
        25.0,
        1,
        1,
        4000.0,
        datetime.now(IST),
        None,
        None,
        "NSE_FO|123",
    )
    out = src.with_depth(row)
    assert (out.bid_qty, out.ask_qty) == (350, 1050)
    with_qty = OptionQuoteRow(**{**row.__dict__, "bid_qty": 5, "ask_qty": 6})
    assert src.with_depth(with_qty) is with_qty  # no extra call when the chain had quantities
