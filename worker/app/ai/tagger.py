"""Stage 1 AI tagger (cheap model): one news item -> structured impacts."""

import logging
from pathlib import Path

from sqlalchemy import Engine, select

from app.ai.client import AiClient, AiOutputError, BudgetExceededError
from app.ai.schemas import TaggerOutput
from app.db.models import Instrument, NewsItem, NewsTag
from app.db.session import session_scope
from app.processing.entity_map import EntityMapper, InstrumentInfo
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VERSION = "tagger_v1"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "tagger_v1.md").read_text(encoding="utf-8")


def build_user_message(
    title: str,
    body: str | None,
    source: str,
    symbols: list[str],
    universe: list[str],
    sectors: list[str],
    body_chars: int,
) -> str:
    hints = ", ".join(symbols) if symbols else "none"
    return (
        f"Allowed F&O symbols: {', '.join(universe)}\n"
        f"Allowed sectors: {', '.join(sectors)}\n"
        f"Keyword-matched symbols (hint only): {hints}\n\n"
        f"Source: {source}\nTitle: {title}\nText: {(body or '')[:body_chars]}"
    )


def clean_output(out: TaggerOutput, universe: set[str], sectors: set[str]) -> TaggerOutput:
    """Drop impacts naming symbols/sectors outside the allowed lists (no invented symbols)."""
    kept = [
        i
        for i in out.impacts
        if (i.symbol in universe) or (i.symbol is None and i.sector in sectors)
    ]
    return out.model_copy(update={"impacts": kept})


def tag_items(engine: Engine | None = None, ai: AiClient | None = None) -> int:
    """Tag items the relevance gate marked 'relevant'. Stops quietly when the budget is spent."""
    cfg = load_yaml("ai.yaml")["tagger"]
    ai = ai or AiClient(engine=engine)
    with session_scope(engine) as s:
        insts = list(s.scalars(select(Instrument)))
        universe = sorted(i.symbol for i in insts if i.is_fno)
        sectors = sorted({i.sector for i in insts})
        pending = list(
            s.scalars(
                select(NewsItem)
                .where(NewsItem.status == "relevant")
                .order_by(NewsItem.id)
                .limit(int(cfg["max_items_per_run"]))
            )
        )
        mapper = EntityMapper(
            [InstrumentInfo(i.symbol, i.name, i.sector, tuple(i.aliases or [])) for i in insts]
        )
        hints = {n.id: mapper.map(f"{n.title} {n.body or ''}").symbols for n in pending}
    done = 0
    for item in pending:
        user = build_user_message(
            item.title,
            item.body,
            item.source,
            hints[item.id],
            universe,
            sectors,
            int(cfg["body_chars"]),
        )
        try:
            out = clean_output(
                ai.parse(
                    "tagger",
                    SYSTEM_PROMPT,
                    user,
                    TaggerOutput,
                    int(cfg["max_tokens"]),
                    thinking=cfg.get("thinking"),
                ),
                set(universe),
                set(sectors),
            )
        except BudgetExceededError:
            log.warning("AI budget reached; tagging paused")
            break
        except AiOutputError as exc:
            log.warning("tagger output rejected for item %s: %s", item.id, exc)
            _set_status(engine, item.id, "tag_failed")
            continue
        _store_tags(engine, item.id, out, ai.model_for("tagger"))
        done += 1
    return done


def _set_status(engine: Engine | None, news_id: int, status: str) -> None:
    with session_scope(engine) as s:
        row = s.get(NewsItem, news_id)
        assert row is not None
        row.status = status


def _store_tags(engine: Engine | None, news_id: int, out: TaggerOutput, model: str) -> None:
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
                    theme=",".join(out.themes) or None,
                    event_type=out.event_type,
                    direction=imp.direction,
                    sentiment=imp.sentiment,
                    novelty=out.novelty,
                    model=model,
                    prompt_ver=PROMPT_VERSION,
                )
            )
        row.status = "tagged"
