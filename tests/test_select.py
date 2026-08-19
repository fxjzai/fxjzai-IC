from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.ingest import Ingest
from ic.read import ReadIntelligence
from ic.select import Select, SelectionScope


class OneItemFeed:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>post-1</guid>
                <title>Agent release with measured results</title>
                <link>https://example.test/post-1</link>
                <description>Benchmarks and migration details.</description>
                </item></channel></rss>""",
            source_url=endpoint.locator,
        )


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
            period_start=datetime(2026, 8, 18, 16, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, 16, tzinfo=UTC),
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
            period_start=datetime(2026, 8, 18, 16, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, 16, tzinfo=UTC),
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


async def changed_feed(endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
    yield FetchedDocument(
        observed_at=datetime(2026, 8, 19, 11, tzinfo=UTC),
        media_type="application/rss+xml",
        payload=b"""<rss><channel><item><guid>post-1</guid>
            <title>Corrected agent release</title>
            <link>https://example.test/post-1</link>
            <description>Corrected evidence.</description>
            </item></channel></rss>""",
        source_url=endpoint.locator,
    )


def test_daily_scope_must_use_midnight_in_its_declared_timezone() -> None:
    with pytest.raises(ValidationError, match="local midnight"):
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 19, tzinfo=UTC),
            period_end=datetime(2026, 8, 20, tzinfo=UTC),
            timezone="Asia/Shanghai",
        )


async def test_stream_snapshot_uses_last_published_watermark() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    adapter = OneItemFeed()
    ingest = Ingest(database, {EndpointKind.RSS: adapter})
    first_ingest = await ingest.ingest("openai-news-rss")
    selector = Select(database)
    first_snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="stream",
            period_start=datetime(2026, 8, 19, 9, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, 10, 30, tzinfo=UTC),
            timezone="UTC",
        )
    )
    selector.select(first_snapshot.id, "tracer-policy-v1")
    adapter.fetch = second_item_feed  # type: ignore[method-assign]
    second_ingest = await ingest.ingest("openai-news-rss")

    second_snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="stream",
            period_start=datetime(2026, 8, 1, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, 12, tzinfo=UTC),
            timezone="UTC",
        )
    )

    assert first_snapshot.item_refs[0].item_id == first_ingest.item_refs[0]
    assert second_snapshot.item_count == 1
    assert second_snapshot.item_refs[0].item_id == second_ingest.item_refs[0]


async def second_item_feed(endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
    yield FetchedDocument(
        observed_at=datetime(2026, 8, 19, 11, tzinfo=UTC),
        media_type="application/rss+xml",
        payload=b"""<rss><channel><item><guid>post-2</guid><title>Second</title>
        <link>https://example.test/post-2</link><description>New evidence.</description>
        </item></channel></rss>""",
        source_url=endpoint.locator,
    )
