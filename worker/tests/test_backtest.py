"""Phase 6 backtester on synthetic history."""

from datetime import date, datetime

import pytest

from app.backtest import fills, metrics, store
from app.backtest.data import HistoryData, OptRow
from app.backtest.engine import Variant
from app.backtest.runner import folds, run
from tests import bt_fixture as F

WF_START = F.START  # train 12 months, then test


def setup(**kw):
    con, cal = F.build(**kw)
    F.add_training_reactions(con, cal)
    return con, cal


def test_folds_are_12_train_3_test() -> None:
    fs = folds(date(2021, 1, 1), date(2022, 7, 1))
    assert [(f.test_start, f.test_end) for f in fs] == [
        (date(2022, 1, 1), date(2022, 4, 1)),
        (date(2022, 4, 1), date(2022, 7, 1)),
    ]
    assert fs[0].train_start == date(2021, 1, 1)


def test_spread_tiers_min_tick_and_multiplier() -> None:
    t = fills.tier_for(2, 1000, 500, True)
    assert t.name == "liquid_large_cap_atm" and t.half_spread_pct == 2
    assert fills.tier_for(2, 1000, 500, False).name == "liquid_otm_or_midcap_atm"
    assert fills.tier_for(20, 1000, 500, True).name == "illiquid_or_deep_otm"
    assert fills.half_spread(100, t, 1.0) == pytest.approx(2.0) and fills.half_spread(
        100, t, 2.0
    ) == pytest.approx(4.0)
    assert fills.half_spread(1.0, t, 1.0) == pytest.approx(0.05)  # one tick minimum


def test_stale_prices_use_settlement_or_nothing() -> None:
    row = OptRow(date(2024, 1, 1), date(2024, 1, 25), 100, "CE", 5, 6, 4, 5.5, 5.2, 10, 0)
    p = fills.tradable(row)
    assert p is not None and p.stale and p.close == 5.2
    assert fills.tradable(row.__class__(**{**row.__dict__, "settle": None})) is None
    assert fills.classify(3, None, True) == "unfillable"
    assert (
        fills.classify(3, 100, False) == "fillable" and fills.classify(30, 100, False) == "partial"
    )


def test_rules_backtest_enters_at_reaction_close_and_exits_later() -> None:
    con, cal = setup()
    F.add_event(con, "e1", cal[300])
    res = run(con, Variant("base"), WF_START, cal[-1])
    assert len(res.trades) == 1, [s.__dict__ for s in res.skips]
    t = res.trades[0]
    assert t.entry_day == cal[300] and t.option_type == "CE"
    assert t.entry_ask > t.entry_mid  # bought at mid + modelled half-spread
    assert t.exit_day is not None and t.exit_day > cal[300]  # exits checked from the next day
    assert t.t1_hit and t.pnl_pct > 0 and t.fills[0]["event"].startswith("T")
    assert t.signal.ai_weight == pytest.approx(30 / 50)  # n / (n + 20) analogue weight


def test_no_look_ahead_future_data_does_not_change_decisions() -> None:
    con, cal = setup()
    F.add_event(con, "e1", cal[300])
    a = run(con, Variant("base"), WF_START, cal[-1]).trades[0]
    # wreck everything after the entry day: future prices and a huge contrary reaction in the test fold
    con.execute(
        "UPDATE hist_options SET close = close * 0.1, open = open * 0.1, high = high * 0.1, low = low * 0.1 WHERE date > ?",
        [cal[300]],
    )
    con.execute("UPDATE hist_prices SET close = close * 0.5 WHERE date > ?", [cal[300]])
    F.add_event(con, "future", cal[310])
    con.execute(
        "INSERT INTO event_reactions VALUES ('future', 'ABC', 3, ?, 'intraday', -40, -40, -40, -40, 'x', NULL, NULL, NULL, NULL, false, 1.0)",
        [cal[310]],
    )
    b = run(con, Variant("base"), WF_START, cal[-1]).trades[0]
    assert (a.entry_day, a.strike, a.expiry, a.entry_ask, a.signal.analogue) == (
        b.entry_day,
        b.strike,
        b.expiry,
        b.entry_ask,
        b.signal.analogue,
    )
    assert b.pnl_pct < a.pnl_pct  # only the outcome changed


