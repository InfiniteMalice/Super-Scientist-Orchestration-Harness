from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
    is_authoritative_verification,
)
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    ChangeClassification,
    EvaluatorAuditRecord,
    MeasurementDecision,
    SelfImprovementMeasurementRecord,
    usage_within_budget,
)
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    Approval,
    DecideEvaluatorSuccession,
    ProposeEvaluatorVersion,
    RecordConfigurationVersion,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
    RejectionCode,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )

_PROHIBITED_OPERATIONS = MappingProxyType(
    {
        "adapter_self_promotion": RejectionCode.PERMISSION_DENIED,
        "rule_proposer_self_approval": RejectionCode.PERMISSION_DENIED,
        "harness_optimizer_altering_evaluation": RejectionCode.PERMISSION_DENIED,
        "evaluator_threshold_rewrite": RejectionCode.PROHIBITED_CLOSED_LOOP,
        "automatic_evaluator_replacement": RejectionCode.PROHIBITED_CLOSED_LOOP,
        "protected_holdout_access": RejectionCode.PROTECTED_DATA_ACCESS,
        "failed_experiment_omission": RejectionCode.PERMISSION_DENIED,
        "self_declared_independent_verification": RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        "closed_loop_governance": RejectionCode.PROHIBITED_CLOSED_LOOP,
        "closed_loop_quality_gate": RejectionCode.PROHIBITED_CLOSED_LOOP,
        "direct_rule_edit": RejectionCode.PERMISSION_DENIED,
        "benchmark_specific_admission": RejectionCode.BENCHMARK_SPECIFIC_ADMISSION,
        "false_finish": RejectionCode.FALSE_FINISH,
        "summary_for_raw_evidence": RejectionCode.MISSING_EVIDENCE,
        "confidence_as_evidence": RejectionCode.INSUFFICIENT_GROUNDING,
        "likelihood_as_evidence": RejectionCode.INSUFFICIENT_GROUNDING,
        "self_consistency_as_evidence": RejectionCode.INSUFFICIENT_GROUNDING,
        "textual_agreement_as_evidence": RejectionCode.INSUFFICIENT_GROUNDING,
    }
)


class AdaptationAuthority:
    """Source-controlled constitutional denials; this service grants no mutation authority."""

    def attempt(self, operation: str, *, proposal_id: str) -> TransactionDecision:
        code = _PROHIBITED_OPERATIONS.get(operation)
        if code is None:
            return AdmissionEngine.rejected(
                proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "adaptive operation is not source-controlled and has no ambient authority",
            )
        return AdmissionEngine.rejected(
            proposal_id,
            code,
            f"adaptive operation is constitutionally prohibited: {operation}",
        )


class ConfigurationReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_configuration(self, configuration_version_id: str) -> ConfigurationVersion | None: ...


class EvaluatorAuditReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None: ...


class MeasurementReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None: ...


class EvaluatorVersionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_evaluator_version(self, evaluator_version_id: str) -> EvaluatorVersion | None: ...

    def measurements_for_candidate(
        self,
        candidate_version_id: str,
    ) -> tuple[SelfImprovementMeasurementRecord, ...]: ...

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None: ...


class EvaluatorSuccessionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_evaluator_version(self, evaluator_version_id: str) -> EvaluatorVersion | None: ...

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None: ...

    def get_succession_decision(
        self,
        decision_id: str,
    ) -> EvaluatorSuccessionDecision | None: ...

    def active_evaluator_version_id(self) -> str | None: ...


class _ConfigurationContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: ConfigurationVersion | None
    predecessor: ConfigurationVersion | None
    rollback: ConfigurationVersion | None


class _AuditContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: EvaluatorAuditRecord | None


class _MeasurementContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: SelfImprovementMeasurementRecord | None
    run: ResearchRun | None
    evaluator_audit: EvaluatorAuditRecord | None


