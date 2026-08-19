from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx

from ic.domain import FetchedDocument, SourceEndpoint


class NativeRssFetchAdapter:
    """Fetch an RSS/Atom response without flattening or interpreting it."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        response = await self._client.get(endpoint.locator, follow_redirects=True)
        response.raise_for_status()
        media_type = response.headers.get("content-type", "application/xml").split(";", 1)[0]
        return [
            FetchedDocument(
                observed_at=datetime.now(UTC),
                media_type=media_type,
                payload=response.content,
                source_url=str(response.url),
            )
        ]


class HorizonHackerNewsFetchAdapter:
    """Preserve raw HN API responses using Horizon's low-level access pattern.

    The acquisition sequence is derived from Thysrael/Horizon at commit
    c33d73330fc956b09ba5a817660e3d23cf4f132c (MIT). Unlike Horizon's public
    scraper result, this adapter deliberately stops before ``ContentItem`` so
    IC retains the original JSON and provenance.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]:
        observed_at = datetime.now(UTC)
        base_url = endpoint.locator.rstrip("/")
        top_response = await self._client.get(f"{base_url}/topstories.json")
        top_response.raise_for_status()
        story_ids = json.loads(top_response.content)
        if not isinstance(story_ids, list) or not all(
            isinstance(value, int) for value in story_ids
        ):
            raise ValueError("Hacker News topstories response must be a list of integer IDs")

        fetch_count = max(1, min(int(endpoint.config.get("fetch_top_stories", 30)), 100))
        selected_ids = story_ids[:fetch_count]

        async def fetch_story(story_id: int) -> FetchedDocument:
            response = await self._client.get(f"{base_url}/item/{story_id}.json")
            response.raise_for_status()
            return FetchedDocument(
                observed_at=observed_at,
                media_type="application/json",
                payload=response.content,
                source_url=f"https://news.ycombinator.com/item?id={story_id}",
                upstream_id=str(story_id),
            )

        stories = await asyncio.gather(*(fetch_story(story_id) for story_id in selected_ids))
        discovery = FetchedDocument(
            observed_at=observed_at,
            media_type="application/json",
            payload=top_response.content,
            source_url=f"{base_url}/topstories.json",
            role="discovery",
        )
        return [discovery, *stories]
