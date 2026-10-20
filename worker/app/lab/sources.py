"""Idea sources (Phase 14). Every hypothesis is pre-registered (rationale + expected effect)
before any test runs and tagged with its source.

- known_pattern: seeded from data/lab_patterns.yaml (once).
- data_mining (no AI): event type x (all | sector | volatility regime) groups from the event study
  on PRE-cutoff reactions only, with strong and stable base rates. Every group scanned counts as a
  trial for the deflated Sharpe (origin_json.scanned).
- system_evidence (rules, optional Haiku write-up): repeated IV-crush losses, rejected candidates
  whose counterfactuals won, watchlist gates that beat the main book.
- ai_proposal (Sonnet, up to 3 a week): flagged AI-memory risk; must pass the holdout.
"""

import logging
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml
from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers.base import ModelProvider
from app.config import IST
from app.db.models import LabHypothesis, PaperTrade, PostMortem, RejectedCandidate
from app.db.session import session_scope
from app.history.event_study import (
    bootstrap_mean_ci,
    grouped_values,
    reaction_rows,
    regime_labels,
    summarize,
    vix_series,
)
from app.lab import ai as lab_ai
from app.lab import registry as R
from app.lab import validate as V
from app.lab.spec import StrategySpec, cfg

log = logging.getLogger(__name__)
PATTERNS = Path(__file__).resolve().parents[3] / "data" / "lab_patterns.yaml"
PROMPTS = Path(__file__).resolve().parents[1] / "ai" / "prompts"


def _exists(s: Session, name: str) -> bool:
    return bool(s.scalar(select(LabHypothesis.id).where(LabHypothesis.name == name)))


def _register(
    s: Session,
    name: str,
    source: str,
    rationale: str,
    effect: str,
    spec: dict[str, Any],
    origin: dict[str, Any] | None = None,
) -> int | None:
    """Pre-register a hypothesis and its first spec. Invalid specs are never registered."""
    if _exists(s, name):
        return None
    try:
        sp = StrategySpec.model_validate({"name": name[:48], "source": source, **spec})
    except ValueError as exc:
        log.info("lab: %s not registered (invalid spec: %s)", name, exc)
        return None
    h = R.register(s, name, source, rationale, effect, origin=origin)
    try:
        return R.add_spec(s, h, sp).id
    except R.RegistryError as exc:
        log.info("lab: %s spec not added: %s", name, exc)
        return None


# ---- known patterns -----------------------------------------------------------------------
def seed_patterns(engine: Engine | None = None, path: Path = PATTERNS) -> int:
    rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("patterns") or []
    n = 0
    with session_scope(engine) as s:
        for p in rows:
            src = p.get("source", "known_pattern")
            if _register(
                s,
                p["name"],
                src,
                p["rationale"],
                p["expected_effect"],
                p["spec"],
                {"pattern": p["name"]},
            ):
                n += 1
    return n


