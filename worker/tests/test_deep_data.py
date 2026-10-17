"""Phase 7: deeper data sources - parsers, counterparty subtypes, materiality, evidence (rule vs
base rate), results fast-path, after-hours entry at the next open, commodities, extracts."""

import json
from dataclasses import replace as dc_replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app import taxonomy
from app.ai.client import AiClient
from app.config import IST, Settings, get_settings
from app.db.models import (
    BulkBlockDeal,
    BusinessUpdate,
    InsiderTrade,
    MacroSnapshot,
    NewsItem,
    NewsTag,
    PaperTrade,
    RegulatoryAction,
    ResultsExtract,
    Shareholding,
    Signal,
    StockStats,
)
from app.db.session import session_scope
from app.deep import collectors, evidence, extracts, parsers, results, rules
from app.history.analogues import AnalogueResult
from app.signals import deferred
from app.signals.candidates import build_candidates, source_kind
from tests.helpers import FakeMessages, seed_instruments

SAMPLES = Path(__file__).parent / "samples" / "deep"
OFF_HOURS = deferred.off_hours  # the real function (conftest pins market hours for other tests)


def sample(name: str) -> Any:
    raw = (SAMPLES / name).read_bytes()
    return json.loads(raw) if name.endswith(".json") else raw


def _ar(n: int, mean: float) -> AnalogueResult:
    return AnalogueResult("event_type", "all", 3, n, float(n), mean, mean, 0.6, 2.0, -1.0, 3.0)


def no_history(*a: Any, **k: Any) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_analogues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence.analogues, "lookup", no_history)


# ---- taxonomy + config ------------------------------------------------------------------
def test_tx2_subtypes_have_rules_and_parents() -> None:
    assert taxonomy.version() == "tx2"
    types = taxonomy.structured_types()
    rule_cfg = evidence.load_yaml("deep_data.yaml")["evidence"]["rules"]
    assert set(rule_cfg) <= set(types)
    for cp in ("promoter", "promoter_group", "fii", "mf_dii", "other"):
        assert f"deal_buy_{cp}" in types and f"deal_sell_{cp}" in types
    for cp in ("promoter", "promoter_group", "director_kmp", "other"):
        assert f"insider_buy_{cp}" in types and f"insider_sell_{cp}" in types
    assert all(v["parent"] in taxonomy.event_types() for v in types.values())
    assert (
        taxonomy.parent("deal_buy_fii") == "block_deal" and taxonomy.parent("results") == "results"
    )
    assert not set(types) & set(taxonomy.event_types())  # the AI tagger enum is unchanged


# ---- parsers + counterparty -------------------------------------------------------------
def test_parse_insider_keeps_trade_date_and_disclosure_time() -> None:
    rows = parsers.parse_insider(sample("insider.json"))
    assert len(rows) == 2  # pledge creation and warrants skipped
    a = rows[0]
    assert a.symbol == "BHARTIARTL" and a.side == "buy" and a.counterparty == "promoter_group"
    assert a.subtype == "insider_buy_promoter_group"
    assert a.event_date == date(2026, 10, 10)
    assert a.disclosed_at == datetime(2026, 10, 14, 19, 2, tzinfo=IST)
    assert rows[1].counterparty == "director_kmp" and rows[1].subtype == "insider_sell_director_kmp"


def test_parse_deals_counterparty_and_publication_time() -> None:
    rows = parsers.parse_deals(sample("bulk_deals.json"), "bulk", {"TCS": {"RANDOM TRADER"}})
    by = {r.client: r for r in rows}
    mf = by["SBI MUTUAL FUND"]
    assert mf.counterparty == "mf_dii" and mf.subtype == "deal_buy_mf_dii"
    assert mf.value_inr == pytest.approx(5_000_000 * 1600.50)
    assert by["GOLDMAN SACHS (SINGAPORE) PTE - ODI"].subtype == "deal_sell_fii"
    assert by["RANDOM TRADER"].counterparty == "promoter"  # known promoter name
    # the deal report is public after the close: never earlier than the configured time
    assert mf.event_date == date(2026, 10, 15)
    assert mf.disclosed_at == datetime(2026, 10, 15, 18, 0, tzinfo=IST)


