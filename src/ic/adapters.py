from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from ic.domain import FetchedDocument, FetchFailure, PartialFetchError, SourceEndpoint


def conditional_request_headers(checkpoint: dict[str, object]) -> dict[str, str]:
    headers: dict[str, str] = {}
    if isinstance(etag := checkpoint.get("etag"), str):
        headers["If-None-Match"] = etag
    if isinstance(last_modified := checkpoint.get("last_modified"), str):
        headers["If-Modified-Since"] = last_modified
    return headers


def response_checkpoint(response: httpx.Response) -> dict[str, str]:
    checkpoint: dict[str, str] = {}
    if etag := response.headers.get("etag"):
        checkpoint["etag"] = etag
    if last_modified := response.headers.get("last-modified"):
        checkpoint["last_modified"] = last_modified
    return checkpoint


def fetch_failure_from_exception(
    error: BaseException,
    *,
    upstream_id: str | None = None,
) -> FetchFailure:
    retryable = isinstance(error, httpx.TransportError)
    retry_at = None
    observed_at = datetime.now(UTC)
    if isinstance(error, httpx.HTTPStatusError):
        retryable = error.response.status_code == 429 or error.response.status_code >= 500
        retry_after = error.response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            retry_at = observed_at + timedelta(seconds=int(retry_after))
        elif retry_after:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                else:
                    retry_at = retry_at.astimezone(UTC)
            except (TypeError, ValueError):
                retry_at = None
    if retryable and retry_at is None:
        retry_at = observed_at + timedelta(seconds=60)
    return FetchFailure(
        error_type=type(error).__name__,
        message=str(error),
        retryable=retryable,
        upstream_id=upstream_id,
        retry_at=retry_at,
    )


class NativeRssFetchAdapter:
    """Fetch an RSS/Atom response without flattening or interpreting it."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, endpoint: SourceEndpoint) -> AsyncIterator[FetchedDocument]:
        response = await self._client.get(
            endpoint.locator,
            follow_redirects=True,
            headers=conditional_request_headers(endpoint.checkpoint),
        )
        if response.status_code == 304:
            return
        response.raise_for_status()
        media_type = response.headers.get("content-type", "application/xml").split(";", 1)[0]
        yield FetchedDocument(
            observed_at=datetime.now(UTC),
            media_type=media_type,
            payload=response.content,
            source_url=str(response.url),
            checkpoint=response_checkpoint(response),
        )


class HorizonHackerNewsFetchAdapter:
    """Preserve raw HN API responses using Horizon's low-level access pattern.

    The acquisition sequence is derived from Thysrael/Horizon at commit
    c33d73330fc956b09ba5a817660e3d23cf4f132c (MIT). Unlike Horizon's public
    scraper result, this adapter deliberately stops before ``ContentItem`` so
    IC retains the original JSON and provenance.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, endpoint: SourceEndpoint) -> AsyncIterator[FetchedDocument]:
        observed_at = datetime.now(UTC)
        base_url = endpoint.locator.rstrip("/")
        headers = conditional_request_headers(endpoint.checkpoint)
        headers["X-Firebase-ETag"] = "true"
        top_response = await self._client.get(
            f"{base_url}/topstories.json",
            headers=headers,
        )
        if top_response.status_code == 304:
            return
        top_response.raise_for_status()
        story_ids = json.loads(top_response.content)
        if not isinstance(story_ids, list) or not all(
            isinstance(value, int) for value in story_ids
        ):
            raise ValueError("Hacker News topstories response must be a list of integer IDs")

        fetch_count = max(1, min(int(endpoint.config.get("fetch_top_stories", 30)), 100))
        max_concurrency = max(1, min(int(endpoint.config.get("max_concurrency", 8)), 16))
        selected_ids = story_ids[:fetch_count]
        semaphore = asyncio.Semaphore(max_concurrency)

        async def fetch_story(story_id: int) -> FetchedDocument:
            async with semaphore:
                response = await self._client.get(f"{base_url}/item/{story_id}.json")
            response.raise_for_status()
            return FetchedDocument(
                observed_at=observed_at,
                media_type="application/json",
                payload=response.content,
                source_url=f"https://news.ycombinator.com/item?id={story_id}",
                upstream_id=str(story_id),
            )

        yield FetchedDocument(
            observed_at=observed_at,
            media_type="application/json",
            payload=top_response.content,
            source_url=f"{base_url}/topstories.json",
            role="discovery",
            checkpoint=response_checkpoint(top_response),
        )
        results = await asyncio.gather(
            *(fetch_story(story_id) for story_id in selected_ids), return_exceptions=True
        )
        failures: list[FetchFailure] = []
        for story_id, result in zip(selected_ids, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(self._failure(story_id, result))
            else:
                yield result
        if failures:
            raise PartialFetchError(failures)

    @staticmethod
    def _failure(story_id: int, error: BaseException) -> FetchFailure:
        return fetch_failure_from_exception(error, upstream_id=str(story_id))
