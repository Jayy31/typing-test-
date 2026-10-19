"""Phase 12 PR 1: market regime (feat.regime)."""

from datetime import date, datetime, timedelta

from sqlalchemy import Engine

from app.config import IST
from app.db.models import MacroSnapshot, MarketRegime
from app.db.session import session_scope
from app.history.analogues import AnalogueResult
from app.history.store import connect
from app.regime import gates
from app.regime.analogues import blend, key, regime_weight
from app.regime.classifier import classify, series
from app.regime.service import backfill, compute_today, current, daily_job


def test_classify_volatility_and_direction() -> None:
    d = date(2026, 10, 19)
    calm = classify(d, 11.0, [15.0] * 100, 3.0, 5000)
    assert (calm.vol, calm.direction, calm.inputs) == ("calm", "risk_on", ("VIX", "Nifty", "FII"))
    stressed = classify(d, 24.0, None, -4.0, None)
    assert (
        stressed.label == "stressed/risk_off" and stressed.inputs_text == "VIX+Nifty; FII unknown"
    )
    # percentile alone can make a market stressed
    high_pct = classify(d, 18.0, [10.0 + i * 0.04 for i in range(200)], 0.0)
    assert high_pct.vol == "stressed" and high_pct.vix_pct and high_pct.vix_pct > 85
    # FII out-votes a flat Nifty
    assert classify(d, 15.0, None, 0.5, -6000).direction == "risk_off"
    assert classify(d, None).label == "unknown/unknown"
    assert "FII 10d" in classify(d, 15.0, None, 1.0, 100).text()


def _hist(n: int = 80) -> tuple[dict[date, float], dict[date, float]]:
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    nifty = {d: 20000.0 + 50 * i for i, d in enumerate(days)}
    vix = {d: (12.0 if i < 60 else 25.0) for i, d in enumerate(days)}
    return nifty, vix


def test_series_uses_only_past_data_and_marks_fii_unknown() -> None:
    nifty, vix = _hist()
    regs = series(nifty, vix)
    days = sorted(regs)
    assert regs[days[5]].nifty_ret_pct is None  # fewer than 20 days of Nifty yet
    assert regs[days[30]].label == "calm/risk_on" and "FII unknown" in regs[days[30]].inputs_text
    assert regs[days[70]].vol == "stressed"
    # changing the future does not change the past
    later = dict(vix)
    later[days[-1]] = 80.0
    assert series(nifty, later)[days[50]] == regs[days[50]]


def _res(n: int, mean: float) -> AnalogueResult:
    return AnalogueResult("sector", "x", 3, n, float(n), mean, mean, 0.6, 2.0, mean - 1, mean + 1)


def test_regime_stats_shrink_toward_all_regimes() -> None:
    all_r, reg = _res(100, 1.0), _res(20, 3.0)
    w = regime_weight(20, 20.0)
    out = blend(all_r, reg, "stressed/risk_off")
    assert 0 < w < 1 and abs(out.mean - (w * 3.0 + (1 - w) * 1.0)) < 1e-9
    assert out.n == 100 and "stressed/risk_off" in out.regime
    few = blend(all_r, _res(2, 9.0), "calm/risk_on")  # below ANALOGUE_MIN_N: all-regime stands
    assert few.mean == 1.0 and "too few" in few.regime
    assert key("normal/risk_on") == "R:normal/risk_on" and key("unknown/unknown") is None


def test_gates_equal_by_default_and_variant_overrides() -> None:
    base = gates.base()
    assert all(gates.for_regime(v) == base for v in ("calm", "normal", "stressed", None))
    over = {"stressed": {"edge_ratio_min": 1.4, "min_p_t1_before_sl": 0.58}}
    s = gates.for_regime("stressed", over)
    assert s["edge_ratio_min"] == 1.4 and s["min_p_t1_before_sl"] == 0.58
    assert gates.for_regime("calm", over) == base


def _store(nifty: dict[date, float], vix: dict[date, float]) -> None:
    with connect(write=True) as con:
        con.executemany(
            "INSERT INTO hist_index VALUES (?, ?, ?)",
            [(d, "Nifty 50", v) for d, v in nifty.items()]
            + [(d, "India VIX", v) for d, v in vix.items()],
        )


def test_backfill_and_live_day_with_all_three_inputs(engine: Engine) -> None:
    nifty, vix = _hist()
    _store(nifty, vix)
    assert backfill(engine) == len(nifty)
    today = max(nifty) + timedelta(days=1)
    now = datetime.combine(today, datetime.min.time(), IST).replace(hour=8, minute=35)
    with session_scope(engine) as s:
        for i in range(10):
            ts = now - timedelta(days=10 - i)
            s.add(MacroSnapshot(ts=ts, india_vix=14.0, fii_net=-900.0))
    r = compute_today(engine, now)
    assert r.inputs == ("VIX", "Nifty", "FII") and r.fii_net_cr == -9000.0
    assert r.direction == "risk_on"  # Nifty up (+1) and FII selling (-1) cancel: not risk-off
    assert daily_job(engine, now) == 1
    got = current(engine, today)
    assert got is not None and got.label == r.label and got.inputs_text == "VIX+Nifty+FII"
    backfill(engine)  # history never overwrites a live day
    with session_scope(engine) as s:
        assert s.get(MarketRegime, today).source == "live"
        assert s.get(MarketRegime, max(nifty)).inputs == "VIX+Nifty; FII unknown"


def test_brief_and_why_show_the_regime(engine: Engine) -> None:
    from app.notify.briefs import morning_brief_text

    nifty, vix = _hist()
    _store(nifty, vix)
    backfill(engine)
    day = max(nifty) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    now = datetime.combine(day, datetime.min.time(), IST).replace(hour=8)
    with session_scope(engine) as s:
        text = morning_brief_text(s, now) or ""
    assert "MARKET REGIME" in text and "FII unknown" in text


def test_backtest_regime_variants_run() -> None:
    import app.backtest.__main__ as cli
    from app.backtest.engine import Variant
    from app.backtest.runner import run
    from tests import bt_fixture as F

    names = [v.name for v in cli.standard_variants("rules")]
    assert "rules: + regime-aware analogues" in names
    assert "rules: regime gates (stressed_strict)" in names
    con, cal = F.build()
    F.add_training_reactions(con, cal)
    F.add_event(con, "e0", cal[300])
    base = run(con, Variant("base"), F.START, cal[-1])
    aware = run(con, Variant("aware", regime_aware=True), F.START, cal[-1])
    strict = run(con, Variant("strict", regime_gates="stressed_strict"), F.START, cal[-1])
    # synthetic VIX is 15 (normal) every day: regime gates change nothing, both variants run
    assert len(strict.trades) == len(base.trades)
    assert len(aware.trades) + len(aware.skips) >= 1
