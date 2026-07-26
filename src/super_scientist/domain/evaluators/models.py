from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ExternalGrounding,
    is_authoritative_verification,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
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


class EvaluationStage(StrEnum):
    PROTECTED = "PROTECTED"
    EXTERNAL = "EXTERNAL"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    CANARY = "CANARY"


class EvaluationResult(_StrictFrozenModel):
    evaluation_id: StableIdentifier
    candidate_evaluator_version_id: StableIdentifier
    stage: EvaluationStage
    provenance: AssessmentProvenance
    grounding: ExternalGrounding
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    passed: bool
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_result_provenance(self) -> EvaluationResult:
        if self.grounding is ExternalGrounding.NONE:
            raise ValueError("evaluator succession results require external grounding")
        provenance_passed = self.provenance.result is AssessmentOutcome.PASSED
        if self.passed != provenance_passed:
            raise ValueError("evaluation result must match assessment provenance")
        if not is_authoritative_verification(self.provenance.category):
            raise ValueError("evaluation result uses a prohibited verification category")
        if self.provenance.proposer_relationship is not ActorRelationship.INDEPENDENT:
            raise ValueError("evaluation reviewer must declare an independent relationship")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evaluation evidence IDs must be unique")
        if self.evidence_ids != self.provenance.evidence_ids:
            raise ValueError("evaluation evidence IDs must match assessment provenance")
        if self.governing_policy_hash != self.provenance.governing_policy_hash:
            raise ValueError("evaluation policy must match assessment provenance")
        if self.stage is EvaluationStage.HUMAN_REVIEW and (
            self.provenance.actor.kind is not ActorKind.HUMAN
            or self.provenance.deterministic_or_learned != "HUMAN"
            or self.grounding is not ExternalGrounding.HUMAN_JUDGMENT
        ):
            raise ValueError("human-review stage requires grounded human provenance")
        return self


