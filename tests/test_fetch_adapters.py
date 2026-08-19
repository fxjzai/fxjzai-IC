from datetime import UTC, datetime

import httpx

from ic.adapters import HorizonHackerNewsFetchAdapter, NativeRssFetchAdapter
from ic.domain import EndpointKind, SourceEndpoint


async def test_native_rss_preserves_the_original_feed_as_one_raw_document() -> None:
    feed = b"""<?xml version="1.0"?><rss><channel><title>OpenAI</title></channel></rss>"""

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/feed.xml"
        return httpx.Response(200, content=feed, headers={"content-type": "application/rss+xml"})

    endpoint = SourceEndpoint(
        id="endpoint-openai",
        source_id="source-openai",
        kind=EndpointKind.RSS,
        locator="https://example.test/feed.xml",
        policy_version="internal-v1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        documents = await NativeRssFetchAdapter(client).fetch(endpoint)

    assert len(documents) == 1
    assert documents[0].payload == feed
    assert documents[0].media_type == "application/rss+xml"
    assert documents[0].source_url == "https://example.test/feed.xml"
    assert documents[0].observed_at.tzinfo is UTC
    assert documents[0].observed_at <= datetime.now(UTC)


async def test_horizon_hacker_news_adapter_preserves_discovery_and_story_json() -> None:
    responses = {
        "/v0/topstories.json": b"[101,102]",
        "/v0/item/101.json": b'{"id":101,"title":"First","score":120,"time":1787100000}',
        "/v0/item/102.json": b'{"id":102,"title":"Second","score":80,"time":1787100100}',
    }

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses[request.url.path])

    endpoint = SourceEndpoint(
        id="endpoint-hn",
        source_id="source-hn",
        kind=EndpointKind.HORIZON_HACKER_NEWS,
        locator="https://hacker-news.firebaseio.com/v0",
        policy_version="internal-v1",
        config={"fetch_top_stories": 2},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        documents = await HorizonHackerNewsFetchAdapter(client).fetch(endpoint)

    assert [(document.role, document.upstream_id) for document in documents] == [
        ("discovery", None),
        ("content", "101"),
        ("content", "102"),
    ]
    assert documents[0].payload == b"[101,102]"
    assert documents[1].payload == responses["/v0/item/101.json"]
    assert documents[1].source_url == "https://news.ycombinator.com/item?id=101"
