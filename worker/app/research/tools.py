"""Read-only research tools (Phase 13). The agent may call ONLY these (plus provider web tools).

Every tool: a strict Pydantic input schema (unknown fields rejected), row limits from
config/research_agent.yaml `tools`, point-in-time reads (nothing after the run's `now`), and a
session that is never committed (Postgres: READ ONLY transaction). Tools import read helpers
only; tests/test_research_safety.py fails if this module reaches write, trading or broker code.
Text that came from outside (news bodies, titles) is returned as data inside JSON.
"""

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.ai.providers.base import ToolSpec
from app.db.models import (
    BulkBlockDeal,
    BusinessUpdate,
    EventCalendar,
    EventLibrary,
    InsiderTrade,
    Instrument,
    MacroRelease,
    MacroSnapshot,
    NewsItem,
    NewsTag,
    OptionQuote,
    Rating,
    RegulatoryAction,
    ResultsExtract,
    Shareholding,
)
from app.yaml_config import load_yaml


def _tcfg() -> dict[str, Any]:
    return dict(load_yaml("research_agent.yaml").get("tools") or {})


def max_rows() -> int:
    return int(_tcfg().get("max_rows", 20))


def max_chars() -> int:
    return int(_tcfg().get("max_result_chars", 8000))


@dataclass
class ToolContext:
    engine: Engine | None
    now: datetime
    symbol: str | None = None
    provided_news_ids: set[int] = field(default_factory=set)  # ids the agent has seen (citations)


@contextmanager
def read_only_session(engine: Engine | None) -> Iterator[Session]:
    """A session that is always rolled back; on Postgres the transaction is READ ONLY."""
    from app.db.session import get_engine

    eng = engine or get_engine()
    factory = sessionmaker(eng, expire_on_commit=False)
    with factory() as s:
        if eng.dialect.name == "postgresql":
            s.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield s
        finally:
            s.rollback()


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SymbolIn(_In):
    symbol: str = Field(description="NSE stock symbol, e.g. TCS")


class NewsIn(_In):
    symbol: str = Field(description="NSE stock symbol")
    days: int = Field(default=7, ge=1, le=60, description="Look back this many days")
    limit: int = Field(default=10, ge=1, le=20)


class DaysIn(_In):
    symbol: str = Field(description="NSE stock symbol")
    days: int = Field(default=90, ge=1, le=730)


class CalendarIn(_In):
    symbol: str = Field(description="NSE stock symbol (market-wide events are included)")
    days: int = Field(default=30, ge=1, le=90, description="Look ahead this many days")


class AnaloguesIn(_In):
    symbol: str
    event_type: str = Field(description="Taxonomy event type, e.g. results_beat")
    window: int | None = Field(default=None, ge=1, le=20, description="Reaction window in days")


class ChainIn(_In):
    symbol: str
    strikes_each_side: int = Field(default=5, ge=1, le=10)


class EmptyIn(_In):
    pass


class SimScenario(_In):
    name: str
    probability: float = Field(ge=0, le=1)
    stock_move_pct: float = Field(ge=-50, le=50)
    timing_days: int = Field(ge=1, le=30)


class SimulatorIn(_In):
    symbol: str
    direction: Literal["bullish", "bearish"]
    scenarios: list[SimScenario] = Field(min_length=1, max_length=8)
    window_days: int = Field(default=5, ge=1, le=20)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _rows(objs: list[Any], cols: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{c: _iso(getattr(o, c, None)) for c in cols} for o in objs]


