from datetime import UTC, datetime

from ic.calibration import Calibration
from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.feedback import Feedback, FeedbackRequest
from ic.ingest import Ingest
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


async def test_feedback_is_idempotent_and_freezes_selection_context() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    await Ingest(database, {EndpointKind.RSS: Feed()}).ingest("openai-news-rss")
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
    entry = digest.entries[0]
    feedback = Feedback(database)
    request = FeedbackRequest(
        actor_id="ray",
        target_kind="digest_entry",
        target_id=entry.id,
        digest_id=digest.id,
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


async def test_feedback_can_target_an_item_revision_directly() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = await Ingest(database, {EndpointKind.RSS: Feed()}).ingest("openai-news-rss")
    receipt = Feedback(database).record_feedback(
        FeedbackRequest(
            actor_id="ray",
            target_kind="item",
            target_id=ingested.item_refs[0],
            label="misclassified",
            idempotency_key="ray:item-one:misclassified",
        )
    )

    assert receipt.target_kind == "item"
    assert receipt.target_id == ingested.item_refs[0]
    assert receipt.item_id == ingested.item_refs[0]
    assert receipt.item_content_hash
    assert receipt.digest_id is None


async def test_feedback_summary_replay_and_manual_policy_activation() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    await Ingest(database, {EndpointKind.RSS: Feed()}).ingest("openai-news-rss")
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
    entry = digest.entries[0]
    Feedback(database).record_feedback(
        FeedbackRequest(
            actor_id="ray",
            target_kind="digest_entry",
            target_id=entry.id,
            digest_id=digest.id,
            label="useful",
            idempotency_key="calibration:useful",
        )
    )
    calibration = Calibration(database, selector)

    summary = calibration.summarize("tracer-policy-v1")
    proposal = calibration.propose(
        base_policy_version="tracer-policy-v1",
        candidate_policy_version="tracer-policy-v2",
        proposed_by="ray",
    )
    replayed = calibration.replay(proposal.id, snapshot.id)
    activated = calibration.activate(replayed.id, activated_by="ray")

    assert summary.counts == {"useful": 1, "useless": 0, "misclassified": 0}
    assert replayed.status == "replayed"
    assert replayed.replay_digest_id
    assert activated.policy_version == "tracer-policy-v2"
    assert activated.activated_by == "ray"
