from datetime import UTC, datetime

from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.ingest import Ingest
from ic.read import ReadIntelligence
from ic.select import Select, SelectionScope


class OneItemFeed:
    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        return [
            FetchedDocument(
                observed_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
                media_type="application/rss+xml",
                payload=b"""<rss><channel><item><guid>post-1</guid>
                <title>Agent release with measured results</title>
                <link>https://example.test/post-1</link>
                <description>Benchmarks and migration details.</description>
                </item></channel></rss>""",
                source_url=endpoint.locator,
            )
        ]


async def test_selection_replays_a_persisted_snapshot_without_duplicate_digest() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingest = Ingest(database, {EndpointKind.RSS: OneItemFeed()})
    await ingest.ingest("openai-news-rss")
    select = Select(database)

    snapshot = select.create_candidate_snapshot(
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 19, tzinfo=UTC),
            period_end=datetime(2026, 8, 20, tzinfo=UTC),
            timezone="Asia/Shanghai",
            limit=20,
        )
    )
    first = select.select(snapshot.id, "tracer-policy-v1")
    replay = select.select(snapshot.id, "tracer-policy-v1")

    assert snapshot.item_count == 1
    assert first.id == replay.id
    assert first.reused is False
    assert replay.reused is True
    assert len(first.entries) == 1
    assert first.entries[0].item_id == snapshot.item_refs[0].item_id
    assert first.entries[0].item_content_hash == snapshot.item_refs[0].content_hash
    assert first.entries[0].summary == "Benchmarks and migration details."


async def test_item_revision_marks_old_score_stale_without_changing_digest() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    adapter = OneItemFeed()
    ingest = Ingest(database, {EndpointKind.RSS: adapter})
    await ingest.ingest("openai-news-rss")
    selector = Select(database)
    snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 19, tzinfo=UTC),
            period_end=datetime(2026, 8, 20, tzinfo=UTC),
            timezone="Asia/Shanghai",
        )
    )
    original = selector.select(snapshot.id, "tracer-policy-v1")
    original_entry = original.entries[0]
    adapter.fetch = changed_feed  # type: ignore[method-assign]

    await ingest.ingest("openai-news-rss")
    persisted = ReadIntelligence(database).get_digest(original.id)

    assert persisted.entries[0].item_content_hash == original_entry.item_content_hash
    assert persisted.entries[0].summary == original_entry.summary
    assert persisted.entries[0].score_status == "stale"


async def changed_feed(endpoint: SourceEndpoint) -> list[FetchedDocument]:
    return [
        FetchedDocument(
            observed_at=datetime(2026, 8, 19, 11, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>post-1</guid>
            <title>Corrected agent release</title>
            <link>https://example.test/post-1</link>
            <description>Corrected evidence.</description>
            </item></channel></rss>""",
            source_url=endpoint.locator,
        )
    ]