def _clip(s: str | None, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _known(s: Session, symbol: str) -> Instrument:
    inst = s.get(Instrument, symbol.upper())
    if inst is None:
        raise ValueError(f"unknown symbol {symbol!r}")
    return inst


# ---- tools ---------------------------------------------------------------------------------
def get_news(s: Session, ctx: ToolContext, a: NewsIn) -> Any:
    since = ctx.now - timedelta(days=a.days)
    q = (
        select(NewsItem, NewsTag)
        .join(NewsTag, NewsTag.news_id == NewsItem.id)
        .where(NewsTag.symbol == a.symbol.upper())
        .where(NewsItem.published_at >= since, NewsItem.published_at <= ctx.now)
        .order_by(NewsItem.published_at.desc())
        .limit(min(a.limit, max_rows()))
    )
    out = []
    for n, t in s.execute(q).all():
        ctx.provided_news_ids |= {int(n.id)}
        out.append(
            {
                "news_id": n.id,
                "published_at": _iso(n.published_at),
                "source": n.source,
                "title": _clip(n.title, 200),
                "body": _clip(n.body, 600),
                "url": n.url,
                "event_type": t.event_type,
                "direction": t.direction,
                "sentiment": t.sentiment,
            }
        )
    return out


def get_filings(s: Session, ctx: ToolContext, a: DaysIn) -> Any:
    sym, since, lim = a.symbol.upper(), ctx.now - timedelta(days=a.days), max_rows()
    out: dict[str, Any] = {}
    for name, model, cols in (
        (
            "insider_trades",
            InsiderTrade,
            (
                "person",
                "person_category",
                "side",
                "mode",
                "subtype",
                "material",
                "event_date",
                "disclosed_at",
                "news_id",
            ),
        ),
        (
            "bulk_block_deals",
            BulkBlockDeal,
            (
                "kind",
                "client",
                "side",
                "subtype",
                "material",
                "event_date",
                "disclosed_at",
                "news_id",
            ),
        ),
        ("shareholding", Shareholding, ("event_date", "subtypes", "material", "disclosed_at")),
        ("ratings", Rating, ("agency", "action", "title", "event_date", "disclosed_at", "url")),
        (
            "regulatory_actions",
            RegulatoryAction,
            ("regulator", "action_type", "title", "event_date", "disclosed_at", "url"),
        ),
        ("business_updates", BusinessUpdate, ("period", "metric", "disclosed_at")),
    ):
        rows = s.scalars(
            select(model)
            .where(model.symbol == sym, model.disclosed_at >= since, model.disclosed_at <= ctx.now)
            .order_by(model.disclosed_at.desc())
            .limit(lim)
        ).all()
        out[name] = _rows(list(rows), cols)
        ctx.provided_news_ids |= {int(getattr(r, "news_id", 0) or 0) for r in rows} - {0}
    return out


def get_results_extract(s: Session, ctx: ToolContext, a: SymbolIn) -> Any:
    rows = s.scalars(
        select(ResultsExtract)
        .where(ResultsExtract.symbol == a.symbol.upper(), ResultsExtract.disclosed_at <= ctx.now)
        .order_by(ResultsExtract.period_end.desc())
        .limit(min(8, max_rows()))
    ).all()
    for r in rows:
        if r.news_id:
            ctx.provided_news_ids |= {int(r.news_id)}
    return _rows(
        list(rows),
        (
            "period_end",
            "disclosed_at",
            "method",
            "consolidated",
            "revenue",
            "pat",
            "ebitda",
            "revenue_yoy_pct",
            "pat_yoy_pct",
            "margin_change_pp",
            "guidance",
            "surprise_json",
            "source_url",
            "news_id",
        ),
    )


def get_option_chain(s: Session, ctx: ToolContext, a: ChainIn) -> Any:
    sym = a.symbol.upper()
    last = s.scalar(
        select(OptionQuote.ts)
        .where(OptionQuote.symbol == sym, OptionQuote.ts <= ctx.now)
        .order_by(OptionQuote.ts.desc())
        .limit(1)
    )
    if last is None:
        return {"available": False, "note": "no stored option quotes for this stock"}
    rows = s.scalars(
        select(OptionQuote).where(OptionQuote.symbol == sym, OptionQuote.ts == last)
    ).all()
    from app.trading.core_rules import monthly_expiries

    allowed = [d for d in monthly_expiries({r.expiry for r in rows}) if d >= ctx.now.date()][:2]
    spot = next((r.spot for r in rows if r.spot), None)
    out: dict[str, Any] = {"available": True, "as_of": _iso(last), "spot": spot, "expiries": []}
    for exp in allowed:
        strikes = sorted({r.strike for r in rows if r.expiry == exp})
        if spot:
            atm = min(strikes, key=lambda k: abs(k - spot))
            i = strikes.index(atm)
            strikes = strikes[max(0, i - a.strikes_each_side) : i + a.strikes_each_side + 1]
        quotes = [r for r in rows if r.expiry == exp and r.strike in set(strikes)]
        out["expiries"].append(
            {
                "expiry": exp.isoformat(),
                "quotes": _rows(
                    sorted(quotes, key=lambda r: (r.strike, r.type)),
                    ("strike", "type", "bid", "ask", "ltp", "iv", "oi", "volume"),
                ),
            }
        )
    return out


def get_positioning(s: Session, ctx: ToolContext, a: SymbolIn) -> Any:
    from app.positioning.service import latest, source_label

    snap = latest(s, a.symbol.upper())
    if snap is None or snap.day > ctx.now.date():
        return {"available": False}
    return {
        "available": True,
        "label": source_label(snap.source, snap.day),
        "states": snap.states_json,
    }


def get_regime(s: Session, ctx: ToolContext, a: EmptyIn) -> Any:
    from app.regime.service import current_in

    r = current_in(s, ctx.now.date())
    return (
        {"available": False} if r is None else {"available": True, **r.as_dict(), "text": r.text()}
    )


def get_analogues(s: Session, ctx: ToolContext, a: AnaloguesIn) -> Any:
    inst = _known(s, a.symbol)
    keys = [f"stock:{inst.symbol}", f"sector:{inst.sector}" if inst.sector else None, "all"]
    q = select(EventLibrary).where(
        EventLibrary.event_type == a.event_type,
        EventLibrary.level_key.in_([k for k in keys if k]),
    )
    if a.window is not None:
        q = q.where(EventLibrary.window == a.window)
    rows = s.scalars(
        q.order_by(EventLibrary.level_key, EventLibrary.window).limit(max_rows())
    ).all()
    return {
        "note": "abnormal returns in % in the news direction; positive = moved as the news "
        "suggested. Start from these base rates.",
        "rows": _rows(
            list(rows),
            ("level_key", "regime", "window", "n", "n_eff", "mean", "median", "hit_rate"),
        ),
    }


def get_kg_neighbours(s: Session, ctx: ToolContext, a: SymbolIn) -> Any:
    from app.kg.graph import neighbours

    edges = neighbours(s, a.symbol.upper(), ctx.now.date())[: max_rows()]
    return [
        {
            "to": e.dst,
            "type": e.type,
            "weight": round(e.weight, 3),
            "exposure_kind": e.exposure_kind,
            "exposure_pct": e.exposure_pct,
            "origin": e.origin,
        }
        for e in edges
    ]


def get_macro(s: Session, ctx: ToolContext, a: EmptyIn) -> Any:
    snap = s.scalars(
        select(MacroSnapshot)
        .where(MacroSnapshot.ts <= ctx.now)
        .order_by(MacroSnapshot.ts.desc())
        .limit(1)
    ).first()
    rel = s.scalars(
        select(MacroRelease)
        .where(
            MacroRelease.released_at <= ctx.now,
            MacroRelease.released_at >= ctx.now - timedelta(days=45),
        )
        .order_by(MacroRelease.released_at.desc())
        .limit(min(8, max_rows()))
    ).all()
    snap_d = None
    if snap is not None:
        snap_d = {
            k: _iso(getattr(snap, k, None))
            for k in (
                "ts",
                "brent",
                "usdinr",
                "dxy",
                "us10y",
                "gift_nifty",
                "india_vix",
                "fii_net",
                "dii_net",
                "commodities_json",
            )
        }
    return {
        "snapshot": snap_d,
        "releases": _rows(list(rel), ("name", "period", "released_at", "source")),
    }


def get_calendar(s: Session, ctx: ToolContext, a: CalendarIn) -> Any:
    today = ctx.now.date()
    rows = s.scalars(
        select(EventCalendar)
        .where(
            EventCalendar.event_date >= today,
            EventCalendar.event_date <= today + timedelta(days=a.days),
        )
        .where(or_(EventCalendar.symbol == a.symbol.upper(), EventCalendar.symbol.is_(None)))
        .order_by(EventCalendar.event_date)
        .limit(max_rows())
    ).all()
    return _rows(list(rows), ("event_date", "event_type", "symbol", "sector", "source"))


def run_simulator(s: Session, ctx: ToolContext, a: SimulatorIn) -> Any:
    """Scenarios in, option results out: the shared Monte Carlo on the nearest allowed monthly
    ATM option from the latest stored chain. Information only; the real gates run later."""
    from app.ai.schemas import Scenario
    from app.config import get_settings
    from app.signals.expiry import trading_days_between
    from app.signals.filters import load_holidays
    from app.sim.simulator import CandidateInput, evaluate, seed_for, simulate_stock
    from app.trading.exits import ExitRules

    total = sum(x.probability for x in a.scenarios)
    if abs(total - 1.0) > 0.02:
        raise ValueError(f"scenario probabilities sum to {total:.2f}, not 1")
    chain = get_option_chain(s, ctx, ChainIn(symbol=a.symbol, strikes_each_side=1))
    if not chain.get("available") or not chain["expiries"] or not chain.get("spot"):
        return {"available": False, "note": "no option chain stored for this stock"}
    spot = float(chain["spot"])
    exp = chain["expiries"][0]
    expiry = date.fromisoformat(exp["expiry"])
    otype = "CE" if a.direction == "bullish" else "PE"
    quotes = [q for q in exp["quotes"] if q["type"] == otype and q["bid"] and q["ask"]]
    if not quotes:
        return {"available": False, "note": "no two-sided ATM quote"}
    q = min(quotes, key=lambda r: abs(r["strike"] - spot))
    inst = _known(s, a.symbol)
    today = ctx.now.date()
    td = trading_days_between(today, expiry, load_holidays(s))
    rules = ExitRules.from_config()
    st = get_settings()
    iv = float(q["iv"] or 30.0)
    days = max(1, min(20, td - rules.time_stop_trading_days))
    scen = [
        Scenario(
            name=x.name,
            probability=x.probability,
            stock_move_pct=x.stock_move_pct,
            timing_days=x.timing_days,
            triggers=[],
            invalidation="",
        )
        for x in a.scenarios
    ]
    paths = min(st.mc_paths, 4000)
    seed = seed_for(f"tool:{a.symbol}:{today}")
    inp = CandidateInput(
        expiry,
        float(q["strike"]),
        otype,
        float(q["bid"]),
        float(q["ask"]),
        iv,
        (expiry - today).days,
        td,
        int(inst.lot_size or 1),
    )
    sign = 1 if a.direction == "bullish" else -1
    pred = sum(x.probability * x.stock_move_pct for x in a.scenarios) * sign
    out = {}
    for label, w in (("your_scenarios", 1.0), ("market_implied", 0.0)):
        paths_ = simulate_stock(spot, scen, iv / 100 / (252**0.5), days, paths, seed, ai_weight=w)
        r = evaluate(inp, paths_, rules, sign, pred, a.window_days, st.risk_free_rate, None)
        out[label] = {
            "ev_pct": round(r.ev_pct, 2),
            "p_t1_before_sl": round(r.p_t1_before_sl, 3),
            "p_t2": round(r.p_t2, 3),
            "breakeven_move_pct": r.breakeven_move_pct,
            "exp_hold_days": round(r.exp_hold_days, 1),
        }
    return {
        "available": True,
        "option": {
            "expiry": exp["expiry"],
            "strike": q["strike"],
            "type": otype,
            "ask": q["ask"],
            "iv": iv,
        },
        "spot": spot,
        "results": out,
        "note": "information only: the real strike/expiry choice and gates run after you answer",
    }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: type[_In]
    fn: Callable[[Session, ToolContext, Any], Any]

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.schema)


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool("get_news", "Recent tagged news for a stock (ids you may cite).", NewsIn, get_news),
        Tool(
            "get_filings",
            "Structured disclosures for a stock: insider trades, bulk/block "
            "deals, shareholding, ratings, regulatory actions, business updates.",
            DaysIn,
            get_filings,
        ),
        Tool(
            "get_results_extract",
            "Extracted quarterly results (revenue, PAT, margins, YoY, "
            "guidance, surprise) for a stock.",
            SymbolIn,
            get_results_extract,
        ),
        Tool(
            "get_option_chain",
            "Latest stored option quotes near the money for the allowed monthly expiries.",
            ChainIn,
            get_option_chain,
        ),
        Tool(
            "get_positioning",
            "Option-market positioning states (OI build-up, PCR, skew, "
            "basis, rollover, unusual activity, delivery).",
            SymbolIn,
            get_positioning,
        ),
        Tool(
            "get_regime",
            "Today's market regime (volatility x risk direction).",
            EmptyIn,
            get_regime,
        ),
        Tool(
            "get_analogues",
            "Historical base rates for an event type (stock, sector, all).",
            AnaloguesIn,
            get_analogues,
        ),
        Tool(
            "get_kg_neighbours",
            "Knowledge-graph links of a stock (suppliers, customers, "
            "competitors, group companies).",
            SymbolIn,
            get_kg_neighbours,
        ),
        Tool("get_macro", "Latest macro snapshot and recent macro releases.", EmptyIn, get_macro),
        Tool(
            "get_calendar", "Upcoming events for a stock and market-wide.", CalendarIn, get_calendar
        ),
        Tool(
            "run_simulator",
            "Run the Monte Carlo on your scenario tree for the ATM option: EV "
            "and probabilities, next to the market-implied baseline.",
            SimulatorIn,
            run_simulator,
        ),
    )
}


@dataclass
class ToolOutcome:
    content: str
    is_error: bool
    rows: int | None
    duration_ms: int


def call_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """The ONLY way the agent reaches data. Unknown tools and bad arguments are errors."""
    t0 = time.monotonic()
    tool = TOOLS.get(name)
    if tool is None:
        return ToolOutcome(f"error: unknown tool {name!r}", True, None, 0)
    try:
        parsed = tool.schema.model_validate(args)
        with read_only_session(ctx.engine) as s:
            data = tool.fn(s, ctx, parsed)
    except (ValidationError, ValueError) as exc:
        return ToolOutcome(f"error: {exc}"[:800], True, None, int((time.monotonic() - t0) * 1000))
    rows = len(data) if isinstance(data, list) else None
    body = json.dumps(data, default=str, ensure_ascii=False)
    if len(body) > max_chars():
        body = body[: max_chars()] + " … [truncated: ask for fewer rows]"
    return ToolOutcome(body, False, rows, int((time.monotonic() - t0) * 1000))


def specs(names: list[str] | None = None) -> list[ToolSpec]:
    return [t.spec() for n, t in TOOLS.items() if names is None or n in names]
