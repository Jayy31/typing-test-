"""Refinement after a failed Stage 0 (Phase 14): deterministic grid variants first (free), then
Sonnet refinement for ai_proposal / system_evidence hypotheses. Max 10 variants per hypothesis;
every variant is a new spec version and every run a trial."""

import copy
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers.base import ModelProvider
from app.config import IST
from app.db.models import LabHypothesis, LabSpec, LabTrial
from app.db.session import session_scope
from app.lab import ai as lab_ai
from app.lab import registry as R
from app.lab.sources import PROMPTS
from app.lab.spec import StrategySpec, cfg, parse

log = logging.getLogger(__name__)


def _merge(d: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(d)
    for k, v in patch.items():
        out[k] = _merge(out.get(k) or {}, v) if isinstance(v, dict) else v
    return out


def candidates(s: Any) -> list[tuple[LabHypothesis, LabSpec]]:
    """Hypotheses whose newest spec was rejected at Stage 0 and that have variants left."""
    out = []
    mx = int(cfg()["registry"]["max_variants_per_hypothesis"])
    for h in s.scalars(
        select(LabHypothesis).where(LabHypothesis.status.in_(("rejected", "testing")))
    ):
        specs = list(
            s.scalars(
                select(LabSpec).where(LabSpec.hypothesis_id == h.id).order_by(LabSpec.version)
            )
        )
        if specs and specs[-1].stage == "rejected" and len(specs) < mx and h.source != "news_core":
            if all(sp.stage == "rejected" for sp in specs):
                out.append((h, specs[-1]))
    return out


def next_grid(base: dict[str, Any], tried: set[str]) -> tuple[StrategySpec, dict[str, Any]] | None:
    for patch in cfg()["refine"]["deterministic_grid"]:
        try:
            nxt = StrategySpec.model_validate(_merge(base, patch))
        except ValueError:
            continue
        if nxt.spec_hash() not in tried:
            return nxt, patch
    return None


def _tried(s: Any, hid: int) -> set[str]:
    return {sp.spec_hash for sp in s.scalars(select(LabSpec).where(LabSpec.hypothesis_id == hid))}


def grid_step(engine: Engine | None = None, limit: int = 10) -> int:
    made = 0
    with session_scope(engine) as s:
        for h, last in candidates(s)[:limit]:
            nxt = next_grid(parse(last.spec_yaml).model_dump(mode="json"), _tried(s, h.id))
            if nxt is None:
                continue
            try:
                R.add_spec(s, h, nxt[0], last.id, f"grid: {nxt[1]}")
                made += 1
            except R.RegistryError:
                pass
    return made


class RefineOut(BaseModel):
    change_note: str = Field(description="One sentence: what changed and why")
    spec: StrategySpec


def ai_step(
    engine: Engine | None = None,
    ai: AiClient | None = None,
    provider: ModelProvider | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(IST)
    ai = ai or AiClient(engine=engine)
    cap = int(cfg()["schedule"]["max_ai_refinements_per_night"])
    made = 0
    with session_scope(engine) as s:
        for h, last in candidates(s):
            if made >= cap:
                break
            if h.source not in cfg()["refine"]["ai_sources"]:
                continue
            grid_left = next_grid(parse(last.spec_yaml).model_dump(mode="json"), _tried(s, h.id))
            if grid_left is not None:
                continue  # free grid variants first
            try:
                lab_ai.check_budget(
                    s,
                    ai,
                    "lab_hypothesis",
                    now,
                    lab_ai.proposal_reserve(s, now or datetime.now(IST)),
                )
            except BudgetExceededError as exc:
                log.info("lab refinement paused: %s", exc)
                break
            diag = s.scalars(
                select(LabTrial).where(LabTrial.spec_id == last.id, LabTrial.kind == "stage0")
            ).first()
            rc = lab_ai.role_config("lab_hypothesis")
            res = lab_ai.run_loop(
                s,
                rc,
                "lab_hypothesis",
                (PROMPTS / "lab_refine_v1.md").read_text(encoding="utf-8"),
                f"HYPOTHESIS {h.name}\nRATIONALE: {h.rationale}\nCURRENT SPEC:\n{last.spec_yaml}\n"
                f"STAGE-0 DIAGNOSTICS: {_diag(diag)}",
                RefineOut,
                lab_ai.LabToolContext(s, {}),
                f"refine:{h.name}",
                provider,
            )
            if isinstance(res.data, RefineOut):
                sp = res.data.spec.model_dump(mode="json")
                sp.update(name=last.name, source=h.source)
                try:
                    R.add_spec(
                        s,
                        h,
                        StrategySpec.model_validate(sp),
                        last.id,
                        f"AI: {res.data.change_note}"[:500],
                    )
                    made += 1
                except (R.RegistryError, ValueError) as exc:
                    log.info("lab refinement not added: %s", exc)
    return made


def _diag(t: LabTrial | None) -> Any:
    return (t.metrics_json or {}).get("diagnostics") if t else "n/a"
