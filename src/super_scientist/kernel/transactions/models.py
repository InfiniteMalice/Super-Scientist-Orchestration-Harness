from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.primitives import NonBlankText, StableIdentifier, UtcTimestamp


class RejectionCode(StrEnum):
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    ENTITY_ID_MISMATCH = "ENTITY_ID_MISMATCH"
    ENTITY_ALREADY_EXISTS = "ENTITY_ALREADY_EXISTS"
    SELF_APPROVAL = "SELF_APPROVAL"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    INDEPENDENT_REVIEW_REQUIRED = "INDEPENDENT_REVIEW_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    approver: ActorIdentity
    approved_at: UtcTimestamp


class ProposalBase(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: StableIdentifier
    idempotency_key: StableIdentifier
    proposer: ActorIdentity
    approval: Approval | None = None


class AddEvidence(ProposalBase):
    proposal_type: Literal["add_evidence"] = "add_evidence"
    evidence: EvidenceRecord

    @field_serializer("evidence", when_used="json")
    def serialize_evidence(self, evidence: EvidenceRecord) -> object:
        return _json_compatible(evidence.model_dump(warnings="none"))


class ProposeClaim(ProposalBase):
    proposal_type: Literal["propose_claim"] = "propose_claim"
    claim: AtomicClaim


class TransitionClaim(ProposalBase):
    proposal_type: Literal["transition_claim"] = "transition_claim"
    next_claim: AtomicClaim


class InvalidProposal(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_type: Literal["invalid_proposal"] = "invalid_proposal"
    proposal_id: StableIdentifier
    idempotency_key: StableIdentifier
    validation_error: NonBlankText


Proposal = Annotated[
    AddEvidence | ProposeClaim | TransitionClaim | InvalidProposal,
    Field(discriminator="proposal_type"),
]


class RejectionReason(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: RejectionCode
    message: NonBlankText


class TransactionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: StableIdentifier
    accepted: bool
    replayed: bool = False
    reasons: tuple[RejectionReason, ...] = ()

    @model_validator(mode="after")
    def validate_reason_state(self) -> TransactionDecision:
        if self.accepted and self.reasons:
            raise ValueError("accepted decisions must not include rejection reasons")
        if not self.accepted and not self.reasons:
            raise ValueError("rejected decisions must include at least one rejection reason")
        return self


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, frozenset):
        compatible = [_json_compatible(item) for item in value]
        return sorted(
            compatible,
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return value
