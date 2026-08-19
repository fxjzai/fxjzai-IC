from datetime import UTC, datetime

from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.feedback import Feedback, FeedbackRequest
from ic.ingest import Ingest
from ic.select import Select, SelectionScope


class Feed:
    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        return [
            FetchedDocument(
                observed_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
                media_type="application/rss+xml",
                payload=b"""<rss><channel><item><guid>one</guid><title>One</title>
                <link>https://example.test/one</link><description>Evidence.</description>
                </item></channel></rss>""",
                source_url=endpoint.locator,
            )
        ]


async def test_feedback_is_idempotent_and_freezes_selection_context() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    await Ingest(database, {EndpointKind.RSS: Feed()}).ingest("openai-news-rss")
    selector = Select(database)
    snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 19, tzinfo=UTC),
            period_end=datetime(2026, 8, 20, tzinfo=UTC),
            timezone="Asia/Shanghai",
        )
    )
    digest = selector.select(snapshot.id, "tracer-policy-v1")
    entry = digest.entries[0]
    feedback = Feedback(database)
    request = FeedbackRequest(
        actor_id="ray",
        digest_id=digest.id,
        digest_entry_id=entry.id,
        label="useful",
        idempotency_key="ray:digest-1:useful",
    )

    first = feedback.record_feedback(request)
    replay = feedback.record_feedback(request)

    assert first.id == replay.id
    assert first.created is True
    assert replay.created is False
    assert first.item_id == entry.item_id
    assert first.item_content_hash == entry.item_content_hash
    assert first.score_id == entry.score_id
    assert first.policy_version == "tracer-policy-v1"
