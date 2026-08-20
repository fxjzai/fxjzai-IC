from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select

from ic.db import (
    Database,
    FeedbackRow,
    PolicyActivationRow,
    PolicyProposalRow,
)
from ic.select import Select


class FeedbackSummary(BaseModel):
    policy_version: str
    counts: dict[str, int]


class PolicyProposal(BaseModel):
    id: str
    base_policy_version: str
    candidate_policy_version: str
    proposed_by: str
    feedback_summary: dict[str, int]
    status: str
    replay_snapshot_id: str | None
    replay_digest_id: str | None
    created_at: datetime


class PolicyActivation(BaseModel):
    id: str
    proposal_id: str
    policy_version: str
    activated_by: str
    activated_at: datetime


class Calibration:
    """Manual calibration workflow; it never mutates policy from one Feedback event."""

    def __init__(self, database: Database, selector: Select) -> None:
        self._database = database
        self._selector = selector

    def summarize(self, policy_version: str) -> FeedbackSummary:
        counts = {"useful": 0, "useless": 0, "misclassified": 0}
        with self._database.session() as session:
            rows = session.execute(
                select(FeedbackRow.label, func.count(FeedbackRow.id))
                .where(FeedbackRow.policy_version == policy_version)
                .group_by(FeedbackRow.label)
            )
            for label, count in rows:
                counts[str(label)] = int(count)
        return FeedbackSummary(policy_version=policy_version, counts=counts)

    def propose(
        self,
        *,
        base_policy_version: str,
        candidate_policy_version: str,
        proposed_by: str,
    ) -> PolicyProposal:
        summary = self.summarize(base_policy_version)
        with self._database.session() as session, session.begin():
            feedback_ids = list(
                session.scalars(
                    select(FeedbackRow.id)
                    .where(FeedbackRow.policy_version == base_policy_version)
                    .order_by(FeedbackRow.created_at, FeedbackRow.id)
                )
            )
            row = PolicyProposalRow(
                id=str(uuid.uuid4()),
                base_policy_version=base_policy_version,
                candidate_policy_version=candidate_policy_version,
                policy_version=base_policy_version,
                producer="ic.calibration",
                producer_version="0.1.0",
                input_ids=[f"feedback:{feedback_id}" for feedback_id in feedback_ids],
                proposed_by=proposed_by,
                feedback_summary=summary.counts,
                status="proposed",
            )
            session.add(row)
            session.flush()
            return self._proposal(row)

    def replay(self, proposal_id: str, snapshot_id: str) -> PolicyProposal:
        with self._database.session() as session:
            proposal = session.get(PolicyProposalRow, proposal_id)
            if proposal is None:
                raise KeyError(f"unknown policy proposal: {proposal_id}")
            candidate_policy_version = proposal.candidate_policy_version
        digest = self._selector.select(snapshot_id, candidate_policy_version, purpose="replay")
        with self._database.session() as session, session.begin():
            self._database.advisory_lock(session, f"policy-proposal:{proposal_id}")
            proposal = session.get(PolicyProposalRow, proposal_id)
            assert proposal is not None
            proposal.replay_snapshot_id = snapshot_id
            proposal.replay_digest_id = digest.id
            proposal.status = "replayed"
            return self._proposal(proposal)

    def activate(self, proposal_id: str, *, activated_by: str) -> PolicyActivation:
        with self._database.session() as session, session.begin():
            self._database.advisory_lock(session, f"policy-activation:{proposal_id}")
            existing = session.scalar(
                select(PolicyActivationRow).where(PolicyActivationRow.proposal_id == proposal_id)
            )
            if existing is not None:
                return self._activation(existing)
            proposal = session.get(PolicyProposalRow, proposal_id)
            if proposal is None:
                raise KeyError(f"unknown policy proposal: {proposal_id}")
            if proposal.status != "replayed" or proposal.replay_digest_id is None:
                raise ValueError("policy proposal must be replayed before activation")
            row = PolicyActivationRow(
                id=str(uuid.uuid4()),
                proposal_id=proposal.id,
                policy_version=proposal.candidate_policy_version,
                activated_by=activated_by,
            )
            session.add(row)
            proposal.status = "active"
            session.flush()
            return self._activation(row)

    @staticmethod
    def _proposal(row: PolicyProposalRow) -> PolicyProposal:
        return PolicyProposal(
            id=row.id,
            base_policy_version=row.base_policy_version,
            candidate_policy_version=row.candidate_policy_version,
            proposed_by=row.proposed_by,
            feedback_summary=dict(row.feedback_summary),
            status=row.status,
            replay_snapshot_id=row.replay_snapshot_id,
            replay_digest_id=row.replay_digest_id,
            created_at=row.created_at,
        )

    @staticmethod
    def _activation(row: PolicyActivationRow) -> PolicyActivation:
        return PolicyActivation(
            id=row.id,
            proposal_id=row.proposal_id,
            policy_version=row.policy_version,
            activated_by=row.activated_by,
            activated_at=row.activated_at,
        )
