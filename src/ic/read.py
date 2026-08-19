from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import desc, select

from ic.db import (
    Database,
    DigestRow,
    ItemRevisionRow,
    ItemRow,
    ProvenanceRow,
    RawDocumentRow,
    ScoreRow,
    SourceRow,
)
from ic.select import DigestView, digest_view


class SourceView(BaseModel):
    id: str
    name: str


class ProvenanceView(BaseModel):
    raw_document_id: str
    source_url: str
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
    language: str
    excerpt: str | None
    status: str
    score: ScoreSummary | None
    provenance: list[ProvenanceView]
    raw_payload_available: bool


class ReadIntelligence:
    def __init__(self, database: Database) -> None:
        self._database = database

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
            score = session.scalar(
                select(ScoreRow)
                .where(
                    ScoreRow.item_id == item.id,
                    ScoreRow.item_content_hash == revision.content_hash,
                )
                .order_by(desc(ScoreRow.created_at))
                .limit(1)
            )
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
            if digest is None:
                raise KeyError(f"unknown Digest: {digest_id}")
            return digest_view(session, digest, reused=True)
