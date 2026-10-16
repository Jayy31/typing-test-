"""Core tables (DESIGN.md section 14). Postgres is the single source of truth."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres, plain JSON elsewhere (unit tests run on SQLite)
Json = JSON().with_variant(JSONB(), "postgresql")
# BigInteger primary keys must be Integer on SQLite for autoincrement
BigId = BigInteger().with_variant(Integer(), "sqlite")


def _now() -> Any:
    return func.now()


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    sector: Mapped[str] = mapped_column(String(64))
    sub_sector: Mapped[str | None] = mapped_column(String(64))
    aliases: Mapped[list[str]] = mapped_column(Json, default=list)
    is_fno: Mapped[bool] = mapped_column(Boolean, default=False)
    lot_size: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class NewsCluster(Base):
    __tablename__ = "news_clusters"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    headline: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    item_count: Mapped[int] = mapped_column(Integer, default=1)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("news_clusters.id"))
    # Postgres is the job queue: pending -> processed / dropped
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)


class NewsTag(Base):
    __tablename__ = "news_tags"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id"), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    theme: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str | None] = mapped_column(String(16))
    sentiment: Mapped[float | None] = mapped_column(Float)
    novelty: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_ver: Mapped[str | None] = mapped_column(String(32))
    taxonomy_ver: Mapped[str | None] = mapped_column(String(16))


class MacroSnapshot(Base):
    __tablename__ = "macro_snapshots"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    brent: Mapped[float | None] = mapped_column(Float)
    usdinr: Mapped[float | None] = mapped_column(Float)
    dxy: Mapped[float | None] = mapped_column(Float)
    us10y: Mapped[float | None] = mapped_column(Float)
    gift_nifty: Mapped[float | None] = mapped_column(Float)
    india_vix: Mapped[float | None] = mapped_column(Float)
    fii_net: Mapped[float | None] = mapped_column(Float)
    dii_net: Mapped[float | None] = mapped_column(Float)
    adr_json: Mapped[dict[str, Any] | None] = mapped_column(Json)


class OptionQuote(Base):
    __tablename__ = "option_quotes"
    __table_args__ = (Index("ix_option_quotes_lookup", "symbol", "expiry", "strike", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(32))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    type: Mapped[str] = mapped_column(String(2))  # CE / PE
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    ltp: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    oi: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    spot: Mapped[float | None] = mapped_column(Float)


class EventCalendar(Base):
    __tablename__ = "events_calendar"
    __table_args__ = (UniqueConstraint("symbol", "sector", "event_type", "event_date"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    event_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(Json)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("news_clusters.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    thesis: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(16))
    move_size: Mapped[float | None] = mapped_column(Float)
    window_days: Mapped[int | None] = mapped_column(Integer)
    risks: Mapped[list[str] | None] = mapped_column(Json)
    counter_args: Mapped[list[str] | None] = mapped_column(Json)
    analogues: Mapped[list[dict[str, Any]] | None] = mapped_column(Json)
    scores_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    confidence: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_ver: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(16))
    strike: Mapped[float | None] = mapped_column(Float)
    expiry: Mapped[date | None] = mapped_column(Date)
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), index=True)
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    # DESIGN_V2: configuration provenance
    engine_version: Mapped[str | None] = mapped_column(String(16))
    flags_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    impact_order: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )  # 1 direct, 2-3 via KG
    # probabilities and EV (null until the phase that fills them)
    p_move: Mapped[float | None] = mapped_column(Float)
    p_t1_before_sl: Mapped[float | None] = mapped_column(Float)
    p_t2: Mapped[float | None] = mapped_column(Float)
    p_t3: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)  # % of premium after costs
    expected_value_inr: Mapped[float | None] = mapped_column(Float)  # per lot
    calibrator_id: Mapped[int | None] = mapped_column(
        ForeignKey("calibrators.id")
    )  # null = uncalibrated
    # predicted vs implied move
    implied_move_pct: Mapped[float | None] = mapped_column(Float)
    predicted_move_pct: Mapped[float | None] = mapped_column(Float)  # in the trade direction
    predicted_move_source: Mapped[str | None] = mapped_column(
        String(16)
    )  # scenario_tree/analogues/blended
    edge_ratio: Mapped[float | None] = mapped_column(Float)
    iv_percentile: Mapped[float | None] = mapped_column(Float)
    event_iv_premium: Mapped[float | None] = mapped_column(Float)


class SignalVersion(Base):
    __tablename__ = "signal_versions"

    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), unique=True)
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_premium: Mapped[float] = mapped_column(Float)
    lots: Mapped[int] = mapped_column(Integer, default=1)
    sl: Mapped[float] = mapped_column(Float)
    t1: Mapped[float] = mapped_column(Float)
    t2: Mapped[float] = mapped_column(Float)
    t3: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), index=True)
    exit_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_premium: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))


class TradeEvent(Base):
    __tablename__ = "trade_events"
    __table_args__ = (UniqueConstraint("trade_id", "event", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("paper_trades.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event: Mapped[str] = mapped_column(String(16))  # ENTRY/T1/T2/T3/SL/TRAIL/THESIS/TIME/NOMOVE
    premium: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    condition_json: Mapped[dict[str, Any]] = mapped_column(Json)
    effect_json: Mapped[dict[str, Any]] = mapped_column(Json)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Config(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[Any] = mapped_column(Json)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # running / ok / error
    error: Mapped[str | None] = mapped_column(Text)
    items_processed: Mapped[int | None] = mapped_column(Integer)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0)


class Alert(Base):
    """Outbox for Telegram messages. Postgres is the queue; a job sends pending rows."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # signal/sector/trade/health/brief
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    text: Mapped[str] = mapped_column(Text)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- DESIGN_V2 tables -------------------------------------------------------------------


