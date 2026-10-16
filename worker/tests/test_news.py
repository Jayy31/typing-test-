from pathlib import Path

from sqlalchemy import Engine, select

from app.collectors.news_rss import RawNews, parse_feed, store_items
from app.db.models import NewsCluster, NewsItem
from app.db.session import session_scope
from app.processing.dedup import canonical_url, similarity, title_tokens, url_hash

FEED = (Path(__file__).parent / "fixtures" / "sample_feed.xml").read_bytes()


def test_canonical_url_strips_tracking_and_trailing_slash() -> None:
    a = canonical_url("HTTPS://Example.com/a/?utm_source=x&id=5#frag")
    assert a == "https://example.com/a?id=5"
    assert url_hash("https://example.com/a?utm_source=x") == url_hash("https://example.com/a/")


def test_similarity() -> None:
    a = title_tokens("Bharti Airtel hikes tariffs by 10% across plans")
    b = title_tokens("Airtel hikes tariffs by 10 percent across plans")
    c = title_tokens("RBI keeps repo rate unchanged")
    assert similarity(a, b) > 0.6
    assert similarity(a, c) < 0.2


def test_parse_feed_normalises_and_skips_bad_entries() -> None:
    items = parse_feed(FEED, "test", "news")
    assert len(items) == 3  # empty-title entry skipped
    first = items[0]
    assert first.body == "Airtel announces tariff hike effective next week."
    assert first.published_at is not None and first.published_at.hour == 10  # 04:30 UTC -> IST


def test_store_dedups_and_clusters(engine: Engine) -> None:
    items = parse_feed(FEED, "test", "news")
    assert store_items(items, engine) == 3
    assert store_items(items, engine) == 0  # idempotent
    with session_scope(engine) as s:
        assert len(s.scalars(select(NewsItem)).all()) == 3
        clusters = s.scalars(select(NewsCluster).order_by(NewsCluster.id)).all()
        assert [c.item_count for c in clusters] == [2, 1]  # the two Airtel stories grouped


def test_store_dedups_same_url_with_tracking(engine: Engine) -> None:
    a = RawNews(
        source="s",
        kind="news",
        title="Big order win for Larsen company",
        body=None,
        url="https://x.com/p?utm_campaign=1",
        published_at=None,
    )
    b = a.model_copy(update={"url": "https://x.com/p"})
    assert store_items([a, b], engine) == 1
