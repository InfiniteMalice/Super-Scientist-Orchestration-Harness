from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, field_validator

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.claims.transitions import validate_transition
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.evaluation.claim_drift.deterministic import run_deterministic_checks
from super_scientist.evaluation.claim_drift.models import CheckOutcome
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    RejectionCode,
    RejectionReason,
    TransactionDecision,
    TransitionClaim,
)


def _freeze_mapping[Value](value: Mapping[str, Value]) -> Mapping[str, Value]:
    return MappingProxyType(dict(value))


class AdmissionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, arbitrary_types_allowed=True)

    active_policy: PolicySnapshot
    evidence_by_id: Mapping[str, EvidenceRecord]
    claim_by_id: Mapping[str, AtomicClaim]
    prior_decision_by_idempotency_key: Mapping[str, TransactionDecision]

    @field_validator(
        "evidence_by_id",
        "claim_by_id",
        "prior_decision_by_idempotency_key",
        mode="after",
    )
    @classmethod
    def freeze_mappings(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _freeze_mapping(value)


class AdmissionEngine:
    def decide(self, proposal: Proposal, context: AdmissionContext) -> TransactionDecision:
        prior = context.prior_decision_by_idempotency_key.get(proposal.idempotency_key)
        if prior is not None:
            return prior.model_copy(update={"replayed": True})

        if proposal.approval and proposal.approval.approver.actor_id == proposal.proposer.actor_id:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.SELF_APPROVAL,
                "proposer cannot approve its own proposal",
            )

        if isinstance(proposal, AddEvidence):
            return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

        if isinstance(proposal, ProposeClaim):
            if proposal.claim.status is not ClaimStatus.PROPOSED:
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    "new claims must begin in PROPOSED",
                )
            return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

        if isinstance(proposal, TransitionClaim):
            return self._decide_transition(proposal, context)

        raise TypeError(f"unsupported proposal type: {type(proposal).__name__}")

    def _decide_transition(
        self,
        proposal: TransitionClaim,
        context: AdmissionContext,
    ) -> TransactionDecision:
        current = context.claim_by_id.get(proposal.claim_id)
        if current is None:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "claim does not exist",
            )
        if current.version != proposal.expected_version:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "claim version does not match expected version",
            )

        transition = validate_transition(current.status, proposal.target_status)
        if not transition.allowed:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                transition.reason or "invalid transition",
            )

        checks = run_deterministic_checks(current, context.evidence_by_id)
        if any(check.outcome is CheckOutcome.FAIL_DETERMINISTIC for check in checks):
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "claim evidence checks failed",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    @staticmethod
    def rejected(
        proposal_id: str,
        code: RejectionCode,
        message: str,
    ) -> TransactionDecision:
        return TransactionDecision(
            proposal_id=proposal_id,
            accepted=False,
            reasons=(RejectionReason(code=code, message=message),),
        )
