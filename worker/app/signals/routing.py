"""Which analysis each candidate gets (Phase 5 owner rules).

Candidates in a cycle are ranked by a pre-AI score (evidence strength, official filing, rules
engine). The top ones get the full multi-agent panel until AI_MULTI_AGENT_MAX_PER_DAY panels ran
today, the next ones the single analyst until AI_SINGLE_ANALYST_MAX_PER_DAY, the rest are
recorded as rejected ('ai_cap'). The panel needs feat.multi_agent and all five panel models set.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import flags
from app.ai.client import AiClient
from app.db.models import Analysis
from app.rules.engine import RuleHit, adjustment_and_alignment
from app.signals.candidates import Candidate
from app.yaml_config import load_yaml

PANEL_ROLES = ("researcher", "novelty", "bull", "bear", "judge")


def prescreen(cand: Candidate, hits: list[RuleHit]) -> float:
    cap = float(load_yaml("signals.yaml")["rules_adjustment_cap"])
    rules = adjustment_and_alignment(hits, cand.direction, cap)[0]
    return round(100 * cand.strength + rules + (10 if cand.has_official else 0), 2)


def runs_today(s: Session, source: str, now: datetime) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        s.scalar(
            select(func.count(Analysis.id)).where(
                Analysis.prediction_source == source, Analysis.created_at >= start
            )
        )
        or 0
    )


def choose_mode(s: Session, ai: AiClient, now: datetime) -> tuple[str | None, str]:
    """('panel' | 'single' | None, why)."""
    st = ai.settings
    panel_n, single_n = runs_today(s, "panel", now), runs_today(s, "single", now)
    panel_ok = flags.active(s, "feat.multi_agent") and ai.configured(*PANEL_ROLES)
    if panel_ok and panel_n < st.ai_multi_agent_max_per_day:
        return "panel", f"panel {panel_n + 1}/{st.ai_multi_agent_max_per_day} today"
    if single_n < st.ai_single_analyst_max_per_day:
        why = (
            "panel cap reached"
            if panel_ok
            else "panel not active (flag off or panel models not set)"
        )
        return (
            "single",
            f"{why}; single analyst {single_n + 1}/{st.ai_single_analyst_max_per_day} today",
        )
    return None, (
        f"daily AI caps reached (panel {panel_n}/{st.ai_multi_agent_max_per_day}, "
        f"single {single_n}/{st.ai_single_analyst_max_per_day})"
    )
