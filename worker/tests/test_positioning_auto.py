"""Phase 12 fixes: positioning history builds itself after the 5-year backfill; previous-session
data is always labelled "EOD, previous session (<date>)"."""

from datetime import date, datetime, timedelta

from sqlalchemy import Engine, select

from app.config import IST
from app.db.config_store import get_config
from app.db.models import Alert, GoliveCheck, PaperTrade, PositioningSnapshot, Signal
from app.db.session import session_scope
from app.history.store import connect
from app.positioning import auto
from app.positioning.service import source_label

NOW = datetime(2026, 10, 19, 23, 40, tzinfo=IST)


def _load(kind_days: int) -> None:
    with connect(write=True) as con:
        d = date(2021, 1, 1)
        for kind in ("prices", "options", "index"):
            for i in range(kind_days):
                con.execute(
                    "INSERT OR REPLACE INTO loaded_files VALUES (?, ?, 'ok', 1, now())",
                    [kind, d + timedelta(days=i)],
                )


def test_waits_for_history_then_builds_once_then_nightly(engine: Engine, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        auto,
        "first_build",
        lambda e, now: (
            calls.append("first")
            or {
                "futures_days": 1200,
                "futures_errors": 0,
                "states": 9000,
                "base_rates": 40,
                "days": 1200,
                "finished_at": now.isoformat(),
            }
        ),
    )
    import app.positioning.history as hist

    monkeypatch.setattr(hist, "nightly", lambda: calls.append("nightly") or 7)
    _load(10)
    assert auto.job(engine, NOW).startswith("waiting")  # history not complete: nothing runs
    assert calls == []
    _load(1300)
    assert auto.job(engine, NOW) == "first build: 1200 days"
    assert auto.job(engine, NOW + timedelta(days=1)) == "nightly: 7 new states"
    assert calls == ["first", "nightly"]
    with session_scope(engine) as s:
        assert get_config(s, auto.DONE_KEY) == "2026-10-19"
        gl = s.scalars(select(GoliveCheck)).one()
        assert gl.kind == "auto" and gl.results_json["title"] == "Positioning history (automatic)"
        alert = s.scalars(select(Alert)).one()
        assert "POSITIONING HISTORY READY" in alert.text and "1200" in alert.text


def test_first_build_uses_cached_futures(monkeypatch) -> None:
    from app.history import backfill

    seen = {}

    def fake_run(kind, start, end, con=None, progress=True, dl=None):
        seen["kind"] = kind
        return {"ok": 3, "error": 0, "no_file": 0, "skipped": 0, "rows": 30}

    monkeypatch.setattr(backfill, "run", fake_run)
    res = auto.first_build(None, NOW)
    assert seen["kind"] == "futures" and res["futures_days"] == 3 and res["futures_errors"] == 0


def test_eod_label_everywhere(engine: Engine) -> None:
    from app.notify.briefs import evening_summary_text

    assert source_label("eod", date(2026, 10, 16)) == "EOD, previous session (2026-10-16)"
    assert source_label("live", "2026-10-19") == "live intraday (2026-10-19)"
    now = NOW.replace(hour=16, minute=0)
    with session_scope(engine) as s:
        g = Signal(
            symbol="TCS", direction="CALL", score=80, status="NEW", created_at=now, updated_at=now
        )
        s.add(g)
        s.add(
            PositioningSnapshot(
                day=date(2026, 10, 16),
                symbol="TCS",
                source="eod",
                states_json={"oi_buildup": ["long_buildup", 1.2]},
            )
        )
    with session_scope(engine) as s:
        text = evening_summary_text(s, now) or ""
    assert "TCS: oi_buildup long_buildup [EOD, previous session (2026-10-16)]" in text
    _ = PaperTrade
