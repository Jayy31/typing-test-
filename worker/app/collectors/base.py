"""Common collector base (DESIGN_V2): every source is fetch -> parse -> store, with a timeout,
per-source health in the config table, and a job entrypoint `run(engine)`.

New sources (filings, insider trades, bulk/block deals, MF holdings, tenders, ratings,
regulators, peers, trends) subclass `Collector` and get scheduling/health for free.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Engine

from app.config import IST
from app.db.config_store import get_config, set_config
from app.db.session import session_scope

log = logging.getLogger(__name__)
HEALTH_KEY = "source_health"


def record_source(engine: Engine | None, name: str, ok: bool, error: str | None = None) -> None:
    """Per-source health: last success, last error, consecutive failures."""
    now = datetime.now(IST).isoformat(timespec="seconds")
    with session_scope(engine) as s:
        health: dict[str, Any] = dict(get_config(s, HEALTH_KEY, {}) or {})
        h = dict(health.get(name, {}))
        if ok:
            h.update(last_ok=now, consecutive_failures=0)
        else:
            h.update(
                last_error=(error or "")[:300],
                last_error_at=now,
                consecutive_failures=int(h.get("consecutive_failures", 0)) + 1,
            )
        health[name] = h
        set_config(s, HEALTH_KEY, health)


def source_health(engine: Engine | None = None) -> dict[str, Any]:
    with session_scope(engine) as s:
        return dict(get_config(s, HEALTH_KEY, {}) or {})


class Collector[T](ABC):
    name: ClassVar[str]
    timeout_s: ClassVar[float] = 20.0

    @abstractmethod
    def fetch(self) -> Any:
        """Network call(s). Must use `self.timeout_s`."""

    @abstractmethod
    def parse(self, raw: Any) -> list[T]:
        """Pure function of the raw payload; unit-tested with saved samples."""

    @abstractmethod
    def store(self, items: list[T], engine: Engine | None) -> int:
        """Idempotent write. Returns rows added/updated."""

    def run(self, engine: Engine | None = None) -> int:
        """Job entrypoint: records source health, re-raises so run_job logs the failure."""
        try:
            n = self.store(self.parse(self.fetch()), engine)
        except Exception as exc:
            record_source(engine, self.name, False, f"{type(exc).__name__}: {exc}")
            raise
        record_source(engine, self.name, True)
        return n
