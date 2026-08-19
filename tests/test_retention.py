from datetime import UTC, datetime

from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.ingest import Ingest
from ic.maintenance import Retention
from ic.read import ReadIntelligence
from ic.select import Select, SelectionScope


class Feed:
    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        return [
            FetchedDocument(
                observed_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
                media_type="application/rss+xml",
                payload=b"""<rss><channel><item><guid>one</guid><title>One</title>
                <link>https://example.test/one</link><description>Full evidence.</description>
                </item></channel></rss>""",
                source_url=endpoint.locator,
            )
        ]


async def test_reselection_after_fulltext_retention_is_explicitly_degraded() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    await Ingest(database, {EndpointKind.RSS: Feed()}).ingest("openai-news-rss")
    selector = Select(database)
    scope = SelectionScope(
        kind="daily",
        period_start=datetime(2026, 8, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 2, tzinfo=UTC),
        timezone="Asia/Shanghai",
    )
    snapshot = selector.create_candidate_snapshot(scope)
    complete = selector.select(snapshot.id, "tracer-policy-v1")

    expired = Retention(database).expire_fulltext(datetime(2026, 8, 9, tzinfo=UTC))
    degraded = selector.select(snapshot.id, "tracer-policy-v2")
    item = ReadIntelligence(database).get_item(snapshot.item_refs[0].item_id)

    assert expired.raw_document_count == 1
    assert expired.item_revision_count == 1
    assert complete.entries[0].score_status == "active"
    assert degraded.entries[0].score_status == "degraded"
    assert item.raw_payload_available is False
    assert item.excerpt == "Full evidence."
