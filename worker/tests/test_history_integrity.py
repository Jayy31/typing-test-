"""Phase 4 owner fixes: corporate actions, event timing, point-in-time universe, confounding,
analogue weight by sample size, sector benchmark."""

from datetime import date, time, timedelta

import duckdb
import pytest

from app.config import get_settings
from app.history import corp_actions, event_study, universe, verify
from app.history.analogues import AnalogueResult
from app.history.corp_actions import Action, adjust, contract_factor, parse_subject
from app.history.event_study import reaction_day
from app.history.store import init_schema


def test_parse_subjects():
    k, f, _ = parse_subject(
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
    )
    assert k == "split" and f == pytest.approx(0.2)
    k, f, _ = parse_subject("Bonus 1:2")
    assert k == "bonus" and f == pytest.approx(2 / 3)
    k, f, amt = parse_subject("Special Dividend - Rs 25 Per Share")
    assert k == "special_dividend" and f is None and amt == 25
    assert parse_subject("Demerger")[:2] == ("demerger", None)
    assert (
        parse_subject("Interim Dividend - Rs 5 Per Share")[0] is None
    )  # ordinary dividends ignored


def test_adjust_known_and_unknown_factors():
    days = [date(2024, 1, d) for d in (1, 2, 3, 4, 5)]
    close = [200.0, 210.0, 100.0, 104.0, 50.0]
    acts = [Action("X", days[2], "split", 0.5), Action("X", days[4], "demerger", None)]
    (adj,), breaks = adjust(days, [close], acts)
    assert adj[:4] == [100.0, 105.0, 100.0, 104.0] and breaks == {4}
    (adj,), _ = adjust(days, [close], [Action("X", days[1], "special_dividend", None, 20.0)])
    assert adj[0] == pytest.approx(200 * 0.9) and adj[1] == 210.0


def test_contract_factor_follows_strike_and_lot():
    acts = [
        Action("X", date(2024, 3, 1), "bonus", 0.5),
        Action("X", date(2024, 4, 1), "special_dividend", None, 5),
    ]
    assert (
        contract_factor(acts, date(2024, 2, 1), date(2024, 3, 1)) == 0.5
    )  # strike 100 -> 50, lot x2
    assert contract_factor(acts, date(2024, 3, 1), date(2024, 5, 1)) == 1.0


def test_reaction_day_labels():
    cal = [date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9)]  # Fri, Mon, Tue
    assert reaction_day(cal, date(2024, 1, 8), time(8, 30)) == (1, "pre_open")
    assert reaction_day(cal, date(2024, 1, 8), time(11, 0)) == (1, "intraday")
    assert reaction_day(cal, date(2024, 1, 8), time(15, 45)) == (2, "post_close")
    assert reaction_day(cal, date(2024, 1, 6), time(10, 0)) == (1, "non_trading_day")  # Saturday
    assert reaction_day(cal, date(2024, 1, 8), None) == (1, "unknown")
    assert reaction_day(cal, date(2024, 1, 9), time(16, 0)) == (None, "post_close")


def test_analogue_weight_by_sample_size():
    def an(n: int, n_eff: float) -> AnalogueResult:
        return AnalogueResult("stock", "all", 3, n, n_eff, 1, 1, 0.6, 1, 0, 2)

    assert an(4, 4).weight == 0 and not an(4, 4).usable
    assert an(5, 5).weight == pytest.approx(5 / 25)
    assert an(30, 20).weight == pytest.approx(0.5)  # effective (weighted) count
    assert "n=30 (effective 20.0), weight 50%" in an(30, 20).line()


# ---- synthetic history: one store exercising every rule -------------------------------------
SPLIT, E1, E3 = 200, 260, 199  # calendar indexes
JUMP = 0.05