class EvaluatorSuccessionDecision(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_succession_decision_id: StableIdentifier
    predecessor_evaluator_version_id: StableIdentifier
    candidate_evaluator_version_id: StableIdentifier
    candidate_evaluator: ActorIdentity
    candidate_producer: ActorIdentity
    change_proposer: ActorIdentity
    evaluator_audit_id: StableIdentifier
    evaluator_audit_result: AssessmentOutcome
    protected_evaluation: EvaluationResult | None
    external_evaluation: EvaluationResult | None
    human_review: EvaluationResult | None
    canary_evaluation: EvaluationResult | None
    predecessor_rollback_target_id: StableIdentifier
    accepted: bool
    rationale: tuple[NonBlankText, ...] = Field(min_length=1)
    decision_authority: ActorIdentity
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_promotion_gates(self) -> EvaluatorSuccessionDecision:
        gate_specs = (
            (self.protected_evaluation, EvaluationStage.PROTECTED),
            (self.external_evaluation, EvaluationStage.EXTERNAL),
            (self.human_review, EvaluationStage.HUMAN_REVIEW),
            (self.canary_evaluation, EvaluationStage.CANARY),
        )
        gates = tuple(gate for gate, _ in gate_specs if gate is not None)
        change_actors = (
            self.candidate_evaluator,
            self.change_proposer,
            self.candidate_producer,
        )
        for gate, expected_stage in gate_specs:
            if gate is None:
                continue
            if gate.stage is not expected_stage:
                raise ValueError("succession gate result is bound to the wrong stage")
            if gate.candidate_evaluator_version_id != self.candidate_evaluator_version_id:
                raise ValueError("succession gate result is bound to the wrong candidate version")
            if gate.governing_policy_hash != self.governing_policy_hash:
                raise ValueError("succession gate result is bound to the wrong policy")
            if any(not are_independent(gate.provenance.actor, actor) for actor in change_actors):
                raise ValueError(
                    "succession gate reviewers must be independent of evaluator, "
                    "proposer, and producer"
                )
        if len({gate.evaluation_id for gate in gates}) != len(gates):
            raise ValueError("succession gates require a unique evaluation result per stage")
        if any(
            not are_independent(left.provenance.actor, right.provenance.actor)
            for index, left in enumerate(gates)
            for right in gates[index + 1 :]
        ):
            raise ValueError("succession gates require a distinct independent reviewer per stage")
        if not self.accepted:
            return self
        if self.evaluator_audit_result is not AssessmentOutcome.PASSED:
            raise ValueError("promotion requires a passed independent audit")
        if self.protected_evaluation is None or not self.protected_evaluation.passed:
            raise ValueError("promotion requires a passed protected evaluation")
        if self.external_evaluation is None or not self.external_evaluation.passed:
            raise ValueError("promotion requires a passed external evaluation")
        if (
            self.human_review is None
            or self.human_review.provenance.deterministic_or_learned != "HUMAN"
            or self.human_review.provenance.actor.kind is not ActorKind.HUMAN
            or not self.human_review.passed
        ):
            raise ValueError("promotion requires passed independent human review")
        if self.canary_evaluation is None or not self.canary_evaluation.passed:
            raise ValueError("promotion requires a passed canary evaluation")
        if self.predecessor_rollback_target_id != self.predecessor_evaluator_version_id:
            raise ValueError("promotion requires the predecessor rollback target")
        if any(not are_independent(self.decision_authority, actor) for actor in change_actors):
            raise ValueError(
                "candidate cannot authorize its own promotion; proposer and producer "
                "are also barred"
            )
        if self.decision_authority.kind is not ActorKind.HUMAN:
            raise ValueError("evaluator promotion authority must be human")
        return self


class CollapseMetrics(_StrictFrozenModel):
    protected_performance: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    external_performance: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    calibration: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    response_diversity: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    hypothesis_diversity: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    source_diversity: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    experiment_diversity: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    adapter_output_entropy: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    repeated_error_rate: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    confidence_error_coupling: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    evaluator_disagreement: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    catastrophic_regression: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    task_distribution_narrowing: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    externally_grounded_data_proportion: float = Field(
        strict=True,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )


class CollapseFindingCode(StrEnum):
    SHARED_PROPOSER_JUDGE_CONFIGURATION = "SHARED_PROPOSER_JUDGE_CONFIGURATION"
    CONFIDENCE_AS_REWARD = "CONFIDENCE_AS_REWARD"
    SELF_CONSISTENCY_AS_TRUTH = "SELF_CONSISTENCY_AS_TRUTH"
    CORRELATED_REVIEWERS = "CORRELATED_REVIEWERS"
    SELF_GENERATED_DATA_SELF_APPROVED = "SELF_GENERATED_DATA_SELF_APPROVED"
    EVALUATOR_GAIN_WITHOUT_EXTERNAL_GAIN = "EVALUATOR_GAIN_WITHOUT_EXTERNAL_GAIN"
    CONFIDENCE_WITHOUT_EVIDENCE = "CONFIDENCE_WITHOUT_EVIDENCE"
    PARAPHRASE_ONLY_REFINEMENT = "PARAPHRASE_ONLY_REFINEMENT"
    TEXTUAL_AGREEMENT_DISPLACES_EMPIRICAL_EVIDENCE = (
        "TEXTUAL_AGREEMENT_DISPLACES_EMPIRICAL_EVIDENCE"
    )


class CollapseFinding(_StrictFrozenModel):
    code: CollapseFindingCode
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    detail: NonBlankText

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("collapse finding evidence_ids must be unique")
        return value


class EvaluatorCollapseReport(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_collapse_report_id: StableIdentifier
    evaluator_version_id: StableIdentifier
    metrics: CollapseMetrics
    catastrophic_regression: bool
    findings: tuple[CollapseFinding, ...]
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    measured_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_complete_nonaggregate_report(self) -> EvaluatorCollapseReport:
        if self.catastrophic_regression != (self.metrics.catastrophic_regression > 0.0):
            raise ValueError("catastrophic flag must match the retained catastrophic dimension")
        finding_codes = tuple(item.code for item in self.findings)
        if len(finding_codes) != len(set(finding_codes)):
            raise ValueError("collapse findings must use unique prohibited-pattern codes")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("collapse report evidence_ids must be unique")
        if any(not set(item.evidence_ids).issubset(self.evidence_ids) for item in self.findings):
            raise ValueError("collapse finding evidence must be retained by the report")
        return self


class CollapsePromotionAssessment(_StrictFrozenModel):
    accepted: Literal[False] = False
    authoritative: Literal[False] = False
    reasons: tuple[NonBlankText, ...] = Field(min_length=1)


def assess_collapse_report_only(report: EvaluatorCollapseReport) -> CollapsePromotionAssessment:
    validated = EvaluatorCollapseReport.model_validate(report)
    if validated.catastrophic_regression:
        return CollapsePromotionAssessment(
            reasons=("catastrophic regression blocks promotion",),
        )
    if validated.findings:
        return CollapsePromotionAssessment(
            reasons=("explicit collapse findings block promotion",),
        )
    return CollapsePromotionAssessment(
        reasons=("collapse reports are non-authoritative for promotion",),
    )


class EvaluatorCollapseRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    evaluator_collapse_record_id: StableIdentifier
    evaluator_version_id: StableIdentifier
    metrics: CollapseMetrics
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    measured_at: UtcTimestamp
    governing_policy_hash: Sha256Hex