def test_point_in_time_universe_keeps_removed_stocks_and_skips_non_members() -> None:
    con, cal = setup(symbols=("ABC", "OLD"))
    con.execute(
        "DELETE FROM hist_fno_universe WHERE symbol = 'OLD' AND date >= ?", [cal[305]]
    )  # removed later
    F.add_training_reactions(con, cal, sym="OLD")
    F.add_event(con, "o1", cal[300], sym="OLD")
    F.add_event(con, "o2", cal[340], sym="OLD")
    res = run(con, Variant("base"), WF_START, cal[-1])
    assert any(
        t.signal.symbol == "OLD" and t.entry_day == cal[300] for t in res.trades
    )  # traded while in F&O
    assert any(s.symbol == "OLD" and s.stage == "universe" for s in res.skips)  # not after removal


def test_zero_volume_entry_uses_settlement_and_is_counted() -> None:
    con, cal = setup(zero_volume_days=(300,))
    F.add_event(con, "e1", cal[300])
    res = run(con, Variant("base"), WF_START, cal[-1])
    assert res.trades and res.trades[0].entry_stale and res.trades[0].fillability == "unfillable"
    data = HistoryData(con)
    m = metrics.compute(res, data)
    assert m["settlement_price_trades"] == 1 and m["excluding_unfillable"]["n"] == 0


def test_no_price_no_settlement_means_no_entry() -> None:
    con, cal = setup(zero_volume_days=(300,), no_settle=True)
    F.add_event(con, "e1", cal[300])
    res = run(con, Variant("base"), WF_START, cal[-1])
    assert not res.trades and any(s.stage == "liquidity" for s in res.skips)


def test_rule_sets_booking_and_spread_sensitivity_differ() -> None:
    con, cal = setup()
    F.add_event(con, "e1", cal[300])
    base = run(con, Variant("v21"), WF_START, cal[-1]).trades[0]
    fixed = run(con, Variant("fixed", rule_set="fixed"), WF_START, cal[-1]).trades[0]
    single = run(con, Variant("se", booking="single_exit"), WF_START, cal[-1]).trades[0]
    wide = run(con, Variant("x2", spread_mult=2.0), WF_START, cal[-1])
    assert len(single.fills) <= len(base.fills)
    assert fixed.signal.key == base.signal.key  # same signal, different exit rules
    assert (
        Variant("f", rule_set="fixed").rules().stop_mode == "premium"
        and Variant("v").rules().window_stop
    )
    if wide.trades:
        assert wide.trades[0].entry_ask > base.entry_ask and wide.trades[0].pnl_pct < base.pnl_pct


def test_metrics_and_report() -> None:
    con, cal = setup()
    for k, i in enumerate((290, 330, 360)):
        F.add_event(con, f"e{k}", cal[i])
    data = HistoryData(con)
    res = run(con, Variant("v21"), WF_START, cal[-1], data=data)
    m = metrics.compute(res, data)
    assert m["all"]["n"] == len(res.trades) and m["folds"] >= 1
    assert "max_drawdown_pct" in m["risk"] and m["benchmarks"]["nifty_buy_hold_pct"] is not None
    assert m["pnl_inr_approx_inferred_lots"] == 0  # all lots exact here
    text = store.render([(1, m)])
    assert "| 1 | v21 |" in text and "% of premium is the primary metric" in text


def test_inferred_lot_trades_report_percent_first() -> None:
    con, cal = setup()
    con.execute("UPDATE hist_fno_universe SET lot_source = 'inferred'")
    F.add_event(con, "e1", cal[300])
    t = run(con, Variant("v21"), WF_START, cal[-1]).trades[0]
    rep = t.report()
    assert rep["primary"] == "pct" and "approximate (inferred lot size)" in rep["text"]


