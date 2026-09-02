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
    GovernedProvenanceSnapshot,
    build_governed_provenance_snapshot,
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

    def resolve_many(
        self,
        references: tuple[CapabilityProfileReceiptRef | CohortPlanReceiptRef, ...],
    ) -> tuple[tuple[Proposal | None, ...], GovernedProvenanceSnapshot]:
        empty_snapshot = build_governed_provenance_snapshot((), ())
        try:
            exact_references = tuple(
                _fresh_cognitive_receipt(reference) for reference in references
            )
            transactions = self._transactions.get_many_by_proposal_ids(
                tuple(reference.proposal_id for reference in exact_references)
            )
            audit_events = self._audit.list_all()
            provenance = build_governed_provenance_snapshot(transactions, audit_events)
            transactions_by_id = {
                transaction.proposal.proposal_id: transaction for transaction in transactions
            }
            audits_by_receipt: dict[tuple[str, str], list[AuditEvent]] = {}
            for event in audit_events:
                audits_by_receipt.setdefault((event.event_id, event.event_hash), []).append(event)
            resolved = tuple(
                self._resolve_from_snapshot(
                    reference,
                    transactions_by_id.get(reference.proposal_id),
                    audits_by_receipt,
                )
                for reference in exact_references
            )
        except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
            return (None,) * len(references), empty_snapshot
        return resolved, provenance

    def _resolve_from_snapshot(
        self,
        reference: CapabilityProfileReceiptRef | CohortPlanReceiptRef,
        transaction: StoredTransaction | None,
        audits_by_receipt: dict[tuple[str, str], list[AuditEvent]],
    ) -> Proposal | None:
        if (
            transaction is None
            or not transaction.decision.accepted
            or transaction.proposal_hash != reference.proposal_hash
        ):
            return None
        matches = tuple(
            event
            for event in audits_by_receipt.get(
                (reference.audit_event_id, reference.audit_event_hash), ()
            )
            if _audit_matches_transaction(event, transaction, self._active_policy.policy_hash)
        )
        return transaction.proposal if len(matches) == 1 else None


def _fresh_cognitive_receipt(
    reference: CapabilityProfileReceiptRef | CohortPlanReceiptRef,
) -> CapabilityProfileReceiptRef | CohortPlanReceiptRef:
    model_type = type(reference)
    if model_type not in (CapabilityProfileReceiptRef, CohortPlanReceiptRef):
        raise TypeError("unsupported cognitive receipt type")
    state = object.__getattribute__(reference, "__dict__")
    fields = type.__getattribute__(model_type, "model_fields")
    if type(state) is not dict or set(state) != set(fields):
        raise TypeError("cognitive receipt state does not match schema")
    return model_type.model_validate(dict(state), strict=True)


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

    def resolve_profile_receipts(
        self,
        references: tuple[CapabilityProfileReceiptRef, ...],
    ) -> tuple[CapabilityProfile | None, ...]:
        proposals, provenance = self.receipts.resolve_many(references)
        retained = self.profiles.get_many_with_provenance(
            tuple(
                proposal.profile.profile_id
                for proposal in proposals
                if isinstance(proposal, RecordCapabilityProfile)
            ),
            provenance,
        )
        retained_by_id = {profile.profile_id: profile for profile in retained}
        return tuple(
            retained_by_id.get(proposal.profile.profile_id)
            if isinstance(proposal, RecordCapabilityProfile)
            and retained_by_id.get(proposal.profile.profile_id) == proposal.profile
            else None
            for proposal in proposals
        )

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

    def resolve_source_receipts(
        self,
        cohort_reference: CohortPlanReceiptRef,
        profile_references: tuple[CapabilityProfileReceiptRef, ...],
    ) -> tuple[CohortPlan | None, tuple[CapabilityProfile | None, ...]]:
        proposals, provenance = self.receipts.resolve_many((cohort_reference, *profile_references))
        cohort_proposal, *profile_proposals = proposals
        retained_cohorts = self.plans.get_many_with_provenance(
            (
                (cohort_proposal.plan.cohort_plan_id,)
                if isinstance(cohort_proposal, RecordCohortPlan)
                else ()
            ),
            provenance,
        )
        resolved_cohort = (
            retained_cohorts[0]
            if isinstance(cohort_proposal, RecordCohortPlan)
            and len(retained_cohorts) == 1
            and retained_cohorts[0] == cohort_proposal.plan
            else None
        )
        retained_profiles = self.profiles.get_many_with_provenance(
            tuple(
                proposal.profile.profile_id
                for proposal in profile_proposals
                if isinstance(proposal, RecordCapabilityProfile)
            ),
            provenance,
        )
        retained_profiles_by_id = {profile.profile_id: profile for profile in retained_profiles}
        resolved_profiles = tuple(
            retained_profiles_by_id.get(proposal.profile.profile_id)
            if isinstance(proposal, RecordCapabilityProfile)
            and retained_profiles_by_id.get(proposal.profile.profile_id) == proposal.profile
            else None
            for proposal in profile_proposals
        )
        return resolved_cohort, resolved_profiles

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