def test_insider_counterparty_classes() -> None:
    assert rules.insider_counterparty("Promoter Group") == "promoter_group"
    assert rules.insider_counterparty("Promoters") == "promoter"
    assert rules.insider_counterparty("Key Managerial Personnel") == "director_kmp"
    assert rules.insider_counterparty("Employees") == "other"
    assert rules.deal_counterparty("HDFC MUTUAL FUND A/C X") == "mf_dii"
    assert rules.deal_counterparty("Morgan Stanley Asia (Singapore) Pte") == "fii"
    assert rules.deal_counterparty("Some Person") == "other"


def test_shareholding_and_regulator_parsers() -> None:
    rows = parsers.parse_shareholding(sample("shareholding.json"))
    assert [r.event_date for r in rows] == [date(2026, 6, 30), date(2026, 9, 30)]
    assert rows[1].disclosed_at == datetime(2026, 10, 17, 18, 5, tzinfo=IST)
    subs = rules.shareholding_subtypes(rows[0].stakes(), rows[1].stakes())
    assert subs == ["holding_up_promoter", "holding_down_fii", "pledge_increase"]
    sebi = parsers.parse_sebi(sample("sebi.xml"))
    assert len(sebi) == 1 and sebi[0].entity == "Bharti Airtel Limited"
    assert sebi[0].action_type == "adjudication" and sebi[0].disclosed_at.tzinfo is not None
    fda = parsers.parse_usfda(sample("usfda.xml"))
    assert len(fda) == 1 and fda[0].entity.startswith("Sun Pharmaceutical")
    assert parsers.rating_action("CRISIL has upgraded the long term rating to AA+") == (
        "upgrade",
        "CRISIL",
    )
    assert parsers.rating_action("ICRA reaffirmed the credit rating")[0] == "reaffirm"  # type: ignore[index]
    assert parsers.rating_action("Outcome of board meeting") is None


# ---- materiality ------------------------------------------------------------------------
def test_value_materiality_thresholds() -> None:
    small = rules.value_materiality("insider", 40_00_000, 50e7, 1e12)
    assert not small.material and "below Rs" in small.reasons[0]
    adv = rules.value_materiality("insider", 20e7, 100e7, 1e13)  # 20% of ADV
    assert adv.material and adv.pct_adv == pytest.approx(20.0)
    tiny = rules.value_materiality("deals", 6e7, 1000e7, 1e13)  # 6% ADV, 0.006% mcap
    assert not tiny.material
    mcap = rules.value_materiality("deals", 6e7, 1000e7, 3e10)  # 0.2% of mcap
    assert mcap.material
    assert rules.value_materiality("deals", 6e7, None, None).material  # Rs minimum only


# ---- evidence ---------------------------------------------------------------------------
def test_rule_until_enough_events_then_base_rate() -> None:
    when = datetime(2026, 10, 14, 19, 0, tzinfo=IST)
    r = evidence.resolve("deal_buy_fii", "BHARTIARTL", "Telecom", when, lambda *a, **k: _ar(2, 3.0))
    assert r is not None and r.source == "rule" and r.sentiment == 0.4
    assert r.novelty == 0.6 and "n=2" in r.detail
    min_n = get_settings().analogue_min_n
    b = evidence.resolve(
        "deal_buy_fii", "BHARTIARTL", "Telecom", when, lambda *a, **k: _ar(min_n, 3.0)
    )
    assert b is not None and b.source == "base_rate" and b.novelty == 1.0
    assert b.sentiment == pytest.approx(0.762, abs=1e-3)  # tanh(3/3)
    # the market historically moved AGAINST the rule: the base rate flips the direction
    f = evidence.resolve("deal_buy_fii", "X", None, when, lambda *a, **k: _ar(min_n, -1.5))
    assert f is not None and f.direction == "bearish"
    assert evidence.resolve("results_inline", "X", None, when, no_history) is None


