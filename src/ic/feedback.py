from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ic.db import Database, DigestEntryRow, DigestRow, FeedbackRow


class FeedbackRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=160)
    digest_id: str
    digest_entry_id: str
    label: Literal["useful", "useless", "misclassified"]
    idempotency_key: str = Field(min_length=1, max_length=240)
    reason_code: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackReceipt(BaseModel):
    id: str
    created: bool
    digest_id: str
    digest_entry_id: str
    item_id: str
    item_content_hash: str
    score_id: str
    policy_version: str
    label: str
    created_at: datetime


class Feedback:
    def __init__(self, database: Database) -> None:
        self._database = database

    def record_feedback(self, request: FeedbackRequest) -> FeedbackReceipt:
        with self._database.session() as session, session.begin():
            existing = session.scalar(
                select(FeedbackRow).where(FeedbackRow.idempotency_key == request.idempotency_key)
            )
            if existing is not None:
                return self._receipt(existing, created=False)

            entry = session.get(DigestEntryRow, request.digest_entry_id)
            if entry is None or entry.digest_id != request.digest_id:
                raise KeyError("feedback target does not exist in the specified Digest")
            digest = session.get(DigestRow, request.digest_id)
            if digest is None:
                raise KeyError("feedback Digest does not exist")
            row = FeedbackRow(
                id=str(uuid.uuid4()),
                idempotency_key=request.idempotency_key,
                actor_id=request.actor_id,
                digest_id=request.digest_id,
                digest_entry_id=request.digest_entry_id,
                item_id=entry.item_id,
                item_content_hash=entry.item_content_hash,
                score_id=entry.score_id,
                policy_version=digest.policy_version,
                label=request.label,
                reason_code=request.reason_code,
                comment=request.comment,
            )
            session.add(row)
            session.flush()
            return self._receipt(row, created=True)

    @staticmethod
    def _receipt(row: FeedbackRow, *, created: bool) -> FeedbackReceipt:
        return FeedbackReceipt(
            id=row.id,
            created=created,
            digest_id=row.digest_id,
            digest_entry_id=row.digest_entry_id,
            item_id=row.item_id,
            item_content_hash=row.item_content_hash,
            score_id=row.score_id,
            policy_version=row.policy_version,
            label=row.label,
            created_at=row.created_at,
        )
