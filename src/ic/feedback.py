from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from ic.db import (
    Database,
    DigestEntryRow,
    DigestRow,
    FeedbackRow,
    ItemRevisionRow,
    ItemRow,
    latest_published_score_statement,
)


class FeedbackRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=160)
    target_kind: Literal["digest_entry", "item"]
    target_id: str
    digest_id: str | None = None
    item_content_hash: str | None = None
    label: Literal["useful", "useless", "misclassified"]
    idempotency_key: str = Field(min_length=1, max_length=240)
    reason_code: str | None = None
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def digest_context_is_present(self) -> FeedbackRequest:
        if self.target_kind == "digest_entry" and self.digest_id is None:
            raise ValueError("digest_id is required for digest_entry feedback")
        if self.target_kind == "item" and self.item_content_hash is None:
            raise ValueError("item_content_hash is required for item feedback")
        return self


class FeedbackReceipt(BaseModel):
    id: str
    created: bool
    target_kind: str
    target_id: str
    digest_id: str | None
    digest_entry_id: str | None
    item_id: str
    item_content_hash: str
    score_id: str | None
    policy_version: str | None
    label: str
    created_at: datetime


class Feedback:
    def __init__(self, database: Database) -> None:
        self._database = database

    def record_feedback(self, request: FeedbackRequest) -> FeedbackReceipt:
        with self._database.session() as session, session.begin():
            self._database.advisory_lock(session, f"feedback:{request.idempotency_key}")
            existing = session.scalar(
                select(FeedbackRow).where(FeedbackRow.idempotency_key == request.idempotency_key)
            )
            if existing is not None:
                return self._receipt(existing, created=False)

            digest_id: str | None = None
            digest_entry_id: str | None = None
            score_id: str | None = None
            policy_version: str | None = None
            if request.target_kind == "digest_entry":
                entry = session.get(DigestEntryRow, request.target_id)
                if entry is None or entry.digest_id != request.digest_id:
                    raise KeyError("feedback target does not exist in the specified Digest")
                digest = session.get(DigestRow, request.digest_id)
                if digest is None:
                    raise KeyError("feedback Digest does not exist")
                item_id = entry.item_id
                item_content_hash = entry.item_content_hash
                score_id = entry.score_id
                policy_version = digest.policy_version
                digest_id = digest.id
                digest_entry_id = entry.id
            else:
                item = session.get(ItemRow, request.target_id)
                if item is None:
                    raise KeyError("feedback Item does not exist")
                item_id = item.id
                assert request.item_content_hash is not None
                revision = session.scalar(
                    select(ItemRevisionRow).where(
                        ItemRevisionRow.item_id == item.id,
                        ItemRevisionRow.content_hash == request.item_content_hash,
                    )
                )
                if revision is None:
                    raise KeyError("feedback Item revision does not exist")
                item_content_hash = revision.content_hash
                score = session.scalar(latest_published_score_statement(item.id, item_content_hash))
                if score is not None:
                    score_id = score.id
                    policy_version = score.policy_version
            row = FeedbackRow(
                id=str(uuid.uuid4()),
                idempotency_key=request.idempotency_key,
                actor_id=request.actor_id,
                target_kind=request.target_kind,
                target_id=request.target_id,
                digest_id=digest_id,
                digest_entry_id=digest_entry_id,
                item_id=item_id,
                item_content_hash=item_content_hash,
                score_id=score_id,
                policy_version=policy_version,
                input_ids=[f"{item_id}:{item_content_hash}"],
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
            target_kind=row.target_kind,
            target_id=row.target_id,
            digest_id=row.digest_id,
            digest_entry_id=row.digest_entry_id,
            item_id=row.item_id,
            item_content_hash=row.item_content_hash,
            score_id=row.score_id,
            policy_version=row.policy_version,
            label=row.label,
            created_at=row.created_at,
        )
