from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ic.api import create_app
from ic.db import Database
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint


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


def test_http_tracer_flow_reaches_feedback() -> None:
    database = Database.for_tests()
    app = create_app(database=database, adapters={EndpointKind.RSS: Feed()})

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "healthy"
        ingested = client.post("/v1/ingest/openai-news-rss").json()
        snapshot = client.post(
            "/v1/snapshots",
            json={
                "kind": "daily",
                "period_start": "2026-08-19T00:00:00Z",
                "period_end": "2026-08-20T00:00:00Z",
                "timezone": "Asia/Shanghai",
                "limit": 20,
            },
        ).json()
        digest = client.post(
            "/v1/digests",
            json={"snapshot_id": snapshot["id"], "policy_version": "tracer-policy-v1"},
        ).json()
        entry = digest["entries"][0]
        receipt = client.post(
            "/v1/feedback",
            json={
                "actor_id": "ray",
                "digest_id": digest["id"],
                "digest_entry_id": entry["id"],
                "label": "useful",
                "idempotency_key": "api:ray:one:useful",
            },
        ).json()
        item = client.get(f"/v1/items/{ingested['item_refs'][0]}").json()

    assert ingested["new_item_count"] == 1
    assert snapshot["item_count"] == 1
    assert receipt["item_content_hash"] == entry["item_content_hash"]
    assert item["canonical_url"] == "https://example.test/one"
