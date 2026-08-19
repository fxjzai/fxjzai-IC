from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select

from ic.db import Database, ItemRevisionRow, ProvenanceRow, RawDocumentRow


class RetentionResult(BaseModel):
    raw_document_count: int
    item_revision_count: int


class Retention:
    def __init__(self, database: Database) -> None:
        self._database = database

    def expire_fulltext(self, before: datetime) -> RetentionResult:
        with self._database.session() as session, session.begin():
            raw_documents = list(
                session.scalars(
                    select(RawDocumentRow).where(
                        RawDocumentRow.observed_at < before,
                        RawDocumentRow.payload.is_not(None),
                    )
                )
            )
            affected_revisions: set[tuple[str, str]] = set()
            for raw in raw_documents:
                raw.payload = None
                raw.availability = "tombstoned"
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
                raw_document_count=len(raw_documents),
                item_revision_count=expired_revision_count,
            )