def _calendar(n: int = 300) -> list[date]:
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture
def hist() -> tuple[duckdb.DuckDBPyConnection, list[date]]:
    con = duckdb.connect()
    init_schema(con)
    cal = _calendar()
    m = s = 100.0
    adj = 100.0
    for i, d in enumerate(cal):
        rm = 0.005 * (1 if i % 2 else -1) + 0.002 * ((i % 5) - 2)
        rs = 1.2 * rm + 0.003 * ((i % 3) - 1)
        if i:
            m, s = m * (1 + rm), s * (1 + rs)
            adj *= 1 + rs + (JUMP if i == E1 + 1 else 0)
        raw = adj * 2 if i < SPLIT else adj  # split 2:1 on SPLIT (factor 0.5)
        con.execute(
            "INSERT INTO hist_index VALUES (?, 'Nifty 50', ?), (?, 'Nifty IT', ?), (?, 'India VIX', 15)",
            [d, m, d, s, d],
        )
        con.execute(
            "INSERT INTO hist_prices VALUES (?, 'INFY', ?, ?, ?, ?, 1, 50)", [d, raw, raw, raw, raw]
        )
        con.execute("INSERT INTO hist_prices VALUES (?, 'TCS', 100, 100, 100, 100, 1, 50)", [d])
        xyz = 140.0 if i >= 100 else 100.0  # +40% with no corporate action -> verify must flag it
        con.execute(
            "INSERT INTO hist_prices VALUES (?, 'XYZ', ?, ?, ?, ?, 1, 50)", [d, xyz, xyz, xyz, xyz]
        )
        lot = 400 if i >= 250 else None  # bhavcopy lots only in the recent (UDiFF) period
        con.execute(
            "INSERT INTO hist_fno_universe (date, symbol, lot_size) VALUES (?, 'INFY', ?)", [d, lot]
        )
        if i < 150:  # XYZ was in F&O, then removed (must stay in history)
            con.execute(
                "INSERT INTO hist_fno_universe (date, symbol, lot_size) VALUES (?, 'XYZ', NULL)",
                [d],
            )
    con.execute(
        "INSERT INTO hist_corp_actions VALUES ('INFY', ?, 'split', 'split', 0.5, NULL, 'nse')",
        [cal[SPLIT]],
    )

    def ev(
        eid: str, i: int, t: time | None, sym: str | None, etype: str, d: date | None = None
    ) -> None:
        con.execute(
            "INSERT INTO hist_events (event_id, date, time, symbol, sector, theme, event_type, direction, source, sentiment, surprise, title, url, taxonomy_ver, prompt_ver) VALUES (?, ?, ?, ?, 'IT', NULL, ?, 'bullish', 's', 0.5, 0.5, 't', 'u', 'tx1', 'p')",
            [eid, d or cal[i], t, sym, etype],
        )

    ev("e1", E1, time(16, 10), "INFY", "results")  # after the close -> next day (the jump)
    ev("e1_dup", E1, time(17, 0), "INFY", "results")  # second article on the same event
    ev("e2", E1 + 3, time(10, 0), "INFY", "management")  # inside e1's 3/5/10-day windows
    ev("e3", E3, time(10, 0), "INFY", "results")  # next to the split -> windows dropped
    ev("e4", 120, time(10, 0), "TCS", "results")  # TCS never in F&O -> skipped
    ev("e5", 230, None, "INFY", "order_win")  # unknown time -> weight 0.5
    sat = cal[240] + timedelta(days=(5 - cal[240].weekday()) % 7)
    ev("e6", 0, time(10, 0), "INFY", "rating", d=sat)  # Saturday -> next trading day
    return con, cal


def test_event_study_rules(hist):
    con, cal = hist
    event_study.run(con)
    r = {
        (e, w): row
        for e, w, *row in con.execute(
            'SELECT event_id, "window", reaction_day, timing, abnormal_ret, abnormal_mkt, abnormal_sector, '
            "benchmark, confounded, weight FROM event_reactions"
        ).fetchall()
    }
    # timing + sector benchmark: jump measured on the next trading day vs Nifty IT, both stored
    day, timing, primary, mkt, sector, bench, confounded, weight = r[("e1", 0)]
    assert day == cal[E1 + 1] and timing == "post_close" and bench == "nifty it"
    assert (
        primary == sector == pytest.approx(JUMP * 100, abs=0.05)
        and mkt is not None
        and mkt != sector
    )
    # confounding: the management event 3 days later taints windows 3+, not 0/1
    assert r[("e1", 1)][6] is False and r[("e1", 3)][6] is True and r[("e1", 3)][7] == 0.5
    # corporate action: e3 is a day before the split -> every window and the drift dropped
    assert not [k for k in r if k[0] == "e3"]
    # point-in-time universe: TCS never in F&O
    assert not [k for k in r if k[0] == "e4"]
    assert r[("e5", 0)][1] == "unknown" and r[("e5", 0)][7] == 0.5
    assert r[("e6", 0)][1] == "non_trading_day"
    # duplicate coverage counts once in the stats
    n, n_eff = con.execute(
        """SELECT n, n_eff FROM analogue_stats WHERE event_type = 'results' AND symbol = 'INFY'
           AND regime = 'all' AND "window" = 0"""
    ).fetchone()
    assert n == 1 and n_eff == 1.0


def test_exclude_mode_for_confounded(hist, monkeypatch):
    con, _ = hist
    cfg = event_study._cfg()
    cfg["confounding"] = {**cfg["confounding"], "mode": "exclude"}
    monkeypatch.setattr(event_study, "_cfg", lambda: cfg)
    event_study.compute_reactions(con)
    assert con.execute(
        """SELECT weight FROM event_reactions WHERE event_id = 'e1' AND "window" = 3"""
    ).fetchone() == (0.0,)