class Scenario(Base):
    """Scenario tree branch from an analysis (probabilities per analysis sum to 1)."""

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    probability: Mapped[float] = mapped_column(Float)
    stock_move_pct: Mapped[float] = mapped_column(Float)
    timing_days: Mapped[int] = mapped_column(Integer)
    triggers: Mapped[list[str] | None] = mapped_column(Json)
    invalidation: Mapped[str | None] = mapped_column(Text)


class SignalCandidate(Base):
    """Every strike/expiry the simulator evaluated for a signal (drill-down table)."""

    __tablename__ = "signal_candidates"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    option_type: Mapped[str] = mapped_column(String(2))  # CE / PE
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    oi: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    spread_pct: Mapped[float | None] = mapped_column(Float)
    ev_pct: Mapped[float | None] = mapped_column(Float)
    ev_inr: Mapped[float | None] = mapped_column(Float)
    p_t1_before_sl: Mapped[float | None] = mapped_column(Float)
    p_t3: Mapped[float | None] = mapped_column(Float)
    max_loss_inr: Mapped[float | None] = mapped_column(Float)
    exp_hold_days: Mapped[float | None] = mapped_column(Float)
    theta_per_day: Mapped[float | None] = mapped_column(Float)
    breakeven_move_pct: Mapped[float | None] = mapped_column(Float)
    iv_sensitivity: Mapped[float | None] = mapped_column(Float)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(Json)


class AgentRun(Base):
    """One multi-agent step (researcher/bull/bear/judge/novelty) for an analysis."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), index=True)
    agent: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    prompt_ver: Mapped[str] = mapped_column(String(32))
    input_refs: Mapped[dict[str, Any] | None] = mapped_column(Json)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Calibrator(Base):
    """Versioned probability calibrator (isotonic / Platt), refit weekly."""

    __tablename__ = "calibrators"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(32))  # p_move / p_t1_before_sl / ...
    method: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    params_json: Mapped[dict[str, Any]] = mapped_column(Json)
    n_samples: Mapped[int] = mapped_column(Integer)
    brier: Mapped[float | None] = mapped_column(Float)
    fitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class CalibrationRecord(Base):
    """Predicted probability vs realised outcome, for Brier score and reliability curves."""

    __tablename__ = "calibration_records"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), index=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("paper_trades.id"))
    target: Mapped[str] = mapped_column(String(32))
    predicted: Mapped[float] = mapped_column(Float)
    calibrator_id: Mapped[int | None] = mapped_column(ForeignKey("calibrators.id"))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[bool | None] = mapped_column(Boolean)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KgNode(Base):
    __tablename__ = "kg_nodes"
    __table_args__ = (UniqueConstraint("kind", "name"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))  # company/sector/commodity/country/...
    name: Mapped[str] = mapped_column(String(256))
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    attrs: Mapped[dict[str, Any] | None] = mapped_column(Json)


class KgEdge(Base):
    __tablename__ = "kg_edges"
    __table_args__ = (UniqueConstraint("src", "dst", "type"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    src: Mapped[int] = mapped_column(ForeignKey("kg_nodes.id"), index=True)
    dst: Mapped[int] = mapped_column(ForeignKey("kg_nodes.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(Json)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    notes: Mapped[str | None] = mapped_column(Text)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    event_ref: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date)
    entry_premium: Mapped[float] = mapped_column(Float)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_premium: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    pnl_pct: Mapped[float | None] = mapped_column(Float)
    pnl_inr: Mapped[float | None] = mapped_column(Float)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
