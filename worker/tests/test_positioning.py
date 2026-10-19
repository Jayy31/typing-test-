"""Phase 12 PR 2: option-market positioning (feat.positioning). Evidence only, never a signal."""

from datetime import date, timedelta

import duckdb
import pytest

from app.ai.schemas import Scenario
from app.history import nse_files
from app.history.store import init_schema
from app.positioning import evidence, features, history
from app.positioning.features import DayInput, OptRow


def test_oi_buildup_quadrants() -> None:
    def st(p1, oi_chg):
        return features.oi_buildup(
            DayInput(fut_close=p1, fut_prev_close=100.0, fut_oi=1000 + oi_chg, fut_chg_oi=oi_chg)
        )[0]

    assert st(102, 100) == "long_buildup"
    assert st(98, 100) == "short_buildup"
    assert st(102, -100) == "short_covering"
    assert st(98, -100) == "long_unwinding"
    assert st(100.1, 100) == "flat"
    assert features.oi_buildup(DayInput(fut_close=100)) is None  # missing inputs: left out


def _chain(spot: float = 100.0) -> list[OptRow]:
    rows = []
    for k in (90, 95, 100, 105, 110):
        rows.append(
            OptRow(
                k,
                "CE",
                oi=1000,
                chg_oi=10,
                volume=100,
                iv=20 + (100 - k) * 0.1,
                avg_volume=100,
                avg_abs_chg_oi=10,
            )
        )
        rows.append(
            OptRow(
                k,
                "PE",
                oi=1500,
                chg_oi=10,
                volume=100,
                iv=26 + (100 - k) * 0.1,
                avg_volume=100,
                avg_abs_chg_oi=10,
            )
        )
    return rows


def test_pcr_skew_term_basis_rollover_delivery_unusual() -> None:
    x = DayInput(
        spot=100.0,
        fut_close=101.0,
        fut_oi=1000,
        next_fut_oi=4000,
        days_to_expiry=3,
        near=_chain(),
        next_atm_iv=18.0,
        prev_pcr=1.2,
        delivery_pct=70,
        delivery_avg=45,
    )
    out = features.compute(x)
    assert out["pcr"] == ("high_rising", 1.5)
    assert out["iv_skew"][0] == "put_skew"
    assert out["term_structure"][0] == "inverted"
    assert out["basis"][0] == "premium"
    assert out["rollover"] == ("high", 0.8)
    assert out["delivery"][0] == "high"
    assert out["unusual_activity"] == ("none", 0.0)
    hot = [
        r
        if not (r.strike == 105 and r.type == "CE")
        else OptRow(105, "CE", 5000, 200, 900, 20, 100, 10)
        for r in _chain()
    ]
    assert features.unusual_activity(DayInput(near=hot))[0] == "unusual_calls"
    # outside expiry week: no rollover state
    assert features.rollover(DayInput(fut_oi=1000, next_fut_oi=4000, days_to_expiry=12)) is None


def test_futures_rows_parsed_from_fo_files() -> None:
    raw = (
        b"FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,OpnPric,HghPric,LwPric,ClsPric,SttlmPric,"
        b"OpnIntrst,ChngInOpnIntrst,TtlTradgVol,NewBrdLotQty,UndrlygPric\n"
        b"STO,INFY,2024-08-29,1800,CE,20,25,18,22,22,1000,50,300,400,1790\n"
        b"STF,INFY,2024-08-29,,,1795,1801,1790,1799,1799.5,500000,25000,9000,400,1790\n"
        b"IDF,NIFTY,2024-08-29,,,1,1,1,1,1,1,1,1,25,1\n"
    )
    p = nse_files.parse_fo(raw, date(2024, 8, 1))
    assert len(p.options) == 1
    assert p.futures == [
        (date(2024, 8, 1), "INFY", date(2024, 8, 29), 1799.0, 1799.5, 500000, 25000, 9000)
    ]


def _store(days: int = 40) -> tuple[duckdb.DuckDBPyConnection, list[date]]:
    con = duckdb.connect()
    init_schema(con)
    cal, d = [], date(2024, 1, 1)
    while len(cal) < days:
        if d.weekday() < 5:
            cal.append(d)
        d += timedelta(days=1)
    exp, nxt = date(2024, 2, 29), date(2024, 3, 28)
    for i, day in enumerate(cal):
        # rising futures + rising OI every day: long build-up; stock outperforms Nifty afterwards
        spot = 100.0 * (1.01**i)
        con.execute(
            "INSERT INTO hist_prices VALUES (?, 'ABC', ?, ?, ?, ?, 1000, 50)",
            [day, spot, spot, spot, spot],
        )
        con.execute("INSERT INTO hist_index VALUES (?, 'Nifty 50', 20000)", [day])
        con.execute(
            "INSERT INTO hist_futures VALUES (?, 'ABC', ?, ?, ?, ?, ?, 1000)",
            [day, exp, spot * 1.002, spot * 1.002, 10000 + 500 * i, 500],
        )
        con.execute(
            "INSERT INTO hist_futures VALUES (?, 'ABC', ?, ?, ?, 2000, 10, 100)",
            [day, nxt, spot * 1.004, spot * 1.004],
        )
        for k in (90, 100, 110):
            for typ, oi in (("CE", 1000), ("PE", 1200)):
                con.execute(
                    "INSERT INTO hist_options VALUES (?, 'ABC', ?, ?, ?, 1, 1, 1, 1, 1, ?, 10, 100)",
                    [day, exp, float(k), typ, oi],
                )
    return con, cal


