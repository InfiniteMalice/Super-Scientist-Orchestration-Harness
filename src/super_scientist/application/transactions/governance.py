from __future__ import annotations

from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.application.transactions.contracts import (
    HandlerReadCapability,
    HandlerWriteCapability,
)
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import GovernancePolicyV1, GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.identity import ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
    is_authoritative_verification,
)
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
    usage_within_budget,
)
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    ProposeGovernancePolicyTransition,
    RejectionCode,
    TransactionDecision,
)


class GovernanceTransitionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_policy(self, policy_hash_value: str) -> PolicySnapshot | None: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None: ...

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None: ...


class _GovernanceTransitionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    prior_policy: PolicySnapshot | None
    rollback_policy: PolicySnapshot | None
    stored_candidate: PolicySnapshot | None
    existing_run: ResearchRun | None
    existing_audit: EvaluatorAuditRecord | None
    existing_measurement: SelfImprovementMeasurementRecord | None


class ProposeGovernancePolicyTransitionHandler:
    proposal_type = "propose_governance_policy_transition"

    def build_context(
        self,
        proposal: ProposeGovernancePolicyTransition,
        reads: HandlerReadCapability,
    ) -> _GovernanceTransitionContext:
        capability = cast(GovernanceTransitionReadCapability, reads)
        return _GovernanceTransitionContext(
            active_policy=capability.policy_snapshot(),
            prior_policy=capability.get_policy(proposal.prior_policy_hash),
            rollback_policy=capability.get_policy(proposal.rollback_policy_hash),
            stored_candidate=capability.get_policy(proposal.candidate_policy_snapshot.policy_hash),
            existing_run=capability.get_run(proposal.research_run.run_id),
            existing_audit=capability.get_evaluator_audit(
                proposal.evaluator_audit.evaluator_audit_id
            ),
            existing_measurement=capability.get_measurement(proposal.measurement.measurement_id),
        )

    def decide(
        self,
        proposal: ProposeGovernancePolicyTransition,
        context: _GovernanceTransitionContext,
    ) -> TransactionDecision:
        identity_rejection = _constitutional_identity_rejection(proposal)
        if identity_rejection is not None:
            return identity_rejection
        classification_rejection = _constitutional_classification_rejection(proposal)
        if classification_rejection is not None:
            return classification_rejection
        if (
            context.active_policy.policy_hash != proposal.prior_policy_hash
            or context.prior_policy != context.active_policy
        ):
            return _rejected(
                proposal,
                RejectionCode.POLICY_HASH_MISMATCH,
                "transition prior hash must be the stored active policy",
            )
        active_requirement_rejection = _active_policy_requirement_rejection(
            proposal,
            context,
        )
        if active_requirement_rejection is not None:
            return active_requirement_rejection
        if context.rollback_policy is None:
            return _rejected(
                proposal,
                RejectionCode.INVALID_LINEAGE,
                "transition rollback policy must already exist",
            )
        if policy_hash(proposal.candidate_policy_snapshot.policy) != (
            proposal.candidate_policy_snapshot.policy_hash
        ):
            return _rejected(
                proposal,
                RejectionCode.POLICY_HASH_MISMATCH,
                "candidate policy hash does not match its exact versioned payload",
            )
        compatibility_rejection = _candidate_compatibility_rejection(proposal, context)
        if compatibility_rejection is not None:
            return compatibility_rejection
        if any(
            record is not None
            for record in (
                context.existing_run,
                context.existing_audit,
                context.existing_measurement,
            )
        ):
            return _rejected(
                proposal,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "transition bootstrap identities must be new",
            )
        measurement_rejection = _measurement_rejection(proposal)
        if measurement_rejection is not None:
            return measurement_rejection
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ProposeGovernancePolicyTransition,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        if not decision.accepted:
            raise ValueError("rejected proposals cannot be projected")
        # Migration 0002 FKs require this exact order. Transaction/audit remain coordinator-owned.
        writes.append_authoritative(proposal.research_run)
        writes.append_authoritative(proposal.evaluator_audit)
        writes.append_authoritative(proposal.measurement)
        writes.update_projection(proposal.candidate_policy_snapshot)


def _constitutional_identity_rejection(
    proposal: ProposeGovernancePolicyTransition,
) -> TransactionDecision | None:
    approval = proposal.approval
    if (
        approval is None
        or approval.approver.kind is not ActorKind.HUMAN
        or not are_independent(proposal.proposer, approval.approver)
    ):
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "constitutional policy transition requires independent human approval",
        )
    if proposal.research_run.creator != proposal.proposer:
        return _rejected(
            proposal,
            RejectionCode.ENTITY_ID_MISMATCH,
            "transition research-run creator must match proposer",
        )
    if proposal.evaluator_audit.proposer != proposal.proposer:
        return _rejected(
            proposal,
            RejectionCode.ENTITY_ID_MISMATCH,
            "evaluator audit proposer must match transition proposer",
        )
    if proposal.measurement.proposer != proposal.proposer:
        return _rejected(
            proposal,
            RejectionCode.ENTITY_ID_MISMATCH,
            "measurement proposer must match transition proposer",
        )
    if proposal.measurement.decision_authority != approval.approver:
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "independent human approver must be the measurement decision authority",
        )
    return None


