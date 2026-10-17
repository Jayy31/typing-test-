"""Phase 8: knowledge graph - building, exposure weights, signs (default table vs learned),
propagation (no double counting, cap, depth), panel slot cap, regulator name matching, linked
reactions in the event study and backtests, AI link extraction, /why and /stats."""

from datetime import date, datetime, time, timedelta
from typing import Any

import duckdb
import pytest
from sqlalchemy import Engine, select

from app.ai.client import AiClient
from app.config import IST, Settings, get_settings
from app.db.models import (
    Analysis,
    InsiderTrade,
    KgEdge,
    KgPath,
    NewsItem,
    NewsTag,
    RegulatoryAction,
    Signal,
)
from app.db.session import session_scope
from app.deep import collectors, matching, parsers
from app.history import event_study
from app.history.analogues import AnalogueResult
from app.history.store import init_schema
from app.kg import extract, graph, history, propagate, signs
from app.processing.entity_map import InstrumentInfo
from app.signals import routing
from app.signals.candidates import build_candidates
from tests.helpers import FakeMessages, seed_instruments
from tests.test_history_integrity import hist  # noqa: F401 - fixture


def _ar(n: int, mean: float) -> AnalogueResult:
    return AnalogueResult("event_type", "all", 3, n, float(n), mean, mean, 0.6, 2.0, -1.0, 3.0)


@pytest.fixture(autouse=True)
def _no_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signs.analogues, "lookup", lambda *a, **k: None)
    monkeypatch.setattr("app.deep.evidence.analogues.lookup", lambda *a, **k: None)


def news(
    engine: Engine,
    symbol: str,
    event_type: str,
    sentiment: float,
    cluster: int | None = None,
    sector_tag: str | None = None,
) -> int:
    with session_scope(engine) as s:
        item = NewsItem(
            url_hash=f"{symbol}{event_type}{sentiment}",
            source="nse_announcements",
            title=f"{symbol}: {event_type}",
            body="b",
            url=f"https://x/{symbol}",
            published_at=datetime.now(IST),
            status="tagged",
        )
        s.add(item)
        s.flush()
        s.add(
            NewsTag(
                news_id=item.id,
                symbol=symbol,
                event_type=event_type,
                direction="bullish" if sentiment > 0 else "bearish",
                sentiment=sentiment,
                novelty=0.9,
                prompt_ver="tagger_v2",
            )
        )
        if sector_tag:
            s.add(
                NewsTag(
                    news_id=item.id,
                    symbol=None,
                    sector=sector_tag,
                    event_type=event_type,
                    direction="bearish",
                    sentiment=sentiment,
                    novelty=0.9,
                    prompt_ver="tagger_v2",
                )
            )
        return int(item.id)


# ---- graph -------------------------------------------------------------------------------
def test_exposure_scales_the_edge_weight() -> None:
    assert graph.effective_weight("supplier_of", "revenue_share", 30) == pytest.approx(0.7)
    assert graph.effective_weight("supplier_of", "revenue_share", 10) == pytest.approx(0.35)
    assert graph.effective_weight("supplier_of", None, None) == pytest.approx(0.21)  # missing: low
    assert graph.effective_weight("same_group", "stake", 50) == pytest.approx(0.6)


def test_build_static_and_point_in_time_neighbours(engine: Engine) -> None:
    seed_instruments(
        engine, {"TCS": 175, "INFY": 400, "WIPRO": 3000, "TATAMOTORS": 550, "TATASTEEL": 5500}
    )
    with session_scope(engine) as s:
        for sym in ("TATAMOTORS", "TATASTEEL"):
            s.add(
                InsiderTrade(
                    ref=f"r{sym}",
                    symbol=sym,
                    person="Tata Sons Private Limited",
                    counterparty="promoter",
                    side="buy",
                    disclosed_at=datetime.now(IST),
                )
            )
    graph.build_static(engine)
    assert graph.build_static(engine) == 0  # idempotent
    with session_scope(engine) as s:
        nb = {(e.dst, e.type) for e in graph.neighbours(s, "INFY")}
        assert {("TCS", "competitor_of"), ("WIPRO", "competitor_of")} <= nb
        assert ("TATASTEEL", "same_group") in {
            (e.dst, e.type) for e in graph.neighbours(s, "TATAMOTORS")
        }
        graph.upsert_edge(
            s, "INFY", "TATAMOTORS", "customer_of", origin="ai", valid_from=date(2026, 1, 1)
        )
        assert "TATAMOTORS" not in {e.dst for e in graph.neighbours(s, "INFY", date(2025, 6, 1))}
        assert "TATAMOTORS" in {e.dst for e in graph.neighbours(s, "INFY", date(2026, 6, 1))}


