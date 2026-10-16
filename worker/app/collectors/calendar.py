"""Calendar tracker: loads the maintained YAML events into events_calendar."""

from datetime import date
from typing import Any

from sqlalchemy import Engine

from app.collectors.nse import store_events
from app.yaml_config import load_yaml


def load_manual_events() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in load_yaml("calendar_events.yaml").get("events") or []:
        d = e["event_date"]
        out.append(
            {
                "symbol": e.get("symbol"),
                "sector": e.get("sector"),
                "event_type": e["event_type"],
                "event_date": d if isinstance(d, date) else date.fromisoformat(str(d)),
                "details": e.get("details"),
                "source": "manual_yaml",
            }
        )
    return out


def sync_manual_calendar(engine: Engine | None = None) -> int:
    return store_events(load_manual_events(), engine)
