import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from ic.calibration import Calibration
from ic.db import (
    CandidateSnapshotRow,
    Database,
    DigestRow,
    ItemRevisionRow,
    PolicyProposalRow,
    ScoreRow,
    SourcePolicyRow,
)
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.feedback import Feedback, FeedbackRequest
from ic.ingest import Ingest
from ic.select import Select, SelectionScope


class OpenAiFeed:
    def __init__(self, observed_at: datetime) -> None:
        self._observed_at = observed_at

    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=self._observed_at,
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>openai-contract-item</guid>
            <title>OpenAI contract item</title>
            <link>https://example.test/openai-contract-item</link>
            <description>Evidence for selection contract tests.</description>
            </item></channel></rss>""",
            source_url=endpoint.locator,
        )


class HackerNewsFeed:
    def __init__(self, observed_at: datetime) -> None:
        self._observed_at = observed_at

    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=self._observed_at,
            media_type="application/json",
            payload=(
                b'{"id":9001,"title":"HN contract item","score":1,'
                b'"time":1787100000,"url":"https://example.test/hn-contract-item"}'
            ),
            source_url="https://news.ycombinator.com/item?id=9001",
            upstream_id="9001",
        )


class TwoItemFeed:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 19, 9, 15, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel>
            <item><guid>first</guid><title>First</title>
            <link>https://example.test/first</link><description>First evidence.</description></item>
            <item><guid>second</guid><title>Second</title>
            <link>https://example.test/second</link>
            <description>Second evidence.</description></item>
            </channel></rss>""",
            source_url=endpoint.locator,
        )


def database() -> Database:
    result = Database.for_tests()
    result.create_schema()
    result.seed_defaults()
    return result


def stream_scope(*, source_id: str, end_hour: int) -> SelectionScope:
    return SelectionScope(
        kind="stream",
        period_start=datetime(2026, 8, 19, 9, tzinfo=UTC),
        period_end=datetime(2026, 8, 19, end_hour, tzinfo=UTC),
        timezone="UTC",
        source_ids=[source_id],
    )


async def test_replay_is_not_published_and_does_not_advance_stream_watermark() -> None:
    db = database()
    await Ingest(
        db, {EndpointKind.RSS: OpenAiFeed(datetime(2026, 8, 19, 9, 15, tzinfo=UTC))}
    ).ingest("openai-news-rss")
    selector = Select(db, clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC))
    first = selector.create_candidate_snapshot(stream_scope(source_id="openai", end_hour=10))

    replay = selector.select(first.id, "tracer-policy-v2", purpose="replay")
    second = selector.create_candidate_snapshot(stream_scope(source_id="openai", end_hour=11))

    assert replay.status == "replay"
    assert replay.published_at is None
    assert second.item_count == 1