def test_replay_enforces_r1(engine, monkeypatch) -> None:
    from app.config import IST, get_settings
    from app.db.models import Analysis, Signal
    from app.db.session import session_scope

    con, cal = setup()
    with session_scope(engine) as s:
        for n, d in (("before", cal[250]), ("after", cal[300])):
            an = Analysis(
                symbol="ABC",
                thesis=n,
                direction="bullish",
                window_days=3,
                created_at=datetime.combine(d, datetime.min.time(), IST).replace(hour=10),
                prediction_source="panel",
            )
            s.add(an)
            s.flush()
            exp = sorted(
                {
                    r[0]
                    for r in con.execute(
                        "SELECT expiry FROM hist_options WHERE date = ? AND expiry > ?", [d, d]
                    ).fetchall()
                }
            )[0]
            s.add(
                Signal(
                    symbol="ABC",
                    direction="bullish",
                    score=80,
                    status="CLOSED",
                    analysis_id=an.id,
                    strike=1000.0,
                    option_type="CE",
                    expiry=exp,
                    predicted_move_pct=2.0,
                    predicted_move_raw_pct=4.0,
                    created_at=an.created_at,
                )
            )
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", cal[280].isoformat())
    get_settings.cache_clear()
    res = run(con, Variant("replay", mode="replay"), WF_START, cal[-1], engine=engine)
    get_settings.cache_clear()
    assert [t.signal.title for t in res.trades] == ["after"] and res.trades[
        0
    ].signal.source == "panel"
    assert any("R1: 1 live signal" in n for n in res.notes)


def test_compare_end_to_end(engine, tmp_path, monkeypatch) -> None:
    """`python -m app.backtest compare`: runs, stores, reports, alerts."""
    from contextlib import contextmanager

    from sqlalchemy import select

    import app.backtest.__main__ as cli
    from app.db.models import Alert, BacktestRun, BacktestTrade
    from app.db.session import session_scope

    con, cal = setup()
    for k, i in enumerate((290, 330)):
        F.add_event(con, f"e{k}", cal[i])

    @contextmanager
    def fake_connect(*a, **k):
        yield con

    monkeypatch.setattr(cli, "connect", fake_connect)
    import app.db.session as sess

    monkeypatch.setattr(sess, "get_engine", lambda: engine)
    text = cli.execute(cli.standard_variants("rules"), WF_START, cal[-1])
    assert "rules: v2.1 rules, per_lot" in text and "spread x1.5" in text and "spread x2" in text
    assert "benchmark: random direction" in text and "benchmark: ATM, no gates" in text
    assert "legacy threshold" in text and "VERDICT:" in text
    with session_scope(engine) as s:
        assert len(s.scalars(select(BacktestRun)).all()) >= 8
        assert s.scalars(select(BacktestTrade)).first() is not None
        assert s.scalars(select(Alert)).one().text.startswith("BACKTEST DONE")


def test_overlap_skip_is_tracked_as_counterfactual() -> None:
    con, cal = setup()
    F.add_event(con, "e1", cal[300])
    F.add_event(
        con, "e2", cal[300], direction="bearish"
    )  # same day, other side: e1's trade is open
    data = HistoryData(con)
    res = run(con, Variant("v21"), WF_START, cal[-1], data=data)
    assert len(res.trades) == 1
    assert any(s.stage == "overlap" and s.reason == "open trade on same stock" for s in res.skips)
    assert (
        len(res.counterfactuals) == 1
        and res.counterfactuals[0].option_type != res.trades[0].option_type
    )
    m = metrics.compute(res, data)
    assert m["counterfactual_open_trade_on_same_stock"]["n"] == 1 and m["all"]["n"] == 1


