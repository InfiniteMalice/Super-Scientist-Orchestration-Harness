from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.cognition.service import (
    RecordCapabilityProfileHandler,
    RecordCohortPlanHandler,
    RecordDiversityAssessmentHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CohortPlan,
    CohortPlanReceiptRef,
    DiversityAssessment,
)
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    Proposal,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordDiversityAssessment,
    TransactionDecision,
    parse_untrusted_proposal_json,
)
from super_scientist.providers.storage.cognitive_records import (
    CapabilityProfileRepository,
    CohortPlanRepository,
    DiversityAssessmentRepository,
)
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    StoredTransaction,
    TransactionRepository,
)

type FixedCognitionHandler = ProposalHandler[BaseModel, BaseModel]


class _AcceptedCognitiveReceiptReader:
    """Resolve only exact accepted cognitive transactions with their exact audit event."""

    def __init__(self, connection: Connection, active_policy: PolicySnapshot) -> None:
        self._transactions = TransactionRepository(connection)
        self._audit = AuditRepository(connection)
        self._active_policy = active_policy

    def resolve_profile(
        self,
        reference: CapabilityProfileReceiptRef,
    ) -> RecordCapabilityProfile | None:
        resolved = self._resolve(reference)
        return resolved if isinstance(resolved, RecordCapabilityProfile) else None

    def resolve_cohort(
        self,
        reference: CohortPlanReceiptRef,
    ) -> RecordCohortPlan | None:
        resolved = self._resolve(reference)
        return resolved if isinstance(resolved, RecordCohortPlan) else None

    def _resolve(
        self,
        reference: CapabilityProfileReceiptRef | CohortPlanReceiptRef,
    ) -> Proposal | None:
        try:
            exact_reference = type(reference).model_validate(
                reference.model_dump(mode="python", warnings=False)
            )
            transaction = self._transactions.get_by_proposal_id(exact_reference.proposal_id)
            if (
                transaction is None
                or not transaction.decision.accepted
                or transaction.proposal_hash != exact_reference.proposal_hash
            ):
                return None
            matches = tuple(
                event
                for event in self._audit.list_all()
                if event.event_id == exact_reference.audit_event_id
                and event.event_hash == exact_reference.audit_event_hash
                and _audit_matches_transaction(event, transaction, self._active_policy.policy_hash)
            )
        except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
            return None
        return transaction.proposal if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class CapabilityProfileCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordCapabilityProfile
    profiles: CapabilityProfileRepository
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_profile(self, profile_id: str) -> CapabilityProfile | None:
        return self.profiles.get(profile_id)

    def append_authoritative(self, record: BaseModel) -> None:
        if type(record) is not CapabilityProfile or record != self.proposal.profile:
            raise TypeError(f"unsupported capability profile record: {type(record)!r}")
        self.profiles.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("capability profiles have no mutable projection")


@dataclass(frozen=True, slots=True)
class CohortPlanCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordCohortPlan
    profiles: CapabilityProfileRepository
    plans: CohortPlanRepository
    receipts: _AcceptedCognitiveReceiptReader
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None:
        return self.plans.get(cohort_plan_id)

    def resolve_profile_receipt(
        self,
        reference: CapabilityProfileReceiptRef,
    ) -> CapabilityProfile | None:
        proposal = self.receipts.resolve_profile(reference)
        if proposal is None:
            return None
        retained = self.profiles.get(proposal.profile.profile_id)
        return retained if retained == proposal.profile else None

    def append_authoritative(self, record: BaseModel) -> None:
        if type(record) is not CohortPlan or record != self.proposal.plan:
            raise TypeError(f"unsupported cohort plan record: {type(record)!r}")
        self.plans.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("cohort plans have no mutable projection")


