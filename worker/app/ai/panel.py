"""Multi-agent panel (DESIGN_V2, Phase 5): researcher -> novelty -> bull + bear -> judge.

Every agent sees the same evidence message (the single analyst's context) plus the outputs of
the agents before it. Every cited news id is checked against the ids actually provided; an
agent whose output fails validation is retried once, then the panel fails as a whole (the
analysis is stored as failed with whatever agent outputs exist).
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.ai.analyst import validate_output
from app.ai.client import AiClient, AiOutputError, CallUsage
from app.ai.schemas import CaseOutput, JudgeOutput, NoveltyOutput, ResearchOutput
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSIONS = {
    "researcher": "researcher_v1",
    "novelty": "novelty_v1",
    "bull": "bull_v1",
    "bear": "bear_v1",
    "judge": "judge_v1",
}
PANEL_PROMPT_VERSION = "panel_v1"  # stored on the analysis (judge_v1 + agents above)


def _prompt(agent: str) -> str:
    return (PROMPTS / f"{PROMPT_VERSIONS[agent]}.md").read_text(encoding="utf-8")


@dataclass
class AgentStep:
    agent: str
    prompt_ver: str
    output: dict[str, Any] | None
    usage: CallUsage | None
    error: str | None = None


@dataclass
class PanelResult:
    out: JudgeOutput | None
    steps: list[AgentStep] = field(default_factory=list)
    error: str | None = None

    @property
    def cost_inr(self) -> float:
        return round(sum(s.usage.cost_inr for s in self.steps if s.usage), 4)


def _check_ids(ids: list[int], provided: set[int], agent: str) -> None:
    unknown = set(ids) - provided
    if unknown:
        raise AiOutputError(f"{agent} cites unknown news ids {sorted(unknown)}")


def check_research(out: ResearchOutput, provided: set[int]) -> None:
    _check_ids(
        out.cited_news_ids + [i for f in out.facts for i in f.news_ids], provided, "researcher"
    )


def check_novelty(out: NoveltyOutput, provided: set[int]) -> None:
    _check_ids(out.cited_news_ids, provided, "novelty")


def check_case(out: CaseOutput, provided: set[int], side: str) -> None:
    if out.side != side:
        raise AiOutputError(f"{side} advocate returned the {out.side} side")
    _check_ids(out.cited_news_ids + [i for a in out.arguments for i in a.news_ids], provided, side)
    if not out.arguments:
        raise AiOutputError(f"{side} case has no arguments")


def _run[T: BaseModel](
    ai: AiClient,
    agent: str,
    message: str,
    schema: type[T],
    check: Callable[[T], None],
    retries: int = 1,
) -> tuple[T, AgentStep]:
    cfg = load_yaml("ai.yaml")["agents"]
    last: AiOutputError | None = None
    for _ in range(retries + 1):
        try:
            out, usage = ai.parse_with_usage(
                agent, _prompt(agent), message, schema, int(cfg[agent]["max_tokens"])
            )
            check(out)
            return out, AgentStep(agent, PROMPT_VERSIONS[agent], out.model_dump(mode="json"), usage)
        except AiOutputError as exc:
            last = exc
            log.warning("%s output rejected: %s", agent, exc)
    assert last is not None
    raise last


def _section(title: str, out: BaseModel) -> str:
    return f"\n\n{title}:\n{out.model_dump_json(indent=1)}"


def run_panel(
    ai: AiClient, message: str, provided: set[int], parallel: bool | None = None
) -> PanelResult:
    """Run the panel. BudgetExceededError propagates (the caller pauses AI work)."""
    res = PanelResult(None)
    if parallel is None:
        parallel = bool(load_yaml("ai.yaml")["agents"].get("parallel_bull_bear", True))
    try:
        research, step = _run(
            ai, "researcher", message, ResearchOutput, lambda o: check_research(o, provided)
        )
        res.steps.append(step)
        ctx = message + _section("RESEARCHER FACTS", research)
        novelty, step = _run(
            ai, "novelty", ctx, NoveltyOutput, lambda o: check_novelty(o, provided)
        )
        res.steps.append(step)
        ctx += _section("NOVELTY CHECK", novelty)

        def case(side: str) -> tuple[CaseOutput, AgentStep]:
            return _run(ai, side, ctx, CaseOutput, lambda o: check_case(o, provided, side))

        if parallel:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fb, fr = pool.submit(case, "bull"), pool.submit(case, "bear")
                (bull, s_bull), (bear, s_bear) = fb.result(), fr.result()
        else:
            (bull, s_bull), (bear, s_bear) = case("bull"), case("bear")
        res.steps += [s_bull, s_bear]
        ctx += _section("BULL CASE", bull) + _section("BEAR CASE", bear)
        judge, step = _run(ai, "judge", ctx, JudgeOutput, lambda o: validate_output(o, provided))
        res.steps.append(step)
        res.out = judge
    except AiOutputError as exc:
        res.error = f"panel failed: {exc}"
    return res