def _constitutional_classification_rejection(
    proposal: ProposeGovernancePolicyTransition,
) -> TransactionDecision | None:
    classification = proposal.classification
    if (
        classification.target is not ChangeTarget.GOVERNANCE_POLICY
        or classification.persistence is not PersistenceScope.GOVERNANCE_POLICY
    ):
        return _rejected(
            proposal,
            RejectionCode.PERMISSION_DENIED,
            "constitutional transition requires governance-policy classification",
        )
    if classification.loop_closure is LoopClosure.CLOSED_LOOP:
        return _rejected(
            proposal,
            RejectionCode.PROHIBITED_CLOSED_LOOP,
            "closed-loop governance-policy transition is prohibited",
        )
    if classification.verification_level not in {
        VerificationLevel.FORMAL_VERIFIER,
        VerificationLevel.EXTERNAL_EMPIRICAL_MEASUREMENT,
        VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    }:
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "governance transition requires independent deterministic-or-stronger verification",
        )
    if classification.grounding is ExternalGrounding.NONE:
        return _rejected(
            proposal,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "governance transition requires external grounding",
        )
    if classification.signal not in {
        ImprovementSignal.EMPIRICAL_MEASUREMENT,
        ImprovementSignal.FORMAL_VERIFICATION,
        ImprovementSignal.HUMAN_CORRECTION,
    }:
        return _rejected(
            proposal,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "governance transition signal is not constitutional evidence",
        )
    return None


def _active_policy_requirement_rejection(
    proposal: ProposeGovernancePolicyTransition,
    context: _GovernanceTransitionContext,
) -> TransactionDecision | None:
    policy = context.active_policy.policy
    if isinstance(policy, GovernancePolicyV1):
        return None
    classification = proposal.classification
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is classification.target
            and item.persistence is classification.persistence
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal,
            RejectionCode.PERMISSION_DENIED,
            "active policy has no matching governance-transition requirement",
        )
    if _verification_rank(classification.verification_level) < _verification_rank(
        requirement.minimum_verification
    ):
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "transition does not meet the active policy verification requirement",
        )
    if classification.grounding not in requirement.permitted_grounding:
        return _rejected(
            proposal,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "transition grounding is not permitted by the active policy",
        )
    approval = proposal.approval
    if approval is None or approval.approver.kind is not requirement.required_approver_kind:
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "transition approver kind does not satisfy the active policy",
        )
    if requirement.protected_evaluation_required and (
        not proposal.measurement.protected_metrics
        or not all(metric.protected for metric in proposal.measurement.protected_metrics)
    ):
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "transition lacks the protected evaluation required by the active policy",
        )
    if requirement.rollback_required and (
        context.rollback_policy is None
        or proposal.measurement.rollback_target_id != proposal.rollback_policy_hash
    ):
        return _rejected(
            proposal,
            RejectionCode.INVALID_LINEAGE,
            "transition lacks the rollback lineage required by the active policy",
        )
    return None


def _candidate_compatibility_rejection(
    proposal: ProposeGovernancePolicyTransition,
    context: _GovernanceTransitionContext,
) -> TransactionDecision | None:
    prior = context.active_policy.policy
    candidate = proposal.candidate_policy_snapshot.policy
    if isinstance(prior, GovernancePolicyV1) and not isinstance(candidate, GovernancePolicyV2):
        return _rejected(
            proposal,
            RejectionCode.INVALID_LINEAGE,
            "V1 bootstrap transition requires an exact GovernancePolicyV2 candidate",
        )
    if not set(prior.required_claim_checks).issubset(candidate.required_claim_checks):
        return _rejected(
            proposal,
            RejectionCode.PERMISSION_DENIED,
            "candidate policy cannot weaken required claim checks",
        )
    if "governance_change" not in candidate.human_approval_for:
        return _rejected(
            proposal,
            RejectionCode.PERMISSION_DENIED,
            "candidate policy must retain human approval for governance changes",
        )
    if isinstance(candidate, GovernancePolicyV2):
        requirement = next(
            (
                item
                for item in candidate.adaptation_requirements
                if item.change_target is ChangeTarget.GOVERNANCE_POLICY
                and item.persistence is PersistenceScope.GOVERNANCE_POLICY
            ),
            None,
        )
        if (
            requirement is None
            or requirement.required_approver_kind is not ActorKind.HUMAN
            or not requirement.protected_evaluation_required
            or not requirement.rollback_required
            or requirement.minimum_verification
            not in {
                VerificationLevel.FORMAL_VERIFIER,
                VerificationLevel.EXTERNAL_EMPIRICAL_MEASUREMENT,
                VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            }
            or ExternalGrounding.NONE in requirement.permitted_grounding
        ):
            return _rejected(
                proposal,
                RejectionCode.PERMISSION_DENIED,
                "candidate governance requirement cannot weaken constitutional safeguards",
            )
    if context.stored_candidate is not None and context.stored_candidate != (
        proposal.candidate_policy_snapshot
    ):
        return _rejected(
            proposal,
            RejectionCode.POLICY_HASH_MISMATCH,
            "stored candidate policy does not match proposal snapshot",
        )
    return None


