from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from ic.domain import EndpointKind, SourceEndpoint


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SourceRow(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    canonical_key: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(80), default="technology")
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourcePolicyRow(Base):
    __tablename__ = "source_policies"
    __table_args__ = (UniqueConstraint("source_id", "version"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    version: Mapped[str] = mapped_column(String(80))
    acquisition_basis: Mapped[str] = mapped_column(String(120), default="internal-research")
    permitted_actions: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["fetch", "process", "team_share"]
    )
    sharing_scope: Mapped[str] = mapped_column(String(80), default="team-internal")
    fulltext_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceEndpointRow(Base):
    __tablename__ = "source_endpoints"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    locator: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    def as_domain(self) -> SourceEndpoint:
        return SourceEndpoint(
            id=self.id,
            source_id=self.source_id,
            kind=EndpointKind(self.kind),
            locator=self.locator,
            policy_version=self.policy_version,
            checkpoint=dict(self.checkpoint or {}),
            config=dict(self.config or {}),
        )


class RawDocumentRow(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("endpoint_id", "payload_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("source_endpoints.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    upstream_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(160))
    payload: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(32), default="available")
    role: Mapped[str] = mapped_column(String(32), default="content")
    policy_version: Mapped[str] = mapped_column(String(80))
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ItemRow(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source_id", "identity_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    identity_key: Mapped[str] = mapped_column(Text)
    current_revision: Mapped[int] = mapped_column(Integer)
    current_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ItemRevisionRow(Base):
    __tablename__ = "item_revisions"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(32), default="und")
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProvenanceRow(Base):
    __tablename__ = "item_provenance"

    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CandidateSnapshotRow(Base):
    __tablename__ = "candidate_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(32))
    scope: Mapped[dict[str, Any]] = mapped_column(JSON)
    query_version: Mapped[str] = mapped_column(String(80))
    producer: Mapped[str] = mapped_column(String(120), default="ic.select")
    producer_version: Mapped[str] = mapped_column(String(80), default="0.1.0")
    input_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(120), default="source-policy-set:v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SnapshotItemRow(Base):
    __tablename__ = "candidate_snapshot_items"

    snapshot_id: Mapped[str] = mapped_column(ForeignKey("candidate_snapshots.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ScoreRow(Base):
    __tablename__ = "scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), index=True)
    item_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    dimensions: Mapped[dict[str, float]] = mapped_column(JSON)
    total: Mapped[float] = mapped_column(Float)
    reason_codes: Mapped[list[str]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(120))
    producer: Mapped[str] = mapped_column(String(120), default="ic.select")
    producer_version: Mapped[str] = mapped_column(String(80), default="0.1.0")
    input_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    input_hash: Mapped[str] = mapped_column(String(64))
    input_availability: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DigestRow(Base):
    __tablename__ = "digests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    run_key: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("candidate_snapshots.id"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(120))
    producer: Mapped[str] = mapped_column(String(120), default="ic.select")
    producer_version: Mapped[str] = mapped_column(String(80), default="0.1.0")
    input_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="published")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DigestEntryRow(Base):
    __tablename__ = "digest_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    digest_id: Mapped[str] = mapped_column(ForeignKey("digests.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), index=True)
    item_content_hash: Mapped[str] = mapped_column(String(64))
    score_id: Mapped[str] = mapped_column(ForeignKey("scores.id"))
    rank: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(40), default="discovery")
    summary: Mapped[str] = mapped_column(Text)
    recommendation_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(240), unique=True)
    actor_id: Mapped[str] = mapped_column(String(160))
    target_kind: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(120), index=True)
    digest_id: Mapped[str | None] = mapped_column(
        ForeignKey("digests.id"), index=True, nullable=True
    )
    digest_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("digest_entries.id"), index=True, nullable=True
    )
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), index=True)
    item_content_hash: Mapped[str] = mapped_column(String(64))
    score_id: Mapped[str | None] = mapped_column(ForeignKey("scores.id"), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    producer: Mapped[str] = mapped_column(String(120), default="ic.feedback")
    producer_version: Mapped[str] = mapped_column(String(80), default="0.1.0")
    input_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(40))
    reason_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PolicyProposalRow(Base):
    __tablename__ = "policy_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    base_policy_version: Mapped[str] = mapped_column(String(120))
    candidate_policy_version: Mapped[str] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(120))
    producer: Mapped[str] = mapped_column(String(120), default="ic.calibration")
    producer_version: Mapped[str] = mapped_column(String(80), default="0.1.0")
    input_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    proposed_by: Mapped[str] = mapped_column(String(160))
    feedback_summary: Mapped[dict[str, int]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default="proposed")
    replay_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_snapshots.id"), nullable=True
    )
    replay_digest_id: Mapped[str | None] = mapped_column(ForeignKey("digests.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PolicyActivationRow(Base):
    __tablename__ = "policy_activations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("policy_proposals.id"), unique=True)
    policy_version: Mapped[str] = mapped_column(String(120), index=True)
    activated_by: Mapped[str] = mapped_column(String(160))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Database:
    def __init__(self, url: str, *, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(url, pool_pre_ping=True)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.is_test = self.engine.dialect.name == "sqlite"

    @classmethod
    def for_tests(cls) -> Database:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        return cls("sqlite+pysqlite:///:memory:", engine=engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def advisory_lock(self, session: Session, key: str) -> None:
        """Serialize deterministic writes in PostgreSQL; SQLite tests are single-process."""
        if self.engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})

    def seed_defaults(self) -> None:
        with self.session() as session, session.begin():
            self.advisory_lock(session, "seed-default-sources")
            defaults: list[Base] = [
                SourceRow(
                    id="openai",
                    canonical_key="openai",
                    name="OpenAI",
                    languages=["en"],
                ),
                SourceRow(
                    id="hacker-news",
                    canonical_key="hacker-news",
                    name="Hacker News",
                    languages=["en"],
                ),
                SourcePolicyRow(
                    id="openai:internal-v1",
                    source_id="openai",
                    version="internal-v1",
                    permitted_actions=["fetch", "process", "team_share"],
                ),
                SourcePolicyRow(
                    id="hacker-news:internal-v1",
                    source_id="hacker-news",
                    version="internal-v1",
                    permitted_actions=["fetch", "process", "team_share"],
                ),
                SourceEndpointRow(
                    id="openai-news-rss",
                    source_id="openai",
                    kind=EndpointKind.RSS.value,
                    locator="https://openai.com/news/rss.xml",
                    policy_version="internal-v1",
                ),
                SourceEndpointRow(
                    id="horizon-hacker-news",
                    source_id="hacker-news",
                    kind=EndpointKind.HORIZON_HACKER_NEWS.value,
                    locator="https://hacker-news.firebaseio.com/v0",
                    policy_version="internal-v1",
                    config={"fetch_top_stories": 15},
                ),
            ]
            for row in defaults:
                if session.get(type(row), row.id) is None:  # type: ignore[attr-defined]
                    session.add(row)
