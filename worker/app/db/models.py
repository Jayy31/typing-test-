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
    isin: Mapped[str | None] = mapped_column(String(16))
    instrument_key: Mapped[str | None] = mapped_column(String(64))  # broker key, e.g. NSE_EQ|INE...
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
    commodities_json: Mapped[dict[str, float] | None] = mapped_column(Json)  # Phase 7: name -> last


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
    bid_qty: Mapped[int | None] = mapped_column(BigInteger)
    ask_qty: Mapped[int | None] = mapped_column(BigInteger)


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
    prediction_source: Mapped[str | None] = mapped_column(String(8))  # panel / single (Phase 5)
    impact_order: Mapped[int | None] = mapped_column(Integer)  # 1 direct, 2-3 via graph (Phase 8)


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
    expected_value_inr: Mapped[float | None] = mapped_column(Float)  # per position (all lots)
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
    # raw (unshrunk AI) values next to the shrunk ones used for gating/ranking until calibration
    shrink_weight: Mapped[float | None] = mapped_column(Float)
    predicted_move_raw_pct: Mapped[float | None] = mapped_column(Float)
    edge_ratio_raw: Mapped[float | None] = mapped_column(Float)
    expected_value_raw: Mapped[float | None] = mapped_column(Float)
    p_t1_before_sl_raw: Mapped[float | None] = mapped_column(Float)
    iv_percentile: Mapped[float | None] = mapped_column(Float)
    event_iv_premium: Mapped[float | None] = mapped_column(Float)
    # option choice and pricing state
    option_type: Mapped[str | None] = mapped_column(String(2))  # CE / PE
    pricing_status: Mapped[str | None] = mapped_column(String(16))  # priced / pending / failed
    # latency and freshness (v2.1 B7)
    news_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    news_to_signal_s: Mapped[float | None] = mapped_column(Float)
    premove_pct: Mapped[float | None] = mapped_column(
        Float
    )  # stock move in our direction before signal
    data_age_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    theme_key: Mapped[str | None] = mapped_column(String(64), index=True)  # overlap grouping (A5)
    # Phase 5: who made the prediction and how probabilities were calibrated
    prediction_source: Mapped[str | None] = mapped_column(String(8))  # panel / single
    calibration_method: Mapped[str | None] = mapped_column(String(16))  # shrink / platt / isotonic
    ai_weight: Mapped[float | None] = mapped_column(Float)  # share of AI paths in the EV simulation
    # Phase 7: signals made after the close / on holidays enter at the next open (+ first 15 min)
    enter_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    pnl: Mapped[float | None] = mapped_column(Float)  # Rs per position, after costs
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    # Phase 3b: contract, rules and live state
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    option_type: Mapped[str | None] = mapped_column(String(2))
    strike: Mapped[float | None] = mapped_column(Float)
    expiry: Mapped[date | None] = mapped_column(Date)
    lot_size: Mapped[int | None] = mapped_column(Integer)
    theme_key: Mapped[str | None] = mapped_column(String(64), index=True)
    rules_hash: Mapped[str | None] = mapped_column(String(16))
    rules_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    state_json: Mapped[dict[str, Any] | None] = mapped_column(Json)  # exits.PositionState
    last_premium: Mapped[float | None] = mapped_column(Float)  # latest bid
    last_spot: Mapped[float | None] = mapped_column(Float)
    last_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pnl_pct: Mapped[float | None] = mapped_column(Float)  # % of entry premium, after costs
    # fills (DESIGN_V2 R2)
    entry_bid: Mapped[float | None] = mapped_column(Float)
    entry_ask: Mapped[float | None] = mapped_column(Float)
    entry_bid_qty: Mapped[int | None] = mapped_column(BigInteger)
    entry_ask_qty: Mapped[int | None] = mapped_column(BigInteger)
    exit_bid_qty: Mapped[int | None] = mapped_column(BigInteger)
    entry_fill: Mapped[str | None] = mapped_column(
        String(12)
    )  # fillable/partial/unfillable/unknown
    exit_fill: Mapped[str | None] = mapped_column(String(12))
    fillability: Mapped[str | None] = mapped_column(String(12), index=True)  # worse of the two
    # underlying, benchmark and attribution (v2.1 B2, B6)
    spot_entry: Mapped[float | None] = mapped_column(Float)
    spot_exit: Mapped[float | None] = mapped_column(Float)
    pre_news_spot: Mapped[float | None] = mapped_column(Float)
    benchmark_stock_pct: Mapped[float | None] = mapped_column(
        Float
    )  # stock move our way, same period
    benchmark_stock_inr: Mapped[float | None] = mapped_column(
        Float
    )  # 1 lot of stock/future instead
    mfe_pct: Mapped[float | None] = mapped_column(Float)  # best premium vs entry, %
    mae_pct: Mapped[float | None] = mapped_column(Float)  # worst premium vs entry, %
    attribution_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    review_flag: Mapped[str | None] = mapped_column(Text)  # e.g. opposite signal (A5 flag mode)
    booking_mode: Mapped[str | None] = mapped_column(String(16))  # per_lot / single_exit
    slippage_inr: Mapped[float | None] = mapped_column(Float)  # total vs trigger levels; <0 = worse