@dataclass(frozen=True, slots=True)
class DiversityAssessmentCapabilities:
    active_policy: PolicySnapshot
    proposal: RecordDiversityAssessment
    profiles: CapabilityProfileRepository
    plans: CohortPlanRepository
    assessments: DiversityAssessmentRepository
    receipts: _AcceptedCognitiveReceiptReader
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None:
        return self.plans.get(cohort_plan_id)

    def get_diversity_assessment(
        self,
        diversity_assessment_id: str,
    ) -> DiversityAssessment | None:
        return self.assessments.get(diversity_assessment_id)

    def resolve_profile_receipt(
        self,
        reference: CapabilityProfileReceiptRef,
    ) -> CapabilityProfile | None:
        proposal = self.receipts.resolve_profile(reference)
        if proposal is None:
            return None
        retained = self.profiles.get(proposal.profile.profile_id)
        return retained if retained == proposal.profile else None

    def resolve_cohort_receipt(
        self,
        reference: CohortPlanReceiptRef,
    ) -> CohortPlan | None:
        proposal = self.receipts.resolve_cohort(reference)
        if proposal is None:
            return None
        retained = self.plans.get(proposal.plan.cohort_plan_id)
        return retained if retained == proposal.plan else None

    def append_authoritative(self, record: BaseModel) -> None:
        if type(record) is not DiversityAssessment or record != self.proposal.assessment:
            raise TypeError(f"unsupported diversity assessment record: {type(record)!r}")
        self.assessments.add_from_proposal(
            self.proposal,
            created_at=self.created_at,
            transaction_id=self.proposal.proposal_id,
            governing_policy_hash=self.active_policy.policy_hash,
        )

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("diversity assessments have no mutable projection")


type CognitionCapabilities = (
    CapabilityProfileCapabilities | CohortPlanCapabilities | DiversityAssessmentCapabilities
)


def fixed_cognition_handlers() -> tuple[FixedCognitionHandler, ...]:
    return (  # type: ignore[return-value]
        RecordCapabilityProfileHandler(),
        RecordCohortPlanHandler(),
        RecordDiversityAssessmentHandler(),
    )


def cognition_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    *,
    current_transaction_created_at: UtcTimestamp,
) -> CognitionCapabilities:
    if isinstance(proposal, RecordCapabilityProfile):
        return CapabilityProfileCapabilities(
            active_policy,
            proposal,
            CapabilityProfileRepository(connection),
            current_transaction_created_at,
        )
    receipts = _AcceptedCognitiveReceiptReader(connection, active_policy)
    if isinstance(proposal, RecordCohortPlan):
        return CohortPlanCapabilities(
            active_policy,
            proposal,
            CapabilityProfileRepository(connection),
            CohortPlanRepository(connection),
            receipts,
            current_transaction_created_at,
        )
    if isinstance(proposal, RecordDiversityAssessment):
        return DiversityAssessmentCapabilities(
            active_policy,
            proposal,
            CapabilityProfileRepository(connection),
            CohortPlanRepository(connection),
            DiversityAssessmentRepository(connection),
            receipts,
            current_transaction_created_at,
        )
    raise TypeError(f"no fixed cognition capability for proposal: {type(proposal)!r}")


def _audit_matches_transaction(
    event: AuditEvent,
    transaction: StoredTransaction,
    active_policy_hash: str,
) -> bool:
    if event.event_type != "transaction_decision":
        return False
    try:
        decoded = json_compatible_payload(event.payload)
        audited_proposal = parse_untrusted_proposal_json(canonical_json_bytes(decoded["proposal"]))
        audited_decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(decoded["decision"]),
            strict=True,
        )
    except (KeyError, MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        return False
    return (
        decoded.get("transaction_persisted") is True
        and decoded.get("policy_hash") == active_policy_hash
        and decoded.get("stored_policy_hash") == active_policy_hash
        and audited_proposal == transaction.proposal
        and audited_decision == transaction.decision
    )


__all__ = [
    "CapabilityProfileCapabilities",
    "CognitionCapabilities",
    "CohortPlanCapabilities",
    "DiversityAssessmentCapabilities",
    "cognition_capabilities",
    "fixed_cognition_handlers",
]