def test_store_insider_creates_evidence_only_when_material(engine: Engine) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175})
    with session_scope(engine) as s:
        s.add(StockStats(symbol="BHARTIARTL", adv20_inr=500e7, mcap_inr=9e12))
    assert collectors.store_insider(parsers.parse_insider(sample("insider.json")), engine) == 2
    assert collectors.store_insider(parsers.parse_insider(sample("insider.json")), engine) == 0
    with session_scope(engine) as s:
        rows = {r.symbol: r for r in s.scalars(select(InsiderTrade))}
        big, small = rows["BHARTIARTL"], rows["TCS"]
        assert big.material and big.news_id and not small.material and small.news_id is None
        item = s.get(NewsItem, big.news_id)
        tag = s.scalars(select(NewsTag).where(NewsTag.news_id == big.news_id)).one()
        assert item is not None and item.source == "deep:insider:rule"
        assert item.published_at.replace(tzinfo=IST) == big.disclosed_at.replace(tzinfo=IST)  # type: ignore[union-attr]
        assert tag.event_type == "insider_buy_promoter_group" and tag.sentiment == 0.5
        assert tag.prompt_ver == "rules:deep_data_v1:rule"
    assert source_kind("deep:insider:rule") == "structured_rule"
    assert source_kind("deep:insider:base_rate") == "structured_base_rate"


def test_hand_set_rule_alone_cannot_make_a_candidate_but_base_rate_can(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475})
    now = datetime.now(IST)
    with session_scope(engine) as s:
        evidence.emit(
            s,
            category="bulk_deal",
            ref="a",
            symbol="BHARTIARTL",
            subtype="deal_buy_promoter",
            disclosed_at=now,
            title="deal",
            url=None,
            detail="d",
        )
    rejected: list[Any] = []
    with session_scope(engine) as s:
        assert build_candidates(s, now, rejected) == []
    assert "single unofficial source" in rejected[0][1]
    min_n = get_settings().analogue_min_n
    with session_scope(engine) as s:
        evidence.emit(
            s,
            category="bulk_deal",
            ref="b",
            symbol="BHARTIARTL",
            subtype="deal_buy_promoter",
            disclosed_at=now,
            title="deal 2",
            url=None,
            detail="d",
            lookup=lambda *a, **k: _ar(min_n, 3.0),
        )
    with session_scope(engine) as s:
        cands = build_candidates(s, now)
        assert len(cands) == 1 and cands[0].has_official
        assert {"deal_buy_promoter", "block_deal"} <= cands[0].event_types  # parent for playbooks


def test_why_shows_which_source_set_the_direction(engine: Engine) -> None:
    assert evidence.source_label("deep:insider:rule") == "hand-set rule (low weight)"
    assert evidence.source_label("deep:results:base_rate") == "event-study base rate"
    assert evidence.source_label("nse_announcements") is None
    text = Path(evidence.__file__).parents[1].joinpath("notify", "explain.py").read_text()
    assert "Direction from:" in text


def test_shareholding_store_and_regulators(engine: Engine) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475, "SUNPHARMA": 350})
    rows = parsers.parse_shareholding(sample("shareholding.json"))
    assert collectors.store_shareholding(rows, engine) == 2
    with session_scope(engine) as s:
        q2 = s.scalars(
            select(Shareholding).where(Shareholding.event_date == date(2026, 9, 30))
        ).one()
        assert q2.material and q2.subtypes == [
            "holding_up_promoter",
            "holding_down_fii",
            "pledge_increase",
        ]
        tags = s.scalars(select(NewsTag)).all()
        assert sorted(t.event_type for t in tags) == sorted(q2.subtypes)  # type: ignore[arg-type]
    collectors.store_regulatory(
        parsers.parse_sebi(sample("sebi.xml")) + parsers.parse_usfda(sample("usfda.xml")), engine
    )
    with session_scope(engine) as s:
        acts = {a.regulator: a for a in s.scalars(select(RegulatoryAction))}
        assert acts["sebi"].symbol == "BHARTIARTL" and acts["sebi"].subtype == "sebi_order"
        assert acts["usfda"].symbol == "SUNPHARMA" and acts["usfda"].news_id is not None


