"""RSS news collector: fetch feeds, normalise, store new items, assign story clusters."""

import logging
import re
from datetime import UTC, datetime, timedelta
from time import struct_time
from typing import Any

import feedparser
import httpx
from pydantic import BaseModel
from sqlalchemy import Engine, select

from app.collectors.base import Collector, record_source
from app.config import IST
from app.db.models import NewsCluster, NewsItem
from app.db.session import session_scope
from app.processing.dedup import similarity, title_tokens, url_hash
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-options-research)"}


class RawNews(BaseModel):
    source: str
    kind: str
    title: str
    body: str | None
    url: str
    published_at: datetime | None


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _to_dt(value: struct_time | None) -> datetime | None:
    if not value:
        return None
    return datetime(*value[:6], tzinfo=UTC).astimezone(IST)


def parse_feed(content: bytes | str, source: str, kind: str) -> list[RawNews]:
    """Normalise one feed's XML into RawNews rows. Entries without title or link are skipped."""
    items: list[RawNews] = []
    for e in feedparser.parse(content).entries:
        title = _strip_html(str(e.get("title", "")))
        link = str(e.get("link", "")).strip()
        if not title or not link:
            continue
        summary = _strip_html(str(e.get("summary", ""))) or None
        published = _to_dt(e.get("published_parsed") or e.get("updated_parsed"))
        items.append(
            RawNews(
                source=source,
                kind=kind,
                title=title,
                body=summary,
                url=link,
                published_at=published,
            )
        )
    return items


def fetch_feed(url: str, timeout: float = 20.0) -> bytes:
    resp = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def store_items(items: list[RawNews], engine: Engine | None = None) -> int:
    """Insert unseen items (by URL hash) and attach each to a story cluster. Idempotent."""
    cfg = load_yaml("relevance.yaml")
    threshold = float(cfg["cluster_similarity"])
    window = timedelta(hours=float(cfg["cluster_window_hours"]))
    now = datetime.now(IST)
    added = 0
    with session_scope(engine) as s:
        recent = s.execute(
            select(NewsCluster.id, NewsCluster.headline).where(
                NewsCluster.last_seen >= now - window
            )
        ).all()
        clusters: dict[int, frozenset[str]] = {cid: title_tokens(h) for cid, h in recent}
        for it in items:
            h = url_hash(it.url)
            if s.scalar(select(NewsItem.id).where(NewsItem.url_hash == h)):
                continue
            toks = title_tokens(it.title)
            best, best_sim = None, 0.0
            for cid, ctoks in clusters.items():
                sim = similarity(toks, ctoks)
                if sim > best_sim:
                    best, best_sim = cid, sim
            if best is not None and best_sim >= threshold:
                cluster = s.get(NewsCluster, best)
                assert cluster is not None
                cluster.item_count += 1
                cluster.last_seen = now
            else:
                cluster = NewsCluster(
                    headline=it.title, first_seen=now, last_seen=now, item_count=1
                )
                s.add(cluster)
                s.flush()
                clusters[cluster.id] = toks
            s.add(
                NewsItem(
                    url_hash=h,
                    source=it.source,
                    title=it.title,
                    body=it.body,
                    url=it.url,
                    published_at=it.published_at,
                    cluster_id=cluster.id,
                    status="pending",
                )
            )
            added += 1
    return added


class RssFeedCollector(Collector[RawNews]):
    """One feed from config/news_sources.yaml."""

    name = "rss"

    def __init__(self, feed: dict[str, Any]) -> None:
        self.feed = feed

    def fetch(self) -> Any:
        return fetch_feed(self.feed["url"], self.timeout_s)

    def parse(self, raw: Any) -> list[RawNews]:
        return parse_feed(raw, self.feed["name"], self.feed["kind"])

    def store(self, items: list[RawNews], engine: Engine | None) -> int:
        return store_items(items, engine)

    def run(self, engine: Engine | None = None) -> int:
        try:
            n = self.store(self.parse(self.fetch()), engine)
        except Exception as exc:
            record_source(engine, f"rss:{self.feed['name']}", False, f"{type(exc).__name__}: {exc}")
            raise
        record_source(engine, f"rss:{self.feed['name']}", True)
        return n


def collect_news(engine: Engine | None = None) -> int:
    """Job entrypoint. A failing feed is recorded and skipped; raises only if every feed fails."""
    feeds: list[dict[str, Any]] = load_yaml("news_sources.yaml")["feeds"]
    total, failures = 0, 0
    for feed in feeds:
        try:
            total += RssFeedCollector(feed).run(engine)
        except Exception as exc:  # noqa: BLE001 - isolate per-feed failures
            failures += 1
            log.warning("feed %s failed: %s", feed["name"], exc)
    if failures == len(feeds):
        raise RuntimeError("all news feeds failed")
    return total
