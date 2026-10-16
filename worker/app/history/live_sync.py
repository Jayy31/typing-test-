"""Keep history growing from live data (same taxonomy and labels as the backfill):
- live tagged news -> hist_events (labels only; event_id 'live:<tag id>')
- curated events from data/historical_events.yaml -> hist_events (source 'curated')
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml
from sqlalchemy import Engine, select

from app import taxonomy
from app.config import IST
from app.db.models import Instrument, NewsItem, NewsTag
from app.db.session import session_scope
from app.history.integrity import LABELS_ONLY
from app.timeutil import as_ist

CURATED = Path(__file__).resolve().parents[3] / "data" / "historical_events.yaml"


def sync_live_events(
    con: duckdb.DuckDBPyConnection, engine: Engine | None = None, days: int = 3
) -> int:
    since = datetime.now(IST) - timedelta(days=days)
    rows: list[tuple[Any, ...]] = []
    with session_scope(engine) as s:
        sectors = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
        for tag, item in s.execute(
            select(NewsTag, NewsItem).join(NewsItem, NewsItem.id == NewsTag.news_id)
        ).all():
            when = as_ist(item.published_at or item.fetched_at)
            if when < since:
                continue
            rows.append(
                (
                    f"live:{tag.id}",
                    when.date(),
                    when.time().replace(tzinfo=None),
                    tag.symbol,
                    tag.sector or sectors.get(tag.symbol or ""),
                    tag.theme,
                    tag.event_type,
                    tag.direction,
                    item.source,
                    tag.sentiment,
                    tag.novelty,
                    item.title,
                    item.url,
                    tag.taxonomy_ver or taxonomy.version(),
                    f"{tag.prompt_ver}:{LABELS_ONLY}",
                )
            )
    con.executemany(
        "INSERT OR REPLACE INTO hist_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def load_curated(con: duckdb.DuckDBPyConnection, path: Path = CURATED) -> int:
    """Owner-verified events: {date, symbol|sector, event_type, direction, title, time?}."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = []
    for i, e in enumerate(data.get("events") or []):
        d = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
        t = time.fromisoformat(str(e["time"])) if e.get("time") else None
        rows.append(
            (
                f"curated:{d}:{e.get('symbol') or e.get('sector')}:{i}",
                d,
                t,
                e.get("symbol"),
                e.get("sector"),
                e.get("theme"),
                e["event_type"],
                e.get("direction"),
                "curated",
                e.get("sentiment"),
                e.get("surprise"),
                e.get("title") or e.get("description"),
                e.get("url"),
                taxonomy.version(),
                "curated",
            )
        )
    con.executemany(
        "INSERT OR REPLACE INTO hist_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)
