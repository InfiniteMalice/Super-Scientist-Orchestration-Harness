from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CohortPlan,
    CohortPlanReceiptRef,
    DiversityAssessment,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.identity import are_independent as identities_are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    GovernedProposalBase,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordDiversityAssessment,
    RejectionCode,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )


class CapabilityProfileReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_profile(self, profile_id: str) -> CapabilityProfile | None: ...


class CohortPlanReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None: ...

    def resolve_profile_receipts(
        self,
        references: tuple[CapabilityProfileReceiptRef, ...],
    ) -> tuple[CapabilityProfile | None, ...]: ...


class DiversityAssessmentReadCapability(CohortPlanReadCapability, Protocol):
    def get_diversity_assessment(
        self,
        diversity_assessment_id: str,
    ) -> DiversityAssessment | None: ...

    def resolve_source_receipts(
        self,
        cohort_reference: CohortPlanReceiptRef,
        profile_references: tuple[CapabilityProfileReceiptRef, ...],
    ) -> tuple[CohortPlan | None, tuple[CapabilityProfile | None, ...]]: ...


class _CapabilityProfileContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing_profile: CapabilityProfile | None


class _CohortPlanContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing_plan: CohortPlan | None
    duplicate_receipts: bool = False
    resolved_profiles: tuple[CapabilityProfile | None, ...]


class _DiversityAssessmentContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing_assessment: DiversityAssessment | None
    duplicate_receipts: bool = False
    resolved_cohort: CohortPlan | None
    resolved_profiles: tuple[CapabilityProfile | None, ...]


class RecordCapabilityProfileHandler:
    proposal_type = "record_capability_profile"

    def build_context(
        self,
        proposal: RecordCapabilityProfile,
        reads: HandlerReadCapability,
    ) -> _CapabilityProfileContext:
        capability = cast(CapabilityProfileReadCapability, reads)
        return _CapabilityProfileContext(
            active_policy=capability.policy_snapshot(),
            existing_profile=capability.get_profile(proposal.profile.profile_id),
        )

    def decide(
        self,
        proposal: RecordCapabilityProfile,
        context: _CapabilityProfileContext,
    ) -> TransactionDecision:
        authority = governed_cognitive_authority_rejection(proposal, context.active_policy)
        if authority is not None:
            return authority
        if proposal.profile.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "capability profile must name the exact active governance policy",
            )
        if not _profile_is_canonical(proposal.profile):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "capability profile derived fields do not match canonical inputs",
            )
        if context.existing_profile is not None:
            return _already_exists(proposal.proposal_id, "capability profile")
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: RecordCapabilityProfile,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.profile)


class RecordCohortPlanHandler:
    proposal_type = "record_cohort_plan"

    def build_context(
        self,
        proposal: RecordCohortPlan,
        reads: HandlerReadCapability,
    ) -> _CohortPlanContext:
        capability = cast(CohortPlanReadCapability, reads)
        duplicate_receipts = len(
            {reference.proposal_id for reference in proposal.profile_receipts}
        ) != len(proposal.profile_receipts)
        return _CohortPlanContext(
            active_policy=capability.policy_snapshot(),
            existing_plan=(
                None
                if duplicate_receipts
                else capability.get_cohort_plan(proposal.plan.cohort_plan_id)
            ),
            duplicate_receipts=duplicate_receipts,
            resolved_profiles=(
                ()
                if duplicate_receipts
                else capability.resolve_profile_receipts(proposal.profile_receipts)
            ),
        )

    def decide(
        self,
        proposal: RecordCohortPlan,
        context: _CohortPlanContext,
    ) -> TransactionDecision:
        authority = governed_cognitive_authority_rejection(proposal, context.active_policy)
        if authority is not None:
            return authority
        if (
            proposal.request.governing_policy_hash != context.active_policy.policy_hash
            or proposal.plan.governing_policy_hash != context.active_policy.policy_hash
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "cohort request and plan must name the exact active governance policy",
            )
        duplicate_receipts = len(
            {reference.proposal_id for reference in proposal.profile_receipts}
        ) != len(proposal.profile_receipts)
        if (
            context.duplicate_receipts
            or duplicate_receipts
            or any(profile is None for profile in context.resolved_profiles)
        ):
            return _stale_reference(proposal.proposal_id, "capability profile receipt")
        resolved = cast(tuple[CapabilityProfile, ...], context.resolved_profiles)
        try:
            expected = build_cohort(proposal.request, resolved)
        except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "cohort inputs cannot produce the claimed canonical plan",
            )
        if expected != proposal.plan:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "cohort plan does not match recomputed grounded selection",
            )
        if context.existing_plan is not None:
            return _already_exists(proposal.proposal_id, "cohort plan")
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: RecordCohortPlan,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.plan)


