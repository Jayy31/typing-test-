"""Stage 1 AI tagger (cheap model): news item -> structured impacts.

One shared core (prompt, message, cleaning, schema) used by two modes with identical output:
  - live:  `tag_items` job, one call per item
  - batch: `submit_batch` / `collect_batch` for history (Message Batches API, half price).
    One-off batch jobs must be cost-estimated (`estimate`) and approved by the owner first.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app import taxonomy
from app.ai.client import AiClient, AiOutputError, BudgetExceededError, CostEstimate, estimate_cost
from app.ai.schemas import TaggerOutput
from app.db.models import Instrument, NewsItem, NewsTag
from app.db.session import session_scope
from app.processing.entity_map import EntityMapper, InstrumentInfo
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VERSION = "tagger_v2"
_PROMPT = (Path(__file__).parent / "prompts" / "tagger_v2.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class TagInput:
    key: str  # live: news_items.id; history: hist event id
    title: str
    body: str | None
    source: str


@dataclass(frozen=True)
class TagContext:
    universe: list[str]
    sectors: list[str]
    mapper: EntityMapper


def _cfg() -> dict[str, Any]:
    return dict(load_yaml("ai.yaml")["tagger"])


def system_prompt() -> str:
    return _PROMPT.replace("{taxonomy}", taxonomy.prompt_block())


def load_context(s: Session) -> TagContext:
    insts = list(s.scalars(select(Instrument)))
    return TagContext(
        universe=sorted(i.symbol for i in insts if i.is_fno),
        sectors=sorted({i.sector for i in insts}),
        mapper=EntityMapper(
            [InstrumentInfo(i.symbol, i.name, i.sector, tuple(i.aliases or [])) for i in insts]
        ),
    )


def build_user_message(inp: TagInput, ctx: TagContext) -> str:
    hints = ctx.mapper.map(f"{inp.title} {inp.body or ''}").symbols
    return (
        f"Allowed F&O symbols: {', '.join(ctx.universe)}\n"
        f"Allowed sectors: {', '.join(ctx.sectors)}\n"
        f"Keyword-matched symbols (hint only): {', '.join(hints) or 'none'}\n\n"
        f"Source: {inp.source}\nTitle: {inp.title}\n"
        f"Text: {(inp.body or '')[: int(_cfg()['body_chars'])]}"
    )


def clean_output(out: TaggerOutput, universe: set[str], sectors: set[str]) -> TaggerOutput:
    """Drop invented symbols/sectors and themes outside the taxonomy."""
    kept = [
        i
        for i in out.impacts
        if (i.symbol in universe) or (i.symbol is None and i.sector in sectors)
    ]
    known = set(taxonomy.themes())
    return out.model_copy(update={"impacts": kept, "themes": [t for t in out.themes if t in known]})


def tag_one(ai: AiClient, inp: TagInput, ctx: TagContext) -> TaggerOutput:
    cfg = _cfg()
    out = ai.parse(
        "tagger",
        system_prompt(),
        build_user_message(inp, ctx),
        TaggerOutput,
        int(cfg["max_tokens"]),
        thinking=cfg.get("thinking"),
    )
    return clean_output(out, set(ctx.universe), set(ctx.sectors))


# ---- batch mode ----------------------------------------------------------------------
def estimate(n_items: int) -> CostEstimate:
    cfg = _cfg()
    return estimate_cost(
        "tagger", n_items, int(cfg["est_input_tokens"]), int(cfg["est_output_tokens"])
    )


def submit_batch(ai: AiClient, inputs: list[TagInput], ctx: TagContext) -> str:
    cfg = _cfg()
    return ai.submit_batch(
        "tagger",
        system_prompt(),
        [(inp.key, build_user_message(inp, ctx)) for inp in inputs],
        TaggerOutput,
        int(cfg["max_tokens"]),
        thinking=cfg.get("thinking"),
    )


def collect_batch(ai: AiClient, batch_id: str, ctx: TagContext) -> dict[str, TaggerOutput | None]:
    raw = ai.batch_results("tagger", batch_id, TaggerOutput)
    return {
        k: (clean_output(v, set(ctx.universe), set(ctx.sectors)) if v else None)
        for k, v in raw.items()
    }


# ---- live job ------------------------------------------------------------------------
def tag_items(engine: Engine | None = None, ai: AiClient | None = None) -> int:
    """Tag items the relevance gate marked 'relevant'. Stops quietly when the budget is spent."""
    ai = ai or AiClient(engine=engine)
    with session_scope(engine) as s:
        ctx = load_context(s)
        pending = [
            TagInput(str(n.id), n.title, n.body, n.source)
            for n in s.scalars(
                select(NewsItem)
                .where(NewsItem.status == "relevant")
                .order_by(NewsItem.id)
                .limit(int(_cfg()["max_items_per_run"]))
            )
        ]
    done = 0
    for inp in pending:
        try:
            out = tag_one(ai, inp, ctx)
        except BudgetExceededError:
            log.warning("AI budget reached; tagging paused")
            break
        except AiOutputError as exc:
            log.warning("tagger output rejected for item %s: %s", inp.key, exc)
            _set_status(engine, int(inp.key), "tag_failed")
            continue
        store_tags(engine, int(inp.key), out, ai.model_for("tagger"))
        done += 1
    return done


def _set_status(engine: Engine | None, news_id: int, status: str) -> None:
    with session_scope(engine) as s:
        row = s.get(NewsItem, news_id)
        assert row is not None
        row.status = status


def store_tags(engine: Engine | None, news_id: int, out: TaggerOutput, model: str) -> None:
    with session_scope(engine) as s:
        row = s.get(NewsItem, news_id)
        assert row is not None
        if not (out.is_actionable and out.impacts):
            row.status = "not_actionable"
            return
        for imp in out.impacts:
            s.add(
                NewsTag(
                    news_id=news_id,
                    symbol=imp.symbol,
                    sector=imp.sector,
                    theme=",".join(out.themes)[:64] or None,  # column is varchar(64)
                    event_type=out.event_type,
                    direction=imp.direction,
                    sentiment=imp.sentiment,
                    novelty=out.novelty,
                    model=model,
                    prompt_ver=PROMPT_VERSION,
                    taxonomy_ver=taxonomy.version(),
                )
            )
        row.status = "tagged"
