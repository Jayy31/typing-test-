"""Which analysis each candidate gets (Phase 5 owner rules).

Candidates in a cycle are ranked by a pre-AI score (evidence strength, official filing, rules
engine). The top ones get the full multi-agent panel until AI_MULTI_AGENT_MAX_PER_DAY panels ran
today, the next ones the single analyst until AI_SINGLE_ANALYST_MAX_PER_DAY, the rest are
recorded as rejected ('ai_cap'). The panel needs feat.multi_agent and all five panel models set.

Phase 13: with feat.research_agent on, the research agent replaces the panel: the top
`live.max_per_day` candidates of the day get an investigation (the first one on research_top when
that role has its own model), the rest go to the single analyst within its cap. The panel is not
used while the agent is on; a failed agent run falls back to the single analyst.
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


def agent_runs_today(s: Session, now: datetime) -> int:
    from app.db.models import ResearchRun

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        s.scalar(
            select(func.count(ResearchRun.id)).where(
                ResearchRun.kind == "live", ResearchRun.created_at >= start
            )
        )
        or 0
    )


def agent_role(s: Session, now: datetime) -> str:
    """research_top for the day's first (strongest) investigation when it has its own model."""
    from app.research.config import top_enabled

    return "research_top" if top_enabled() and agent_runs_today(s, now) == 0 else "research_live"


def graph_panels_today(s: Session, now: datetime) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        s.scalar(
            select(func.count(Analysis.id)).where(
                Analysis.prediction_source == "panel",
                Analysis.impact_order > 1,
                Analysis.created_at >= start,
            )
        )
        or 0
    )


def choose_mode(
    s: Session, ai: AiClient, now: datetime, impact_order: int = 1
) -> tuple[str | None, str]:
    """('agent' | 'panel' | 'single' | None, why). Graph-propagated candidates (impact_order > 1) may use at
    most KG_MAX_PANEL_SLOTS of the daily panels; the rest go to the single analyst."""
    st = ai.settings
    single_n = runs_today(s, "single", now)
    if flags.active(s, "feat.research_agent"):
        from app.research.config import cfg as research_cfg

        cap = int(research_cfg()["live"]["max_per_day"])
        agent_n = agent_runs_today(s, now)
        if agent_n < cap:
            return "agent", f"research agent {agent_n + 1}/{cap} today"
        if single_n < st.ai_single_analyst_max_per_day:
            return "single", (
                f"research agent cap reached ({cap}); single analyst "
                f"{single_n + 1}/{st.ai_single_analyst_max_per_day} today"
            )
        return None, (
            f"daily AI caps reached (research agent {agent_n}/{cap}, "
            f"single {single_n}/{st.ai_single_analyst_max_per_day})"
        )
    panel_n = runs_today(s, "panel", now)
    panel_ok = flags.active(s, "feat.multi_agent") and ai.configured(*PANEL_ROLES)
    kg_full = impact_order > 1 and graph_panels_today(s, now) >= st.kg_max_panel_slots
    if panel_ok and panel_n < st.ai_multi_agent_max_per_day and not kg_full:
        return "panel", f"panel {panel_n + 1}/{st.ai_multi_agent_max_per_day} today"
    if kg_full and single_n < st.ai_single_analyst_max_per_day:
        return "single", (
            f"graph candidates' panel slots used ({st.kg_max_panel_slots}); single analyst "
            f"{single_n + 1}/{st.ai_single_analyst_max_per_day} today"
        )
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