def test_history_build_is_incremental_and_stats_have_no_lookahead() -> None:
    con, cal = _store()
    n = history.build(con)
    assert n > 0 and history.build(con) == 0  # nothing new the second time
    states = dict(
        con.execute(
            "SELECT feature, state FROM hist_positioning WHERE date = ?", [cal[10]]
        ).fetchall()
    )
    assert states["oi_buildup"] == "long_buildup" and states["pcr"] == "neutral"
    full = history.summarize(history.stat_rows(con))
    early = history.summarize(history.stat_rows(con, before=cal[20]))
    k = ("oi_buildup", "long_buildup", 3)
    assert full[k][0] > early[k][0] and full[k][1] > 0  # positive forward abnormal return
    assert history.compute_stats(con) >= 1


def test_support_only_until_enough_history_then_base_rate(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ANALOGUE_MIN_N", "5")
    get_settings.cache_clear()
    states = {"oi_buildup": ("long_buildup", 1.2), "pcr": ("high", 1.5)}
    stats = {
        ("oi_buildup", "long_buildup", 3): (40, 0.8, 0.6, 1.5),
        ("pcr", "high", 3): (2, 5.0, 0.9, 1.0),
    }
    ev = {e.feature: e for e in evidence.assess(states, lambda f, s, w: stats.get((f, s, w)))}
    assert ev["pcr"].basis == "support" and ev["pcr"].weight == 0.0  # too few days: no effect
    assert ev["oi_buildup"].basis == "base_rate" and 0 < ev["oi_buildup"].weight <= 0.25
    scen = [
        Scenario(
            name="ai",
            probability=1.0,
            stock_move_pct=-1.0,
            timing_days=3,
            triggers=[],
            invalidation="x",
        )
    ]
    mixed = evidence.tilt(scen, list(ev.values()), 3)
    assert abs(sum(s.probability for s in mixed) - 1.0) < 1e-9
    assert any(s.name.startswith("positioning oi_buildup") for s in mixed)
    # support-only evidence alone never changes anything (never a standalone signal)
    assert evidence.tilt(scen, [ev["pcr"]], 3) == scen
    assert evidence.tilt([], list(ev.values()), 3) == []
    get_settings.cache_clear()


def test_total_weight_is_capped(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ANALOGUE_MIN_N", "5")
    get_settings.cache_clear()
    states = {
        f: (f"s{i}", 1.0)
        for i, f in enumerate(["oi_buildup", "pcr", "basis", "delivery", "iv_skew"])
    }
    ev = evidence.assess(states, lambda f, s, w: (500, 1.0, 0.6, 1.0))
    assert sum(e.weight for e in ev) == pytest.approx(0.25, abs=1e-3)
    assert sum(1 for e in ev if e.weight > 0) == 4  # strongest max_states only
    get_settings.cache_clear()


def test_backtest_positioning_variant_runs() -> None:
    import app.backtest.__main__ as cli
    from app.backtest.engine import Variant
    from app.backtest.runner import run
    from tests import bt_fixture as F

    assert "rules: + positioning evidence" in [v.name for v in cli.standard_variants("rules")]
    con, cal = F.build()
    F.add_training_reactions(con, cal)
    F.add_event(con, "e0", cal[300])
    base = run(con, Variant("base"), F.START, cal[-1])
    pos = run(con, Variant("pos", positioning=True), F.START, cal[-1])
    assert len(pos.trades) == len(base.trades)  # no positioning history in the fixture: no change


def test_signal_prediction_tilts_only_with_base_rate_evidence() -> None:
    from app.ai.schemas import AnalystOutput
    from app.signals.service import WorkItem, predict
    from tests.helpers import analyst_output

    out = AnalystOutput.model_validate(analyst_output())
    base = {"as_of": "2026-10-14", "source": "eod"}
    support = {
        **base,
        "evidence": [evidence.Evidence("pcr", "high", 1.5, "support", "bullish", 2).as_dict()],
    }
    used = {
        **base,
        "evidence": [
            evidence.Evidence(
                "oi_buildup", "long_buildup", 1.2, "base_rate", "bullish", 80, 2.0, 0.6, 0.2
            ).as_dict()
        ],
    }

    class C:
        direction = out.direction

    def item(pos):
        return WorkItem(C(), [], "", set(), "Telecom", None, positioning=pos)  # type: ignore[arg-type]

    p0, _ = predict(item(None), out)
    p1, _ = predict(item(support), out)
    p2, d2 = predict(item(used), out)
    assert p0 == p1  # support-only: no effect at all
    assert p2 is not None and p2.source.endswith("+positioning") and p2.move_pct != p0.move_pct
    assert any(s.name.startswith("positioning") for s in d2)
