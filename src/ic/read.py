from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import func, select

from ic.db import (
    Database,
    DigestEntryRow,
    DigestRow,
    ItemRevisionRow,
    ItemRow,
    ProvenanceRow,
    RawDocumentRow,
    ScoreRow,
    SourceEndpointRow,
    SourceRow,
    latest_published_score_statement,
)
from ic.select import DigestView, digest_view


class SourceView(BaseModel):
    id: str
    name: str


class ProvenanceView(BaseModel):
    raw_document_id: str
    source_url: str
    source_href: str | None
    observed_at: datetime
    payload_hash: str
    availability: str
    deleted_at: datetime | None
    deletion_reason: str | None


class ScoreSummary(BaseModel):
    id: str
    total: float
    model_id: str
    policy_version: str
    status: str


class ItemView(BaseModel):
    item_id: str
    revision: int
    content_hash: str
    source: SourceView
    title: str
    authors: list[str]
    published_at: datetime | None
    discovered_at: datetime
    canonical_url: str | None
    canonical_href: str | None
    language: str
    excerpt: str | None
    status: str
    score: ScoreSummary | None
    provenance: list[ProvenanceView]
    raw_payload_available: bool


class EndpointStatusView(BaseModel):
    id: str
    kind: str
    status: str
    last_observed_at: datetime | None


class SourceStatusView(BaseModel):
    id: str
    name: str
    status: str
    item_count: int
    endpoints: list[EndpointStatusView]


class ItemSummaryView(BaseModel):
    item_id: str
    title: str
    source: SourceView
    discovered_at: datetime
    published_at: datetime | None
    canonical_url: str | None
    canonical_href: str | None
    language: str
    excerpt: str | None


class DashboardView(BaseModel):
    total_items: int
    total_digests: int
    sources: list[SourceStatusView]
    latest_digests: list[DigestSummaryView]
    latest_items: list[ItemSummaryView]


class DigestSummaryView(BaseModel):
    id: str
    kind: str
    status: str
    policy_version: str
    published_at: datetime
    entry_count: int


class DigestPageEntryView(BaseModel):
    id: str
    item_id: str
    item_content_hash: str
    rank: int
    title: str
    source: SourceView
    summary: str
    recommendation_reason: str
    score_status: str


class DigestPageView(BaseModel):
    id: str
    kind: str
    status: str
    policy_version: str
    generated_at: datetime
    published_at: datetime | None
    entries: list[DigestPageEntryView]


def _safe_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return candidate