# ---- data mining (no AI, pre-cutoff only) -------------------------------------------------
def mine(
    con: duckdb.DuckDBPyConnection, engine: Engine | None = None, limit: int | None = None
) -> int:
    mc = cfg()["mining"]
    limit = int(cfg()["schedule"]["max_new_mined_per_night"]) if limit is None else limit
    before = V.cutoff() + timedelta(days=1)  # holdout data is never mined
    rows = reaction_rows(con, before=before)
    vix = vix_series(con)
    labels = regime_labels(con) if "regime" in mc["splits"] else None
    groups = grouped_values(rows, vix, labels)
    stats = summarize(groups)
    w = int(mc["window"])
    scanned, found = 0, []
    for key, v in stats.items():
        etype, sec, sym, reg, win = key
        if win != w or sym is not None or etype is None or str(etype).startswith("kg:"):
            continue
        split = "sector" if sec is not None else ("regime" if str(reg).startswith("R:") else "none")
        if split not in mc["splits"] or (split == "none" and reg != "all"):
            continue
        scanned += 1
        n, mean, hit = int(v[0]), float(v[2]), float(v[4])
        if n < int(mc["min_n"]) or max(hit, 1 - hit) < float(mc["min_hit_rate"]):
            continue
        vals = groups[key]
        lo, hi = bootstrap_mean_ci(vals)
        if mean > 0 and lo <= float(mc["require_ci_lower_above"]):
            continue
        if mean < 0 and hi >= -float(mc["require_ci_lower_above"]):
            continue
        if mc.get("stable_halves", True):
            half = len(vals) // 2
            a = sum(x for x, _ in vals[:half]) / max(half, 1)
            b = sum(x for x, _ in vals[half:]) / max(len(vals) - half, 1)
            if (a > 0) != (mean > 0) or (b > 0) != (mean > 0):
                continue
        found.append((abs(mean) * (n**0.5), etype, sec, reg, n, mean, hit))
    found.sort(key=lambda x: -x[0])
    made = 0
    with session_scope(engine) as s:
        for _, etype, sec, reg, n, mean, hit in found:
            if made >= limit:
                break
            tag = sec or str(reg).removeprefix("R:").replace("/", "_") if reg != "all" else "all"
            name = f"mined_{etype}_{tag}".lower().replace(" ", "_")[:48]
            spec: dict[str, Any] = {
                "trigger": {
                    "event_types": [etype],
                    "direction": "from_event" if mean > 0 else "base_rate",
                    "min_analogue_n": 0 if mean > 0 else 20,
                },
                "exits": {"max_hold_days": w + 2},
            }
            if sec:
                spec["filters"] = {"sectors": [sec]}
            elif str(reg).startswith("R:"):
                spec["filters"] = {"regimes": [str(reg).removeprefix("R:").split("/")[0]]}
            if _register(
                s,
                name,
                "data_mining",
                f"Pre-cutoff event study: {etype}{' in ' + sec if sec else ''}"
                f"{' (' + str(reg) + ')' if reg != 'all' else ''}: n={n}, mean abnormal move "
                f"{mean:+.2f}% our way, hit {hit:.0%} over {w} days, stable in both halves.",
                f"{'Calls/puts in the news direction' if mean > 0 else 'Fade the news direction'} "
                f"earn a positive expectancy after costs.",
                spec,
                {"scanned": scanned, "n": n, "mean": round(mean, 3), "hit": round(hit, 3)},
            ):
                made += 1
    return made


# ---- system evidence (rules) --------------------------------------------------------------
def detect_evidence(engine: Engine | None = None, now: datetime | None = None) -> int:
    ec = cfg()["evidence"]
    now = now or datetime.now(IST)
    since = now - timedelta(days=int(ec["lookback_days"]))
    made = 0
    with session_scope(engine) as s:
        crush = (
            s.scalar(
                select(func.count(PostMortem.id)).where(
                    PostMortem.primary_cause == "iv_crush", PostMortem.created_at >= since
                )
            )
            or 0
        )
        if crush >= int(ec["iv_crush_losses"]) and _register(
            s,
            f"evidence_iv_crush_{now:%Y%m}",
            "system_evidence",
            f"{crush} paper trades in {ec['lookback_days']} days lost mainly to IV crush after "
            "results: entering after the announcement may keep the move without paying the "
            "event premium.",
            "Entering at the next open after results (instead of before) has a higher expectancy.",
            {
                "trigger": {"event_types": ["results_beat", "results_miss"]},
                "entry": {"timing": "next_open"},
                "exits": {"max_hold_days": 5},
            },
            {"detector": "iv_crush", "count": crush},
        ):
            made += 1
        won: Counter[str] = Counter()
        for stage, ret in s.execute(
            select(RejectedCandidate.stage, RejectedCandidate.option_return_pct).where(
                RejectedCandidate.resolved_at >= since
            )
        ):
            if ret is not None and ret > 0:
                won[stage] += 1
        for stage, k in won.items():
            if k >= int(ec["rejected_winners"]) and stage in ("edge", "probability", "ev"):
                if _register(
                    s,
                    f"evidence_rejected_{stage}_{now:%Y%m}",
                    "system_evidence",
                    f"{k} candidates rejected at the '{stage}' gate in {ec['lookback_days']} days "
                    "would have made money (counterfactual tracker).",
                    f"News signals without the '{stage}' gate still have a positive expectancy.",
                    {"trigger": {"event_types": ["results", "order_win", "rating"]}},
                    {"detector": "rejected_winners", "stage": stage, "count": k},
                ):
                    made += 1
        wl = s.execute(
            select(func.count(PaperTrade.id), func.avg(PaperTrade.pnl_pct)).where(
                PaperTrade.book == "watchlist",
                PaperTrade.status == "CLOSED",
                PaperTrade.exit_ts >= since,
            )
        ).one()
        if (
            (wl[0] or 0) >= int(ec["watchlist_gate_edge"])
            and (wl[1] or 0) > 0
            and _register(
                s,
                f"evidence_watchlist_{now:%Y%m}",
                "system_evidence",
                f"The watchlist book closed {wl[0]} trades averaging {float(wl[1]):+.1f}% of "
                "premium: near-misses may be under-rated by the gates.",
                "News signals traded in calm or normal regimes with looser gates stay profitable.",
                {
                    "trigger": {"event_types": ["results", "order_win"]},
                    "filters": {"regimes": ["calm", "normal"]},
                },
                {"detector": "watchlist", "count": int(wl[0])},
            )
        ):
            made += 1
    return made


