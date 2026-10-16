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