def test_strict_threshold_needs_hit_rate_and_ci_above_zero() -> None:
    from app.backtest.engine import direction_supported
    from app.history.analogues import AnalogueResult

    noisy = [
        (v, 1.0) for v in [6, -5, 7, -6, 5, -4, 8, -7, 6, -5, 4, 3]
    ]  # hit 58%, mean small, CI spans 0
    an = AnalogueResult("stock", "all", 3, 12, 12.0, 0.17, 3.5, 0.58, 5.5, -5, 6)
    ok, why = direction_supported(an, noisy, "strict")
    assert not ok and "does not exclude" in why
    assert direction_supported(an, noisy, "legacy")[0]
    low_hit = AnalogueResult("stock", "all", 3, 12, 12.0, 2.0, 1.0, 0.52, 2.0, 0, 4)
    assert "hit rate 52% below 55%" in direction_supported(low_hit, [(2.0, 1.0)] * 12, "strict")[1]
    clean = [(v, 1.0) for v in [4, 5, 6, 3, 5, 4, 6, -1, 5, 4]]
    assert direction_supported(
        AnalogueResult("stock", "all", 3, 10, 10.0, 4.1, 4.5, 0.9, 2, 4, 5), clean, "strict"
    )[0]


def test_verdict_requires_significance_and_2x_spread() -> None:
    def m(name: str, exp: float, p5: float, p95: float, **v: object) -> dict:
        return {
            "variant": {
                "name": name,
                "direction_mode": "signal",
                "selection": "ev",
                "spread_mult": 1.0,
                **v,
            },
            "all": {"n": 40, "expectancy_pct": exp},
            "bootstrap": {"expectancy_p5": p5, "expectancy_p95": p95},
        }

    rows = [
        (1, m("default", 5, 1, 9)),
        (2, m("fixed", 8, 4, 12)),
        (3, m("random", 20, 15, 25, direction_mode="random")),
    ]
    v = store.best_vs_default(rows)
    assert v["best"] == "fixed" and not v["significant"]  # overlapping intervals; benchmark ignored
    assert "NOT SIGNIFICANT" in store.verdict_text(v)
    rows[1] = (2, m("fixed", 15, 10, 20))
    v = store.best_vs_default(rows)
    assert v["significant"]
    assert "Suggestion: consider switching" in store.verdict_text(v, best_x2=6.0, default_x2=2.0)
    assert "not better at 2x spread" in store.verdict_text(v, best_x2=1.0, default_x2=2.0)
    assert "current default" in store.verdict_text(
        store.best_vs_default([(1, m("default", 9, 5, 12)), (2, m("fixed", 3, 1, 5))])
    )


def test_units_detected_from_lot_multiples() -> None:
    from app.history import units

    con, cal = F.build()
    # synthetic volumes are 5000 = 50 x lot 100 (shares-like); OI 2,000,000 too
    res = units.detect(con)
    assert res["fo_old"]["volume"]["unit"] == "shares" and res["fo_old"]["oi"]["unit"] == "shares"
    assert units.store(con, res) == []
    assert HistoryData(con).units[("fo_old", "volume")] == "shares"
    con.execute(
        "UPDATE hist_options SET volume = volume + 37"
    )  # contract counts: not multiples of 100
    assert units.detect(con)["fo_old"]["volume"]["unit"] == "contracts"
    con.execute(
        "UPDATE hist_options SET volume = CASE WHEN strike % 40 = 0 THEN 5000 ELSE 5037 END"
    )  # mixed
    res = units.detect(con)
    assert res["fo_old"]["volume"]["unit"] is None and units.store(
        con, res
    )  # inconclusive -> alert text


def test_auto_backtests_wait_for_history_then_run_once(engine, monkeypatch) -> None:
    from contextlib import contextmanager

    import app.backtest.__main__ as cli
    from app.backtest import auto

    con, cal = setup()
    ready, why = auto.history_ready(con)
    assert not ready and "prices" in why

    @contextmanager
    def fake_connect(*a, **k):
        yield con

    monkeypatch.setattr(auto, "connect", fake_connect)
    calls: list[str] = []
    monkeypatch.setattr(cli, "execute", lambda variants, s, e: calls.append(variants[0].mode) or "")
    monkeypatch.setattr(auto, "history_ready", lambda c: (True, "ready"))
    assert auto.auto_backtests(engine).startswith("rules compare")
    assert auto.auto_backtests(engine).startswith("nothing due")  # once only
    assert calls == ["rules"]
