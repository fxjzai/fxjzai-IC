from __future__ import annotations

import calendar
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import struct_time
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import feedparser
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ic.adapters import fetch_failure_from_exception
from ic.db import (
    Database,
    ItemRevisionRow,
    ItemRow,
    ProvenanceRow,
    RawDocumentRow,
    ScoreRow,
    SourceEndpointRow,
    SourcePolicyRow,
)
from ic.domain import EndpointKind, FetchedDocument, FetchFailure, PartialFetchError, SourceEndpoint
from ic.metrics import (
    INGEST_DOCUMENTS,
    INGEST_DURATION,
    INGEST_ERRORS,
    INGEST_ITEMS,
    INGEST_RUNS,
)

logger = logging.getLogger(__name__)


class FetchAdapter(Protocol):
    def fetch(self, endpoint: SourceEndpoint) -> AsyncIterator[FetchedDocument]: ...


class IngestError(BaseModel):
    error_type: str
    message: str
    retryable: bool = False
    upstream_id: str | None = None
    retry_at: datetime | None = None
    raw_document_id: str | None = None


class ProvenanceLink(BaseModel):
    item_id: str
    item_content_hash: str
    raw_document_id: str


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
    new_item_refs: list[str] = Field(default_factory=list)
    new_revision_refs: list[str] = Field(default_factory=list)
    duplicate_item_refs: list[str] = Field(default_factory=list)
    quarantined_raw_document_refs: list[str] = Field(default_factory=list)
    provenance: list[ProvenanceLink] = Field(default_factory=list)
    health: str
    errors: list[IngestError] = Field(default_factory=list)


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
        started_monotonic = time.monotonic()
        with self._database.session() as session:
            endpoint_row = session.get(SourceEndpointRow, endpoint_id)
            if endpoint_row is None:
                raise KeyError(f"unknown endpoint: {endpoint_id}")
            endpoint = endpoint_row.as_domain()
            policy = session.scalar(
                select(SourcePolicyRow).where(
                    SourcePolicyRow.source_id == endpoint.source_id,
                    SourcePolicyRow.version == endpoint.policy_version,
                )
            )

        if endpoint_row.status != "active":
            return self._observe(
                self._failed_before_fetch(
                    endpoint, started_at, "EndpointInactive", "SourceEndpoint is not active"
                ),
                started_monotonic,
            )
        required_actions = {"fetch", "process"}
        permitted_actions = set(policy.permitted_actions) if policy is not None else set()
        missing_actions = sorted(required_actions - permitted_actions)
        if missing_actions:
            return self._observe(
                self._failed_before_fetch(
                    endpoint,
                    started_at,
                    "SourcePolicyDenied",
                    f"SourcePolicy does not permit required actions: {', '.join(missing_actions)}",
                ),
                started_monotonic,
            )

        adapter = self._adapters.get(endpoint.kind)
        if adapter is None:
            raise KeyError(f"no fetch adapter for endpoint kind: {endpoint.kind}")
        documents: list[FetchedDocument] = []
        fetch_failures: list[FetchFailure] = []
        with self._database.try_session_lock(f"endpoint-fetch:{endpoint.id}") as acquired:
            if not acquired:
                return self._observe(
                    self._failed_before_fetch(
                        endpoint,
                        started_at,
                        "EndpointBusy",
                        "another worker is already fetching this SourceEndpoint",
                        retryable=True,
                        retry_at=datetime.now(UTC) + timedelta(seconds=60),
                    ),
                    started_monotonic,
                )
            try:
                async for document in adapter.fetch(endpoint):
                    documents.append(document)
            except PartialFetchError as exc:
                fetch_failures.extend(exc.failures)
            except Exception as exc:
                fetch_failures.append(self._fetch_failure(exc))
            return self._persist_fetch_result(
                endpoint,
                documents,
                fetch_failures,
                started_at,
                started_monotonic,
            )

    def _persist_fetch_result(
        self,
        endpoint: SourceEndpoint,
        documents: list[FetchedDocument],
        fetch_failures: list[FetchFailure],
        started_at: datetime,
        started_monotonic: float,
    ) -> IngestResult:
        if not documents and fetch_failures:
            return self._observe(
                IngestResult(
                    run_id=str(uuid.uuid4()),
                    endpoint_id=endpoint.id,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    checkpoint_before=endpoint.checkpoint,
                    checkpoint_after=endpoint.checkpoint,
                    health="failed",
                    errors=[self._ingest_error(failure) for failure in fetch_failures],
                ),
                started_monotonic,
            )

        result = IngestResult(
            run_id=str(uuid.uuid4()),
            endpoint_id=endpoint.id,
            started_at=started_at,
            completed_at=started_at,
            checkpoint_before=endpoint.checkpoint,
            checkpoint_after=endpoint.checkpoint,
            health="success",
            errors=[self._ingest_error(failure) for failure in fetch_failures],
        )
        with self._database.session() as session, session.begin():
            self._database.advisory_lock(session, f"ingest:{endpoint.id}")
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
                    result.quarantined_raw_document_refs.append(raw.id)
                    result.errors.append(
                        IngestError(
                            error_type=type(exc).__name__,
                            message=str(exc),
                            raw_document_id=raw.id,
                        )
                    )
                    continue
                for parsed in parsed_items:
                    item_id, outcome = self._save_item(
                        session, endpoint, raw.id, document.observed_at, parsed
                    )
                    if item_id not in result.item_refs:
                        result.item_refs.append(item_id)
                    if outcome == "new":
                        result.new_item_count += 1
                        result.new_item_refs.append(item_id)
                    elif outcome == "revision":
                        result.new_revision_count += 1
                        result.new_revision_refs.append(item_id)
                    else:
                        result.duplicate_count += 1
                        result.duplicate_item_refs.append(item_id)
                    result.provenance.append(
                        ProvenanceLink(
                            item_id=item_id,
                            item_content_hash=parsed.content_hash,
                            raw_document_id=raw.id,
                        )
                    )

            if documents:
                observed_at = max(document.observed_at for document in documents)
                endpoint_row.last_observed_at = observed_at
            if documents and not result.errors:
                checkpoint = dict(endpoint_row.checkpoint or {})
                for document in documents:
                    checkpoint.update(document.checkpoint)
                checkpoint["observed_at"] = observed_at.isoformat()
                endpoint_row.checkpoint = checkpoint
                result.checkpoint_after = dict(endpoint_row.checkpoint)
            result.health = "degraded" if result.errors else "success"
            result.completed_at = datetime.now(UTC)
        return self._observe(result, started_monotonic)

    @staticmethod
    def _observe(result: IngestResult, started_monotonic: float) -> IngestResult:
        duration = time.monotonic() - started_monotonic
        INGEST_RUNS.labels(endpoint=result.endpoint_id, health=result.health).inc()
        INGEST_DOCUMENTS.labels(endpoint=result.endpoint_id).inc(len(result.raw_document_refs))
        for outcome, count in (
            ("new", result.new_item_count),
            ("revision", result.new_revision_count),
            ("duplicate", result.duplicate_count),
            ("quarantined", result.quarantined_count),
        ):
            INGEST_ITEMS.labels(endpoint=result.endpoint_id, outcome=outcome).inc(count)
        for error in result.errors:
            INGEST_ERRORS.labels(endpoint=result.endpoint_id, error_type=error.error_type).inc()
        INGEST_DURATION.labels(endpoint=result.endpoint_id).observe(duration)
        logger.info(
            "ingest_completed endpoint=%s health=%s documents=%d new=%d revisions=%d "
            "duplicates=%d quarantined=%d errors=%d duration_seconds=%.3f",
            result.endpoint_id,
            result.health,
            len(result.raw_document_refs),
            result.new_item_count,
            result.new_revision_count,
            result.duplicate_count,
            result.quarantined_count,
            len(result.errors),
            duration,
        )
        return result

    @staticmethod
    def _failed_before_fetch(
        endpoint: SourceEndpoint,
        started_at: datetime,
        error_type: str,
        message: str,
        *,
        retryable: bool = False,
        retry_at: datetime | None = None,
    ) -> IngestResult:
        return IngestResult(
            run_id=str(uuid.uuid4()),
            endpoint_id=endpoint.id,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            checkpoint_before=endpoint.checkpoint,
            checkpoint_after=endpoint.checkpoint,
            health="failed",
            errors=[
                IngestError(
                    error_type=error_type,
                    message=message,
                    retryable=retryable,
                    retry_at=retry_at,
                )
            ],
        )

    @staticmethod
    def _ingest_error(failure: FetchFailure) -> IngestError:
        return IngestError(
            error_type=failure.error_type,
            message=failure.message,
            retryable=failure.retryable,
            upstream_id=failure.upstream_id,
            retry_at=failure.retry_at,
        )

    @staticmethod
    def _fetch_failure(error: Exception) -> FetchFailure:
        return fetch_failure_from_exception(error)

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
            return [Ingest._parse_hacker_news(document.payload)]
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
                    canonical_url=Ingest._canonicalize_url(str(entry.get("link") or "")),
                    language=str(feed.feed.get("language") or "en"),
                    excerpt=excerpt,
                    body=body or None,
                )
            )
        if not parsed:
            raise ValueError("RSS document contains no usable entries")
        return parsed

    @staticmethod
    def _parse_hacker_news(payload: bytes) -> ParsedItem:
        story = json.loads(payload)
        if not isinstance(story, dict) or not story.get("id") or not story.get("title"):
            raise ValueError("invalid Hacker News story")
        story_id = str(story["id"])
        body = str(story.get("text") or "").strip() or None
        return ParsedItem(
            identity_key=story_id,
            title=str(story["title"]).strip(),
            authors=[str(story.get("by") or "unknown")],
            published_at=datetime.fromtimestamp(int(story["time"]), tz=UTC),
            canonical_url=Ingest._canonicalize_url(
                str(story.get("url") or f"https://news.ycombinator.com/item?id={story_id}")
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
    def _canonicalize_url(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        try:
            parsed = urlsplit(value)
            if not parsed.scheme or not parsed.hostname:
                return value
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname.lower()
            port = parsed.port
            include_port = port is not None and not (
                (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
            )
            netloc = f"{hostname}:{port}" if include_port else hostname
            return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
        except ValueError:
            return value

    @staticmethod
    def _save_item(
        session: Session,
        endpoint: SourceEndpoint,
        raw_document_id: str,
        discovered_at: datetime,
        parsed: ParsedItem,
    ) -> tuple[str, str]:
        identity_matches = [ItemRow.identity_key == parsed.identity_key]
        if parsed.canonical_url is not None:
            identity_matches.append(ItemRevisionRow.canonical_url == parsed.canonical_url)
        item = session.scalar(
            select(ItemRow)
            .join(
                ItemRevisionRow,
                (ItemRevisionRow.item_id == ItemRow.id)
                & (ItemRevisionRow.content_hash == ItemRow.current_content_hash),
            )
            .where(
                ItemRow.source_id == endpoint.source_id,
                or_(*identity_matches),
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