# ---- signs -------------------------------------------------------------------------------
def test_sign_default_table_then_learned() -> None:
    today = date(2026, 10, 17)
    s1 = signs.resolve("competitor_of", "disaster", "TCS", "IT", today)
    assert (s1.sign, s1.basis, s1.strength) == (-1, "default", 0.6)  # rival's plant outage helps
    assert signs.resolve("competitor_of", "regulatory", "TCS", "IT", today).sign == 1  # contagion
    assert signs.resolve("supplier_of", "disaster", "TCS", "IT", today).sign == 1
    u = signs.resolve("competitor_of", "results", "TCS", "IT", today)
    assert (u.sign, u.basis) == (0, "unknown")
    # structured subtypes use their parent type (results_beat -> results)
    assert signs.kg_event_type("customer_of", "results_beat") == "kg:customer_of:results"
    n = get_settings().analogue_min_n
    learned = signs.resolve(
        "competitor_of", "disaster", "TCS", "IT", today, lambda *a, **k: _ar(n, 2.0)
    )
    assert learned.sign == -1 and learned.basis == "base_rate"  # confirms the default
    flipped = signs.resolve(
        "competitor_of", "disaster", "TCS", "IT", today, lambda *a, **k: _ar(n, -2.0)
    )
    assert flipped.sign == 1  # history says the competitor fell too: contagion, not gain
    unk = signs.resolve(
        "competitor_of", "results", "TCS", "IT", today, lambda *a, **k: _ar(n, -1.0)
    )
    assert unk.sign == -1 and unk.basis == "base_rate"  # unknown default learned from data


# ---- propagation -------------------------------------------------------------------------
def _graph(engine: Engine, *edges: tuple[str, str, str, float | None]) -> None:
    with session_scope(engine) as s:
        for a, b, t, exp in edges:
            graph.upsert_edge(
                s,
                a,
                b,
                t,
                origin="seed",
                exposure_kind="revenue_share" if exp else None,
                exposure_pct=exp,
            )


def test_propagation_sign_strength_and_order(engine: Engine) -> None:
    seed_instruments(engine, {"TCS": 175, "INFY": 400, "WIPRO": 3000})
    _graph(engine, ("INFY", "TCS", "competitor_of", None), ("TCS", "WIPRO", "supplier_of", 40.0))
    news(engine, "INFY", "disaster", -0.8)
    now = datetime.now(IST)
    with session_scope(engine) as s:
        counts = propagate.propagate(s, now)
    assert counts["propagated"] == 2
    with session_scope(engine) as s:
        paths = {p.target: p for p in s.scalars(select(KgPath))}
        tcs, wip = paths["TCS"], paths["WIPRO"]
        assert tcs.hops == 1 and tcs.role == "propagated" and tcs.sign_basis == "default"
        assert tcs.sentiment == pytest.approx(-0.8 * -1 * 0.6)  # opposite: the competitor gains
        assert tcs.weight == pytest.approx(0.18)  # 0.6 x missing-exposure 0.3
        assert wip.hops == 2 and wip.weight == pytest.approx(0.18 * 0.7 * 0.5)  # hop decay
        item = s.get(NewsItem, tcs.news_id)
        assert item is not None and item.source == "kg:1:competitor_of:default:propagated"
    with session_scope(engine) as s:  # idempotent
        assert propagate.propagate(s, now) == {
            "propagated": 0,
            "support": 0,
            "unknown": 0,
            "capped": 0,
        }
    with session_scope(engine) as s:
        cands = {c.symbol: c for c in build_candidates(s, now, [])}
        assert "TCS" not in cands  # default-sign graph evidence alone is support-only
    with session_scope(engine) as s:
        tags = {
            t.symbol: t
            for t in s.scalars(select(NewsTag).where(NewsTag.prompt_ver.like("rules:kg%")))
        }
        assert tags["TCS"].event_type == "kg:competitor_of:disaster"


