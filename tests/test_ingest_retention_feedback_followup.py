from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from pydantic import ValidationError

from ic.adapters import HorizonHackerNewsFetchAdapter, NativeRssFetchAdapter
from ic.db import Database, SourceEndpointRow, SourcePolicyRow
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.feedback import Feedback, FeedbackRequest
from ic.ingest import Ingest
from ic.maintenance import Retention
from ic.read import ReadIntelligence


class HistoricalFeed:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>historical</guid>
            <title>Historical policy</title><link>https://example.test/historical</link>
            <description>Temporarily retained body.</description></item></channel></rss>""",
            source_url=endpoint.locator,
        )


class RevisableFeed:
    def __init__(self) -> None:
        self.title = "First revision"

    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 1, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=(
                "<rss><channel><item><guid>revisable</guid>"
                f"<title>{self.title}</title>"
                "<link>https://example.test/revisable</link>"
                "<description>Evidence.</description></item></channel></rss>"
            ).encode(),
            source_url=endpoint.locator,
        )


class SameArticleAcrossEndpoints:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        guid = "primary-id" if endpoint.id == "openai-news-rss" else "mirror-id"
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 19, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=(
                f"<rss><channel><item><guid>{guid}</guid>"
                "<title>Canonical article</title>"
                "<link>https://example.test/canonical-article</link>"
                "<description>Shared evidence.</description></item></channel></rss>"
            ).encode(),
            source_url=endpoint.locator,
        )


async def test_ingest_preserves_retry_after_for_http_status_failures() -> None:
    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"}, request=request)

    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    before = datetime.now(UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limited)) as client:
        result = await Ingest(
            database,
            {EndpointKind.RSS: NativeRssFetchAdapter(client)},
        ).ingest("openai-news-rss")
    after = datetime.now(UTC)

    assert result.health == "failed"
    assert len(result.errors) == 1
    assert result.errors[0].retryable is True
    assert result.errors[0].retry_at is not None
    assert before + timedelta(seconds=120) <= result.errors[0].retry_at
    assert result.errors[0].retry_at <= after + timedelta(seconds=120)


async def test_retryable_failure_without_header_gets_a_bounded_default_retry_time() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    before = datetime.now(UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        result = await Ingest(
            database,
            {EndpointKind.RSS: NativeRssFetchAdapter(client)},
        ).ingest("openai-news-rss")
    after = datetime.now(UTC)

    assert result.errors[0].retryable is True
    assert result.errors[0].retry_at is not None
    assert before + timedelta(seconds=60) <= result.errors[0].retry_at
    assert result.errors[0].retry_at <= after + timedelta(seconds=60)


async def test_rss_checkpoint_uses_etag_to_avoid_refetching_unchanged_content() -> None:
    requests: list[httpx.Request] = []

    def conditional_feed(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-none-match") == '"rss-v1"':
            return httpx.Response(304, request=request)
        return httpx.Response(
            200,
            content=(
                b"<rss><channel><item><guid>etag-item</guid><title>ETag item</title>"
                b"<link>https://example.test/etag</link>"
                b"<description>Evidence.</description></item></channel></rss>"
            ),
            headers={"ETag": '"rss-v1"', "Content-Type": "application/rss+xml"},
            request=request,
        )

    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    async with httpx.AsyncClient(transport=httpx.MockTransport(conditional_feed)) as client:
        ingest = Ingest(database, {EndpointKind.RSS: NativeRssFetchAdapter(client)})
        first = await ingest.ingest("openai-news-rss")
        unchanged = await ingest.ingest("openai-news-rss")

    assert first.new_item_count == 1
    assert first.checkpoint_after["etag"] == '"rss-v1"'
    assert unchanged.health == "success"
    assert unchanged.raw_document_refs == []
    assert unchanged.checkpoint_after == first.checkpoint_after
    assert len(requests) == 2


async def test_horizon_checkpoint_uses_firebase_etag_for_incremental_fetch() -> None:
    top_requests: list[httpx.Request] = []

    def conditional_hacker_news(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/topstories.json"):
            top_requests.append(request)
            assert request.headers["x-firebase-etag"] == "true"
            if request.headers.get("if-none-match") == '"hn-v1"':
                return httpx.Response(304, request=request)
            return httpx.Response(
                200,
                json=[42],
                headers={"ETag": '"hn-v1"'},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "id": 42,
                "title": "Incremental HN story",
                "score": 1,
                "time": 1787100000,
                "url": "https://example.test/hn-incremental",
            },
            request=request,
        )

    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    async with httpx.AsyncClient(transport=httpx.MockTransport(conditional_hacker_news)) as client:
        ingest = Ingest(
            database,
            {EndpointKind.HORIZON_HACKER_NEWS: HorizonHackerNewsFetchAdapter(client)},
        )
        first = await ingest.ingest("horizon-hacker-news")
        unchanged = await ingest.ingest("horizon-hacker-news")

    assert first.new_item_count == 1
    assert first.checkpoint_after["etag"] == '"hn-v1"'
    assert unchanged.health == "success"
    assert unchanged.raw_document_refs == []
    assert unchanged.checkpoint_after == first.checkpoint_after
    assert len(top_requests) == 2


async def test_partial_fetch_preserves_http_date_retry_after() -> None:
    retry_at = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    def unavailable_story(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/topstories.json"):
            return httpx.Response(200, json=[42], request=request)
        return httpx.Response(
            503,
            headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
            request=request,
        )

    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable_story)) as client:
        result = await Ingest(
            database,
            {EndpointKind.HORIZON_HACKER_NEWS: HorizonHackerNewsFetchAdapter(client)},
        ).ingest("horizon-hacker-news")

    assert result.health == "degraded"
    assert len(result.errors) == 1
    assert result.errors[0].upstream_id == "42"
    assert result.errors[0].retry_at == retry_at


async def test_missing_historical_policy_expires_fulltext_after_seven_days() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = await Ingest(
        database,
        {EndpointKind.RSS: HistoricalFeed()},
    ).ingest("openai-news-rss")
    with database.session() as session, session.begin():
        policy = session.get(SourcePolicyRow, "openai:internal-v1")
        assert policy is not None
        session.delete(policy)

    expired = Retention(database).expire_due(datetime(2026, 8, 9, tzinfo=UTC))
    item = ReadIntelligence(database).get_item(ingested.item_refs[0])

    assert expired.raw_document_count == 1
    assert expired.item_revision_count == 1
    assert item.raw_payload_available is False
    assert item.provenance[0].availability == "tombstoned"
    assert item.provenance[0].deletion_reason == "default_retention_expired"


async def test_direct_item_feedback_freezes_the_requested_historical_revision() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    adapter = RevisableFeed()
    ingest = Ingest(database, {EndpointKind.RSS: adapter})
    first = await ingest.ingest("openai-news-rss")
    first_content_hash = first.provenance[0].item_content_hash
    adapter.title = "Second revision"
    await ingest.ingest("openai-news-rss")

    receipt = Feedback(database).record_feedback(
        FeedbackRequest(
            actor_id="ray",
            target_kind="item",
            target_id=first.item_refs[0],
            item_content_hash=first_content_hash,
            label="useful",
            idempotency_key="historical-item-revision",
        )
    )

    assert receipt.item_content_hash == first_content_hash


def test_direct_item_feedback_requires_a_content_hash() -> None:
    with pytest.raises(ValidationError, match="item_content_hash is required"):
        FeedbackRequest(
            actor_id="ray",
            target_kind="item",
            target_id="item-id",
            label="useful",
            idempotency_key="missing-item-revision",
        )


async def test_direct_item_feedback_rejects_an_unknown_revision() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = await Ingest(
        database,
        {EndpointKind.RSS: HistoricalFeed()},
    ).ingest("openai-news-rss")

    with pytest.raises(KeyError, match="Item revision does not exist"):
        Feedback(database).record_feedback(
            FeedbackRequest(
                actor_id="ray",
                target_kind="item",
                target_id=ingested.item_refs[0],
                item_content_hash="0" * 64,
                label="useful",
                idempotency_key="unknown-item-revision",
            )
        )


async def test_ingest_fails_closed_when_policy_denies_processing() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    with database.session() as session, session.begin():
        policy = session.get(SourcePolicyRow, "openai:internal-v1")
        assert policy is not None
        policy.permitted_actions = ["fetch"]

    result = await Ingest(
        database,
        {EndpointKind.RSS: HistoricalFeed()},
    ).ingest("openai-news-rss")

    assert result.health == "failed"
    assert result.raw_document_refs == []
    assert result.errors[0].error_type == "SourcePolicyDenied"
    assert "process" in result.errors[0].message


async def test_cross_endpoint_canonical_url_adds_provenance_without_a_second_item() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    with database.session() as session, session.begin():
        session.add(
            SourceEndpointRow(
                id="openai-news-rss-mirror",
                source_id="openai",
                kind=EndpointKind.RSS.value,
                locator="https://mirror.example.test/openai.xml",
                policy_version="internal-v1",
            )
        )
    ingest = Ingest(database, {EndpointKind.RSS: SameArticleAcrossEndpoints()})

    primary = await ingest.ingest("openai-news-rss")
    mirror = await ingest.ingest("openai-news-rss-mirror")
    item = ReadIntelligence(database).get_item(primary.item_refs[0])

    assert mirror.item_refs == primary.item_refs
    assert mirror.new_item_count == 0
    assert mirror.duplicate_count == 1
    assert len(item.provenance) == 2
