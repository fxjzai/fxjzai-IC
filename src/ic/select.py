from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from ic.db import (
    CandidateSnapshotRow,
    Database,
    DigestEntryRow,
    DigestRow,
    ItemRevisionRow,
    ItemRow,
    PolicyActivationRow,
    ProvenanceRow,
    RawDocumentRow,
    ScoreRow,
    SnapshotItemRow,
    SourceEndpointRow,
    SourcePolicyRow,
    utc_now,
)

MODEL_ID = "tracer-heuristic-v1"
PROMPT_VERSION = "none-v1"
QUERY_VERSION = "current-revisions-v1"
BOOTSTRAP_POLICY_VERSIONS = frozenset({"tracer-policy-v1"})
SelectionPurpose = Literal["publish", "replay"]


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
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("selection periods must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        try:
            timezone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc
        if self.kind == "daily":
            local_start = self.period_start.astimezone(timezone)
            local_end = self.period_end.astimezone(timezone)
            if local_start.time() != time.min or local_end.time() != time.min:
                raise ValueError("daily period must start and end at local midnight")
            if (local_end.date() - local_start.date()).days != 1:
                raise ValueError("daily period must cover exactly one local calendar day")
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
    published_at: datetime | None
    reused: bool
    entries: list[DigestEntryView]


def digest_view(session: Session, digest: DigestRow, *, reused: bool) -> DigestView:
    entries = list(
        session.scalars(
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
        published_at=digest.published_at,
        reused=reused,
        entries=[
            DigestEntryView(
                id=entry.id,
                item_id=entry.item_id,
                item_content_hash=entry.item_content_hash,
                score_id=entry.score_id,
                score_status=(
                    score.status
                    if (score := session.get(ScoreRow, entry.score_id)) is not None
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


class Select:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        bootstrap_policy_versions: frozenset[str] = BOOTSTRAP_POLICY_VERSIONS,
    ) -> None:
        self._database = database
        self._clock = clock
        self._bootstrap_policy_versions = bootstrap_policy_versions

    def create_candidate_snapshot(self, scope: SelectionScope) -> CandidateSnapshotView:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("selection clock must be timezone-aware")
        if scope.kind == "daily" and scope.period_end > now:
            raise ValueError("daily period has not ended")
        normalized_scope = scope.model_copy(
            update={
                "source_ids": sorted(set(scope.source_ids)),
                "languages": sorted(set(scope.languages)),
            }
        )
        with self._database.session() as session, session.begin():
            scope_lock = (
                self._stream_identity(normalized_scope)
                if scope.kind == "stream"
                else json.dumps(normalized_scope.model_dump(mode="json"), sort_keys=True)
            )
            self._database.advisory_lock(session, f"candidate-snapshot:{scope_lock}")
            resolved_scope = normalized_scope
            if scope.kind == "stream":
                watermark = self._stream_watermark(session, normalized_scope)
                if watermark is not None:
                    if watermark.tzinfo is None:
                        watermark = watermark.replace(tzinfo=UTC)
                    if watermark >= scope.period_end:
                        raise ValueError("stream watermark must be before period_end")
                    resolved_scope = scope.model_copy(update={"period_start": watermark})
            statement = (
                sql_select(ItemRow, ItemRevisionRow)
                .join(
                    ItemRevisionRow,
                    (ItemRevisionRow.item_id == ItemRow.id)
                    & (ItemRevisionRow.content_hash == ItemRow.current_content_hash),
                )
                .where(
                    ItemRevisionRow.discovered_at >= resolved_scope.period_start,
                    ItemRevisionRow.discovered_at < resolved_scope.period_end,
                )
                .order_by(ItemRevisionRow.discovered_at, ItemRow.id)
            )
            if resolved_scope.source_ids:
                statement = statement.where(ItemRow.source_id.in_(resolved_scope.source_ids))
            if resolved_scope.languages:
                statement = statement.where(ItemRevisionRow.language.in_(resolved_scope.languages))
            rows: list[tuple[ItemRow, ItemRevisionRow]] = []
            source_policy_refs: set[str] = set()
            for item, revision in session.execute(statement).all():
                allowed_policy_refs = self._source_policy_refs_allowing_selection(
                    session, item.id, revision.content_hash
                )
                if not allowed_policy_refs:
                    continue
                if len(rows) == resolved_scope.limit:
                    if resolved_scope.kind == "stream":
                        raise ValueError(
                            "stream candidate count exceeds limit; "
                            "increase the limit or narrow the period"
                        )
                    break
                rows.append((item, revision))
                source_policy_refs.update(allowed_policy_refs)
            refs = [
                SnapshotItemRef(item_id=item.id, content_hash=revision.content_hash)
                for item, revision in rows
            ]
            identity = json.dumps(
                {
                    "query_version": QUERY_VERSION,
                    "scope": resolved_scope.model_dump(mode="json"),
                    "refs": [ref.model_dump() for ref in refs],
                    "source_policy_refs": sorted(source_policy_refs),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ic:snapshot:{identity}"))
            policy_set_hash = hashlib.sha256(
                json.dumps(sorted(source_policy_refs), separators=(",", ":")).encode()
            ).hexdigest()
            existing = session.get(CandidateSnapshotRow, snapshot_id)
            if existing is None:
                existing = CandidateSnapshotRow(
                    id=snapshot_id,
                    kind=resolved_scope.kind,
                    scope=resolved_scope.model_dump(mode="json"),
                    query_version=QUERY_VERSION,
                    producer="ic.select",
                    producer_version="0.1.0",
                    input_ids=[f"item:{ref.item_id}:{ref.content_hash}" for ref in refs],
                    policy_version=f"source-policy-set:{policy_set_hash}",
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

    def select(
        self,
        snapshot_id: str,
        policy_version: str,
        *,
        purpose: SelectionPurpose = "publish",
    ) -> DigestView:
        if purpose not in {"publish", "replay"}:
            raise ValueError(f"unknown selection purpose: {purpose}")
        run_key = hashlib.sha256(
            f"{snapshot_id}:{policy_version}:{MODEL_ID}:{PROMPT_VERSION}:{purpose}".encode()
        ).hexdigest()
        with self._database.session() as session, session.begin():
            self._database.advisory_lock(session, f"digest:{run_key}")
            snapshot = session.get(CandidateSnapshotRow, snapshot_id)
            if snapshot is None:
                raise KeyError(f"unknown candidate snapshot: {snapshot_id}")
            if purpose == "publish" and not self._policy_is_active(session, policy_version):
                raise ValueError(f"selection policy is not active: {policy_version}")
            existing = session.scalar(sql_select(DigestRow).where(DigestRow.run_key == run_key))
            if existing is not None:
                return digest_view(session, existing, reused=True)

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
                producer="ic.select",
                producer_version="0.1.0",
                input_ids=[f"snapshot:{snapshot.id}"],
                status="published" if purpose == "publish" else "replay",
                generated_at=generated_at,
                published_at=generated_at if purpose == "publish" else None,
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
            digest_is_degraded = False
            for rank, (item_ref, revision, score_total) in enumerate(ranked, start=1):
                availability = "full" if revision.body else "excerpt_only"
                status = "active" if revision.body else "degraded"
                digest_is_degraded = digest_is_degraded or status == "degraded"
                scoring_input = json.dumps(
                    {
                        "body": revision.body,
                        "excerpt": revision.excerpt,
                        "input_availability": availability,
                        "title": revision.title,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
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
                    producer="ic.select",
                    producer_version="0.1.0",
                    input_ids=[
                        f"snapshot:{snapshot.id}",
                        f"item:{item_ref.item_id}:{item_ref.content_hash}",
                    ],
                    input_hash=hashlib.sha256(scoring_input.encode()).hexdigest(),
                    input_availability=availability,
                    status=status,
                )
                session.add(score)
                digest.input_ids = [*digest.input_ids, f"score:{score_id}"]
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
            if digest_is_degraded:
                digest.status = "degraded" if purpose == "publish" else "replay_degraded"
            session.flush()
            return digest_view(session, digest, reused=False)

    def _stream_watermark(self, session: Session, scope: SelectionScope) -> datetime | None:
        identity = self._stream_identity(scope)
        rows = session.execute(
            sql_select(DigestRow, CandidateSnapshotRow)
            .join(CandidateSnapshotRow, CandidateSnapshotRow.id == DigestRow.snapshot_id)
            .where(
                DigestRow.kind == "stream",
                DigestRow.status.in_(["published", "degraded"]),
                DigestRow.published_at.is_not(None),
            )
        )
        watermarks = [
            digest.period_end
            for digest, snapshot in rows
            if self._stream_identity_from_mapping(snapshot.scope) == identity
        ]
        return max(watermarks) if watermarks else None

    @staticmethod
    def _stream_identity(scope: SelectionScope) -> str:
        return Select._stream_identity_from_mapping(scope.model_dump(mode="json"))

    @staticmethod
    def _stream_identity_from_mapping(scope: dict[str, object]) -> str:
        source_ids = scope.get("source_ids")
        languages = scope.get("languages")
        return json.dumps(
            {
                "kind": "stream",
                "source_ids": sorted(
                    {str(value) for value in source_ids} if isinstance(source_ids, list) else set()
                ),
                "languages": sorted(
                    {str(value) for value in languages} if isinstance(languages, list) else set()
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _source_policy_refs_allowing_selection(
        session: Session, item_id: str, content_hash: str
    ) -> set[str]:
        policies = list(
            session.scalars(
                sql_select(SourcePolicyRow)
                .join(
                    SourceEndpointRow,
                    SourceEndpointRow.source_id == SourcePolicyRow.source_id,
                )
                .join(
                    RawDocumentRow,
                    (RawDocumentRow.endpoint_id == SourceEndpointRow.id)
                    & (RawDocumentRow.policy_version == SourcePolicyRow.version),
                )
                .join(
                    ProvenanceRow,
                    ProvenanceRow.raw_document_id == RawDocumentRow.id,
                )
                .where(
                    ProvenanceRow.item_id == item_id,
                    ProvenanceRow.content_hash == content_hash,
                )
            ).unique()
        )
        required_actions = {"process", "team_share"}
        return {
            f"{policy.source_id}:{policy.version}"
            for policy in policies
            if required_actions.issubset(set(policy.permitted_actions))
        }

    def _policy_is_active(self, session: Session, policy_version: str) -> bool:
        if policy_version in self._bootstrap_policy_versions:
            return True
        return (
            session.scalar(
                sql_select(PolicyActivationRow.id).where(
                    PolicyActivationRow.policy_version == policy_version
                )
            )
            is not None
        )

    @staticmethod
    def _score(revision: ItemRevisionRow) -> float:
        signal_length = len(revision.title) + len(revision.excerpt or "")
        return min(10.0, 5.0 + signal_length / 200.0)