# ---- AI proposals (Sonnet) ----------------------------------------------------------------
class Proposal(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]{3,40}$")
    rationale: str = Field(description="Why this should work (mechanism), 2-4 sentences")
    expected_effect: str = Field(description="What result you expect BEFORE any test")
    spec: StrategySpec


class ProposalOut(BaseModel):
    proposals: list[Proposal] = Field(max_length=3)


def proposals_this_week(s: Session, now: datetime) -> int:
    week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        s.scalar(
            select(func.count(LabHypothesis.id)).where(
                LabHypothesis.source == "ai_proposal", LabHypothesis.registered_at >= week
            )
        )
        or 0
    )


def propose(
    con: duckdb.DuckDBPyConnection,
    engine: Engine | None = None,
    ai: AiClient | None = None,
    provider: ModelProvider | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(IST)
    ai = ai or AiClient(engine=engine)
    room = int(cfg()["schedule"]["ai_proposals_per_week"])
    stats = summarize(
        grouped_values(reaction_rows(con, before=V.cutoff() + timedelta(days=1)), vix_series(con))
    )
    made = 0
    with session_scope(engine) as s:
        left = room - proposals_this_week(s, now)
        if left <= 0:
            return 0
        try:
            lab_ai.check_budget(s, ai, "lab_hypothesis", now)
        except BudgetExceededError as exc:
            log.info("lab proposals paused: %s", exc)
            return 0
        rc = lab_ai.role_config("lab_hypothesis")
        res = lab_ai.run_loop(
            s,
            rc,
            "lab_hypothesis",
            (PROMPTS / "lab_propose_v1.md").read_text(encoding="utf-8"),
            f"TODAY: {now:%Y-%m-%d}. Propose up to {min(left, 3)} new, distinct hypotheses.",
            ProposalOut,
            lab_ai.LabToolContext(s, stats),
            "propose",
            provider,
        )
        if not isinstance(res.data, ProposalOut):
            return 0
        for p in res.data.proposals[:left]:
            spec = p.spec.model_dump(mode="json")
            spec.pop("name", None)
            spec.pop("source", None)
            if _register(
                s,
                p.name,
                "ai_proposal",
                p.rationale,
                p.expected_effect,
                spec,
                {"run_id": res.run_id, "model": rc.model, "ai_memory_risk": True},
            ):
                made += 1
    return made


def all_scanned(s: Session) -> int:
    """Mining groups scanned (for the deflated Sharpe's trial count)."""
    vals = [
        (h.origin_json or {}).get("scanned", 0)
        for h in s.scalars(select(LabHypothesis).where(LabHypothesis.source == "data_mining"))
    ]
    return int(max(vals, default=0))


_ = date