def test_no_double_counting_unknown_and_cap(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_instruments(engine, {"TCS": 175, "INFY": 400, "WIPRO": 3000, "HCLTECH": 350})
    _graph(
        engine,
        ("INFY", "TCS", "competitor_of", None),
        ("INFY", "WIPRO", "competitor_of", None),
        ("INFY", "HCLTECH", "competitor_of", None),
        ("WIPRO", "TCS", "competitor_of", None),
    )
    nid = news(engine, "INFY", "regulatory", -0.7)
    with session_scope(engine) as s:  # the same news also names TCS directly
        s.add(
            NewsTag(
                news_id=nid,
                symbol="TCS",
                event_type="regulatory",
                direction="bearish",
                sentiment=-0.5,
                novelty=0.9,
                prompt_ver="tagger_v2",
            )
        )
    cfg = dict(propagate.load_yaml("kg.yaml"))
    cfg["propagation"] = {**cfg["propagation"], "max_propagated_per_day": 1, "max_depth": 1}
    monkeypatch.setattr(propagate, "load_yaml", lambda name: cfg)
    with session_scope(engine) as s:
        propagate.propagate(s, datetime.now(IST))
    with session_scope(engine) as s:
        roles = {p.target: p.role for p in s.scalars(select(KgPath))}
    assert roles["TCS"] == "support"  # already covered by the same news: supporting evidence only
    assert sorted([roles["WIPRO"], roles["HCLTECH"]]) == ["capped", "propagated"]
    # unknown sign: recorded for /why, no evidence row
    news(engine, "WIPRO", "results", 0.9)
    with session_scope(engine) as s:
        propagate.propagate(s, datetime.now(IST))
        unk = s.scalars(select(KgPath).where(KgPath.role == "unknown")).all()
        assert unk and all(p.news_id is None for p in unk)


def test_learned_graph_evidence_forms_a_second_order_candidate(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_instruments(engine, {"TCS": 175, "INFY": 400})
    _graph(engine, ("INFY", "TCS", "customer_of", 30.0))
    n = get_settings().analogue_min_n
    monkeypatch.setattr(signs.analogues, "lookup", lambda *a, **k: _ar(n, 3.0))
    news(engine, "INFY", "order_win", 0.9)
    now = datetime.now(IST)
    with session_scope(engine) as s:
        propagate.propagate(s, now)
    with session_scope(engine) as s:
        cands = {c.symbol: c for c in build_candidates(s, now, [])}
        assert cands["TCS"].impact_order == 2 and cands["TCS"].has_official
        assert cands["INFY"].impact_order == 1


def test_graph_candidates_use_at_most_one_panel_slot(engine: Engine) -> None:
    st = Settings(
        _env_file=None,
        ai_model_researcher="m",
        ai_model_novelty="m",
        ai_model_bull="m",
        ai_model_bear="m",
        ai_model_judge="m",
        ai_model_analyst="m",
    )  # type: ignore[call-arg]
    ai = AiClient(st, engine, FakeMessages(None))
    now = datetime.now(IST)
    with session_scope(engine) as s:
        assert routing.choose_mode(s, ai, now, 2)[0] == "panel"
        s.add(
            Analysis(
                symbol="TCS",
                thesis="t",
                direction="bullish",
                prediction_source="panel",
                impact_order=2,
                created_at=now,
            )
        )
    with session_scope(engine) as s:
        mode, why = routing.choose_mode(s, ai, now, 2)
        assert mode == "single" and "panel slots used" in why
        assert routing.choose_mode(s, ai, now, 1)[0] == "panel"  # direct candidates unaffected


# ---- regulator matching ------------------------------------------------------------------
def _insts() -> list[InstrumentInfo]:
    return [
        InstrumentInfo("BHARTIARTL", "Bharti Airtel", "Telecom", ("Airtel", "Bharti Airtel")),
        InstrumentInfo("SUNPHARMA", "Sun Pharmaceutical Industries", "Pharma", ("Sun Pharma",)),
        InstrumentInfo("TATAMOTORS", "Tata Motors", "Auto", ("Tata Motors",)),
        InstrumentInfo("TATASTEEL", "Tata Steel", "Metals", ("Tata Steel",)),
    ]


def test_regulator_name_match_confidence() -> None:
    assert matching.match("Bharti Airtel Limited", _insts())[0].score == 1.0
    assert matching.match("Sun Pharmaceutical Industries Ltd.", _insts())[0].score == 1.0
    m = matching.match("Sun Pharma Advanced Research Company", _insts())[0]
    assert m.symbol == "SUNPHARMA" and m.score == 0.8 < matching.threshold()
    tata = matching.match("Tata Group entity", _insts())
    assert tata[0].score < matching.threshold()  # 'Tata' alone: partial, ambiguous


def test_low_confidence_regulator_items_go_to_review(engine: Engine) -> None:
    from app.notify import digest

    seed_instruments(engine, {"SUNPHARMA": 350})
    item = parsers.RegulatorItem(
        "ref1",
        "usfda",
        "warning_letter",
        "Sun Pharma Advanced Research - WL",
        "https://fda/x",
        datetime.now(IST),
        None,
        "Sun Pharma Advanced Research Company",
    )
    collectors.store_regulatory([item], engine)
    with session_scope(engine) as s:
        row = s.scalars(select(RegulatoryAction)).one()
        assert row.symbol is None and row.news_id is None and row.match_confidence == 0.8
        assert row.match_candidates[0]["symbol"] == "SUNPHARMA"  # type: ignore[index]
        text, data = digest.build(
            s, datetime.now(IST).date() - timedelta(days=datetime.now(IST).weekday())
        )
    assert (
        data["regulator_review"]
        and "REGULATOR ITEMS TO REVIEW" in text
        and "SUNPHARMA 0.80" in text
    )


# ---- AI link extraction ------------------------------------------------------------------
def test_extracted_links_map_names_both_directions(engine: Engine) -> None:
    seed_instruments(engine, {"MARUTI": 50, "MOTHERSON": 6000})
    out = extract.Links(
        links=[
            extract.Link(
                a="Samvardhana Motherson International",
                b="Maruti Suzuki India",
                type="supplier_of",
                exposure_kind="revenue_share",
                exposure_pct=25,
                confidence=0.9,
                quote="supplies",
            ),
            extract.Link(
                a="Unknown Co",
                b="Maruti Suzuki India",
                type="supplier_of",
                exposure_kind=None,
                exposure_pct=None,
                confidence=0.95,
                quote="x",
            ),
            extract.Link(
                a="Motherson",
                b="Maruti",
                type="competitor_of",
                exposure_kind=None,
                exposure_pct=None,
                confidence=0.3,
                quote="weak",
            ),
        ]
    )
    insts = extract._instruments(engine)
    assert extract.store_links(engine, out, date(2026, 10, 1), None, insts) == 2
    with session_scope(engine) as s:
        nb = {(e.dst, e.type, e.exposure_pct) for e in graph.neighbours(s, "MOTHERSON")}
        assert ("MARUTI", "supplier_of", 25) in nb
        back = graph.neighbours(s, "MARUTI")
        assert [(e.dst, e.type, e.exposure_pct) for e in back] == [
            ("MOTHERSON", "customer_of", None)
        ]
        assert s.scalars(select(KgEdge)).first().valid_from == date(2026, 10, 1)  # type: ignore[union-attr]


def test_live_extraction_bills_each_item_once(engine: Engine) -> None:
    seed_instruments(engine, {"MARUTI": 50, "MOTHERSON": 6000})
    news(engine, "MARUTI", "business_update", 0.4)
    fake = FakeMessages(extract.Links(links=[]))
    ai = AiClient(Settings(_env_file=None, ai_model_tagger="cheap"), engine, fake)  # type: ignore[call-arg]
    extract.extract_live(engine, ai)
    extract.extract_live(engine, ai)
    assert len(fake.calls) == 1 and fake.calls[0]["model"] == "cheap"


def test_history_batch_needs_estimate_and_yes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        extract, "history_items", lambda engine=None: [("1", "a and b", date(2024, 1, 1))] * 1000
    )
    called: list[Any] = []
    monkeypatch.setattr(extract, "AiClient", lambda *a, **k: called.append(1))
    assert extract.main(["submit"]) == 0
    out = capsys.readouterr().out
    assert "1,000 items via Batch API" in out and "Not submitted" in out and not called


# ---- history: linked reactions + backtests ------------------------------------------------
def test_linked_reactions_learned_per_edge_and_event_type(hist) -> None:  # noqa: F811
    con, cal = hist
    con.execute("DELETE FROM hist_events")
    # INFY plant fire after the close on day 99; competitor XYZ (in F&O then) jumps 40% on day 100
    con.execute(
        "INSERT INTO hist_events (event_id, date, time, symbol, sector, event_type, direction, title) "
        "VALUES ('fire', ?, ?, 'INFY', 'IT', 'disaster', 'bearish', 'plant fire')",
        [cal[99], time(16, 10)],
    )
    con.execute(
        "INSERT INTO hist_kg_edges VALUES ('INFY', 'XYZ', 'competitor_of', 0.6, 'IT', NULL, NULL)"
    )
    event_study.run(con)
    rows = [r for r in event_study.kg_reaction_rows(con) if r[3] == 0]
    assert len(rows) == 1
    etype, sector, sym, w, direction, day, wt, abn = rows[0]
    assert (etype, sym, day) == ("kg:competitor_of:disaster", "XYZ", cal[100])
    assert direction == "bullish" and abn > 30  # default 'opposite' and the data agree
    stat = con.execute(
        """SELECT n, mean_abn_ret FROM analogue_stats WHERE event_type = 'kg:competitor_of:disaster'
           AND symbol IS NULL AND sector IS NULL AND regime = 'all' AND "window" = 0"""
    ).fetchone()
    assert stat[0] == 1 and stat[1] > 30
    # the direct event's own stats are unaffected by the derived row
    assert (
        con.execute("SELECT count(*) FROM analogue_stats WHERE event_type = 'disaster'").fetchone()[
            0
        ]
        > 0
    )


def test_backtest_linked_events_depth_and_no_double_counting(hist) -> None:  # noqa: F811
    from app.backtest.data import Event, HistoryData

    con, cal = hist
    con.executemany(
        "INSERT INTO hist_kg_edges VALUES (?, ?, ?, 0.6, NULL, NULL, NULL)",
        [
            ("INFY", "XYZ", "competitor_of"),
            ("XYZ", "TCS", "supplier_of"),
            ("TCS", "INFY", "same_group"),
        ],
    )
    data = HistoryData(con)
    d = cal[50]
    direct = [
        Event("e", ("e",), "INFY", "IT", "disaster", "bearish", d, "intraday", "fire"),
        Event("t", ("t",), "TCS", "IT", "results", "bullish", d, "intraday", "res"),
    ]
    one = data.linked_events(direct[:1], 1)
    assert [(e.symbol, e.direction, e.event_type) for e in one] == [
        ("XYZ", "bullish", "kg:competitor_of:disaster")
    ]
    two = {e.symbol for e in data.linked_events(direct, 2)}
    assert "TCS" not in two  # TCS has its own direct event that day
    three = data.linked_events(direct[:1], 3)
    assert {e.symbol for e in three} == {"XYZ", "TCS"}  # never back to the source


def test_edges_sync_to_history(engine: Engine) -> None:
    seed_instruments(engine, {"TCS": 175, "INFY": 400})
    graph.build_static(engine)
    con = duckdb.connect()
    init_schema(con)
    assert history.sync_kg_edges(con, engine) >= 2
    assert con.execute("SELECT dst_sector FROM hist_kg_edges WHERE src = 'INFY'").fetchone() == (
        "IT",
    )


# ---- outputs -----------------------------------------------------------------------------
def test_why_and_stats_show_graph_paths(engine: Engine) -> None:
    from tests.test_notify import Recorder, bot

    seed_instruments(engine, {"TCS": 175, "INFY": 400})
    text = bot(engine, Recorder()).reply_for("/stats")
    assert "Order 2 (via graph, 1 hop)" in text and "Order 1 (direct)" in text
    assert propagate.kg_label("kg:1:competitor_of:base_rate:support").startswith(
        "Graph path, 1 hop(s), supporting evidence"
    )
    assert "default table" in propagate.kg_label("kg:2:supplier_of:default:propagated")
    with session_scope(engine) as s:
        s.add(Signal(symbol="TCS", direction="bullish", score=70, status="NEW", impact_order=2))
    assert Signal.impact_order is not None


def test_why_lists_graph_path_hops_and_sign_basis(engine: Engine) -> None:
    from app.notify.explain import build_why

    seed_instruments(engine, {"TCS": 175, "INFY": 400})
    _graph(engine, ("INFY", "TCS", "competitor_of", None))
    news(engine, "INFY", "disaster", -0.8)
    with session_scope(engine) as s:
        propagate.propagate(s, datetime.now(IST))
    with session_scope(engine) as s:
        kg_id = s.scalars(select(KgPath)).one().news_id
        an = Analysis(symbol="TCS", thesis="Rival plant fire", direction="bullish",
                      scores_json={"cited_news_ids": [kg_id]}, created_at=datetime.now(IST))
        s.add(an)
        s.flush()
        sig = Signal(symbol="TCS", direction="bullish", score=70, status="NEW", analysis_id=an.id,
                     impact_order=2)
        s.add(sig)
        s.flush()
        text = build_why(s, sig.id) or ""
    assert "impact order 2: via graph, 1 hop" in text
    assert "Graph path, 1 hop(s), graph-propagated; sign from the default table" in text
    assert "INFY competitor_of TCS (weight 0.18): default table: opposite" in text
