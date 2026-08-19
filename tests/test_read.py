from datetime import UTC, datetime

from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.ingest import Ingest
from ic.read import ReadIntelligence
from ic.select import Select, SelectionScope


class Feed:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>one</guid><title>One</title>
                <link>https://example.test/one</link><description>Evidence.</description>
                </item></channel></rss>""",
            source_url=endpoint.locator,
        )


async def test_item_view_is_traceable_and_does_not_return_fulltext() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = await Ingest(database, {EndpointKind.RSS: Feed()}).ingest("openai-news-rss")
    selector = Select(database)
    snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 18, 16, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, 16, tzinfo=UTC),
            timezone="Asia/Shanghai",
        )
    )
    digest = selector.select(snapshot.id, "tracer-policy-v1")
    reader = ReadIntelligence(database)

    item = reader.get_item(ingested.item_refs[0])
    persisted_digest = reader.get_digest(digest.id)

    assert item.source.id == "openai"
    assert item.title == "One"
    assert item.content_hash == digest.entries[0].item_content_hash
    assert item.excerpt == "Evidence."
    assert item.raw_payload_available is True
    assert item.provenance[0].source_url == "https://openai.com/news/rss.xml"
    assert "body" not in item.model_dump()
    assert persisted_digest.id == digest.id
    assert persisted_digest.entries[0].score_id == digest.entries[0].score_id