def test_deals_store_materiality_from_stock_stats(engine: Engine) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475, "TCS": 175})
    with session_scope(engine) as s:
        s.add(StockStats(symbol="BHARTIARTL", adv20_inr=1000e7, mcap_inr=9e12))
    collectors.store_deals(parsers.parse_deals(sample("bulk_deals.json"), "bulk"), engine)
    with session_scope(engine) as s:
        deals = {d.client: d for d in s.scalars(select(BulkBlockDeal))}
        assert deals["SBI MUTUAL FUND"].material  # Rs 800 cr = 80% of ADV
        assert not deals["RANDOM TRADER"].material  # Rs 4 lakh
        assert deals["SBI MUTUAL FUND"].event_date == date(2026, 10, 15)


# ---- results fast-path ------------------------------------------------------------------
def test_xbrl_extract_and_surprise() -> None:
    fig = results.parse_xbrl(sample("results.xml"), date(2026, 9, 30))
    assert fig is not None and fig.consolidated is True and fig.method == "xbrl"
    assert fig.revenue == pytest.approx(50_000) and fig.revenue_py == pytest.approx(40_000)
    assert fig.revenue_yoy == pytest.approx(25.0) and fig.pat_yoy == pytest.approx(50.0)
    assert fig.ebitda == pytest.approx(25_000) and fig.margin_change_pp == pytest.approx(-2.5)
    score, detail = results.surprise(fig, [])
    assert (
        score == pytest.approx(0.49, abs=0.01) and detail["trend_revenue_yoy"] == 10
    )  # default trend (no history)
    assert results.subtype_for(score) == "results_beat"
    flat = results.Figures(date(2026, 9, 30), True, "xbrl", 100, 10, None, 90, 9.1)
    s2, _ = results.surprise(flat, [(10.0, 10.0)] * 4)
    assert results.subtype_for(s2) == "results_inline"


def test_results_feed_to_evidence_and_immediate_cycle(engine: Engine) -> None:
    seed_instruments(engine, {"BHARTIARTL": 475})
    now = datetime(2026, 10, 17, 16, 6, tzinfo=IST)
    calls: list[set[str]] = []
    downloads: list[str] = []

    def dl(url: str) -> bytes:
        downloads.append(url)
        return sample("results.xml")  # type: ignore[no-any-return]

    feed = results.parse_results_feed(sample("results_feed.json"))
    assert feed[0].consolidated is True  # consolidated first for the same quarter
    for f in feed:
        results.process_filing(f, engine, None, now, download=dl)
    assert downloads == ["https://nsearchives.nseindia.com/corporate/xbrl/BHARTI_Q2.xml"]
    with session_scope(engine) as s:
        ex = s.scalars(select(ResultsExtract)).one()
        assert ex.subtype == "results_beat" and ex.latency_s == pytest.approx(48.0)
        item = s.get(NewsItem, ex.news_id)
        assert item is not None and item.source == "deep:results:rule"
    with session_scope(engine) as s:  # results count as an official filing for the gate
        assert build_candidates(s, now + timedelta(minutes=1))[0].has_official
    n = results.poll_results(
        engine,
        ai=SimpleNamespace(),
        fetch=lambda: sample("results_feed.json"),
        cycle=lambda e, only: calls.append(only) or 1,
        now=now,
    )
    assert n == 0 and calls == []  # already extracted: nothing new
    with session_scope(engine) as s:
        s.delete(s.scalars(select(ResultsExtract)).one())
    results.poll_results(
        engine,
        ai=SimpleNamespace(),
        fetch=lambda: sample("results_feed.json"),
        cycle=lambda e, only: calls.append(only) or 1,
        now=now,
        download=dl,
    )
    assert calls == [{"BHARTIARTL"}]


