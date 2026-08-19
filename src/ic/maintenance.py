from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select

from ic.db import (
    Database,
    ItemRevisionRow,
    ProvenanceRow,
    RawDocumentRow,
    SourceEndpointRow,
    SourcePolicyRow,
)


class RetentionResult(BaseModel):
    raw_document_count: int
    item_revision_count: int


class Retention:
    def __init__(self, database: Database) -> None:
        self._database = database

    def expire_due(self, now: datetime) -> RetentionResult:
        if now.tzinfo is None:
            raise ValueError("retention clock must be timezone-aware")
        with self._database.session() as session, session.begin():
            raw_documents = list(
                session.scalars(select(RawDocumentRow).where(RawDocumentRow.payload.is_not(None)))
            )
            affected_revisions: set[tuple[str, str]] = set()
            expired_documents: list[RawDocumentRow] = []
            for raw in raw_documents:
                endpoint = session.get(SourceEndpointRow, raw.endpoint_id)
                if endpoint is None:
                    continue
                policy = session.scalar(
                    select(SourcePolicyRow).where(
                        SourcePolicyRow.source_id == endpoint.source_id,
                        SourcePolicyRow.version == raw.policy_version,
                    )
                )
                if policy is None:
                    continue
                observed_at = raw.observed_at
                if observed_at.tzinfo is None:
                    observed_at = observed_at.replace(tzinfo=UTC)
                due_at = observed_at + timedelta(days=policy.fulltext_retention_days)
                if due_at > now:
                    continue
                raw.payload = None
                raw.availability = "tombstoned"
                raw.deleted_at = now
                raw.deletion_reason = "source_policy_retention_expired"
                expired_documents.append(raw)
                provenance = session.scalars(
                    select(ProvenanceRow).where(ProvenanceRow.raw_document_id == raw.id)
                )
                affected_revisions.update((row.item_id, row.content_hash) for row in provenance)

            expired_revision_count = 0
            for item_id, content_hash in affected_revisions:
                available = session.scalar(
                    select(RawDocumentRow.id)
                    .join(
                        ProvenanceRow,
                        ProvenanceRow.raw_document_id == RawDocumentRow.id,
                    )
                    .where(
                        ProvenanceRow.item_id == item_id,
                        ProvenanceRow.content_hash == content_hash,
                        RawDocumentRow.payload.is_not(None),
                    )
                    .limit(1)
                )
                if available is not None:
                    continue
                revision = session.scalar(
                    select(ItemRevisionRow).where(
                        ItemRevisionRow.item_id == item_id,
                        ItemRevisionRow.content_hash == content_hash,
                    )
                )
                if revision is not None and revision.body is not None:
                    revision.body = None
                    expired_revision_count += 1

            return RetentionResult(
                raw_document_count=len(expired_documents),
                item_revision_count=expired_revision_count,
            )