class RecordDiversityAssessmentHandler:
    proposal_type = "record_diversity_assessment"

    def build_context(
        self,
        proposal: RecordDiversityAssessment,
        reads: HandlerReadCapability,
    ) -> _DiversityAssessmentContext:
        capability = cast(DiversityAssessmentReadCapability, reads)
        references = (
            proposal.cohort_plan_receipt.proposal_id,
            *(reference.proposal_id for reference in proposal.profile_receipts),
        )
        duplicate_receipts = len(set(references)) != len(references)
        resolved_cohort: CohortPlan | None = None
        resolved_profiles: tuple[CapabilityProfile | None, ...] = ()
        if not duplicate_receipts:
            resolved_cohort, resolved_profiles = capability.resolve_source_receipts(
                proposal.cohort_plan_receipt,
                proposal.profile_receipts,
            )
        return _DiversityAssessmentContext(
            active_policy=capability.policy_snapshot(),
            existing_assessment=(
                None
                if duplicate_receipts
                else capability.get_diversity_assessment(
                    proposal.assessment.diversity_assessment_id
                )
            ),
            duplicate_receipts=duplicate_receipts,
            resolved_cohort=resolved_cohort,
            resolved_profiles=resolved_profiles,
        )

    def decide(
        self,
        proposal: RecordDiversityAssessment,
        context: _DiversityAssessmentContext,
    ) -> TransactionDecision:
        authority = governed_cognitive_authority_rejection(proposal, context.active_policy)
        if authority is not None:
            return authority
        if proposal.assessment.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "diversity assessment must name the exact active governance policy",
            )
        references = (
            proposal.cohort_plan_receipt.proposal_id,
            *(reference.proposal_id for reference in proposal.profile_receipts),
        )
        duplicate_receipts = len(set(references)) != len(references)
        if (
            context.duplicate_receipts
            or duplicate_receipts
            or context.resolved_cohort is None
            or any(profile is None for profile in context.resolved_profiles)
        ):
            return _stale_reference(proposal.proposal_id, "diversity source receipt")
        profiles = cast(tuple[CapabilityProfile, ...], context.resolved_profiles)
        try:
            expected = assess_diversity(
                context.resolved_cohort,
                profiles,
                proposal.error_correlations,
            )
        except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "diversity inputs cannot produce the claimed canonical assessment",
            )
        if expected != proposal.assessment:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.DERIVATION_MISMATCH,
                "diversity assessment does not match recomputed operational evidence",
            )
        if context.existing_assessment is not None:
            return _already_exists(proposal.proposal_id, "diversity assessment")
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: RecordDiversityAssessment,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.assessment)


def governed_cognitive_authority_rejection(
    proposal: GovernedProposalBase,
    snapshot: PolicySnapshot,
) -> TransactionDecision | None:
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "governed cognitive records require an active governance policy V2",
        )
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is ChangeTarget.RESEARCH_PROCESS
            and item.persistence is PersistenceScope.RUN_LOCAL
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy does not govern run-local research-process records",
        )
    if (
        requirement.minimum_verification is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or ExternalGrounding.HUMAN_JUDGMENT not in requirement.permitted_grounding
        or requirement.protected_evaluation_required
        or requirement.rollback_required
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "cognitive admission does not satisfy the active policy requirement",
        )
    approval = proposal.approval
    if (
        approval is None
        or approval.approver.kind is not requirement.required_approver_kind
        or not identities_are_independent(proposal.proposer, approval.approver)
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "cognitive mutation requires independent policy-matched approval",
        )
    if requirement.required_approver_kind is not ActorKind.HUMAN:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "cognitive durable authority is human only",
        )
    return None


def _profile_is_canonical(profile: CapabilityProfile) -> bool:
    try:
        rebuilt = CapabilityProfile.build(
            **profile.model_dump(mode="python", exclude={"content_hash"}, warnings=False)
        )
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return False
    return rebuilt == profile


def _accepted(proposal_id: str) -> TransactionDecision:
    return TransactionDecision(proposal_id=proposal_id, accepted=True)


def _already_exists(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.ENTITY_ALREADY_EXISTS,
        f"{label} already exists",
    )


def _stale_reference(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.STALE_REFERENCE,
        f"{label} does not resolve to exact current accepted state",
    )


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")


__all__ = [
    "CapabilityProfileReadCapability",
    "CohortPlanReadCapability",
    "DiversityAssessmentReadCapability",
    "RecordCapabilityProfileHandler",
    "RecordCohortPlanHandler",
    "RecordDiversityAssessmentHandler",
    "governed_cognitive_authority_rejection",
]