def test_results_ai_fallback_cheap_then_mid(engine: Engine) -> None:
    good = results.ResultsExtractOut(
        period_end=date(2026, 9, 30),
        consolidated=True,
        revenue_cr=110,
        revenue_prev_year_cr=100,
        pat_cr=12,
        pat_prev_year_cr=10,
        ebitda_cr=None,
        ebitda_prev_year_cr=None,
        guidance="raised",
        exceptional_items=False,
        notes=[],
    )

    class CheapFails(FakeMessages):
        def parse(self, **kwargs: Any) -> Any:
            resp = super().parse(**kwargs)
            if kwargs["model"] == "cheap":
                return SimpleNamespace(
                    parsed_output=None, stop_reason="max_tokens", usage=resp.usage
                )
            return resp

    fake = CheapFails(good)
    ai = AiClient(
        Settings(_env_file=None, ai_model_tagger="cheap", ai_model_analyst="mid"), engine, fake
    )  # type: ignore[call-arg]
    fig = results.ai_extract(ai, "BHARTIARTL", "filing text")
    assert [c["model"] for c in fake.calls] == ["cheap", "mid"]
    assert fig.method == "ai" and fig.model == "mid" and fig.guidance == "raised"
    assert fig.revenue_yoy == pytest.approx(10.0)


def test_fast_path_window() -> None:
    assert results.in_fast_window(datetime(2026, 10, 17, 9, 0, tzinfo=IST))
    assert results.in_fast_window(datetime(2026, 10, 17, 23, 30, tzinfo=IST))
    assert not results.in_fast_window(datetime(2026, 10, 17, 23, 31, tzinfo=IST))
    assert not results.in_fast_window(datetime(2026, 10, 17, 8, 59, tzinfo=IST))


# ---- after hours: enter at the next open --------------------------------------------------
def test_next_open_and_entry_start() -> None:
    fri_night = datetime(2026, 10, 16, 21, 0, tzinfo=IST)  # Friday
    assert deferred.next_open(fri_night, set()) == datetime(2026, 10, 19, 9, 15, tzinfo=IST)
    assert deferred.next_open(fri_night, {date(2026, 10, 19)}) == datetime(
        2026, 10, 20, 9, 15, tzinfo=IST
    )
    early = datetime(2026, 10, 19, 7, 0, tzinfo=IST)
    assert deferred.next_open(early, set()) == datetime(2026, 10, 19, 9, 15, tzinfo=IST)
    assert OFF_HOURS(fri_night, set()) and not OFF_HOURS(
        datetime(2026, 10, 16, 11, 0, tzinfo=IST), set()
    )
    assert OFF_HOURS(datetime(2026, 10, 16, 11, 0, tzinfo=IST), {date(2026, 10, 16)})
    sig = Signal(created_at=fri_night, enter_after=datetime(2026, 10, 19, 9, 15, tzinfo=IST))
    assert deferred.entry_start(sig) == datetime(2026, 10, 19, 9, 30, tzinfo=IST)


