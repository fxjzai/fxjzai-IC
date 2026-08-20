import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ic.api import create_app
from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint
from ic.ingest import Ingest
from ic.select import Select, SelectionScope


class DashboardFeed:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 20, 8, 30, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>dashboard-one</guid>
            <title>Agent release with evidence</title>
            <link>https://example.test/dashboard-one</link>
            <description>A concrete release for the team.</description>
            </item></channel></rss>""",
            source_url=endpoint.locator,
        )


class UnsafeLinkFeed:
    async def fetch(self, endpoint: SourceEndpoint):  # type: ignore[no-untyped-def]
        yield FetchedDocument(
            observed_at=datetime(2026, 8, 20, 8, 30, tzinfo=UTC),
            media_type="application/rss+xml",
            payload=b"""<rss><channel><item><guid>unsafe-link</guid>
            <title>Untrusted link</title>
            <link>javascript:alert(document.domain)</link>
            <description>Content from an untrusted feed.</description>
            </item></channel></rss>""",
            source_url="data:text/html,unsafe",
        )


def test_dashboard_shows_real_items_sources_and_navigation() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = asyncio.run(
        Ingest(database, {EndpointKind.RSS: DashboardFeed()}).ingest("openai-news-rss")
    )

    with TestClient(create_app(database=database)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "反向加载" in response.text
    assert "Agent release with evidence" in response.text
    assert "OpenAI" in response.text
    assert f"/items/{ingested.item_refs[0]}" in response.text


def test_item_page_shows_readable_evidence_and_original_link() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = asyncio.run(
        Ingest(database, {EndpointKind.RSS: DashboardFeed()}).ingest("openai-news-rss")
    )

    with TestClient(create_app(database=database)) as client:
        response = client.get(f"/items/{ingested.item_refs[0]}")

    assert response.status_code == 200
    assert "Agent release with evidence" in response.text
    assert "A concrete release for the team." in response.text
    assert "证据链" in response.text
    assert 'href="https://example.test/dashboard-one"' in response.text


def test_published_digest_is_discoverable_and_readable() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    asyncio.run(Ingest(database, {EndpointKind.RSS: DashboardFeed()}).ingest("openai-news-rss"))
    selector = Select(database, clock=lambda: datetime(2026, 8, 21, tzinfo=UTC))
    snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 19, 16, tzinfo=UTC),
            period_end=datetime(2026, 8, 20, 16, tzinfo=UTC),
            timezone="Asia/Shanghai",
        )
    )
    digest = selector.select(snapshot.id, "tracer-policy-v1")

    with TestClient(create_app(database=database)) as client:
        dashboard = client.get("/")
        detail = client.get(f"/digests/{digest.id}")

    assert dashboard.status_code == 200
    assert f"/digests/{digest.id}" in dashboard.text
    assert "每日精选" in dashboard.text
    assert detail.status_code == 200
    assert "精选结果" in detail.text
    assert "Agent release with evidence" in detail.text
    assert "Included by deterministic P0 tracer policy." in detail.text
    assert 'data-feedback-label="useful"' in detail.text
    assert 'data-feedback-label="useless"' in detail.text
    assert 'data-feedback-label="misclassified"' in detail.text
    assert f'data-digest-id="{digest.id}"' in detail.text
    assert f'data-entry-id="{digest.entries[0].id}"' in detail.text

    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert 'fetch("/v1/feedback"' in script.text


def test_untrusted_external_protocols_are_not_rendered_as_links() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    ingested = asyncio.run(
        Ingest(database, {EndpointKind.RSS: UnsafeLinkFeed()}).ingest("openai-news-rss")
    )

    with TestClient(create_app(database=database)) as client:
        dashboard = client.get("/")
        detail = client.get(f"/items/{ingested.item_refs[0]}")

    assert dashboard.status_code == 200
    assert detail.status_code == 200
    assert "javascript:" not in dashboard.text
    assert "javascript:" not in detail.text
    assert "data:text/html" not in detail.text


def test_replay_digest_is_not_exposed_as_published_content() -> None:
    database = Database.for_tests()
    database.create_schema()
    database.seed_defaults()
    asyncio.run(Ingest(database, {EndpointKind.RSS: DashboardFeed()}).ingest("openai-news-rss"))
    selector = Select(database, clock=lambda: datetime(2026, 8, 21, tzinfo=UTC))
    snapshot = selector.create_candidate_snapshot(
        SelectionScope(
            kind="daily",
            period_start=datetime(2026, 8, 19, 16, tzinfo=UTC),
            period_end=datetime(2026, 8, 20, 16, tzinfo=UTC),
            timezone="Asia/Shanghai",
        )
    )
    replay = selector.select(snapshot.id, "tracer-policy-v2", purpose="replay")

    with TestClient(create_app(database=database)) as client:
        html_response = client.get(f"/digests/{replay.id}")
        api_response = client.get(f"/v1/digests/{replay.id}")

    assert html_response.status_code == 404
    assert api_response.status_code == 404
