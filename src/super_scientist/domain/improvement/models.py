from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ActorRelationship(StrEnum):
    SAME_ACTOR = "SAME_ACTOR"
    SHARED_MODEL_CONFIGURATION = "SHARED_MODEL_CONFIGURATION"
    ORGANIZATIONAL_DEPENDENCY = "ORGANIZATIONAL_DEPENDENCY"
    UNKNOWN = "UNKNOWN"
    INDEPENDENT = "INDEPENDENT"


class AssessmentOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    ABSTAINED = "ABSTAINED"


class MeasurementDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class ChangeClassification(_StrictFrozenModel):
    target: ChangeTarget
    loop_closure: LoopClosure
    persistence: PersistenceScope
    verification_level: VerificationLevel
    grounding: ExternalGrounding
    signal: ImprovementSignal


class AssessmentProvenance(_StrictFrozenModel):
    actor: ActorIdentity
    actor_version: StableIdentifier
    category: VerificationLevel
    deterministic_or_learned: Literal["DETERMINISTIC", "LEARNED", "HUMAN"]
    proposer_relationship: ActorRelationship
    assumptions: tuple[NonBlankText, ...]
    evidence_ids: tuple[StableIdentifier, ...]
    checks_run: tuple[StableIdentifier, ...] = Field(min_length=1)
    limitations: tuple[NonBlankText, ...] = Field(min_length=1)
    result: AssessmentOutcome
    meaningful_confidence: float | None = Field(
        default=None,
        strict=True,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    assessed_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_category_authority(self) -> AssessmentProvenance:
        if (
            self.deterministic_or_learned == "LEARNED"
            and self.category is VerificationLevel.FORMAL_VERIFIER
        ):
            raise ValueError("learned assessment cannot claim formal verifier category")
        if self.deterministic_or_learned == "HUMAN" and self.actor.kind is not ActorKind.HUMAN:
            raise ValueError("human assessment provenance requires a human actor")
        return self


class EvaluatorAuditRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_audit_id: StableIdentifier
    auditor: ActorIdentity
    auditor_version: StableIdentifier
    auditor_category: VerificationLevel
    evaluator: ActorIdentity
    evaluator_version: StableIdentifier
    proposer: ActorIdentity
    candidate_producer: ActorIdentity
    auditor_to_evaluator: ActorRelationship
    auditor_to_proposer: ActorRelationship
    auditor_to_candidate_producer: ActorRelationship
    independence_enforced: Literal[True]
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    checks_run: tuple[StableIdentifier, ...] = Field(min_length=1)
    assumptions: tuple[NonBlankText, ...]
    limitations: tuple[NonBlankText, ...] = Field(min_length=1)
    result: AssessmentOutcome
    audited_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_independent_auditor(self) -> EvaluatorAuditRecord:
        actors = (self.evaluator, self.proposer, self.candidate_producer)
        relationships = (
            self.auditor_to_evaluator,
            self.auditor_to_proposer,
            self.auditor_to_candidate_producer,
        )
        if any(relationship is not ActorRelationship.INDEPENDENT for relationship in relationships):
            raise ValueError("auditor must be independent of evaluator, proposer, and producer")
        if any(not are_independent(self.auditor, actor) for actor in actors):
            raise ValueError("auditor must be independent of evaluator, proposer, and producer")
        return self


class MetricObservation(_StrictFrozenModel):
    metric_id: StableIdentifier
    value: float = Field(strict=True, allow_inf_nan=False)
    source_id: StableIdentifier
    protected: bool
    external: bool


class ResourceBudget(_StrictFrozenModel):
    cost_usd: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    compute_units: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    tokens: int = Field(strict=True, ge=0)
    elapsed_seconds: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    tool_calls: int = Field(strict=True, ge=0)
    human_interventions: int = Field(strict=True, ge=0)


class ResourceUsage(_StrictFrozenModel):
    cost_usd: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    compute_units: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    tokens: int = Field(strict=True, ge=0)
    elapsed_seconds: float = Field(strict=True, ge=0.0, allow_inf_nan=False)
    tool_calls: int = Field(strict=True, ge=0)
    human_interventions: int = Field(strict=True, ge=0)


class PerformanceTrajectoryPoint(_StrictFrozenModel):
    step_index: int = Field(strict=True, ge=0)
    metrics: tuple[MetricObservation, ...] = Field(min_length=1)
    attempted_change_ids: tuple[StableIdentifier, ...]
    admitted_change_ids: tuple[StableIdentifier, ...]
    rejected_change_ids: tuple[StableIdentifier, ...]
    regressions: tuple[NonBlankText, ...]
    rollback_event_ids: tuple[StableIdentifier, ...]
    usage: ResourceUsage

    @model_validator(mode="after")
    def require_complete_change_partition(self) -> PerformanceTrajectoryPoint:
        if self.attempted_change_ids != (
            *self.admitted_change_ids,
            *self.rejected_change_ids,
        ):
            raise ValueError("admitted and rejected changes must partition attempted changes")
        return self


class SelfImprovementMeasurementRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    measurement_id: StableIdentifier
    change_id: StableIdentifier
    run_id: StableIdentifier
    classification: ChangeClassification
    proposer: ActorIdentity
    evaluator: ActorIdentity
    evaluator_version: StableIdentifier
    evaluator_tier: StableIdentifier
    grounding: tuple[ExternalGrounding, ...] = Field(min_length=1)
    baseline_version_id: StableIdentifier
    candidate_version_id: StableIdentifier
    protected_metrics: tuple[MetricObservation, ...] = Field(min_length=1)
    countermetrics: tuple[MetricObservation, ...] = Field(min_length=1)
    trajectory: tuple[PerformanceTrajectoryPoint, ...]
    attempted_changes: tuple[StableIdentifier, ...] = Field(min_length=1)
    admitted_changes: tuple[StableIdentifier, ...]
    rejected_changes: tuple[StableIdentifier, ...]
    regressions: tuple[NonBlankText, ...]
    rollback_events: tuple[StableIdentifier, ...]
    execution_budget: ResourceBudget
    search_budget: ResourceBudget
    evaluation_budget: ResourceBudget
    judging_budget: ResourceBudget
    human_budget: ResourceBudget
    usage: ResourceUsage
    failures: tuple[NonBlankText, ...]
    rollback_target_id: StableIdentifier
    evaluator_audit_id: StableIdentifier
    decision: MeasurementDecision
    decision_authority: ActorIdentity
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_complete_measurement(self) -> SelfImprovementMeasurementRecord:
        if len(self.trajectory) < 2 or self.trajectory[0].step_index != 0:
            raise ValueError("measurement must retain complete m_0 through m_T trajectory")
        if tuple(point.step_index for point in self.trajectory) != tuple(
            range(len(self.trajectory))
        ):
            raise ValueError("trajectory step indexes must be consecutive from m_0")
        if self.attempted_changes != (*self.admitted_changes, *self.rejected_changes):
            raise ValueError("admitted and rejected changes must partition attempted changes")
        if ExternalGrounding.NONE in self.grounding:
            raise ValueError("durable measurements require external grounding")
        if not all(metric.protected and metric.external for metric in self.protected_metrics):
            raise ValueError("protected metrics must come from protected external evaluation")
        return self
