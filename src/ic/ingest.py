from __future__ import annotations

import calendar
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time
from typing import Protocol

import feedparser
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ic.db import (
    Database,
    ItemRevisionRow,
    ItemRow,
    ProvenanceRow,
    RawDocumentRow,
    ScoreRow,
    SourceEndpointRow,
)
from ic.domain import EndpointKind, FetchedDocument, SourceEndpoint


class FetchAdapter(Protocol):
    async def fetch(self, endpoint: SourceEndpoint) -> list[FetchedDocument]: ...


class IngestResult(BaseModel):
    run_id: str
    endpoint_id: str
    started_at: datetime
    completed_at: datetime
    checkpoint_before: dict[str, object]
    checkpoint_after: dict[str, object]
    new_item_count: int = 0
    new_revision_count: int = 0
    duplicate_count: int = 0
    quarantined_count: int = 0
    item_refs: list[str] = Field(default_factory=list)
    raw_document_refs: list[str] = Field(default_factory=list)
    health: str
    errors: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ParsedItem:
    identity_key: str
    title: str
    authors: list[str]
    published_at: datetime | None
    canonical_url: str | None
    language: str
    excerpt: str | None
    body: str | None

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            {
                "authors": self.authors,
                "body": self.body,
                "canonical_url": self.canonical_url,
                "excerpt": self.excerpt,
                "language": self.language,
                "published_at": self.published_at.isoformat() if self.published_at else None,
                "title": self.title,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class Ingest:
    def __init__(self, database: Database, adapters: dict[EndpointKind, FetchAdapter]) -> None:
        self._database = database
        self._adapters = adapters

    async def ingest(self, endpoint_id: str) -> IngestResult:
        started_at = datetime.now(UTC)
        with self._database.session() as session:
            endpoint_row = session.get(SourceEndpointRow, endpoint_id)
            if endpoint_row is None:
                raise KeyError(f"unknown endpoint: {endpoint_id}")
            endpoint = endpoint_row.as_domain()

        adapter = self._adapters.get(endpoint.kind)
        if adapter is None:
            raise KeyError(f"no fetch adapter for endpoint kind: {endpoint.kind}")
        try:
            documents = await adapter.fetch(endpoint)
        except Exception as exc:
            return IngestResult(
                run_id=str(uuid.uuid4()),
                endpoint_id=endpoint.id,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                checkpoint_before=endpoint.checkpoint,
                checkpoint_after=endpoint.checkpoint,
                health="failed",
                errors=[f"{type(exc).__name__}: {exc}"],
            )

        result = IngestResult(
            run_id=str(uuid.uuid4()),
            endpoint_id=endpoint.id,
            started_at=started_at,
            completed_at=started_at,
            checkpoint_before=endpoint.checkpoint,
            checkpoint_after=endpoint.checkpoint,
            health="success",
        )
        with self._database.session() as session, session.begin():
            endpoint_row = session.get(SourceEndpointRow, endpoint.id)
            assert endpoint_row is not None
            for document in documents:
                raw = self._save_raw_document(session, endpoint, document)
                result.raw_document_refs.append(raw.id)
                if document.role != "content":
                    continue
                try:
                    parsed_items = self._parse(endpoint, document)
                except Exception as exc:
                    raw.availability = "quarantined"
                    raw.parse_error = f"{type(exc).__name__}: {exc}"
                    result.quarantined_count += 1
                    result.errors.append(raw.parse_error)
                    continue
                for parsed in parsed_items:
                    item_id, outcome = self._save_item(
                        session, endpoint, raw.id, document.observed_at, parsed
                    )
                    if item_id not in result.item_refs:
                        result.item_refs.append(item_id)
                    if outcome == "new":
                        result.new_item_count += 1
                    elif outcome == "revision":
                        result.new_revision_count += 1
                    else:
                        result.duplicate_count += 1

            if documents and not result.errors:
                observed_at = max(document.observed_at for document in documents)
                endpoint_row.checkpoint = {"observed_at": observed_at.isoformat()}
                result.checkpoint_after = dict(endpoint_row.checkpoint)
            result.health = "degraded" if result.errors else "success"
            result.completed_at = datetime.now(UTC)
        return result

    @staticmethod
    def _save_raw_document(
        session: Session, endpoint: SourceEndpoint, document: FetchedDocument
    ) -> RawDocumentRow:
        payload_hash = hashlib.sha256(document.payload).hexdigest()
        existing = session.scalar(
            select(RawDocumentRow).where(
                RawDocumentRow.endpoint_id == endpoint.id,
                RawDocumentRow.payload_hash == payload_hash,
            )
        )
        if existing is not None:
            return existing
        raw = RawDocumentRow(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{endpoint.id}:{payload_hash}")),
            endpoint_id=endpoint.id,
            observed_at=document.observed_at,
            upstream_id=document.upstream_id,
            source_url=document.source_url,
            media_type=document.media_type,
            payload=document.payload,
            payload_hash=payload_hash,
            byte_size=len(document.payload),
            role=document.role,
            policy_version=endpoint.policy_version,
        )
        session.add(raw)
        return raw

    @staticmethod
    def _parse(endpoint: SourceEndpoint, document: FetchedDocument) -> list[ParsedItem]:
        if endpoint.kind == EndpointKind.RSS:
            return Ingest._parse_rss(document.payload)
        if endpoint.kind == EndpointKind.HORIZON_HACKER_NEWS:
            parsed = Ingest._parse_hacker_news(document.payload, endpoint)
            return [parsed] if parsed is not None else []
        raise ValueError(f"unsupported endpoint kind: {endpoint.kind}")

    @staticmethod
    def _parse_rss(payload: bytes) -> list[ParsedItem]:
        feed = feedparser.parse(payload)
        if not feed.entries:
            raise ValueError("RSS document contains no entries")
        parsed: list[ParsedItem] = []
        for entry in feed.entries:
            identity_key = str(entry.get("id") or entry.get("link") or "").strip()
            title = str(entry.get("title") or "").strip()
            if not identity_key or not title:
                continue
            body = str(
                entry.get("content", [{}])[0].get("value")
                if entry.get("content")
                else entry.get("summary") or ""
            ).strip()
            authors = [str(author.get("name")) for author in entry.get("authors", [])]
            published = Ingest._feed_datetime(entry.get("published_parsed"))
            excerpt = " ".join(body.split())[:500] or None
            parsed.append(
                ParsedItem(
                    identity_key=identity_key,
                    title=title,
                    authors=authors,
                    published_at=published,
                    canonical_url=str(entry.get("link") or "").strip() or None,
                    language=str(feed.feed.get("language") or "en"),
                    excerpt=excerpt,
                    body=body or None,
                )
            )
        if not parsed:
            raise ValueError("RSS document contains no usable entries")
        return parsed

    @staticmethod
    def _parse_hacker_news(payload: bytes, endpoint: SourceEndpoint) -> ParsedItem | None:
        story = json.loads(payload)
        if not isinstance(story, dict) or not story.get("id") or not story.get("title"):
            raise ValueError("invalid Hacker News story")
        min_score = int(endpoint.config.get("min_score", 0))
        if int(story.get("score", 0)) < min_score:
            return None
        story_id = str(story["id"])
        body = str(story.get("text") or "").strip() or None
        return ParsedItem(
            identity_key=story_id,
            title=str(story["title"]).strip(),
            authors=[str(story.get("by") or "unknown")],
            published_at=datetime.fromtimestamp(int(story["time"]), tz=UTC),
            canonical_url=str(
                story.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            ),
            language="en",
            excerpt=" ".join((body or "").split())[:500] or None,
            body=body,
        )

    @staticmethod
    def _feed_datetime(value: struct_time | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)

    @staticmethod
    def _save_item(
        session: Session,
        endpoint: SourceEndpoint,
        raw_document_id: str,
        discovered_at: datetime,
        parsed: ParsedItem,
    ) -> tuple[str, str]:
        item = session.scalar(
            select(ItemRow).where(
                ItemRow.source_id == endpoint.source_id,
                ItemRow.identity_key == parsed.identity_key,
            )
        )
        if item is None:
            item_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL, f"ic:item:{endpoint.source_id}:{parsed.identity_key}"
                )
            )
            item = ItemRow(
                id=item_id,
                source_id=endpoint.source_id,
                identity_key=parsed.identity_key,
                current_revision=1,
                current_content_hash=parsed.content_hash,
            )
            session.add(item)
            revision = 1
            outcome = "new"
        elif item.current_content_hash == parsed.content_hash:
            revision = item.current_revision
            outcome = "duplicate"
        else:
            previous_content_hash = item.current_content_hash
            item.current_revision += 1
            item.current_content_hash = parsed.content_hash
            revision = item.current_revision
            outcome = "revision"
            session.execute(
                update(ScoreRow)
                .where(
                    ScoreRow.item_id == item.id,
                    ScoreRow.item_content_hash == previous_content_hash,
                    ScoreRow.status != "stale",
                )
                .values(status="stale")
            )

        if outcome != "duplicate":
            session.add(
                ItemRevisionRow(
                    item_id=item.id,
                    revision=revision,
                    content_hash=parsed.content_hash,
                    title=parsed.title,
                    authors=parsed.authors,
                    published_at=parsed.published_at,
                    discovered_at=discovered_at,
                    canonical_url=parsed.canonical_url,
                    language=parsed.language,
                    excerpt=parsed.excerpt,
                    body=parsed.body,
                )
            )
        provenance = session.get(ProvenanceRow, (item.id, parsed.content_hash, raw_document_id))
        if provenance is None:
            session.add(
                ProvenanceRow(
                    item_id=item.id,
                    content_hash=parsed.content_hash,
                    raw_document_id=raw_document_id,
                )
            )
        return item.id, outcome
