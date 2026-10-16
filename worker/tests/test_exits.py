"""Exit rules: the same pure module serves live paper trading, the simulator and the backtester."""

import pytest

from app.trading.exits import Bar, ExitRules, PositionState, step

R = ExitRules.from_config()


def bar(
    o: float,
    h: float,
    lo: float,
    c: float,
    dte: int = 15,
    since: int | None = None,
    broken: bool = False,
) -> Bar:
    return Bar(o, h, lo, c, dte, since, broken)


def run(*bars: Bar, entry: float = 100.0) -> PositionState:
    st = PositionState.open(entry, R)
    for b in bars:
        st = step(st, b, R)
    return st


def events(st: PositionState) -> list[str]:
    return [f.event for f in st.fills]


def test_config_matches_design() -> None:
    assert (R.stop_loss_pct, R.targets_pct) == (-50, (25, 50, 100))
    assert R.time_stop_trading_days == 5 and R.no_move_sessions == 2


def test_stop_loss_at_minus_50() -> None:
    st = run(bar(100, 101, 60, 70), bar(70, 71, 49, 55))
    assert events(st) == ["SL"] and st.closed and st.fills[0].price == 50
    assert st.realised_pct() == pytest.approx(-50)


def test_gap_below_stop_fills_at_open() -> None:
    st = run(bar(40, 45, 35, 42))
    assert events(st) == ["SL"] and st.fills[0].price == 40


def test_targets_book_thirds_and_move_stop() -> None:
    st = run(bar(100, 126, 95, 120))
    assert (
        events(st) == ["T1"] and st.stop == 100 and st.remaining == pytest.approx(2 / 3, abs=1e-3)
    )
    st = step(st, bar(120, 151, 110, 140), R)
    assert events(st) == ["T1", "T2"] and st.stop == 125  # trails at T1 level after T2
    st = step(st, bar(140, 201, 135, 190), R)
    assert events(st) == ["T1", "T2", "T3"] and st.closed and st.remaining == 0
    assert st.realised_pct() == pytest.approx((25 + 50 + 100) / 3, abs=0.1)


def test_one_bar_can_hit_several_targets() -> None:
    st = run(bar(100, 160, 99, 150))
    assert events(st) == ["T1", "T2"] and st.stop == 125


def test_gap_above_target_fills_at_open() -> None:
    st = run(bar(130, 135, 128, 132))
    assert events(st) == ["T1"] and st.fills[0].price == 130


def test_stop_and_target_in_same_bar_assumes_stop_first() -> None:
    st = run(bar(100, 130, 45, 110))
    assert events(st) == ["SL"] and st.fills[0].price == 50


def test_breakeven_stop_after_t1_is_trail_exit() -> None:
    st = run(bar(100, 126, 101, 110), bar(110, 112, 99, 100))
    assert events(st) == ["T1", "TRAIL"] and st.fills[1].price == 100
    assert st.realised_pct() == pytest.approx(25 / 3, abs=0.1)


def test_trailing_stop_after_t2_at_t1_level() -> None:
    st = run(bar(100, 151, 100, 140), bar(140, 141, 120, 125))
    assert events(st) == ["T1", "T2", "TRAIL"] and st.fills[2].price == 125


def test_thesis_broken_exits_at_close() -> None:
    st = run(bar(100, 110, 90, 95, broken=True))
    assert events(st) == ["THESIS"] and st.fills[0].price == 95


def test_time_stop_before_expiry() -> None:
    st = run(bar(100, 110, 90, 104, dte=6), bar(104, 110, 95, 102, dte=5))
    assert events(st) == ["TIME"] and st.fills[0].price == 102


def test_no_move_after_event() -> None:
    st = run(bar(100, 105, 96, 103, since=1), bar(103, 106, 97, 104, since=2))
    assert events(st) == ["NOMOVE"]
    # a meaningful move (>= 10%) keeps the trade open
    st = run(bar(100, 112, 96, 111, since=2))
    assert not st.closed


def test_closed_position_ignores_further_bars() -> None:
    st = run(bar(40, 45, 35, 42), bar(200, 300, 150, 250))
    assert events(st) == ["SL"]
