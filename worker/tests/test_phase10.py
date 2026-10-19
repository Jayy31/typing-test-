"""Phase 10: analytics, AI vs rules, monthly report (validated AI summary, PDF), learning loop
(97.5% interval, cap, PR on accept, one merge per month, 2-month review + revert), hardening."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select

from app.ai.client import AiClient
from app.analytics import ai_vs_rules, github, learning, live, monthly, yaml_edit
from app.collectors.base import circuit_open, record_source
from app.config import IST, Settings, get_settings
from app.db.models import (
    AiVsRules,
    Alert,
    Analysis,
    EventCalendar,
    MonthlyReport,
    OptionQuote,
    PaperTrade,
    PortfolioSnapshot,
    PredictionScore,
    Signal,
    Suggestion,
)
from app.db.session import session_scope
from app.health import quality, watchdog
from app.history.analogues import AnalogueResult
from tests.helpers import FakeMessages, monthly_expiry_after, seed_instruments

NOW = datetime(2026, 11, 1, 8, 0, tzinfo=IST)


def _trade(
    s: Any,
    i: int,
    pnl_pct: float,
    when: datetime,
    *,
    score: float = 70,
    edge: float = 1.5,
    fill: str = "fillable",
    sector: str = "IT",
    exit_reason: str = "T1",
) -> None:
    sig = Signal(
        id=i,
        symbol=f"S{i}",
        sector=sector,
        direction="bullish",
        score=score,
        status="CONVERTED_TO_TRADE",
        edge_ratio=edge,
        p_t1_before_sl=0.6,
        expected_value=15,
        impact_order=1,
        prediction_source="single",
    )
    s.add(sig)
    s.flush()
    s.add(
        PaperTrade(
            signal_id=i,
            symbol=f"S{i}",
            entry_ts=when - timedelta(days=2),
            entry_premium=10,
            lots=3,
            sl=5,
            t1=12.5,
            t2=15,
            t3=20,
            status="CLOSED",
            exit_ts=when,
            exit_premium=10 * (1 + pnl_pct / 100),
            exit_reason=exit_reason,
            pnl=pnl_pct * 30,
            pnl_pct=pnl_pct,
            fillability=fill,
            lot_size=100,
            option_type="CE",
            strike=100,
            expiry=monthly_expiry_after((when - timedelta(days=2)).date() + timedelta(days=10)),
        )
    )


# ---- analytics ----------------------------------------------------------------------------
def test_live_metrics_breakdowns_and_unfillable(engine: Engine) -> None:
    start = datetime(2026, 10, 1, tzinfo=IST)
    with session_scope(engine) as s:
        for i in range(1, 13):
            _trade(
                s,
                i,
                20.0 if i % 3 else -50.0,
                start + timedelta(days=i),
                score=82 if i < 5 else 68,
                fill="unfillable" if i == 12 else "fillable",
                sector="IT" if i % 2 else "Banks",
            )
        for d in range(5):
            s.add(
                PortfolioSnapshot(
                    ts=start + timedelta(days=d),
                    capital=1e6,
                    realised_inr=0,
                    unrealised_inr=0,
                    equity=1e6 + d * 1000 - (3000 if d == 3 else 0),
                    peak_equity=1e6,
                    drawdown_pct=0,
                    open_trades=0,
                    premium_at_risk_inr=0,
                )
            )
    with session_scope(engine) as s:
        out = live.summary(s)
    a = out["all"]
    assert a["n"] == 12 and a["win_rate"] == pytest.approx(8 / 12, abs=1e-3)
    assert a["expectancy_pct"] == pytest.approx((8 * 20 - 4 * 50) / 12, abs=0.01)
    assert a["ci"] and a["ci"][0] < a["expectancy_pct"] < a["ci"][1]
    assert out["excluding_unfillable"]["n"] == 11
    assert set(out["breakdowns"]["sector"]) == {"IT", "Banks"}
    assert set(out["breakdowns"]["score_band"]) == {"80+", "65-69"}
    assert out["risk"]["days"] == 5 and out["risk"]["max_drawdown_pct"] > 0


def test_drift_alert_when_live_outside_backtest_interval(engine: Engine) -> None:
    from app.db.models import BacktestRun

    start = datetime(2026, 9, 1, tzinfo=IST)
    with session_scope(engine) as s:
        for i in range(1, 31):
            _trade(s, i, -20.0, start + timedelta(days=i))
        s.add(
            BacktestRun(
                config_hash="h",
                config_json={},
                period_start=date(2021, 1, 1),
                period_end=date(2026, 1, 1),
                status="done",
                started_at=start,
                metrics_json={
                    "variant": {"name": "rules: v2.1 rules, per_lot"},
                    "bootstrap": {"expectancy_p5": 2.0, "expectancy_p95": 12.0},
                },
            )
        )
    assert live.drift_check(engine, NOW) == 1
    with session_scope(engine) as s:
        assert "below the backtest's 90% interval" in s.scalars(select(Alert)).one().text


def _an(n: int, mean: float, hit: float = 0.65, std: float = 2.0) -> AnalogueResult:
    return AnalogueResult("event_type", "all", 3, n, float(n), mean, mean, hit, std, -1.0, 3.0)


def test_rules_decision_and_ai_vs_rules_summary(engine: Engine) -> None:
    assert ai_vs_rules.rules_decision(None, "bullish")[0] is None
    assert ai_vs_rules.rules_decision(_an(3, 2.0), "bullish")[0] is None  # too few events
    assert ai_vs_rules.rules_decision(_an(40, 2.0), "bullish")[0] == "bullish"
    assert (
        ai_vs_rules.rules_decision(_an(40, 0.2, std=4.0), "bullish")[0] is None
    )  # interval spans 0
    assert ai_vs_rules.rules_decision(_an(40, 2.0, hit=0.5), "bearish")[0] is None
    seed_instruments(engine, {"TCS": 175})
    with session_scope(engine) as s:
        for ai_sig, ai_dir, an, move in [
            (True, "bullish", _an(40, 2.0), 3.0),
            (True, "bearish", None, 2.0),
            (False, "bullish", _an(40, 2.0), -1.0),
        ]:
            a = Analysis(symbol="TCS", thesis="t", direction=ai_dir, created_at=NOW, window_days=3)
            s.add(a)
            s.flush()
            ai_vs_rules.record(s, a, "bullish", an, ai_sig)
            s.add(
                PredictionScore(
                    analysis_id=a.id,
                    symbol="TCS",
                    direction=ai_dir,
                    predicted_move_pct=2,
                    window_days=3,
                    spot_start=100,
                    created_at=NOW,
                    path_json=[],
                    status="scored",
                    realised_move_pct=move if ai_dir == "bullish" else -move,
                )
            )
    assert ai_vs_rules.resolve(engine) == 3
    with session_scope(engine) as s:
        rows = s.scalars(select(AiVsRules)).all()
        out = ai_vs_rules.summarize(list(rows))
    assert (
        out["ai"]["n"] == 2 and out["ai"]["hit_rate"] == 0.5
    )  # bullish up 3% ok, bearish while stock rose 2% wrong
    assert out["rules"]["n"] == 2 and out["rules_only"]["n"] == 1 and out["ai_only"]["n"] == 1


# ---- monthly report -------------------------------------------------------------------------
def test_ai_summary_figures_must_exist_in_the_data(engine: Engine) -> None:
    data = {
        "trades": {"all": {"n": 12, "win_rate": 0.583, "expectancy_pct": 4.17}},
        "ai_spend_inr": 412.0,
        "month": "2026-10",
    }
    assert (
        monthly.unknown_figures(
            "12 trades with a 58% win rate and +4.2% expectancy in 2026-10; spend Rs 412.", data
        )
        == []
    )
    assert monthly.unknown_figures("Expectancy improved by 3.5% versus September.", data) == ["3.5"]

    class Seq(FakeMessages):
        pass

    good = monthly.SummaryOut(summary="October had 12 trades, win rate 58%.")
    bad = monthly.SummaryOut(summary="Profits rose 99% on strong demand.")
    st = Settings(_env_file=None, ai_model_tagger="cheap")  # type: ignore[call-arg]
    assert monthly.ai_summary(AiClient(st, engine, Seq(bad, good)), data) == (
        "October had 12 trades, win rate 58%.",
        "ok",
    )
    assert monthly.ai_summary(AiClient(st, engine, Seq(bad)), data) == (None, "rejected")


def test_monthly_report_job_pdf_and_alert(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HISTORY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    with session_scope(engine) as s:
        for i in range(1, 8):
            _trade(
                s,
                i,
                25.0 if i % 2 else -30.0,
                datetime(2026, 10, 2 + i, 12, tzinfo=IST),
                sector="IT" if i < 5 else "Banks",
            )
    ai = AiClient(
        Settings(_env_file=None, ai_model_tagger="cheap"),
        engine,
        FakeMessages(monthly.SummaryOut(summary="7 trades closed.")),
    )  # type: ignore[call-arg]
    assert monthly.monthly_report_job(engine, NOW, ai) == 1
    assert monthly.monthly_report_job(engine, NOW, ai) == 0  # once per month
    with session_scope(engine) as s:
        rep = s.scalars(select(MonthlyReport)).one()
        alert = s.scalars(select(Alert)).one()
    assert (
        rep.month == date(2026, 10, 1)
        and rep.ai_summary_status == "ok"
        and "WHAT WORKED" in rep.text
    )
    assert "IT" in rep.text and rep.data_json["trades"]["all"]["n"] == 7
    assert (
        alert.kind == "report"
        and alert.file_path
        and Path(alert.file_path).read_bytes()[:4] == b"%PDF"
    )
    get_settings.cache_clear()


# ---- learning loop ----------------------------------------------------------------------------
def test_yaml_edit_changes_one_line_and_keeps_comments() -> None:
    text = "a: 1\nselection:\n  enabled: true\n  min_p_t1_before_sl: 0.55   # gate\nother:\n  min_p_t1_before_sl: 9\n"
    new = yaml_edit.set_value(text, "selection.min_p_t1_before_sl", 0.6)
    assert (
        new.splitlines()[3] == "  min_p_t1_before_sl: 0.6   # gate"
        and new.splitlines()[5] == "  min_p_t1_before_sl: 9"
    )
    assert yaml_edit.get_value(text, "other.min_p_t1_before_sl") == "9"
    with pytest.raises(KeyError):
        yaml_edit.set_value(text, "selection.missing", 1)


def _opps_data(s: Any, start: datetime) -> None:
    """Trades with edge ratio 1.2-1.3 lose; 1.3+ win: raising edge_ratio_min to 1.3 should help."""
    for i in range(1, 81):
        edge = 1.25 if i % 2 else 1.6
        _trade(
            s,
            i,
            -30.0 + (i % 5) if edge < 1.3 else 25.0 + (i % 7),
            start + timedelta(hours=20 * i),
            edge=edge,
            score=70,
        )


def test_learning_suggests_only_robust_changes_with_cap(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime(2026, 8, 3, tzinfo=IST)
    with session_scope(engine) as s:
        _opps_data(s, start)
    assert learning.run_monthly(engine, NOW) == 1
    with session_scope(engine) as s:
        g = s.scalars(select(Suggestion)).one()
        assert (g.param, g.current_value, g.proposed_value, g.status) == (
            "edge_ratio_min",
            1.2,
            1.3,
            "pending",
        )
        e = g.evidence_json
        assert (
            e["ci_level"] == 0.975
            and e["ci"][0] > 0
            and min(e["halves"]) > 0
            and e["stress_mean_diff_pct"] > 0
        )
        assert g.expected_effect > 0
        assert s.scalars(select(Alert).where(Alert.kind == "suggestion")).one()
    assert learning.run_monthly(engine, NOW) == 0  # no duplicate while one is pending
    cfg = dict(learning.cfg())
    cfg["max_suggestions_per_month"] = 0
    monkeypatch.setattr(learning, "cfg", lambda: cfg)
    with session_scope(engine) as s:
        s.query(Suggestion).delete()
    assert learning.run_monthly(engine, NOW) == 0  # cap


class FakeGitHub:
    def __init__(self) -> None:
        self.files = {
            "worker/config/options.yaml": Path(learning.CONFIG_DIR / "options.yaml").read_text()
        }
        self.prs: dict[int, dict[str, Any]] = {}
        self.merged: list[int] = []

    def get_file(self, path: str) -> tuple[str, str]:
        return self.files[path], "sha1"

    def create_branch(self, branch: str) -> None:
        self.branch = branch

    def put_file(self, path: str, content: str, sha: str, branch: str, message: str) -> None:
        self.put = (path, content, branch)

    def create_pr(self, branch: str, title: str, body: str) -> tuple[int, str]:
        n = len(self.prs) + 1
        self.prs[n] = {"title": title, "body": body}
        return n, f"https://github.com/o/r/pull/{n}"

    def merge_pr(self, number: int) -> None:
        self.merged.append(number)


def test_accept_opens_pr_and_one_merge_per_month(engine: Engine) -> None:
    gh = FakeGitHub()
    with session_scope(engine) as s:
        for param, path, cur, new, eff in [
            ("edge_ratio_min", "edge_ratio_min", 1.2, 1.3, 8.0),
            ("min_p_t1_before_sl", "selection.min_p_t1_before_sl", 0.55, 0.6, 12.0),
        ]:
            s.add(
                Suggestion(
                    month=date(2026, 10, 1),
                    kind="change",
                    param=param,
                    file="worker/config/options.yaml",
                    yaml_path=path,
                    current_value=cur,
                    proposed_value=new,
                    expected_effect=eff,
                    evidence_json={
                        "n": 60,
                        "mean_diff_pct": 0.5,
                        "ci": [0.1, 0.9],
                        "ci_level": 0.975,
                        "halves": [0.4, 0.6],
                        "stress_mean_diff_pct": 0.2,
                    },
                    status="accepted",
                    decided_at=datetime(2026, 10, 20, tzinfo=IST),
                )
            )
    assert learning.open_prs(engine, gh) == 2  # type: ignore[arg-type]
    assert learning.open_prs(engine, gh) == 0
    assert "min_p_t1_before_sl: 0.6" in gh.put[1] and "97.5% interval" in gh.prs[1]["body"]
    assert learning.merge_monthly(engine, NOW, gh) == 1  # type: ignore[arg-type]
    assert learning.merge_monthly(engine, NOW, gh) == 0  # a re-run never merges a second change
    with session_scope(engine) as s:
        applied = s.scalars(select(Suggestion).where(Suggestion.status == "applied")).all()
    assert (
        len(gh.merged) == 1 and applied[0].param == "min_p_t1_before_sl"
    )  # larger expected effect first


def test_merge_is_one_per_month(engine: Engine) -> None:
    gh = FakeGitHub()
    with session_scope(engine) as s:
        for i in range(2):
            s.add(
                Suggestion(
                    month=date(2026, 10, 1),
                    kind="change",
                    param=f"p{i}",
                    file="worker/config/options.yaml",
                    yaml_path="edge_ratio_min",
                    current_value=1.2,
                    proposed_value=1.3,
                    expected_effect=5.0 + i,
                    evidence_json={},
                    status="accepted",
                    pr_number=10 + i,
                    decided_at=datetime(2026, 10, 9, tzinfo=IST),
                )
            )
    assert learning.merge_monthly(engine, NOW, gh) == 1  # type: ignore[arg-type]
    with session_scope(engine) as s:
        assert (
            len(s.scalars(select(Suggestion).where(Suggestion.status == "accepted")).all()) == 1
        )  # waits for next month


def test_review_after_two_months_suggests_revert(engine: Engine) -> None:
    applied_at = datetime(2026, 8, 25, tzinfo=IST)
    with session_scope(engine) as s:
        s.add(
            Suggestion(
                id=7,
                month=date(2026, 8, 1),
                kind="change",
                param="edge_ratio_min",
                file="worker/config/options.yaml",
                yaml_path="edge_ratio_min",
                current_value=1.2,
                proposed_value=1.3,
                expected_effect=6.0,
                evidence_json={},
                status="applied",
                applied_at=applied_at,
            )
        )
        # since the change: trades with edge 1.2-1.3 (now excluded) would have WON -> the change hurt
        for i in range(1, 61):
            edge = 1.25 if i % 2 else 1.6
            _trade(
                s, i, 30.0 if edge < 1.3 else 5.0, applied_at + timedelta(hours=20 * i), edge=edge
            )
    created = learning.review_applied(engine, NOW)
    with session_scope(engine) as s:
        orig = s.get(Suggestion, 7)
        rv = s.get(Suggestion, created[0])
        assert orig is not None and orig.review_json and orig.review_json["helped"] is False
        assert (
            rv is not None
            and rv.kind == "revert"
            and (rv.current_value, rv.proposed_value) == (1.3, 1.2)
            and rv.reverts_id == 7
        )


def test_github_client_never_leaks_the_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(403, json={"message": "Bad credentials secret-token"})

    gh = github.GitHub(
        "secret-token", "o/r", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(github.GitHubError) as e:
        gh.get_file("worker/config/options.yaml")
    assert "secret-token" not in str(e.value) and "403" in str(e.value)
    assert github.from_settings() is None  # no token: record-only


# ---- hardening -------------------------------------------------------------------------------
def test_data_quality_checks(engine: Engine) -> None:
    seed_instruments(engine, {"TCS": 175, "INFY": 400})
    now = datetime(2026, 10, 15, 11, 0, tzinfo=IST)
    with session_scope(engine) as s:
        q = dict(symbol="TCS", expiry=date(2026, 10, 29), strike=4000, type="CE", oi=1, volume=1)
        s.add(
            OptionQuote(ts=now - timedelta(hours=2), bid=10, ask=9, ltp=9.5, iv=20, spot=4000, **q)
        )
        s.add(
            OptionQuote(ts=now - timedelta(hours=1), bid=9, ask=10, ltp=9.5, iv=500, spot=5000, **q)
        )
        s.add(
            Signal(
                symbol="INFY",
                direction="bullish",
                score=70,
                status="NEW",
                created_at=now - timedelta(hours=1),
                data_age_json={"quote_s": 900},
            )
        )
    issues = quality.run_checks(engine, now)
    assert issues["bid_above_ask"] and issues["iv_out_of_range"] and issues["spot_jump"]
    assert issues["stale_chain"] == ["INFY: no quote in 24 h"] and issues["stale_signal_quote"]
    with session_scope(engine) as s:
        s.add(EventCalendar(symbol="TCS", event_type="split", event_date=now.date(), source="nse"))
    assert "spot_jump" not in quality.run_checks(engine, now)  # explained by a corporate action
    assert quality.quality_job(engine, now) > 0


def test_circuit_breaker_backs_off(engine: Engine) -> None:
    for _ in range(5):
        record_source(engine, "nse_x", False, "boom")
    assert circuit_open(engine, "nse_x")
    later = datetime.now(IST) + timedelta(minutes=6)
    assert not circuit_open(engine, "nse_x", later)  # 5 min pause over: try again
    record_source(engine, "nse_x", True)
    assert not circuit_open(engine, "nse_x")


def test_stuck_runs_and_ping(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.models import JobRun

    now = datetime(2026, 10, 15, 11, 0, tzinfo=IST)
    with session_scope(engine) as s:
        s.add(JobRun(job="signal_cycle", started_at=now - timedelta(hours=3), status="running"))
        s.add(JobRun(job="macro", started_at=now - timedelta(minutes=5), status="running"))
    assert watchdog.stuck_runs(engine, {"signal_cycle": 5}, now) == [
        "signal_cycle (started 15-Oct 08:00)"
    ]
    calls: list[str] = []
    monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.example/abc")
    get_settings.cache_clear()
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: (calls.append(str(r.url)), httpx.Response(200))[1])
    )
    assert watchdog.ping(http) and calls == ["https://hc-ping.example/abc"]
    monkeypatch.delenv("HEALTHCHECK_PING_URL")
    get_settings.cache_clear()
    assert not watchdog.ping(http)  # not configured


def test_cost_report(engine: Engine) -> None:
    from app.db.models import AiUsage

    with session_scope(engine) as s:
        s.add(
            AiUsage(
                ts=datetime.now(IST),
                model="m",
                input_tokens=1000,
                output_tokens=10,
                cached_tokens=3000,
                cost_inr=2.5,
            )
        )
    out = watchdog.cost_report(engine)
    assert out["m"]["cost_inr"] == 2.5 and out["m"]["cache_hit_rate"] == 0.75


def test_thresholds_come_from_yaml_unless_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).signal_score_threshold == 65  # type: ignore[call-arg]
    assert Settings(_env_file=None).edge_ratio_min == 1.2  # type: ignore[call-arg]
    monkeypatch.setenv("EDGE_RATIO_MIN", "1.4")
    assert Settings(_env_file=None).edge_ratio_min == 1.4  # type: ignore[call-arg]


def test_one_trading_day_end_to_end(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_selection_gates: None
) -> None:
    """news -> signal (AI vs rules recorded) -> paper entry -> T1 -> breakeven exit -> evening summary
    -> analytics, all on one simulated day with fixtures."""
    from app.notify.briefs import evening_summary_text
    from app.signals.service import run_signal_cycle
    from app.trading.paper_engine import trading_cycle
    from tests.helpers import analyst_output, seed_news
    from tests.test_options_stage import FakeSource
    from tests.test_paper_trading import weekday_at

    monkeypatch.setenv("MC_PATHS", "2000")
    monkeypatch.setenv("HISTORY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    seed_instruments(engine, {"BHARTIARTL": 475})
    seed_news(engine)
    src = FakeSource()
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"), engine, FakeMessages(analyst_output())
    )  # type: ignore[call-arg]
    assert run_signal_cycle(engine, ai, src) == 1
    now = weekday_at(11, 0)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        sig.created_at = now - timedelta(minutes=5)
        assert s.scalars(select(AiVsRules)).one().ai_signal is True
    assert trading_cycle(engine, src, now) == 1
    src.spot = 4030.0
    trading_cycle(engine, src, now + timedelta(minutes=30))
    src.spot = 3995.0
    trading_cycle(engine, src, now + timedelta(minutes=60))
    with session_scope(engine) as s:
        tr = s.scalars(select(PaperTrade)).one()
        assert tr.status == "CLOSED" and tr.exit_reason == "TRAIL"
        text = evening_summary_text(s, now.replace(hour=16)) or ""
        stats = live.summary(s)
    assert "Realised Rs" in text and "(1 closed)" in text and "S-1 BHARTIARTL" in text
    assert stats["all"]["n"] == 1 and stats["breakdowns"]["prediction_source"]
    get_settings.cache_clear()


def test_report_rupees_and_headings() -> None:
    assert monthly._rs(1041200) == "Rs 10,41,200" and monthly._rs(-950) == "Rs -950".replace(
        "Rs -", "-Rs "
    )
    assert monthly._rs(12500000) == "Rs 1,25,00,000" and monthly._rs(None) == "n/a"
    assert monthly._heading("REJECTED (counterfactual)") and monthly._heading("COSTS AND UPTIME")
    assert not monthly._heading("  ai: 22 calls") and not monthly._heading("AI spend Rs 1")
