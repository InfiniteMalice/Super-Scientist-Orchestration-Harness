from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.claims.transitions import validate_transition
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import are_independent
from super_scientist.domain.primitives import StableIdentifier
from super_scientist.evaluation.claim_drift.deterministic import run_deterministic_checks
from super_scientist.evaluation.claim_drift.models import CheckOutcome, CheckResult
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    InvalidProposal,
    Proposal,
    ProposeClaim,
    RejectionCode,
    RejectionReason,
    TransactionDecision,
    TransitionClaim,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
INVALID_PROPOSAL_ID = "invalid-proposal"


def _freeze_mapping[Value](value: Mapping[str, Value]) -> Mapping[str, Value]:
    return MappingProxyType(dict(value))


class AdmissionContext(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    active_policy: PolicySnapshot
    evidence_by_id: Mapping[StableIdentifier, EvidenceRecord]
    claim_by_id: Mapping[StableIdentifier, AtomicClaim]
    prior_decision_by_idempotency_key: Mapping[StableIdentifier, TransactionDecision]

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
    def decide(self, proposal: object, context: object) -> TransactionDecision:
        try:
            normalized_proposal = PROPOSAL_ADAPTER.validate_python(_model_data(proposal))
            normalized_context = AdmissionContext.model_validate(_model_data(context))
        except Exception:
            return self.rejected(
                _proposal_id(proposal),
                RejectionCode.INVALID_PROPOSAL,
                "proposal or admission context is invalid",
            )

        if not _mapping_entity_ids_match(normalized_context):
            return self.rejected(
                normalized_proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "context mapping key does not match contained entity id",
            )

        prior = normalized_context.prior_decision_by_idempotency_key.get(
            normalized_proposal.idempotency_key
        )
        if prior is not None:
            return prior.model_copy(update={"replayed": True})

        if isinstance(normalized_proposal, InvalidProposal):
            return self.rejected(
                normalized_proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                normalized_proposal.validation_error,
            )

        if normalized_proposal.approval and not are_independent(
            normalized_proposal.proposer,
            normalized_proposal.approval.approver,
        ):
            return self.rejected(
                normalized_proposal.proposal_id,
                RejectionCode.SELF_APPROVAL,
                "proposer and approver must be independent",
            )

        if isinstance(normalized_proposal, AddEvidence):
            if (
                normalized_proposal.evidence.ingestion_actor_id
                != normalized_proposal.proposer.actor_id
            ):
                return self.rejected(
                    normalized_proposal.proposal_id,
                    RejectionCode.ENTITY_ID_MISMATCH,
                    "evidence ingestion actor must match the proposal proposer",
                )
            if normalized_proposal.evidence.evidence_id in normalized_context.evidence_by_id:
                return self.rejected(
                    normalized_proposal.proposal_id,
                    RejectionCode.ENTITY_ALREADY_EXISTS,
                    "evidence already exists",
                )
            return TransactionDecision(proposal_id=normalized_proposal.proposal_id, accepted=True)

        if isinstance(normalized_proposal, ProposeClaim):
            if normalized_proposal.claim.created_by != normalized_proposal.proposer.actor_id:
                return self.rejected(
                    normalized_proposal.proposal_id,
                    RejectionCode.ENTITY_ID_MISMATCH,
                    "claim creator must match the proposal proposer",
                )
            if normalized_proposal.claim.claim_id in normalized_context.claim_by_id:
                return self.rejected(
                    normalized_proposal.proposal_id,
                    RejectionCode.ENTITY_ALREADY_EXISTS,
                    "claim already exists",
                )
            if normalized_proposal.claim.status is not ClaimStatus.PROPOSED:
                return self.rejected(
                    normalized_proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    "new claims must begin in PROPOSED",
                )
            return TransactionDecision(proposal_id=normalized_proposal.proposal_id, accepted=True)

        if isinstance(normalized_proposal, TransitionClaim):
            return self._decide_transition(normalized_proposal, normalized_context)

        return self.rejected(
            normalized_proposal.proposal_id,
            RejectionCode.INVALID_PROPOSAL,
            "unsupported proposal type",
        )

    def _decide_transition(
        self,
        proposal: TransitionClaim,
        context: AdmissionContext,
    ) -> TransactionDecision:
        next_claim = proposal.next_claim
        current = context.claim_by_id.get(next_claim.claim_id)
        if current is None:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "claim does not exist",
            )
        if next_claim.version != current.version + 1:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "next claim version must exactly succeed the current version",
            )

        if next_claim.parent_version_id != f"{current.claim_id}:{current.version}":
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "next claim parent must reference the current version",
            )
        if next_claim.created_by != proposal.proposer.actor_id:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "next claim creator must match the transition proposer",
            )
        immutable_fields = (
            "proposition",
            "scope",
            "population_or_system",
            "epistemic_modality",
        )
        if any(getattr(next_claim, field) != getattr(current, field) for field in immutable_fields):
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "claim identity content cannot change during a status transition",
            )

        transition = validate_transition(current.status, next_claim.status)
        if not transition.allowed:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                transition.reason or "invalid transition",
            )

        if next_claim.status is ClaimStatus.WITHDRAWN:
            if (
                next_claim.assumptions != current.assumptions
                or next_claim.evidence_links != current.evidence_links
            ):
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    "withdrawal may change only status and required lineage metadata",
                )
            return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

        if next_claim.status in {
            ClaimStatus.REPRODUCED,
            ClaimStatus.CORROBORATED,
            ClaimStatus.CONSTRAINT_VALIDATED,
            ClaimStatus.FALSIFIED,
            ClaimStatus.SUPERSEDED,
        }:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                f"{next_claim.status.value} proof is not implemented",
            )

        if next_claim.status is ClaimStatus.EVIDENCE_LINKED:
            current_links = set(current.evidence_links)
            if not any(link not in current_links for link in next_claim.evidence_links):
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.MISSING_EVIDENCE,
                    "EVIDENCE_LINKED requires at least one new evidence link",
                )

        checks = run_deterministic_checks(next_claim, context.evidence_by_id)
        if any(check.outcome is CheckOutcome.FAIL_DETERMINISTIC for check in checks):
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "claim evidence checks failed",
            )
        if any(check.outcome is CheckOutcome.REQUIRES_INDEPENDENT_REVIEW for check in checks):
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "claim checks require independent review",
            )
        if not _required_checks_pass(checks, context.active_policy.policy.required_claim_checks):
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "required policy checks are unresolved",
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


def _model_data(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python", warnings="none")
    return value


def _proposal_id(value: object) -> str:
    try:
        if isinstance(value, Mapping):
            candidate = value.get("proposal_id")
        else:
            candidate = getattr(value, "proposal_id", None)
    except Exception:
        return INVALID_PROPOSAL_ID
    return candidate if isinstance(candidate, str) and candidate else INVALID_PROPOSAL_ID


def _mapping_entity_ids_match(context: AdmissionContext) -> bool:
    return all(
        mapping_key == evidence.evidence_id
        for mapping_key, evidence in context.evidence_by_id.items()
    ) and all(mapping_key == claim.claim_id for mapping_key, claim in context.claim_by_id.items())


def _required_checks_pass(
    checks: tuple[CheckResult, ...],
    required_codes: tuple[str, ...],
) -> bool:
    for required_code in required_codes:
        matching_checks = [check for check in checks if check.code == required_code]
        if not matching_checks:
            return False
        if any(check.outcome is not CheckOutcome.PASS_DETERMINISTIC for check in matching_checks):
            return False
    return True