async def test_publish_requires_activation_but_tracer_v1_is_explicitly_bootstrapped() -> None:
    db = database()
    await Ingest(
        db, {EndpointKind.RSS: OpenAiFeed(datetime(2026, 8, 19, 9, 15, tzinfo=UTC))}
    ).ingest("openai-news-rss")
    selector = Select(db, clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC))
    snapshot = selector.create_candidate_snapshot(stream_scope(source_id="openai", end_hour=10))

    with pytest.raises(ValueError, match="not active"):
        selector.select(snapshot.id, "tracer-policy-v2", purpose="publish")

    bootstrap = selector.select(snapshot.id, "tracer-policy-v1", purpose="publish")
    assert bootstrap.status == "published"
    assert bootstrap.published_at is not None
    entry = bootstrap.entries[0]
    Feedback(db).record_feedback(
        FeedbackRequest(
            actor_id="ray",
            target_kind="digest_entry",
            target_id=entry.id,
            digest_id=bootstrap.id,
            label="useful",
            idempotency_key="selection-contract:useful",
        )
    )

    calibration = Calibration(db, selector)
    proposal = calibration.propose(
        base_policy_version="tracer-policy-v1",
        candidate_policy_version="tracer-policy-v2",
        proposed_by="ray",
    )
    replayed = calibration.replay(proposal.id, snapshot.id)
    calibration.activate(replayed.id, activated_by="ray")
    published = selector.select(snapshot.id, "tracer-policy-v2", purpose="publish")

    assert replayed.replay_digest_id != published.id
    assert published.status == "published"
    assert published.published_at is not None

    with db.session() as session:
        snapshot_row = session.get(CandidateSnapshotRow, snapshot.id)
        bootstrap_row = session.get(DigestRow, bootstrap.id)
        score_row = session.get(ScoreRow, entry.score_id)
        proposal_row = session.get(PolicyProposalRow, proposal.id)
        assert snapshot_row is not None
        assert bootstrap_row is not None
        assert score_row is not None
        assert proposal_row is not None
        revision = session.scalar(
            select(ItemRevisionRow).where(
                ItemRevisionRow.item_id == entry.item_id,
                ItemRevisionRow.content_hash == entry.item_content_hash,
            )
        )
        assert revision is not None
        score_input = json.dumps(
            {
                "body": revision.body,
                "excerpt": revision.excerpt,
                "input_availability": "full",
                "title": revision.title,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        assert snapshot_row.policy_version.startswith("source-policy-set:")
        assert snapshot_row.input_ids == [f"item:{entry.item_id}:{entry.item_content_hash}"]
        assert bootstrap_row.input_ids == [f"snapshot:{snapshot.id}", f"score:{entry.score_id}"]
        assert score_row.input_hash == hashlib.sha256(score_input.encode()).hexdigest()
        assert score_row.input_hash != score_row.item_content_hash
        assert proposal_row.policy_version == "tracer-policy-v1"
        assert len(proposal_row.input_ids) == 1
        assert proposal_row.input_ids[0].startswith("feedback:")


def test_daily_snapshot_cannot_close_before_period_end() -> None:
    selector = Select(database(), clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC))
    scope = SelectionScope(
        kind="daily",
        period_start=datetime(2026, 8, 18, 16, tzinfo=UTC),
        period_end=datetime(2026, 8, 19, 16, tzinfo=UTC),
        timezone="Asia/Shanghai",
    )

    with pytest.raises(ValueError, match="period has not ended"):
        selector.create_candidate_snapshot(scope)


async def test_selection_enforces_process_and_team_share_source_policy_actions() -> None:
    db = database()
    await Ingest(
        db, {EndpointKind.RSS: OpenAiFeed(datetime(2026, 8, 19, 9, 15, tzinfo=UTC))}
    ).ingest("openai-news-rss")
    with db.session() as session, session.begin():
        policy = session.get(SourcePolicyRow, "openai:internal-v1")
        assert policy is not None
        policy.permitted_actions = ["fetch", "process"]
    selector = Select(db, clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC))

    snapshot = selector.create_candidate_snapshot(stream_scope(source_id="openai", end_hour=10))

    assert snapshot.item_count == 0


async def test_stream_watermarks_are_isolated_by_stable_source_and_language_filters() -> None:
    db = database()
    observed_at = datetime(2026, 8, 19, 9, 15, tzinfo=UTC)
    ingest = Ingest(
        db,
        {
            EndpointKind.RSS: OpenAiFeed(observed_at),
            EndpointKind.HORIZON_HACKER_NEWS: HackerNewsFeed(observed_at),
        },
    )
    await ingest.ingest("openai-news-rss")
    await ingest.ingest("horizon-hacker-news")
    selector = Select(db, clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC))
    openai = selector.create_candidate_snapshot(stream_scope(source_id="openai", end_hour=10))
    selector.select(openai.id, "tracer-policy-v1", purpose="publish")

    hacker_news = selector.create_candidate_snapshot(
        SelectionScope(
            kind="stream",
            period_start=datetime(2026, 8, 19, 9, tzinfo=UTC),
            period_end=datetime(2026, 8, 19, 11, tzinfo=UTC),
            timezone="UTC",
            source_ids=["hacker-news"],
            languages=["en"],
        )
    )

    assert hacker_news.item_count == 1


async def test_stream_snapshot_refuses_to_advance_past_candidates_over_its_limit() -> None:
    db = database()
    await Ingest(db, {EndpointKind.RSS: TwoItemFeed()}).ingest("openai-news-rss")
    selector = Select(db, clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC))
    scope = stream_scope(source_id="openai", end_hour=10).model_copy(update={"limit": 1})

    with pytest.raises(ValueError, match="candidate count exceeds limit"):
        selector.create_candidate_snapshot(scope)
