"""Shared test helpers: fake Anthropic messages API and DB seeding."""

import hashlib
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Engine

from app.ai.schemas import AnalystOutput, Scenario
from app.collectors.fno_meta import SYMBOL_MASTER, parse_symbol_master, sync_instruments
from app.config import IST
from app.db.models import NewsItem, NewsTag
from app.db.session import session_scope


class FakeMessages:
    """Returns the queued outputs one by one (the last one repeats)."""

    def __init__(self, *outputs: Any, stop_reason: str = "end_turn") -> None:
        self.outputs = list(outputs)
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        usage = SimpleNamespace(
            input_tokens=2000,
            output_tokens=400,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return SimpleNamespace(parsed_output=out, stop_reason=self.stop_reason, usage=usage)


def analyst_output(**kw: Any) -> AnalystOutput:
    base: dict[str, Any] = dict(
        direction="bullish",
        thesis="Airtel announced a tariff hike. ARPU should rise.",
        expected_move_pct=4.0,
        window_days=3,
        impact=80,
        surprise=70,
        clarity=80,
        timing=75,
        confidence="high",
        risks=["competitor holds tariffs"],
        counter_arguments=["already priced in"],
        invalidators=["tariff hike rolled back"],
        scenarios=[
            Scenario(
                name="hike sticks",
                probability=0.6,
                stock_move_pct=5.0,
                timing_days=3,
                triggers=["peers follow"],
                invalidation="rollback",
            ),
            Scenario(
                name="muted reaction",
                probability=0.3,
                stock_move_pct=0.5,
                timing_days=3,
                triggers=["priced in"],
                invalidation="strong volumes",
            ),
            Scenario(
                name="sell the news",
                probability=0.1,
                stock_move_pct=-3.0,
                timing_days=2,
                triggers=["profit booking"],
                invalidation="fresh buying",
            ),
        ],
        cited_news_ids=[1],
        analogue_notes=None,
    )
    base.update(kw)
    return AnalystOutput(**base)


def seed_instruments(engine: Engine, fno: dict[str, int] | None = None) -> None:
    rows = parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8"))
    sync_instruments(rows, fno or {"BHARTIARTL": 475, "TCS": 175, "SBIN": 750}, engine)


def seed_news(
    engine: Engine,
    symbol: str = "BHARTIARTL",
    sentiment: float = 0.8,
    source: str = "nse_announcements",
    title: str = "Bharti Airtel announces tariff hike across plans",
    n: int = 1,
) -> list[int]:
    """Insert n tagged news items for a symbol. Returns the news ids."""
    ids: list[int] = []
    with session_scope(engine) as s:
        for k in range(n):
            item = NewsItem(
                url_hash=hashlib.sha256(
                    f"{symbol}{source}{title}{k}{sentiment}".encode()
                ).hexdigest(),
                source=source,
                title=f"{title} {k}" if n > 1 else title,
                body="Details of the announcement.",
                url=f"https://x/{symbol}/{k}/{sentiment}",
                published_at=datetime.now(IST),
                status="tagged",
            )
            s.add(item)
            s.flush()
            s.add(
                NewsTag(
                    news_id=item.id,
                    symbol=symbol,
                    sector=None,
                    theme="tariffs",
                    event_type="policy",
                    direction="bullish" if sentiment > 0 else "bearish",
                    sentiment=sentiment,
                    novelty=0.9,
                    model="m",
                    prompt_ver="tagger_v1",
                )
            )
            ids.append(item.id)
    return ids
