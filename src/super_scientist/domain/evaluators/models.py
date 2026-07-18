from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import ExternalGrounding
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class EvaluatorThreshold(_StrictFrozenModel):
    threshold_id: StableIdentifier
    metric_id: StableIdentifier
    value: float = Field(strict=True, allow_inf_nan=False)
    effective_at: UtcTimestamp


class EvaluatorVersion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_version_id: StableIdentifier
    evaluator: ActorIdentity
    configuration_hash: Sha256Hex
    threshold_history: tuple[EvaluatorThreshold, ...] = Field(min_length=1)
    benchmark_version_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    predecessor_evaluator_version_id: StableIdentifier | None
    rollback_evaluator_version_id: StableIdentifier | None
    candidate_producer: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_candidate_rollback(self) -> EvaluatorVersion:
        if self.predecessor_evaluator_version_id is not None and (
            self.rollback_evaluator_version_id != self.predecessor_evaluator_version_id
        ):
            raise ValueError("candidate evaluator must retain its predecessor as rollback target")
        return self


class EvaluationResult(_StrictFrozenModel):
    evaluation_id: StableIdentifier
    provenance: AssessmentProvenance
    grounding: ExternalGrounding
    protected: bool
    passed: bool

    @model_validator(mode="after")
    def validate_result_provenance(self) -> EvaluationResult:
        if self.grounding is ExternalGrounding.NONE:
            raise ValueError("evaluator succession results require external grounding")
        provenance_passed = self.provenance.result is AssessmentOutcome.PASSED
        if self.passed != provenance_passed:
            raise ValueError("evaluation result must match assessment provenance")
        return self


class EvaluatorSuccessionDecision(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_succession_decision_id: StableIdentifier
    predecessor_evaluator_version_id: StableIdentifier
    candidate_evaluator_version_id: StableIdentifier
    candidate_evaluator: ActorIdentity
    evaluator_audit_id: StableIdentifier
    evaluator_audit_result: AssessmentOutcome
    protected_evaluation: EvaluationResult | None
    external_evaluation: EvaluationResult | None
    human_review: AssessmentProvenance | None
    canary_evaluation: EvaluationResult | None
    predecessor_rollback_target_id: StableIdentifier
    accepted: bool
    rationale: tuple[NonBlankText, ...] = Field(min_length=1)
    decision_authority: ActorIdentity
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_promotion_gates(self) -> EvaluatorSuccessionDecision:
        if not self.accepted:
            return self
        if self.evaluator_audit_result is not AssessmentOutcome.PASSED:
            raise ValueError("promotion requires a passed independent audit")
        if self.protected_evaluation is None or not (
            self.protected_evaluation.protected and self.protected_evaluation.passed
        ):
            raise ValueError("promotion requires a passed protected evaluation")
        if self.external_evaluation is None or not self.external_evaluation.passed:
            raise ValueError("promotion requires a passed external evaluation")
        if (
            self.human_review is None
            or self.human_review.deterministic_or_learned != "HUMAN"
            or self.human_review.actor.kind is not ActorKind.HUMAN
            or self.human_review.result is not AssessmentOutcome.PASSED
        ):
            raise ValueError("promotion requires passed independent human review")
        if self.canary_evaluation is None or not self.canary_evaluation.passed:
            raise ValueError("promotion requires a passed canary evaluation")
        if self.predecessor_rollback_target_id != self.predecessor_evaluator_version_id:
            raise ValueError("promotion requires the predecessor rollback target")
        if not are_independent(self.decision_authority, self.candidate_evaluator):
            raise ValueError("candidate cannot authorize its own promotion")
        if self.decision_authority.kind is not ActorKind.HUMAN:
            raise ValueError("evaluator promotion authority must be human")
        return self


class CollapseMetrics(_StrictFrozenModel):
    protected_performance: float = Field(strict=True, allow_inf_nan=False)
    external_performance: float = Field(strict=True, allow_inf_nan=False)
    calibration: float = Field(strict=True, allow_inf_nan=False)
    response_diversity: float = Field(strict=True, allow_inf_nan=False)
    hypothesis_diversity: float = Field(strict=True, allow_inf_nan=False)
    source_diversity: float = Field(strict=True, allow_inf_nan=False)
    experiment_diversity: float = Field(strict=True, allow_inf_nan=False)
    adapter_output_entropy: float = Field(strict=True, allow_inf_nan=False)
    repeated_error_rate: float = Field(strict=True, allow_inf_nan=False)
    confidence_error_coupling: float = Field(strict=True, allow_inf_nan=False)
    evaluator_disagreement: float = Field(strict=True, allow_inf_nan=False)
    catastrophic_regression: float = Field(strict=True, allow_inf_nan=False)
    task_distribution_narrowing: float = Field(strict=True, allow_inf_nan=False)
    externally_grounded_data_proportion: float = Field(strict=True, allow_inf_nan=False)


class EvaluatorCollapseRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_collapse_record_id: StableIdentifier
    evaluator_version_id: StableIdentifier
    metrics: CollapseMetrics
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    measured_at: UtcTimestamp
    governing_policy_hash: Sha256Hex