class _EvaluatorVersionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: EvaluatorVersion | None
    predecessor: EvaluatorVersion | None
    candidate_measurements: tuple[SelfImprovementMeasurementRecord, ...]
    evaluator_audits: tuple[EvaluatorAuditRecord, ...]


class _SuccessionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing: EvaluatorSuccessionDecision | None
    predecessor: EvaluatorVersion | None
    candidate: EvaluatorVersion | None
    evaluator_audit: EvaluatorAuditRecord | None
    active_evaluator_version_id: str | None


class RecordConfigurationVersionHandler:
    proposal_type = "record_configuration_version"

    def build_context(
        self,
        proposal: RecordConfigurationVersion,
        reads: HandlerReadCapability,
    ) -> _ConfigurationContext:
        capability = cast(ConfigurationReadCapability, reads)
        record = proposal.configuration_version
        predecessor_id = record.predecessor_configuration_version_id
        rollback_id = record.rollback_configuration_version_id
        return _ConfigurationContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_configuration(record.configuration_version_id),
            predecessor=(
                None if predecessor_id is None else capability.get_configuration(predecessor_id)
            ),
            rollback=(
                None
                if rollback_id == record.configuration_version_id
                else capability.get_configuration(rollback_id)
            ),
        )

    def decide(
        self,
        proposal: RecordConfigurationVersion,
        context: _ConfigurationContext,
    ) -> TransactionDecision:
        record = proposal.configuration_version
        rejection = _adaptation_requirement_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            proposal.classification,
            context.active_policy,
            protected_evaluation=False,
            rollback_present=bool(record.rollback_configuration_version_id),
        )
        if rejection is not None:
            return rejection
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "configuration version already exists",
            )
        if record.created_by != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "configuration author must match proposer",
            )
        if record.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "configuration must name the active policy",
            )
        if record.predecessor_configuration_version_id is not None and context.predecessor is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "configuration predecessor does not exist",
            )
        if (
            record.rollback_configuration_version_id != record.configuration_version_id
            and context.rollback is None
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "configuration rollback target does not exist",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordConfigurationVersion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.configuration_version)


class RecordEvaluatorAuditHandler:
    proposal_type = "record_evaluator_audit"

    def build_context(
        self,
        proposal: RecordEvaluatorAudit,
        reads: HandlerReadCapability,
    ) -> _AuditContext:
        capability = cast(EvaluatorAuditReadCapability, reads)
        return _AuditContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_evaluator_audit(proposal.evaluator_audit.evaluator_audit_id),
        )

    def decide(
        self,
        proposal: RecordEvaluatorAudit,
        context: _AuditContext,
    ) -> TransactionDecision:
        rejection = _support_record_authority_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            context.active_policy,
        )
        if rejection is not None:
            return rejection
        audit = proposal.evaluator_audit
        if not is_authoritative_verification(audit.auditor_category):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "evaluator audit category is not authoritative evidence",
            )
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "evaluator audit already exists",
            )
        if audit.auditor != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "audit actor must match proposer",
            )
        if audit.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "audit must name the active policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordEvaluatorAudit,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.evaluator_audit)


