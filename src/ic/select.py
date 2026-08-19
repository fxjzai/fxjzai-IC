from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select as sql_select

from ic.db import (
    CandidateSnapshotRow,
    Database,
    DigestEntryRow,
    DigestRow,
    ItemRevisionRow,
    ItemRow,
    ScoreRow,
    SnapshotItemRow,
    utc_now,
)

MODEL_ID = "tracer-heuristic-v1"
PROMPT_VERSION = "none-v1"
QUERY_VERSION = "current-revisions-v1"


class SelectionScope(BaseModel):
    kind: Literal["daily", "stream"]
    period_start: datetime
    period_end: datetime
    timezone: str
    limit: int = Field(default=50, ge=1, le=500)
    source_ids: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def period_is_forward(self) -> SelectionScope:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        return self


class SnapshotItemRef(BaseModel):
    item_id: str
    content_hash: str


class CandidateSnapshotView(BaseModel):
    id: str
    kind: str
    item_count: int
    item_refs: list[SnapshotItemRef]
    created_at: datetime
    closed_at: datetime


class DigestEntryView(BaseModel):
    id: str
    item_id: str
    item_content_hash: str
    score_id: str
    score_status: str
    rank: int
    channel: str
    summary: str
    recommendation_reason: str


class DigestView(BaseModel):
    id: str
    snapshot_id: str
    kind: str
    policy_version: str
    status: str
    generated_at: datetime
    reused: bool
    entries: list[DigestEntryView]