def test_point_in_time_lots_and_universe(hist):
    con, cal = hist
    assert universe.fill_lots(con) > 0
    assert universe.lot_on(con, "INFY", cal[260]) == (400, "bhavcopy")
    assert universe.lot_on(con, "INFY", cal[100]) == (200, "inferred")  # before the 2:1 split
    assert universe.fno_on(con, cal[10]) == {"INFY", "XYZ"}
    assert universe.fno_on(con, cal[200]) == {"INFY"}
    assert universe.lot_on(con, "XYZ", cal[200]) == (None, None)  # removed by then
    assert universe.all_symbols(con) == {"INFY", "XYZ"}  # removed stocks kept


def test_verify_flags_big_moves_without_corporate_action(hist):
    con, _ = hist
    event_study.run(con)
    big = verify.big_moves_without_action(con, 25)
    assert [b[0] for b in big] == ["XYZ"]  # INFY's split day is explained by the recorded action
    lines, problems = verify.report(con)
    text = "\n".join(lines)
    assert (
        problems
        and "XYZ" in text
        and "since removed or delisted" in text
        and "timing post_close" in text
    )


def test_manual_actions_win_over_nse_text(tmp_path):
    con = duckdb.connect()
    init_schema(con)
    f = tmp_path / "ca.yaml"
    f.write_text("actions:\n  - {symbol: ABC, ex_date: 2024-05-02, kind: demerger, factor: 0.9}\n")
    assert corp_actions.load_manual(con, f) == 1
    corp_actions.store(
        con,
        corp_actions.parse_rows(
            [{"symbol": "ABC", "subject": "Demerger", "exDate": "02-May-2024"}]
        ),
    )
    assert con.execute("SELECT factor, source FROM hist_corp_actions").fetchall() == [
        (0.9, "manual")
    ]


def test_settings_defaults():
    s = get_settings()
    assert s.analogue_prior_n == 20 and s.analogue_min_n == 5


def test_old_store_is_upgraded_on_read(tmp_path):
    from app.history.store import SCHEMA_VERSION, connect

    path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE store_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    con.execute("INSERT INTO store_meta VALUES ('schema_version', '2')")
    con.execute('CREATE TABLE event_reactions (event_id VARCHAR, "window" INTEGER)')
    con.close()
    with connect(path) as ro:
        cols = {r[0] for r in ro.execute("DESCRIBE event_reactions").fetchall()}
        ver = ro.execute("SELECT value FROM store_meta WHERE key = 'schema_version'").fetchone()
    assert {"timing", "abnormal_sector", "confounded", "weight"} <= cols and ver == (
        str(SCHEMA_VERSION),
    )


def test_why_shows_n_and_weight():
    from app.notify.explain import analogue_lines
    from app.signals.service import analogue_json

    a = analogue_json(
        AnalogueResult("sector", "normal", 3, 12, 10.0, 1.5, 1.2, 0.6, 2.0, -0.5, 3.0)
    )
    text = "\n".join(analogue_lines(a))
    assert "n=12 (effective 10.0), weight 33% in the prediction" in text
    small = analogue_json(AnalogueResult("sector", "all", 3, 3, 3.0, 1, 1, 1, 1, 0, 2))
    assert "too few events: shown only, weight 0%" in analogue_lines(small)[0]


def test_lot_overrides_replace_inferred_only(hist, tmp_path):
    con, cal = hist
    universe.fill_lots(con)
    f = tmp_path / "lots.yaml"
    f.write_text(
        f"overrides:\n  - {{symbol: INFY, from: {cal[0]}, to: {cal[99]}, lot_size: 250}}\n"
        f"  - {{symbol: INFY, from: {cal[255]}, lot_size: 999}}\n"
    )
    universe.apply_overrides(con, f)
    assert universe.lot_on(con, "INFY", cal[50]) == (250, "override")
    assert universe.lot_on(con, "INFY", cal[150]) == (200, "inferred")
    assert universe.lot_on(con, "INFY", cal[260]) == (
        400,
        "bhavcopy",
    )  # exact lots never overridden


def test_value_report_pct_primary_for_inferred_lots():
    r = universe.value_report(12.5, 3000, "inferred")
    assert (
        r.primary == "pct"
        and r.text() == "+12.5% of premium (Rs +3,000, approximate (inferred lot size))"
    )
    assert universe.value_report(12.5, 3000, None).primary == "pct"
    exact = universe.value_report(12.5, 3000, "bhavcopy")
    assert exact.primary == "inr" and exact.note is None
    assert universe.value_report(-5, 1000, "override").text() == "Rs +1,000 (-5.0% of premium)"