class TradeEvent(Base):
    __tablename__ = "trade_events"
    __table_args__ = (UniqueConstraint("trade_id", "event", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("paper_trades.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event: Mapped[str] = mapped_column(String(16))  # ENTRY/T1/T2/T3/SL/TRAIL/THESIS/TIME/NOMOVE
    premium: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    fraction: Mapped[float | None] = mapped_column(Float)  # of the position closed by this event
    lots: Mapped[int | None] = mapped_column(Integer)  # whole lots bought/sold by this event
    trigger_level: Mapped[float | None] = mapped_column(Float)  # stop/target level that fired
    slippage_inr: Mapped[float | None] = mapped_column(
        Float
    )  # (fill - trigger) x units; <0 = worse
    gap: Mapped[bool | None] = mapped_column(Boolean)  # filled worse than the level (gap/slippage)
    spot: Mapped[float | None] = mapped_column(Float)
    bid_qty: Mapped[int | None] = mapped_column(BigInteger)
    ask_qty: Mapped[int | None] = mapped_column(BigInteger)


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
    file_path: Mapped[str | None] = mapped_column(Text)  # Phase 10: sent as a document (PDF)


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
    source: Mapped[str] = mapped_column(
        String(8), default="all", server_default="all"
    )  # all/panel/single
    n_events: Mapped[int | None] = mapped_column(Integer)  # independent news events (clusters)


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
    # Phase 5: `predicted` is the RAW model probability (calibrator input); predicted_used is what
    # the gates used. cluster_key groups records from the same news event (weighted as one).
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id"), index=True)
    rejection_id: Mapped[int | None] = mapped_column(ForeignKey("rejected_candidates.id"))
    symbol: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(8))  # panel / single
    cluster_key: Mapped[str | None] = mapped_column(String(64), index=True)
    predicted_used: Mapped[float | None] = mapped_column(Float)


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
    # Phase 8: exposure (scales the weight), point-in-time validity, provenance
    exposure_kind: Mapped[str | None] = mapped_column(
        String(24)
    )  # revenue_share/input_cost_share/stake
    exposure_pct: Mapped[float | None] = mapped_column(Float)
    valid_from: Mapped[date | None] = mapped_column(Date)  # null = structural (always)
    valid_to: Mapped[date | None] = mapped_column(Date)
    origin: Mapped[str | None] = mapped_column(String(16))  # seed / sub_sector / insider / ai
    evidence_news_id: Mapped[int | None] = mapped_column(ForeignKey("news_items.id"))


class KgPath(Base):
    """Phase 8: one graph propagation (or a support-only / unknown-sign path) for /why."""

    __tablename__ = "kg_paths"
    __table_args__ = (UniqueConstraint("source_news_id", "target", "path_key", name="uq_kg_paths"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    source_news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id"), index=True)
    source_symbol: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(32), index=True)
    path_key: Mapped[str] = mapped_column(String(256))  # A>type>B>type>C
    hops: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(12))  # propagated / support / unknown / capped
    sign_basis: Mapped[str] = mapped_column(String(12))  # default / base_rate / unknown
    weight: Mapped[float] = mapped_column(Float)  # product of hop weights x decay
    sentiment: Mapped[float | None] = mapped_column(Float)
    news_id: Mapped[int | None] = mapped_column(ForeignKey("news_items.id"))  # evidence row
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(Json)


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


class RejectedCandidate(Base):
    """Every candidate rejected from the candidate stage onward, with stage and reason (v2.1 B1).

    The counterfactual tracker (Phase 3b) fills the outcome columns: what the stock and the
    would-be option did afterwards under the same exit rules.
    """

    __tablename__ = "rejected_candidates"
    __table_args__ = (UniqueConstraint("dedupe_key"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(
        String(24)
    )  # candidate/score/eligibility/liquidity/edge/entry/ev
    reason: Mapped[str] = mapped_column(Text)
    direction: Mapped[str | None] = mapped_column(String(8))  # bullish / bearish
    score: Mapped[float | None] = mapped_column(Float)
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id"))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    spot_at_reject: Mapped[float | None] = mapped_column(Float)
    predicted_move_pct: Mapped[float | None] = mapped_column(Float)
    window_days: Mapped[int | None] = mapped_column(Integer)
    ref_expiry: Mapped[date | None] = mapped_column(Date)
    ref_strike: Mapped[float | None] = mapped_column(Float)
    ref_option_type: Mapped[str | None] = mapped_column(String(2))
    ref_premium: Mapped[float | None] = mapped_column(Float)  # ask at rejection time
    details_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # counterfactual outcome (Phase 3b tracker)
    outcome_status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    stock_return_pct: Mapped[float | None] = mapped_column(Float)
    option_return_pct: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(16))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PortfolioSnapshot(Base):
    """Notional paper portfolio (v2.1 A6): equity curve and drawdown."""

    __tablename__ = "portfolio_snapshots"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    capital: Mapped[float] = mapped_column(Float)
    realised_inr: Mapped[float] = mapped_column(Float)
    unrealised_inr: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    peak_equity: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float)
    open_trades: Mapped[int] = mapped_column(Integer)
    premium_at_risk_inr: Mapped[float] = mapped_column(Float)
    exposure_json: Mapped[dict[str, Any] | None] = mapped_column(Json)  # per theme
    realised_fillable_inr: Mapped[float | None] = mapped_column(Float)  # excl. unfillable (R2)
    capital_used_inr: Mapped[float | None] = mapped_column(
        Float
    )  # entry premium x units, open trades


class PredictionScore(Base):
    """Scorecard for every directional prediction, traded or not (v2.1 B3)."""

    __tablename__ = "prediction_scores"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), unique=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    predicted_move_pct: Mapped[float] = mapped_column(Float)  # our direction, shrunk (gating value)
    predicted_move_raw_pct: Mapped[float | None] = mapped_column(Float)  # AI scenario tree alone
    predicted_source: Mapped[str | None] = mapped_column(String(16))
    prediction_source: Mapped[str | None] = mapped_column(String(8))  # panel / single
    p_move: Mapped[float | None] = mapped_column(Float)
    window_days: Mapped[int] = mapped_column(Integer)
    spot_start: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    path_json: Mapped[list[Any] | None] = mapped_column(Json)  # [[date, close], ...]
    status: Mapped[str] = mapped_column(
        String(12), default="open", server_default="open", index=True
    )
    realised_move_pct: Mapped[float | None] = mapped_column(Float)  # our direction, at window end
    direction_hit: Mapped[bool | None] = mapped_column(Boolean)
    move_error_pct: Mapped[float | None] = mapped_column(Float)  # realised - predicted
    days_to_half_move: Mapped[int | None] = mapped_column(Integer)
    timing_error_days: Mapped[int | None] = mapped_column(Integer)
    max_favourable_pct: Mapped[float | None] = mapped_column(Float)
    max_adverse_pct: Mapped[float | None] = mapped_column(Float)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostMortem(Base):
    """AI post-mortem of a closed paper trade (v2.1 B4)."""

    __tablename__ = "post_mortems"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("paper_trades.id"), unique=True)
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("analyses.id"))
    prediction_source: Mapped[str | None] = mapped_column(String(8))
    primary_cause: Mapped[str] = mapped_column(String(24), index=True)
    secondary_causes: Mapped[list[str] | None] = mapped_column(Json)
    thesis_correct: Mapped[bool | None] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text)
    output_json: Mapped[dict[str, Any]] = mapped_column(Json)
    model: Mapped[str] = mapped_column(String(128))
    prompt_ver: Mapped[str] = mapped_column(String(32))
    cost_inr: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Digest(Base):
    """Weekly review digest (DESIGN_V2 R3): suggestions only, never applied automatically."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    text: Mapped[str] = mapped_column(Text)
    data_json: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class AiSmokeRun(Base):
    """Live AI smoke test: one panel, one single-analyst call, one post-mortem on fixed samples."""

    __tablename__ = "ai_smoke_runs"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(String(16))  # startup / manual
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    total_cost_inr: Mapped[float] = mapped_column(Float, default=0)
    panel_seconds: Mapped[float | None] = mapped_column(Float)
    results_json: Mapped[dict[str, Any]] = mapped_column(Json)
    fixes_json: Mapped[list[str] | None] = mapped_column(Json)  # auto-fixes applied


# --- Phase 7: deeper data sources -------------------------------------------------------------
# Every disclosure table keeps BOTH the event date (when it happened) and disclosed_at (when it
# became public). Research (event study, rules mode, backtester) only ever uses disclosed_at.


class _Structured:
    """Shared columns: evidence outcome of a structured row."""

    subtype: Mapped[str | None] = mapped_column(String(64))  # tx2 structured event type
    material: Mapped[bool] = mapped_column(Boolean, default=False)
    materiality_json: Mapped[dict[str, Any] | None] = mapped_column(Json)  # why (not) material
    news_id: Mapped[int | None] = mapped_column(ForeignKey("news_items.id"))  # evidence row
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(Json)


class InsiderTrade(_Structured, Base):
    __tablename__ = "insider_trades"
    __table_args__ = (UniqueConstraint("ref", name="uq_insider_trades_ref"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(64))  # hash of the disclosure
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    person: Mapped[str | None] = mapped_column(Text)
    person_category: Mapped[str | None] = mapped_column(String(64))
    counterparty: Mapped[str] = mapped_column(
        String(16)
    )  # promoter/promoter_group/director_kmp/other
    side: Mapped[str] = mapped_column(String(4))  # buy / sell
    quantity: Mapped[float | None] = mapped_column(Float)
    value_inr: Mapped[float | None] = mapped_column(Float)
    mode: Mapped[str | None] = mapped_column(String(64))
    event_date: Mapped[date | None] = mapped_column(Date)  # trade date
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BulkBlockDeal(_Structured, Base):
    __tablename__ = "bulk_block_deals"
    __table_args__ = (UniqueConstraint("ref", name="uq_bulk_block_deals_ref"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))  # bulk / block
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    client: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str] = mapped_column(
        String(16)
    )  # promoter/promoter_group/fii/mf_dii/other
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    value_inr: Mapped[float | None] = mapped_column(Float)
    event_date: Mapped[date | None] = mapped_column(Date)  # deal date
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Shareholding(_Structured, Base):
    __tablename__ = "shareholding"
    __table_args__ = (UniqueConstraint("symbol", "event_date", name="uq_shareholding"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    event_date: Mapped[date] = mapped_column(Date)  # quarter end
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    promoter_pct: Mapped[float | None] = mapped_column(Float)
    fii_pct: Mapped[float | None] = mapped_column(Float)
    dii_pct: Mapped[float | None] = mapped_column(Float)
    public_pct: Mapped[float | None] = mapped_column(Float)
    pledge_pct: Mapped[float | None] = mapped_column(Float)  # % of promoter holding pledged
    subtypes: Mapped[list[str] | None] = mapped_column(Json)  # one filing can move several stakes


class Rating(_Structured, Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("ref", name="uq_ratings_ref"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    agency: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))  # upgrade / downgrade / reaffirm / other
    event_date: Mapped[date | None] = mapped_column(Date)  # rating action date
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)


class RegulatoryAction(_Structured, Base):
    __tablename__ = "regulatory_actions"
    __table_args__ = (UniqueConstraint("ref", name="uq_regulatory_actions_ref"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(64))
    regulator: Mapped[str] = mapped_column(String(16))  # sebi / usfda
    match_confidence: Mapped[float | None] = mapped_column(Float)  # Phase 8 name match (0-1)
    match_candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(Json)
    action_type: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(
        String(32), index=True
    )  # null = not mapped to a stock
    event_date: Mapped[date | None] = mapped_column(Date)  # order / letter date
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)


class ResultsExtract(_Structured, Base):
    __tablename__ = "results_extracts"
    __table_args__ = (UniqueConstraint("symbol", "period_end", name="uq_results_extracts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    method: Mapped[str] = mapped_column(String(8))  # xbrl / ai
    consolidated: Mapped[bool | None] = mapped_column(Boolean)
    revenue: Mapped[float | None] = mapped_column(Float)  # Rs crore
    pat: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    revenue_yoy_pct: Mapped[float | None] = mapped_column(Float)
    pat_yoy_pct: Mapped[float | None] = mapped_column(Float)
    margin_change_pp: Mapped[float | None] = mapped_column(Float)
    guidance: Mapped[str | None] = mapped_column(String(16))  # raised / cut / maintained / none
    surprise_score: Mapped[float | None] = mapped_column(Float)  # -1..1 vs own trend
    surprise_json: Mapped[dict[str, Any] | None] = mapped_column(Json)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_ver: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    latency_s: Mapped[float | None] = mapped_column(Float)  # disclosure -> extract stored


class BusinessUpdate(_Structured, Base):
    __tablename__ = "business_updates"
    __table_args__ = (UniqueConstraint("symbol", "period", "metric", name="uq_business_updates"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    period: Mapped[date] = mapped_column(Date)  # month
    metric: Mapped[str] = mapped_column(String(32))  # total_units / domestic_units / exports_units
    value: Mapped[float | None] = mapped_column(Float)
    yoy_pct: Mapped[float | None] = mapped_column(Float)
    disclosed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_ver: Mapped[str | None] = mapped_column(String(32))


class MacroRelease(Base):
    __tablename__ = "macro_releases"
    __table_args__ = (UniqueConstraint("name", "period", name="uq_macro_releases"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32))  # cpi / wpi / iip / gdp / gst / pmi_mfg ...
    period: Mapped[date] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)
    prior: Mapped[float | None] = mapped_column(Float)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(32))


class StockStats(Base):
    """Nightly materiality inputs: 20-day average traded value and market cap."""

    __tablename__ = "stock_stats"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    adv20_inr: Mapped[float | None] = mapped_column(Float)
    issued_shares: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    mcap_inr: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


# --- Phase 9: dashboard access ----------------------------------------------------------------


class DashboardOwner(Base):
    """The one email allowed to read the dashboard (synced from DASHBOARD_ALLOWED_EMAIL)."""

    __tablename__ = "dashboard_owner"

    email: Mapped[str] = mapped_column(String(256), primary_key=True)


class ConfigAudit(Base):
    """Every change to an owner switch (feature flags, briefs, mute): who, when, old -> new.
    Written by a database trigger (Postgres) and by the worker for Telegram /mute."""

    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    key: Mapped[str] = mapped_column(String(128), index=True)
    old_value: Mapped[Any | None] = mapped_column(Json)
    new_value: Mapped[Any | None] = mapped_column(Json)
    changed_by: Mapped[str | None] = mapped_column(String(256))


class EventLibrary(Base):
    """Event-study statistics copied from the history store weekly, for the dashboard's Event
    Library (the store itself is DuckDB on the VM). Direct events and graph keys (kg:...)."""

    __tablename__ = "event_library"
    __table_args__ = (
        UniqueConstraint("event_type", "level_key", "regime", "window", name="uq_event_library"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    level_key: Mapped[str] = mapped_column(String(96))  # 'all' / 'sector:<name>' / 'stock:<symbol>'
    regime: Mapped[str] = mapped_column(String(16))
    window: Mapped[int] = mapped_column(Integer)
    n: Mapped[int] = mapped_column(Integer)
    n_eff: Mapped[float] = mapped_column(Float)
    mean: Mapped[float] = mapped_column(Float)
    median: Mapped[float] = mapped_column(Float)
    hit_rate: Mapped[float] = mapped_column(Float)
    p25: Mapped[float] = mapped_column(Float)
    p75: Mapped[float] = mapped_column(Float)
    examples_json: Mapped[list[dict[str, Any]] | None] = mapped_column(Json)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# --- Phase 10: analytics, monthly report, learning loop ------------------------------------------


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    month: Mapped[date] = mapped_column(Date, unique=True)  # first day of the reported month
    data_json: Mapped[dict[str, Any]] = mapped_column(Json)
    text: Mapped[str] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(
        Text
    )  # only if every figure it cites is in data_json
    ai_summary_status: Mapped[str | None] = mapped_column(
        String(24)
    )  # ok / rejected / off / failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Suggestion(Base):
    """Learning-loop proposal. The owner accepts/rejects on the dashboard (status only); the worker
    opens a GitHub PR editing the YAML for accepted ones and merges one per month at month start."""

    __tablename__ = "suggestions"
    __table_args__ = (UniqueConstraint("month", "param", "kind", name="uq_suggestions"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # change / revert
    param: Mapped[str] = mapped_column(String(64))
    file: Mapped[str] = mapped_column(String(128))  # e.g. worker/config/options.yaml
    yaml_path: Mapped[str] = mapped_column(String(128))  # e.g. selection.min_p_t1_before_sl
    current_value: Mapped[float] = mapped_column(Float)
    proposed_value: Mapped[float] = mapped_column(Float)
    expected_effect: Mapped[float] = mapped_column(Float)  # % of premium per month (total)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[str] = mapped_column(
        String(12), index=True
    )  # pending/accepted/rejected/applied/superseded
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(256))
    pr_url: Mapped[str | None] = mapped_column(Text)
    pr_number: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_json: Mapped[dict[str, Any] | None] = mapped_column(
        Json
    )  # actual vs predicted after 2 months
    reverts_id: Mapped[int | None] = mapped_column(ForeignKey("suggestions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class AiVsRules(Base):
    """B3: for each analysis, what the rules-only path (labels + analogues, no AI) would have
    done."""

    __tablename__ = "ai_vs_rules"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    news_direction: Mapped[str] = mapped_column(String(8))
    ai_direction: Mapped[str | None] = mapped_column(String(8))
    ai_signal: Mapped[bool] = mapped_column(Boolean, default=False)  # a live signal was made
    rules_direction: Mapped[str | None] = mapped_column(String(8))  # None = rules would not trade
    rules_reason: Mapped[str | None] = mapped_column(Text)
    analogue_n: Mapped[int | None] = mapped_column(Integer)
    analogue_mean: Mapped[float | None] = mapped_column(Float)
    realised_move_pct: Mapped[float | None] = mapped_column(Float)  # signed stock move (+ up)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoliveCheck(Base):
    """Phase 11: 2-hour quick check and 24-hour first-run report results."""

    __tablename__ = "golive_checks"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8))  # quick / full / manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    status: Mapped[str] = mapped_column(String(8))  # ok / warn / fail (worst check)
    results_json: Mapped[dict[str, Any]] = mapped_column(Json)
    text: Mapped[str] = mapped_column(Text)


class MarketRegime(Base):
    """Phase 12 (feat.regime): one deterministic market-regime label per trading day."""

    __tablename__ = "market_regime"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    vol: Mapped[str] = mapped_column(String(12))  # calm / normal / stressed / unknown
    direction: Mapped[str] = mapped_column(String(12))  # risk_on / risk_off / unknown
    label: Mapped[str] = mapped_column(String(32))
    inputs: Mapped[str] = mapped_column(String(32))  # e.g. "VIX+Nifty; FII unknown"
    vix: Mapped[float | None] = mapped_column(Float)
    vix_pct: Mapped[float | None] = mapped_column(Float)
    nifty_ret_pct: Mapped[float | None] = mapped_column(Float)
    fii_net_cr: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(8))  # live / history
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class PositioningSnapshot(Base):
    """Phase 12 (feat.positioning): option-market positioning states per stock per day."""

    __tablename__ = "positioning_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    states_json: Mapped[dict[str, Any]] = mapped_column(Json)  # feature -> [state, value]
    source: Mapped[str] = mapped_column(String(8))  # live / eod
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