class Select:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create_candidate_snapshot(self, scope: SelectionScope) -> CandidateSnapshotView:
        with self._database.session() as session, session.begin():
            statement = (
                sql_select(ItemRow, ItemRevisionRow)
                .join(
                    ItemRevisionRow,
                    (ItemRevisionRow.item_id == ItemRow.id)
                    & (ItemRevisionRow.content_hash == ItemRow.current_content_hash),
                )
                .where(
                    ItemRevisionRow.discovered_at >= scope.period_start,
                    ItemRevisionRow.discovered_at < scope.period_end,
                )
                .order_by(ItemRevisionRow.discovered_at, ItemRow.id)
                .limit(scope.limit)
            )
            if scope.source_ids:
                statement = statement.where(ItemRow.source_id.in_(scope.source_ids))
            if scope.languages:
                statement = statement.where(ItemRevisionRow.language.in_(scope.languages))
            rows = list(session.execute(statement).all())
            refs = [
                SnapshotItemRef(item_id=item.id, content_hash=revision.content_hash)
                for item, revision in rows
            ]
            identity = json.dumps(
                {
                    "query_version": QUERY_VERSION,
                    "scope": scope.model_dump(mode="json"),
                    "refs": [ref.model_dump() for ref in refs],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ic:snapshot:{identity}"))
            existing = session.get(CandidateSnapshotRow, snapshot_id)
            if existing is None:
                now = utc_now()
                existing = CandidateSnapshotRow(
                    id=snapshot_id,
                    kind=scope.kind,
                    scope=scope.model_dump(mode="json"),
                    query_version=QUERY_VERSION,
                    created_at=now,
                    closed_at=now,
                )
                session.add(existing)
                for position, ref in enumerate(refs):
                    session.add(
                        SnapshotItemRow(
                            snapshot_id=snapshot_id,
                            item_id=ref.item_id,
                            content_hash=ref.content_hash,
                            position=position,
                        )
                    )
            return CandidateSnapshotView(
                id=existing.id,
                kind=existing.kind,
                item_count=len(refs),
                item_refs=refs,
                created_at=existing.created_at,
                closed_at=existing.closed_at,
            )

    def select(self, snapshot_id: str, policy_version: str) -> DigestView:
        run_key = hashlib.sha256(
            f"{snapshot_id}:{policy_version}:{MODEL_ID}:{PROMPT_VERSION}".encode()
        ).hexdigest()
        with self._database.session() as session, session.begin():
            existing = session.scalar(sql_select(DigestRow).where(DigestRow.run_key == run_key))
            if existing is not None:
                return self._digest_view(session, existing, reused=True)

            snapshot = session.get(CandidateSnapshotRow, snapshot_id)
            if snapshot is None:
                raise KeyError(f"unknown candidate snapshot: {snapshot_id}")
            scope = snapshot.scope
            digest_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ic:digest:{run_key}"))
            generated_at = utc_now()
            digest = DigestRow(
                id=digest_id,
                run_key=run_key,
                kind=snapshot.kind,
                snapshot_id=snapshot.id,
                period_start=datetime.fromisoformat(scope["period_start"]),
                period_end=datetime.fromisoformat(scope["period_end"]),
                timezone=str(scope["timezone"]),
                policy_version=policy_version,
                status="published",
                generated_at=generated_at,
                published_at=generated_at,
            )
            session.add(digest)
            snapshot_items = list(
                session.scalars(
                    sql_select(SnapshotItemRow)
                    .where(SnapshotItemRow.snapshot_id == snapshot_id)
                    .order_by(SnapshotItemRow.position)
                )
            )
            ranked: list[tuple[SnapshotItemRow, ItemRevisionRow, float]] = []
            for item_ref in snapshot_items:
                revision = session.scalar(
                    sql_select(ItemRevisionRow).where(
                        ItemRevisionRow.item_id == item_ref.item_id,
                        ItemRevisionRow.content_hash == item_ref.content_hash,
                    )
                )
                if revision is None:
                    raise RuntimeError("snapshot references a missing Item revision")
                score_total = self._score(revision)
                ranked.append((item_ref, revision, score_total))
            ranked.sort(key=lambda value: (-value[2], value[0].position))
            for rank, (item_ref, revision, score_total) in enumerate(ranked, start=1):
                availability = "full" if revision.body else "excerpt_only"
                status = "active" if revision.body else "degraded"
                score_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"ic:score:{run_key}:{item_ref.item_id}:{item_ref.content_hash}",
                    )
                )
                score = ScoreRow(
                    id=score_id,
                    item_id=item_ref.item_id,
                    item_content_hash=item_ref.content_hash,
                    dimensions={"relevance": score_total, "evidence": 5.0},
                    total=score_total,
                    reason_codes=["TRACER_HEURISTIC"],
                    rationale=(
                        "Deterministic tracer-bullet ranking; replace after model evaluation."
                    ),
                    model_id=MODEL_ID,
                    prompt_version=PROMPT_VERSION,
                    policy_version=policy_version,
                    input_hash=item_ref.content_hash,
                    input_availability=availability,
                    status=status,
                )
                session.add(score)
                summary = revision.excerpt or revision.title
                session.add(
                    DigestEntryRow(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{digest_id}:{rank}")),
                        digest_id=digest_id,
                        item_id=item_ref.item_id,
                        item_content_hash=item_ref.content_hash,
                        score_id=score_id,
                        rank=rank,
                        channel="discovery",
                        summary=summary,
                        recommendation_reason="Included by deterministic P0 tracer policy.",
                    )
                )
            session.flush()
            return self._digest_view(session, digest, reused=False)

    @staticmethod
    def _score(revision: ItemRevisionRow) -> float:
        signal_length = len(revision.title) + len(revision.excerpt or "")
        return min(10.0, 5.0 + signal_length / 200.0)

    @staticmethod
    def _digest_view(session: object, digest: DigestRow, *, reused: bool) -> DigestView:
        entries = list(
            session.scalars(  # type: ignore[attr-defined]
                sql_select(DigestEntryRow)
                .where(DigestEntryRow.digest_id == digest.id)
                .order_by(DigestEntryRow.rank)
            )
        )
        return DigestView(
            id=digest.id,
            snapshot_id=digest.snapshot_id,
            kind=digest.kind,
            policy_version=digest.policy_version,
            status=digest.status,
            generated_at=digest.generated_at,
            reused=reused,
            entries=[
                DigestEntryView(
                    id=entry.id,
                    item_id=entry.item_id,
                    item_content_hash=entry.item_content_hash,
                    score_id=entry.score_id,
                    score_status=(
                        score.status
                        if (score := session.get(ScoreRow, entry.score_id)) is not None  # type: ignore[attr-defined]
                        else "missing"
                    ),
                    rank=entry.rank,
                    channel=entry.channel,
                    summary=entry.summary,
                    recommendation_reason=entry.recommendation_reason,
                )
                for entry in entries
            ],
        )