class RecordSelfImprovementMeasurementHandler:
    proposal_type = "record_self_improvement_measurement"

    def build_context(
        self,
        proposal: RecordSelfImprovementMeasurement,
        reads: HandlerReadCapability,
    ) -> _MeasurementContext:
        capability = cast(MeasurementReadCapability, reads)
        record = proposal.measurement
        return _MeasurementContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_measurement(record.measurement_id),
            run=capability.get_run(record.run_id),
            evaluator_audit=capability.get_evaluator_audit(record.evaluator_audit_id),
        )

    def decide(
        self,
        proposal: RecordSelfImprovementMeasurement,
        context: _MeasurementContext,
    ) -> TransactionDecision:
        measurement = proposal.measurement
        rejection = _adaptation_requirement_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            measurement.classification,
            context.active_policy,
            protected_evaluation=bool(measurement.protected_metrics),
            rollback_present=bool(measurement.rollback_target_id),
        )
        if rejection is not None:
            return rejection
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "measurement already exists",
            )
        if context.run is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "measurement research run does not exist",
            )
        audit = context.evaluator_audit
        if (
            audit is None
            or audit.result is not AssessmentOutcome.PASSED
            or not is_authoritative_verification(audit.auditor_category)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "durable measurement requires a passed independent evaluator audit",
            )
        if not _audit_matches_measurement(audit, measurement):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "evaluator audit does not bind the measured evaluator, version, and proposer",
            )
        if measurement.proposer != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "measurement proposer must match proposal proposer",
            )
        if (
            proposal.approval is None
            or proposal.approval.approver != measurement.decision_authority
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "measurement authority must supply the independent approval",
            )
        if measurement.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "measurement must name the active policy",
            )
        if not _usage_within_declared_budgets(measurement, context.run):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNMATCHED_BUDGETS,
                "measurement usage exceeds declared separate budgets",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordSelfImprovementMeasurement,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.measurement)


class ProposeEvaluatorVersionHandler:
    proposal_type = "propose_evaluator_version"

    def build_context(
        self,
        proposal: ProposeEvaluatorVersion,
        reads: HandlerReadCapability,
    ) -> _EvaluatorVersionContext:
        capability = cast(EvaluatorVersionReadCapability, reads)
        record = proposal.evaluator_version
        predecessor_id = record.predecessor_evaluator_version_id
        measurements = capability.measurements_for_candidate(record.evaluator_version_id)
        audits = tuple(
            audit
            for measurement in measurements
            if (audit := capability.get_evaluator_audit(measurement.evaluator_audit_id)) is not None
        )
        return _EvaluatorVersionContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_evaluator_version(record.evaluator_version_id),
            predecessor=(
                None if predecessor_id is None else capability.get_evaluator_version(predecessor_id)
            ),
            candidate_measurements=measurements,
            evaluator_audits=audits,
        )

    def decide(
        self,
        proposal: ProposeEvaluatorVersion,
        context: _EvaluatorVersionContext,
    ) -> TransactionDecision:
        record = proposal.evaluator_version
        classification_rejection = _evaluator_policy_classification_rejection(
            proposal.proposal_id,
            proposal.classification,
        )
        if classification_rejection is not None:
            return classification_rejection
        is_root = record.predecessor_evaluator_version_id is None
        candidate_measurements = tuple(
            measurement
            for measurement in context.candidate_measurements
            if _measurement_binds_evaluator_candidate(
                measurement,
                record,
                context.active_policy.policy_hash,
                proposal.classification,
            )
        )
        candidate_measurement_present = bool(candidate_measurements)
        linked_audits = tuple(
            audit
            for measurement in candidate_measurements
            for audit in context.evaluator_audits
            if (
                _audit_matches_measurement(audit, measurement)
                and audit.candidate_producer == record.candidate_producer
            )
        )
        passed_measurement = any(
            audit.result is AssessmentOutcome.PASSED
            and is_authoritative_verification(audit.auditor_category)
            for audit in linked_audits
        )
        rejection = _adaptation_requirement_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            proposal.classification,
            context.active_policy,
            protected_evaluation=is_root or candidate_measurement_present,
            rollback_present=is_root or bool(record.rollback_evaluator_version_id),
        )
        if rejection is not None:
            return rejection
        if not passed_measurement and any(
            audit.result is AssessmentOutcome.PASSED for audit in linked_audits
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "candidate evaluator audit category is not authoritative evidence",
            )
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "evaluator version already exists",
            )
        if record.candidate_producer != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "candidate producer must match proposer",
            )
        if record.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "evaluator version must name the active policy",
            )
        if not is_root and context.predecessor is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "evaluator predecessor does not exist",
            )
        if not is_root and not passed_measurement:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.CIRCULAR_EVALUATOR_APPROVAL,
                "candidate lacks a producer-bound passed evaluator audit",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ProposeEvaluatorVersion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.evaluator_version)