def test_after_hours_signal_is_repriced_and_entered_at_next_open(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_options_stage import FakeSource

    monkeypatch.setenv("MC_PATHS", "2000")
    get_settings.cache_clear()
    monkeypatch.setattr(deferred, "off_hours", lambda now, holidays: True)
    from app.signals.service import run_signal_cycle
    from app.trading.paper_engine import trading_cycle
    from tests.helpers import analyst_output, seed_news

    class ClockSource(FakeSource):  # quotes stamped at the simulated time
        clock: datetime | None = None

        def chain(self, symbol: str, expiry: date, as_of: date | None = None) -> Any:
            c = super().chain(symbol, expiry, as_of)
            ts = self.clock or c.ts
            return dc_replace(c, ts=ts, rows=tuple(dc_replace(r, ts=ts) for r in c.rows))

        def quote(self, symbol: str) -> Any:
            q = super().quote(symbol)
            return dc_replace(q, ts=self.clock or q.ts)

    seed_instruments(engine, {"BHARTIARTL": 475})
    seed_news(engine)
    src = ClockSource()
    ai = AiClient(
        Settings(_env_file=None, ai_model_analyst="m"), engine, FakeMessages(analyst_output())
    )  # type: ignore[call-arg]
    run_signal_cycle(engine, ai, src)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.pricing_status == "pending" and sig.enter_after is not None
        opens = (
            sig.enter_after.replace(tzinfo=IST)
            if sig.enter_after.tzinfo is None
            else sig.enter_after
        )
        sig_id = sig.id
    monkeypatch.setattr(deferred, "off_hours", lambda now, holidays: False)
    # 09:20 at the next open: re-priced with live quotes, but no entry in the first 15 minutes
    src.clock = opens + timedelta(minutes=5)
    trading_cycle(engine, src, opens + timedelta(minutes=5))
    with session_scope(engine) as s:
        sig = s.get(Signal, sig_id)
        assert sig is not None and sig.pricing_status == "priced" and sig.strike
        assert s.scalars(select(PaperTrade)).all() == []
    # 09:35: entered; the entry TTL counts from 09:30, not from the evening the signal was made
    src.clock = opens + timedelta(minutes=20)
    assert trading_cycle(engine, src, opens + timedelta(minutes=20)) == 1
    with session_scope(engine) as s:
        assert s.scalars(select(PaperTrade)).one().signal_id == sig_id


# ---- commodities, extracts --------------------------------------------------------------
def test_commodities_in_snapshot_and_changes(engine: Engine) -> None:
    from app.collectors.macro import build_snapshot
    from app.rules.macro_changes import compute_changes

    t0 = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    old = build_snapshot({"HRC=F": (800.0, 790.0)}, t0)
    new = build_snapshot({"HRC=F": (840.0, 800.0)}, t0 + timedelta(hours=25))
    assert new.commodities_json == {"steel_hrc": 840.0}
    with session_scope(engine) as s:
        s.add_all([old, new])
    with session_scope(engine) as s:
        assert compute_changes(s, t0)["steel_hrc"] == pytest.approx(5.0)
        assert s.scalars(select(MacroSnapshot)).first() is not None


def test_auto_sales_extract_once_per_announcement(engine: Engine) -> None:
    seed_instruments(engine, {"MARUTI": 50})
    with session_scope(engine) as s:
        s.add(
            NewsItem(
                url_hash="m1",
                source="nse_announcements",
                title="MARUTI: Sales update for September 2026",
                body="Total sales 200,000 units vs 160,000",
                url="https://x/m1",
                published_at=datetime.now(IST),
                status="tagged",
            )
        )
    out = extracts.AutoSalesOut(
        is_sales_update=True,
        period=date(2026, 9, 1),
        total_units=200000,
        total_units_prev_year=160000,
        yoy_pct_printed=None,
    )
    fake = FakeMessages(out)
    ai = AiClient(
        Settings(_env_file=None, ai_model_tagger="cheap", ai_model_analyst="mid"), engine, fake
    )  # type: ignore[call-arg]
    assert extracts.scan_auto_sales(engine, ai) == 1
    assert extracts.scan_auto_sales(engine, ai) == 0 and len(fake.calls) == 1  # never re-billed
    with session_scope(engine) as s:
        bu = s.scalars(select(BusinessUpdate)).one()
        assert bu.subtype == "auto_sales_strong" and bu.yoy_pct == pytest.approx(25.0)