def _verification_rank(level: VerificationLevel) -> int:
    ranks = {
        VerificationLevel.MODEL_LIKELIHOOD: 0,
        VerificationLevel.MODEL_CONFIDENCE: 0,
        VerificationLevel.SELF_CONSISTENCY: 0,
        VerificationLevel.SELF_CRITIQUE: 1,
        VerificationLevel.CROSS_MODEL_AGREEMENT: 1,
        VerificationLevel.RUBRIC_JUDGE: 2,
        VerificationLevel.INDEPENDENT_LEARNED_JUDGE: 3,
        VerificationLevel.EXECUTION_FEEDBACK: 4,
        VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK: 5,
        VerificationLevel.EXTERNAL_EMPIRICAL_MEASUREMENT: 6,
        VerificationLevel.FORMAL_VERIFIER: 7,
    }
    return ranks[level]


def _measurement_rejection(
    proposal: ProposeGovernancePolicyTransition,
) -> TransactionDecision | None:
    run = proposal.research_run
    audit = proposal.evaluator_audit
    measurement = proposal.measurement
    prior_hash = proposal.prior_policy_hash
    candidate_hash = proposal.candidate_policy_snapshot.policy_hash
    if (
        run.active_governance_policy_hash != prior_hash
        or audit.governing_policy_hash != prior_hash
        or measurement.governing_policy_hash != prior_hash
    ):
        return _rejected(
            proposal,
            RejectionCode.POLICY_HASH_MISMATCH,
            "bootstrap run, audit, and measurement must be governed by the prior policy",
        )
    if audit.result is not AssessmentOutcome.PASSED or not is_authoritative_verification(
        audit.auditor_category
    ):
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "governance transition requires a passed independent evaluator audit",
        )
    if (
        measurement.run_id != run.run_id
        or measurement.evaluator_audit_id != audit.evaluator_audit_id
        or measurement.evaluator != audit.evaluator
        or measurement.evaluator_version != audit.evaluator_version
    ):
        return _rejected(
            proposal,
            RejectionCode.INVALID_LINEAGE,
            "measurement lineage must bind the dedicated run and evaluator audit",
        )
    if measurement.classification != proposal.classification:
        return _rejected(
            proposal,
            RejectionCode.INVALID_LINEAGE,
            "measurement and transition classifications must match exactly",
        )
    if (
        measurement.baseline_version_id != prior_hash
        or measurement.candidate_version_id != candidate_hash
        or measurement.rollback_target_id != proposal.rollback_policy_hash
    ):
        return _rejected(
            proposal,
            RejectionCode.INVALID_LINEAGE,
            "measurement must bind prior, candidate, and rollback policy hashes",
        )
    if measurement.decision is not MeasurementDecision.ACCEPTED:
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "governance transition requires an accepted complete measurement",
        )
    if not measurement.protected_metrics or not all(
        metric.protected and metric.external for metric in measurement.protected_metrics
    ):
        return _rejected(
            proposal,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "governance transition requires protected external metrics",
        )
    if not _usage_within_budgets(measurement, run):
        return _rejected(
            proposal,
            RejectionCode.UNMATCHED_BUDGETS,
            "measurement usage exceeds declared separate budgets",
        )
    return None


def _usage_within_budgets(
    measurement: SelfImprovementMeasurementRecord,
    run: ResearchRun,
) -> bool:
    allocations = (
        (
            measurement.usage_by_category.execution,
            measurement.execution_budget,
            run.budget_allocation.execution,
        ),
        (
            measurement.usage_by_category.search,
            measurement.search_budget,
            run.budget_allocation.search,
        ),
        (
            measurement.usage_by_category.evaluation,
            measurement.evaluation_budget,
            run.budget_allocation.evaluation,
        ),
        (
            measurement.usage_by_category.judging,
            measurement.judging_budget,
            run.budget_allocation.judging,
        ),
        (
            measurement.usage_by_category.human,
            measurement.human_budget,
            run.budget_allocation.human,
        ),
    )
    return all(
        usage_within_budget(usage, measurement_budget) and usage_within_budget(usage, run_budget)
        for usage, measurement_budget, run_budget in allocations
    )


def _rejected(
    proposal: ProposeGovernancePolicyTransition,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal.proposal_id, code, message)