class DecideEvaluatorSuccessionHandler:
    proposal_type = "decide_evaluator_succession"

    def build_context(
        self,
        proposal: DecideEvaluatorSuccession,
        reads: HandlerReadCapability,
    ) -> _SuccessionContext:
        capability = cast(EvaluatorSuccessionReadCapability, reads)
        record = proposal.succession_decision
        return _SuccessionContext(
            active_policy=capability.policy_snapshot(),
            existing=capability.get_succession_decision(record.evaluator_succession_decision_id),
            predecessor=capability.get_evaluator_version(record.predecessor_evaluator_version_id),
            candidate=capability.get_evaluator_version(record.candidate_evaluator_version_id),
            evaluator_audit=capability.get_evaluator_audit(record.evaluator_audit_id),
            active_evaluator_version_id=capability.active_evaluator_version_id(),
        )

    def decide(
        self,
        proposal: DecideEvaluatorSuccession,
        context: _SuccessionContext,
    ) -> TransactionDecision:
        record = proposal.succession_decision
        classification_rejection = _evaluator_policy_classification_rejection(
            proposal.proposal_id,
            proposal.classification,
        )
        if classification_rejection is not None:
            return classification_rejection
        rejection = _adaptation_requirement_rejection(
            proposal.proposal_id,
            proposal.proposer,
            proposal.approval,
            proposal.classification,
            context.active_policy,
            protected_evaluation=record.protected_evaluation is not None,
            rollback_present=bool(record.predecessor_rollback_target_id),
        )
        if rejection is not None:
            return rejection
        if context.existing is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "succession decision already exists",
            )
        if context.predecessor is None or context.candidate is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "succession evaluator version does not exist",
            )
        if context.active_evaluator_version_id != record.predecessor_evaluator_version_id:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "succession predecessor is not the active evaluator",
            )
        if (
            context.candidate.predecessor_evaluator_version_id
            != record.predecessor_evaluator_version_id
            or context.candidate.rollback_evaluator_version_id
            != record.predecessor_evaluator_version_id
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "candidate lineage does not preserve predecessor rollback",
            )
        audit = context.evaluator_audit
        if audit is not None and not is_authoritative_verification(audit.auditor_category):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "succession audit category is not authoritative evidence",
            )
        if (
            audit is None
            or audit.result is not AssessmentOutcome.PASSED
            or audit.evaluator != context.candidate.evaluator
            or audit.evaluator_version != context.candidate.evaluator_version_id
            or audit.candidate_producer != context.candidate.candidate_producer
            or audit.governing_policy_hash != context.active_policy.policy_hash
            or context.candidate.governing_policy_hash != context.active_policy.policy_hash
            or record.governing_policy_hash != context.active_policy.policy_hash
            or record.evaluator_audit_result is not audit.result
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.CIRCULAR_EVALUATOR_APPROVAL,
                "candidate lacks a passed independent evaluator audit",
            )
        if (
            record.candidate_producer != context.candidate.candidate_producer
            or record.change_proposer != audit.proposer
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "succession decision does not bind the audited proposer and candidate producer",
            )
        gate_evidence_ids = {
            evidence_id
            for gate in (
                record.protected_evaluation,
                record.external_evaluation,
                record.human_review,
                record.canary_evaluation,
            )
            if gate is not None
            for evidence_id in gate.evidence_ids
        }
        if not gate_evidence_ids or not gate_evidence_ids.issubset(audit.evidence_ids):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "succession gate evidence is unrelated to the independent evaluator audit",
            )
        if record.candidate_evaluator != context.candidate.evaluator:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "succession candidate identity does not match evaluator version",
            )
        if record.decision_authority != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "succession authority must match proposer",
            )
        if record.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "succession must name the active policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: DecideEvaluatorSuccession,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _project_accepted(decision, writes, proposal.succession_decision)
        if proposal.succession_decision.accepted:
            writes.update_projection(proposal.succession_decision)


