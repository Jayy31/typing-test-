"""Overnight Strategy Lab loop (Phase 14, 23:10 IST, feat.strategy_lab).

Waits for the 5-year history (same check as the automatic backtests), then each night:
seed known patterns (once) -> data mining (no AI) -> system-evidence detectors (weekly) ->
AI proposals (<= 3 a week) -> Stage-0 screening queue -> holdout checks -> refinement (grid, then
AI) -> Haiku write-ups. Backtests and mining cost nothing; lab AI stops at its own caps and
before the overall caps (paused first).
"""

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app import flags
from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers.base import ModelProvider
from app.backtest.auto import history_ready
from app.backtest.data import HistoryData
from app.config import IST
from app.db.models import LabSpec, LabVerdict
from app.db.session import session_scope
from app.history.store import connect
from app.lab import ai as lab_ai
from app.lab import books, refine, sources
from app.lab import registry as R
from app.lab.spec import cfg

log = logging.getLogger(__name__)


class Writeup(BaseModel):
    text: str = Field(description="3-5 sentences, plain English")


def summaries(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    provider: ModelProvider | None = None,
    now: datetime | None = None,
) -> int:
    """Haiku write-ups for verdicts that have none (capped per night)."""
    ai = ai or AiClient(engine=engine)
    cap = int(cfg()["schedule"]["max_summaries_per_night"])
    done = 0
    with session_scope(engine) as s:
        for v in s.scalars(select(LabVerdict).order_by(LabVerdict.id.desc()).limit(50)):
            if done >= cap:
                break
            if (v.checks_json or {}).get("writeup"):
                continue
            try:
                lab_ai.check_budget(
                    s, ai, "lab_summary", now, lab_ai.proposal_reserve(s, now or datetime.now(IST))
                )
            except BudgetExceededError as exc:
                log.info("lab write-ups paused: %s", exc)
                break
            sp = s.get(LabSpec, v.spec_id)
            rc = lab_ai.role_config("lab_summary")
            from app.lab.sources import PROMPTS

            res = lab_ai.run_loop(
                s,
                rc,
                "lab_summary",
                (PROMPTS / "lab_summary_v1.md").read_text(encoding="utf-8"),
                f"SPEC:\n{sp.spec_yaml if sp else ''}\nSTAGE: {v.stage}\nRESULT: {v.summary}\n"
                f"CHECKS: {v.checks_json}",
                Writeup,
                lab_ai.LabToolContext(s, {}),
                f"writeup:{v.id}",
                provider,
            )
            if isinstance(res.data, Writeup):
                v.checks_json = {**(v.checks_json or {}), "writeup": res.data.text[:1200]}
                done += 1
    return done


def job(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    provider: ModelProvider | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(IST)
    with session_scope(engine) as s:
        if not flags.active(s, "feat.strategy_lab"):
            return {"skipped": "feat.strategy_lab is off"}
    out: dict[str, Any] = {}
    with connect() as con:
        ok, why = history_ready(con)
        if not ok:
            return {"waiting": why}
        data = HistoryData(con)
        out["seeded"] = sources.seed_patterns(engine)
        out["mined"] = sources.mine(con, engine)
        if now.weekday() == 6 or int(cfg()["schedule"]["evidence_every_days"]) <= 1:
            out["evidence"] = sources.detect_evidence(engine, now)
        out["proposed"] = sources.propose(con, engine, ai, provider, now)
        out["grid"] = refine.grid_step(engine)
        out["screened"] = screen_queue(con, data, engine)
        out["holdout"] = holdout_queue(con, data, engine)
        out["refined"] = refine.ai_step(engine, ai, provider, now)
        out["books_opened"] = books.open_books(engine, now)
        out["eod_books"] = books.eod_update(con, engine, data)
        out["books"] = books.review_books(engine, now)
        store_portfolio(engine)
    out["writeups"] = summaries(engine, ai, provider, now)
    log.info("strategy lab: %s", out)
    return out


def store_portfolio(engine: Engine | None = None) -> None:
    """The combined portfolio view (EOD incubation) is kept on the news_core book for the
    dashboard and Telegram."""
    from app.db.models import LabBook

    view = books.portfolio(engine)
    with session_scope(engine) as s:
        b = s.scalars(select(LabBook).where(LabBook.book == books.NEWS_CORE)).first()
        if b is not None:
            b.stats_json = {**(b.stats_json or {}), "portfolio": view}


def screen_queue(con: Any, data: HistoryData, engine: Engine | None) -> int:
    cap = int(cfg()["schedule"]["max_screens_per_night"])
    with session_scope(engine) as s:
        ids = list(
            s.scalars(
                select(LabSpec.id).where(LabSpec.stage == "stage0").order_by(LabSpec.id).limit(cap)
            )
        )
    for sid in ids:
        try:
            R.screen(con, sid, engine, data)
        except Exception as exc:  # noqa: BLE001 - one spec must not stop the night
            log.warning("lab screen of spec %s failed: %s", sid, exc)
    return len(ids)


def holdout_queue(con: Any, data: HistoryData, engine: Engine | None) -> int:
    with session_scope(engine) as s:
        ids = list(s.scalars(select(LabSpec.id).where(LabSpec.stage == "holdout_pending")))
    n = 0
    for sid in ids:
        try:
            out = R.holdout(con, sid, engine, data)
            n += 0 if isinstance(out, dict) else 1
        except R.RegistryError as exc:
            log.info("lab holdout: %s", exc)
    return n