class ReadIntelligence:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_dashboard(self, *, item_limit: int = 40) -> DashboardView:
        if not 1 <= item_limit <= 100:
            raise ValueError("dashboard item_limit must be between 1 and 100")
        with self._database.session() as session:
            source_rows = list(session.scalars(select(SourceRow).order_by(SourceRow.name)))
            endpoint_rows = list(
                session.scalars(
                    select(SourceEndpointRow).order_by(
                        SourceEndpointRow.source_id,
                        SourceEndpointRow.priority,
                        SourceEndpointRow.id,
                    )
                )
            )
            item_counts: dict[str, int] = {
                source_id: int(count)
                for source_id, count in session.execute(
                    select(ItemRow.source_id, func.count(ItemRow.id)).group_by(ItemRow.source_id)
                ).all()
            }
            endpoints_by_source: dict[str, list[EndpointStatusView]] = {}
            for endpoint in endpoint_rows:
                endpoints_by_source.setdefault(endpoint.source_id, []).append(
                    EndpointStatusView(
                        id=endpoint.id,
                        kind=endpoint.kind,
                        status=endpoint.status,
                        last_observed_at=endpoint.last_observed_at,
                    )
                )
            latest_rows = session.execute(
                select(ItemRow, ItemRevisionRow, SourceRow)
                .join(
                    ItemRevisionRow,
                    (ItemRevisionRow.item_id == ItemRow.id)
                    & (ItemRevisionRow.content_hash == ItemRow.current_content_hash),
                )
                .join(SourceRow, SourceRow.id == ItemRow.source_id)
                .order_by(ItemRevisionRow.discovered_at.desc(), ItemRow.id)
                .limit(item_limit)
            ).all()
            digest_rows = session.execute(
                select(DigestRow, func.count(DigestEntryRow.id))
                .outerjoin(DigestEntryRow, DigestEntryRow.digest_id == DigestRow.id)
                .where(
                    DigestRow.status.in_(["published", "degraded"]),
                    DigestRow.published_at.is_not(None),
                )
                .group_by(DigestRow.id)
                .order_by(DigestRow.published_at.desc(), DigestRow.id)
                .limit(8)
            ).all()
            total_digests = session.scalar(
                select(func.count(DigestRow.id)).where(
                    DigestRow.status.in_(["published", "degraded"]),
                    DigestRow.published_at.is_not(None),
                )
            )
            return DashboardView(
                total_items=sum(int(count) for count in item_counts.values()),
                total_digests=int(total_digests or 0),
                sources=[
                    SourceStatusView(
                        id=source.id,
                        name=source.name,
                        status=source.status,
                        item_count=int(item_counts.get(source.id, 0)),
                        endpoints=endpoints_by_source.get(source.id, []),
                    )
                    for source in source_rows
                ],
                latest_digests=[
                    DigestSummaryView(
                        id=digest.id,
                        kind=digest.kind,
                        status=digest.status,
                        policy_version=digest.policy_version,
                        published_at=digest.published_at,
                        entry_count=int(entry_count),
                    )
                    for digest, entry_count in digest_rows
                    if digest.published_at is not None
                ],
                latest_items=[
                    ItemSummaryView(
                        item_id=item.id,
                        title=revision.title,
                        source=SourceView(id=source.id, name=source.name),
                        discovered_at=revision.discovered_at,
                        published_at=revision.published_at,
                        canonical_url=revision.canonical_url,
                        canonical_href=_safe_http_url(revision.canonical_url),
                        language=revision.language,
                        excerpt=revision.excerpt,
                    )
                    for item, revision, source in latest_rows
                ],
            )

    def get_digest_page(self, digest_id: str) -> DigestPageView:
        with self._database.session() as session:
            digest = session.get(DigestRow, digest_id)
            if (
                digest is None
                or digest.status not in {"published", "degraded"}
                or digest.published_at is None
            ):
                raise KeyError(f"unknown Digest: {digest_id}")
            rows = session.execute(
                select(DigestEntryRow, ItemRevisionRow, SourceRow, ScoreRow)
                .join(
                    ItemRevisionRow,
                    (ItemRevisionRow.item_id == DigestEntryRow.item_id)
                    & (ItemRevisionRow.content_hash == DigestEntryRow.item_content_hash),
                )
                .join(ItemRow, ItemRow.id == DigestEntryRow.item_id)
                .join(SourceRow, SourceRow.id == ItemRow.source_id)
                .join(ScoreRow, ScoreRow.id == DigestEntryRow.score_id)
                .where(DigestEntryRow.digest_id == digest.id)
                .order_by(DigestEntryRow.rank)
            ).all()
            return DigestPageView(
                id=digest.id,
                kind=digest.kind,
                status=digest.status,
                policy_version=digest.policy_version,
                generated_at=digest.generated_at,
                published_at=digest.published_at,
                entries=[
                    DigestPageEntryView(
                        id=entry.id,
                        item_id=entry.item_id,
                        item_content_hash=entry.item_content_hash,
                        rank=entry.rank,
                        title=revision.title,
                        source=SourceView(id=source.id, name=source.name),
                        summary=entry.summary,
                        recommendation_reason=entry.recommendation_reason,
                        score_status=score.status,
                    )
                    for entry, revision, source, score in rows
                ],
            )

    def get_item(self, item_id: str) -> ItemView:
        with self._database.session() as session:
            item = session.get(ItemRow, item_id)
            if item is None:
                raise KeyError(f"unknown Item: {item_id}")
            revision = session.scalar(
                select(ItemRevisionRow).where(
                    ItemRevisionRow.item_id == item.id,
                    ItemRevisionRow.content_hash == item.current_content_hash,
                )
            )
            source = session.get(SourceRow, item.source_id)
            if revision is None or source is None:
                raise RuntimeError("Item references missing source or revision")
            score = session.scalar(latest_published_score_statement(item.id, revision.content_hash))
            provenance_rows = list(
                session.execute(
                    select(ProvenanceRow, RawDocumentRow)
                    .join(
                        RawDocumentRow,
                        RawDocumentRow.id == ProvenanceRow.raw_document_id,
                    )
                    .where(
                        ProvenanceRow.item_id == item.id,
                        ProvenanceRow.content_hash == revision.content_hash,
                    )
                    .order_by(RawDocumentRow.observed_at)
                ).all()
            )
            provenance = [
                ProvenanceView(
                    raw_document_id=raw.id,
                    source_url=raw.source_url,
                    source_href=_safe_http_url(raw.source_url),
                    observed_at=raw.observed_at,
                    payload_hash=raw.payload_hash,
                    availability=raw.availability,
                    deleted_at=raw.deleted_at,
                    deletion_reason=raw.deletion_reason,
                )
                for _, raw in provenance_rows
            ]
            return ItemView(
                item_id=item.id,
                revision=item.current_revision,
                content_hash=revision.content_hash,
                source=SourceView(id=source.id, name=source.name),
                title=revision.title,
                authors=list(revision.authors),
                published_at=revision.published_at,
                discovered_at=revision.discovered_at,
                canonical_url=revision.canonical_url,
                canonical_href=_safe_http_url(revision.canonical_url),
                language=revision.language,
                excerpt=revision.excerpt,
                status=revision.status,
                score=(
                    ScoreSummary(
                        id=score.id,
                        total=score.total,
                        model_id=score.model_id,
                        policy_version=score.policy_version,
                        status=score.status,
                    )
                    if score is not None
                    else None
                ),
                provenance=provenance,
                raw_payload_available=any(raw.payload is not None for _, raw in provenance_rows),
            )

    def get_digest(self, digest_id: str) -> DigestView:
        with self._database.session() as session:
            digest = session.get(DigestRow, digest_id)
            if (
                digest is None
                or digest.status not in {"published", "degraded"}
                or digest.published_at is None
            ):
                raise KeyError(f"unknown Digest: {digest_id}")
            return digest_view(session, digest, reused=True)