def _adaptation_requirement_rejection(
    proposal_id: str,
    proposer: ActorIdentity,
    approval: Approval | None,
    classification: ChangeClassification,
    snapshot: PolicySnapshot,
    *,
    protected_evaluation: bool,
    rollback_present: bool,
) -> TransactionDecision | None:
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "new persistent proposal kinds require an active governance policy V2",
        )
    if classification.loop_closure is LoopClosure.CLOSED_LOOP:
        return _rejected(
            proposal_id,
            RejectionCode.PROHIBITED_CLOSED_LOOP,
            "closed-loop durable adaptation is prohibited",
        )
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
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy has no matching persistence-aware adaptation requirement",
        )
    if _verification_rank(classification.verification_level) < _verification_rank(
        requirement.minimum_verification
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "classification does not meet minimum verification authority",
        )
    if (
        classification.grounding is ExternalGrounding.NONE
        or classification.grounding not in requirement.permitted_grounding
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "classification grounding is not permitted by active policy",
        )
    if approval is None or not hasattr(proposer, "actor_id"):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "durable adaptation requires independent approval",
        )
    if approval.approver.kind is not requirement.required_approver_kind or not are_independent(
        proposer, approval.approver
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "approval does not satisfy policy identity and independence",
        )
    if requirement.protected_evaluation_required and not protected_evaluation:
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "protected evaluation required by active policy",
        )
    if requirement.rollback_required and not rollback_present:
        return _rejected(
            proposal_id, RejectionCode.INVALID_LINEAGE, "rollback target required by active policy"
        )
    return None


def _evaluator_policy_classification_rejection(
    proposal_id: str,
    classification: ChangeClassification,
) -> TransactionDecision | None:
    if _is_evaluator_policy_classification(classification):
        return None
    return _rejected(
        proposal_id,
        RejectionCode.PERMISSION_DENIED,
        "evaluator proposals require fixed evaluator-policy classification",
    )


def _is_evaluator_policy_classification(classification: ChangeClassification) -> bool:
    return (
        classification.target is ChangeTarget.EVALUATOR
        and classification.persistence is PersistenceScope.EVALUATOR_POLICY
    )


def _measurement_binds_evaluator_candidate(
    measurement: SelfImprovementMeasurementRecord,
    candidate: EvaluatorVersion,
    active_policy_hash: str,
    proposal_classification: ChangeClassification,
) -> bool:
    return (
        measurement.decision is MeasurementDecision.ACCEPTED
        and measurement.classification == proposal_classification
        and measurement.evaluator == candidate.evaluator
        and measurement.evaluator_version == candidate.evaluator_version_id
        and measurement.governing_policy_hash == active_policy_hash
    )


def _support_record_authority_rejection(
    proposal_id: str,
    proposer: ActorIdentity,
    approval: Approval | None,
    snapshot: PolicySnapshot,
) -> TransactionDecision | None:
    if not isinstance(snapshot.policy, GovernancePolicyV2):
        return _rejected(
            proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "new persistent proposal kinds require an active governance policy V2",
        )
    if (
        approval is None
        or approval.approver.kind is not ActorKind.HUMAN
        or not hasattr(proposer, "actor_id")
        or not are_independent(proposer, approval.approver)
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "supporting evaluator records require independent human approval",
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


def _usage_within_declared_budgets(
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


def _audit_matches_measurement(
    audit: EvaluatorAuditRecord,
    measurement: SelfImprovementMeasurementRecord,
) -> bool:
    return (
        audit.evaluator_audit_id == measurement.evaluator_audit_id
        and audit.evaluator == measurement.evaluator
        and audit.evaluator_version == measurement.evaluator_version
        and audit.proposer == measurement.proposer
        and audit.governing_policy_hash == measurement.governing_policy_hash
    )


def _project_accepted(
    decision: TransactionDecision,
    writes: HandlerWriteCapability,
    record: BaseModel,
) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")
    writes.append_authoritative(record)


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
