"""Phase 14 PR 0: strategy specs (core rules in the schema) and the spec backtester."""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.backtest.engine import Variant
from app.backtest.runner import run_rules
from app.lab import scheduled
from app.lab.backtest import RunOptions, run_spec
from app.lab.spec import StrategySpec, parse
from tests import bt_fixture as F


def spec(**kw) -> StrategySpec:
    base = {
        "name": "post_results",
        "source": "known_pattern",
        "trigger": {"event_types": ["results"]},
    }
    for k, v in kw.items():
        base[k] = {**base.get(k, {}), **v} if isinstance(v, dict) else v
    return StrategySpec.model_validate(base)


@pytest.mark.parametrize(
    "bad",
    [
        {"filters": {"symbols": ["NIFTY"]}},  # index derivative
        {"filters": {"symbols": ["BANKNIFTY"]}},
        {"option": {"expiry": "weekly"}},  # weekly expiry
        {"option": {"side": "sell"}},  # no selling field exists
        {"legs": 2},  # no multi-leg field exists
        {"option": {"strike": "delta"}},  # delta without a target
        {"trigger": {"event_types": ["not_a_type"]}},
        {
            "entry": {"timing": "before_event", "days_before": 5}
        },  # direction from_event before the event
        {
            "entry": {"timing": "before_event", "days_before": 30},
            "trigger": {"direction": "bullish"},
        },
        {"exits": {"exit_before_event": True}},
        {"exits": {"targets_pct": [50, 25]}},
    ],
)
def test_core_rules_are_in_the_schema(bad) -> None:
    with pytest.raises(ValidationError):
        spec(**bad)


def test_option_type_only_follows_direction_and_round_trips() -> None:
    assert (
        StrategySpec.option_type("bullish") == "CE" and StrategySpec.option_type("bearish") == "PE"
    )
    s = spec(option={"strike": "delta", "target_delta": 0.4}, exits={"max_hold_days": 5})
    assert parse(s.to_yaml()) == s
    assert (
        s.spec_hash()
        == spec(
            option={"strike": "delta", "target_delta": 0.4},
            exits={"max_hold_days": 5},
            description="x",
        ).spec_hash()
    )
    with pytest.raises(ValidationError):
        spec(trigger={"event_types": ["results"], "direction": "base_rate"})  # needs min n


def setup():
    con, cal = F.build()
    F.add_training_reactions(con, cal)
    F.add_event(con, "e1", cal[300])
    return con, cal


def test_news_core_reproduces_the_rules_backtest() -> None:
    con, cal = setup()
    a = run_rules(con, Variant("news_core"), F.START, cal[-1])
    b = run_spec(con, spec(name="news_core", source="news_core"), F.START, cal[-1])
    key = lambda r: [(t.entry_day, t.strike, t.expiry, t.pnl_inr) for t in r.trades]  # noqa: E731
    assert key(a) == key(b) and len(a.trades) == 1


def test_event_close_entry_atm_call_monthly_expiry() -> None:
    con, cal = setup()
    res = run_spec(con, spec(), F.START, cal[-1])
    assert len(res.trades) == 1, [s.__dict__ for s in res.skips]
    t = res.trades[0]
    assert t.entry_day == cal[300] and t.option_type == "CE" and t.entry_ask > t.entry_mid
    assert abs(t.strike - t.spot_entry) <= 20 and t.exit_day and t.exit_day > cal[300]
    assert t.expiry.day >= 20  # a monthly expiry (last trading day of its month)


def test_next_open_itm_and_time_exit() -> None:
    con, cal = setup()
    t = run_spec(
        con,
        spec(
            entry={"timing": "next_open"},
            option={"strike": "itm1"},
            exits={"max_hold_days": 2, "targets_pct": [400]},
        ),
        F.START,
        cal[-1],
    ).trades[0]
    assert t.entry_day == cal[301] and t.strike < t.spot_entry
    assert t.exit_reason == "TIME" and t.exit_day == cal[303]


def test_iv_spike_exit() -> None:
    con, cal = setup()
    base = run_spec(con, spec(exits={"targets_pct": [400]}), F.START, cal[-1]).trades[0]
    con.execute(
        "INSERT INTO hist_iv VALUES (?, 'ABC', ?, ?, 'CE', 90.0, 1000)",
        [cal[302], base.expiry, base.strike],
    )
    t = run_spec(
        con, spec(exits={"targets_pct": [400], "iv_spike_exit_pts": 20}), F.START, cal[-1]
    ).trades[0]
    assert t.exit_reason == "IV_SPIKE" and t.exit_day == cal[302]


def test_pre_event_entry_needs_a_public_date_and_exits_before() -> None:
    con, cal = setup()
    s = spec(
        name="pre_results",
        trigger={"event_types": ["results"], "direction": "bullish"},
        entry={"timing": "before_event", "days_before": 5},
        exits={"exit_before_event": True, "targets_pct": [400]},
    )
    con.execute(
        "INSERT INTO hist_scheduled VALUES ('ABC', 'results', ?, ?, 'nse', 'n1')",
        [cal[330], cal[310]],
    )  # public 15 days before the entry
    t = run_spec(con, s, F.START, cal[-1]).trades[0]
    assert t.entry_day == cal[325] and t.exit_reason == "EVENT_EXIT" and t.exit_day == cal[329]
    con.execute("UPDATE hist_scheduled SET intimated_on = ?", [cal[324]])  # 1 day: too late
    res = run_spec(con, s, F.START, cal[-1])
    assert not res.trades and any(k.stage == "lookahead" for k in res.skips)


def test_filters_and_spread_multiplier() -> None:
    con, cal = setup()
    assert not run_spec(con, spec(filters={"sectors": ["Pharma"]}), F.START, cal[-1]).trades
    one = run_spec(con, spec(), F.START, cal[-1]).trades[0]
    two = run_spec(con, spec(), F.START, cal[-1], opts=RunOptions(spread_mult=2.0)).trades[0]
    assert two.entry_ask > one.entry_ask and two.pnl_inr < one.pnl_inr


def test_no_look_ahead_in_spec_decisions() -> None:
    con, cal = setup()
    a = run_spec(con, spec(), F.START, cal[-1]).trades[0]
    con.execute("UPDATE hist_prices SET close = close * 0.5 WHERE date > ?", [cal[300]])
    b = run_spec(con, spec(), F.START, cal[-1]).trades[0]
    assert (a.entry_day, a.strike, a.expiry, a.entry_ask) == (
        b.entry_day,
        b.strike,
        b.expiry,
        b.entry_ask,
    )


def test_board_meeting_parser() -> None:
    rows = [
        (
            "n1",
            datetime(2024, 1, 3, 18, 0),
            "TCS",
            "Board Meeting Intimation",
            "to consider and approve the Unaudited Financial Results on 11-01-2024",
        ),
        (
            "n2",
            datetime(2024, 1, 2, 18, 0),
            "TCS",
            "Board Meeting Intimation",
            "Meeting on January 11, 2024 to consider financial results",
        ),
        ("n3", datetime(2024, 1, 3), "INFY", "Board Meeting", "fund raising on 20-01-2024"),
    ]
    got = scheduled.parse(rows)
    assert got == [
        ("TCS", "results", date(2024, 1, 11), date(2024, 1, 2), "nse_board_meeting", "n2")
    ]
