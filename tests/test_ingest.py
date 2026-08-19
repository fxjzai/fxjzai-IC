from datetime import UTC, datetime

from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.ingest import Ingest


class FixedAdapter:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        return [
            FetchedDocument(
                observed_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
                media_type="application/rss+xml",
                payload=self.payload,
                source_url=endpoint.locator,
            )
        ]


async def test_replaying_an_endpoint_returns_the_same_item_without_duplication() -> None:
    feed = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>OpenAI</title><item>
      <guid>post-1</guid><title>Shipping a useful agent</title>
      <link>https://openai.com/index/agent/</link>
      <description>A concrete release with benchmarks.</description>
      <pubDate>Wed, 19 Aug 2026 01:00:00 GMT</pubDate>
    </item></channel></rss>"""
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingest = Ingest(database, {EndpointKind.RSS: FixedAdapter(feed)})

    first = await ingest.ingest("openai-news-rss")
    replay = await ingest.ingest("openai-news-rss")

    assert first.health == "success"
    assert first.new_item_count == 1
    assert first.duplicate_count == 0
    assert replay.health == "success"
    assert replay.new_item_count == 0
    assert replay.duplicate_count == 1
    assert replay.new_revision_count == 0
    assert replay.item_refs == first.item_refs
    assert replay.checkpoint_after == first.checkpoint_after


async def test_changed_content_creates_a_revision_without_changing_item_identity() -> None:
    first_feed = b"""<rss><channel><item><guid>post-1</guid><title>First title</title>
      <link>https://example.test/post-1</link><description>Original</description>
    </item></channel></rss>"""
    changed_feed = b"""<rss><channel><item><guid>post-1</guid><title>Corrected title</title>
      <link>https://example.test/post-1</link><description>Corrected</description>
    </item></channel></rss>"""
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    adapter = FixedAdapter(first_feed)
    ingest = Ingest(database, {EndpointKind.RSS: adapter})

    first = await ingest.ingest("openai-news-rss")
    adapter.payload = changed_feed
    changed = await ingest.ingest("openai-news-rss")

    assert changed.item_refs == first.item_refs
    assert changed.new_item_count == 0
    assert changed.new_revision_count == 1
    assert changed.duplicate_count == 0


class MixedHackerNewsAdapter:
    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        observed_at = datetime(2026, 8, 19, 10, tzinfo=UTC)
        return [
            FetchedDocument(
                observed_at=observed_at,
                media_type="application/json",
                payload=b'{"id":101,"title":"Useful release","score":120,"time":1787100000}',
                source_url="https://news.ycombinator.com/item?id=101",
                upstream_id="101",
            ),
            FetchedDocument(
                observed_at=observed_at,
                media_type="application/json",
                payload=b"not-json",
                source_url="https://news.ycombinator.com/item?id=102",
                upstream_id="102",
            ),
        ]


async def test_one_corrupt_hacker_news_document_does_not_rollback_valid_item() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingest = Ingest(database, {EndpointKind.HORIZON_HACKER_NEWS: MixedHackerNewsAdapter()})

    result = await ingest.ingest("horizon-hacker-news")

    assert result.health == "degraded"
    assert result.new_item_count == 1
    assert result.quarantined_count == 1
    assert len(result.item_refs) == 1
    assert len(result.raw_document_refs) == 2
    assert result.checkpoint_before == result.checkpoint_after == {}


class LowScoreHackerNewsAdapter:
    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        return [
            FetchedDocument(
                observed_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
                media_type="application/json",
                payload=b'{"id":103,"title":"Low signal","score":1,"time":1787100000}',
                source_url="https://news.ycombinator.com/item?id=103",
                upstream_id="103",
            )
        ]


async def test_hacker_news_min_score_is_a_filter_not_a_quarantine_error() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingest = Ingest(database, {EndpointKind.HORIZON_HACKER_NEWS: LowScoreHackerNewsAdapter()})

    result = await ingest.ingest("horizon-hacker-news")

    assert result.health == "success"
    assert result.new_item_count == 0
    assert result.quarantined_count == 0
    assert result.errors == []
