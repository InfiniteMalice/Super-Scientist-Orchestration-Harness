from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator
from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ReviewerAssessment,
    RuleConsolidationDecision,
    RuleIncident,
    RuleRegressionCase,
    RuleStatus,
    SemanticVersion,
)
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorCollapseRecord,
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    TrailAssessment,
    TrailCheckResult,
)
from super_scientist.domain.harness_eval.models import (
    HarnessDecisionStatus,
    HarnessPartition,
    HarnessVariant,
)
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.models import (
    AssessmentOutcome,
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import (
    GitObjectId,
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    ProgressPlan,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
)
from super_scientist.domain.representations.models import TransformationKind
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent
from super_scientist.providers.storage.append_only import (
    AppendOnlyRecordRepository,
    OrderedReferenceBinding,
    ReferencedAppendOnlyRecordRepository,
    StrictFrozenStorageRecord,
    _require_integrity,
    _stored_integer,
    _stored_relationship_value,
    _stored_string,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import (
    behavior_rule_link_versions,
    behavioral_rule_heads,
    behavioral_rule_version_incidents,
    behavioral_rule_version_supersessions,
    behavioral_rule_versions,
    completion_decisions,
    configuration_versions,
    counterexample_evidence,
    counterexample_records,
    counterexample_simulations,
    counterexample_verification_results,
    evaluator_audits,
    evaluator_collapse_records,
    evaluator_heads,
    evaluator_succession_decisions,
    evaluator_versions,
    evidence_trail_assessments,
    evidence_trail_checks,
    evidence_trail_heads,
    evidence_trail_nodes,
    evidence_trail_relations,
    evidence_trail_versions,
    executable_model_specs,
    handbook_verification_records,
    harness_budgets,
    harness_campaign_heads,
    harness_campaigns,
    harness_confounds,
    harness_decisions,
    harness_metrics,
    harness_observations,
    harness_partition_manifests,
    hypothesis_admission_counterexamples,
    hypothesis_admission_decisions,
    hypothesis_admission_models,
    hypothesis_admission_revisions,
    hypothesis_admission_verification_results,
    hypothesis_heads,
    hypothesis_revision_counterexamples,
    hypothesis_revision_verification_results,
    hypothesis_revisions,
    hypothesis_version_evidence,
    hypothesis_version_primitives,
    hypothesis_versions,
    primitive_evaluation_evidence,
    primitive_evaluation_verification_results,
    primitive_evaluations,
    primitive_heads,
    primitive_version_dependencies,
    primitive_version_measurements,
    primitive_version_predecessors,
    primitive_versions,
    progress_events,
    progress_heads,
    progress_plans,
    progress_subtasks,
    report_sentence_bindings,
    research_run_events,
    research_run_heads,
    research_runs,
    reviewer_assessment_incidents,
    reviewer_assessment_rule_versions,
    reviewer_assessments,
    rule_consolidation_assessments,
    rule_consolidation_decisions,
    rule_consolidation_incidents,
    rule_incidents,
    rule_regression_case_incidents,
    rule_regression_cases,
    run_budgets,
    run_checkpoints,
    self_improvement_measurements,
    simulation_results,
    verification_mechanism_specs,
    verification_result_simulations,
    verification_results,
)

STABLE_IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)
SEMANTIC_VERSION_ADAPTER: TypeAdapter[SemanticVersion] = TypeAdapter(SemanticVersion)


class PrimitiveStatus(StrEnum):
    PROPOSED = "PROPOSED"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    UNDER_DEFINITION = "UNDER_DEFINITION"
    EXPERIMENTAL = "EXPERIMENTAL"
    LOCALLY_USEFUL = "LOCALLY_USEFUL"
    REPLICATED = "REPLICATED"
    STABILIZED = "STABILIZED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


class PrimitiveEvaluationFrame(StrEnum):
    OLD_FRAME = "OLD_FRAME"
    NEW_FRAME = "NEW_FRAME"


class EvaluationOutcome(StrEnum):
    PASS = "PASS"  # nosec B105
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"


class HypothesisAdmissionStatus(StrEnum):
    SOURCE_PATTERN_REVIEW_PENDING = "SOURCE_PATTERN_REVIEW_PENDING"
    GENERIC_PATTERN_EXTRACTED = "GENERIC_PATTERN_EXTRACTED"
    TRANSFER_TESTING = "TRANSFER_TESTING"
    TRANSFER_VALIDATED = "TRANSFER_VALIDATED"
    DOMAIN_SPECIFIC = "DOMAIN_SPECIFIC"
    BENCHMARK_SPECIFIC = "BENCHMARK_SPECIFIC"
    REJECTED = "REJECTED"
    ADMITTED_TO_SSOH = "ADMITTED_TO_SSOH"


class ModelType(StrEnum):
    SOURCE_CONTROLLED_METADATA = "SOURCE_CONTROLLED_METADATA"
    SYMBOLIC_EQUATION = "SYMBOLIC_EQUATION"
    PROBABILISTIC_MODEL = "PROBABILISTIC_MODEL"
    CAUSAL_GRAPH = "CAUSAL_GRAPH"
    STATE_TRANSITION_SYSTEM = "STATE_TRANSITION_SYSTEM"
    DETERMINISTIC_SIMULATOR = "DETERMINISTIC_SIMULATOR"
    FORMAL_SPECIFICATION = "FORMAL_SPECIFICATION"
    APPROVED_DOMAIN_MODEL = "APPROVED_DOMAIN_MODEL"


class ModelExecutionMode(StrEnum):
    METADATA_ONLY = "METADATA_ONLY"
    BUILTIN_DETERMINISTIC_SIMULATOR = "BUILTIN_DETERMINISTIC_SIMULATOR"


class BuiltinSimulatorId(StrEnum):
    """Closed inert names; execution dispatch remains outside storage."""

    THERMAL_CHAMBER_V1 = "thermal-chamber-v1"
    EXPONENTIAL_DECAY_V1 = "exponential-decay-v1"


class VerificationMechanismCategory(StrEnum):
    FORMAL_VERIFIER = "FORMAL_VERIFIER"
    INDEPENDENT_DETERMINISTIC_CHECKER = "INDEPENDENT_DETERMINISTIC_CHECKER"
    LEARNED_JUDGE = "LEARNED_JUDGE"


class VerificationResultCategory(StrEnum):
    FORMAL_VERIFICATION_RESULT = "FORMAL_VERIFICATION_RESULT"
    DETERMINISTIC_CHECK_RESULT = "DETERMINISTIC_CHECK_RESULT"
    LEARNED_JUDGE_RESULT = "LEARNED_JUDGE_RESULT"


class VerificationOutcome(StrEnum):
    PASS = "PASS"  # nosec B105
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"


class AdmissionDecisionOutcome(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


class MetricValueRecord(StrictFrozenStorageRecord):
    metric_id: StableIdentifier
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("metric value must be finite")
        return value


class BehaviorRuleLinkVersionRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    link_version_id: StableIdentifier
    behavior_id: StableIdentifier
    version: int = Field(ge=1)
    rule_version_id: StableIdentifier
    manifest_hash: Sha256Hex
    created_by: StableIdentifier
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class HandbookVerificationRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    verification_id: StableIdentifier
    manifest_hash: Sha256Hex
    repository_commit: GitObjectId
    source_hashes: tuple[Sha256Hex, ...] = Field(min_length=1)
    generated_artifact_hash: Sha256Hex
    stale_locations: tuple[NonBlankText, ...]
    missing_symbols: tuple[NonBlankText, ...]
    outcome: AssessmentOutcome
    verified_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("source_hashes")
    @classmethod
    def require_unique_source_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_references(value, "source_hashes")


class HarnessCampaignRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    campaign_id: StableIdentifier
    version: int = Field(ge=1)
    variants: tuple[HarnessVariant, ...] = Field(min_length=1)
    model_id: StableIdentifier
    model_version: StableIdentifier
    adapter_id: StableIdentifier | None
    baseline_variant: HarnessVariant | None = None
    candidate_variant: HarnessVariant | None = None
    baseline_harness_version_id: StableIdentifier | None = None
    candidate_harness_version_id: StableIdentifier | None = None
    rollback_harness_version_id: StableIdentifier | None = None
    evaluator_id: StableIdentifier | None = None
    evaluator_version_id: StableIdentifier | None = None
    candidate_producer_id: StableIdentifier | None = None
    canonical_campaign_hash: Sha256Hex | None = None
    created_by: StableIdentifier
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("variants")
    @classmethod
    def require_unique_variants(
        cls,
        value: tuple[HarnessVariant, ...],
    ) -> tuple[HarnessVariant, ...]:
        if len(set(value)) != len(value):
            raise ValueError("variants must be unique")
        return value


class HarnessPartitionManifestRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    partition_manifest_id: StableIdentifier
    campaign_id: StableIdentifier
    campaign_version: int | None = Field(default=None, ge=1)
    partition: HarnessPartition
    task_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    manifest_hash: Sha256Hex
    protected_content_hash: Sha256Hex | None
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("task_ids")
    @classmethod
    def require_unique_task_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_references(value, "task_ids")


class HarnessBudgetRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    budget_id: StableIdentifier
    campaign_id: StableIdentifier
    variant: HarnessVariant
    budget_hash: Sha256Hex
    model_id: StableIdentifier
    model_version: StableIdentifier
    adapter_id: StableIdentifier | None
    feedback_mode: StableIdentifier
    tool_ids: tuple[StableIdentifier, ...]
    attempts: int = Field(ge=1)
    token_limit: int = Field(ge=1)
    reasoning_limit: int = Field(ge=1)
    evaluator_call_limit: int = Field(ge=1)
    wall_clock_seconds: Decimal = Field(gt=0)
    cost_limit: Decimal = Field(ge=0)
    human_intervention_limit: int = Field(ge=0)
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("tool_ids")
    @classmethod
    def require_unique_tool_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_references(value, "tool_ids")

    @field_validator("wall_clock_seconds", "cost_limit")
    @classmethod
    def require_finite_budget(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("budget values must be finite")
        return value


class HarnessObservationRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    observation_id: StableIdentifier
    campaign_id: StableIdentifier
    partition_manifest_id: StableIdentifier
    task_id: StableIdentifier
    variant: HarnessVariant
    iteration_index: int | None = Field(default=None, ge=0)
    budget_id: StableIdentifier | None = None
    candidate_output_hash: Sha256Hex
    attempt: int = Field(ge=1)
    negative_result: bool
    result_id: StableIdentifier | None = None
    outcome: AssessmentOutcome | None = None
    evaluator_version_id: StableIdentifier | None = None
    observed_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class HarnessMetricRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    result_id: StableIdentifier
    campaign_id: StableIdentifier
    task_id: StableIdentifier
    expected_output_hash: Sha256Hex
    candidate_output_hash: Sha256Hex
    checker_id: StableIdentifier
    checker_version: StableIdentifier
    outcome: AssessmentOutcome
    metric_values: tuple[MetricValueRecord, ...]
    evaluated_at: UtcTimestamp

    @field_validator("metric_values")
    @classmethod
    def require_unique_metrics(
        cls,
        value: tuple[MetricValueRecord, ...],
    ) -> tuple[MetricValueRecord, ...]:
        metric_ids = tuple(item.metric_id for item in value)
        _require_unique_references(metric_ids, "metric_values")
        return value


class HarnessConfoundRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    confound_id: StableIdentifier
    campaign_id: StableIdentifier
    code: StableIdentifier
    description: NonBlankText
    affected_variant: HarnessVariant | None
    resolved: bool = False
    independent_analysis_id: StableIdentifier | None = None
    recorded_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class HarnessDecisionRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    decision_id: StableIdentifier
    campaign_id: StableIdentifier
    status: HarnessDecisionStatus
    admitted: bool
    rationale: tuple[NonBlankText, ...] = Field(min_length=1)
    authority_id: StableIdentifier
    rollback_target_id: StableIdentifier | None = None
    evaluator_audit_id: StableIdentifier | None = None
    measurement_id: StableIdentifier | None = None
    metric_result_ids: tuple[StableIdentifier, ...] = ()
    confound_ids: tuple[StableIdentifier, ...] = ()
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_exact_admission_status(self) -> Self:
        if self.admitted != (self.status is HarnessDecisionStatus.ADMITTED):
            raise ValueError("admitted must be true exactly for ADMITTED status")
        return self


def _require_unique_references(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must contain unique identifiers")
    return value


class PrimitiveVersionRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    primitive_version_id: StableIdentifier
    primitive_id: StableIdentifier
    semantic_version: SemanticVersion
    transformation_kind: TransformationKind
    definition: NonBlankText
    motivation: NonBlankText
    parent_vocabulary: tuple[NonBlankText, ...]
    contrasts: tuple[NonBlankText, ...]
    examples: tuple[NonBlankText, ...]
    counterexamples: tuple[NonBlankText, ...]
    construction_method: NonBlankText
    expected_uses: tuple[NonBlankText, ...]
    predecessor_primitive_version_ids: tuple[StableIdentifier, ...]
    dependency_primitive_version_ids: tuple[StableIdentifier, ...]
    measurement_ids: tuple[StableIdentifier, ...]
    falsification_tests: tuple[NonBlankText, ...] = Field(min_length=1)
    ambiguity: tuple[NonBlankText, ...]
    proposer: ActorIdentity
    proposer_id: StableIdentifier
    status: PrimitiveStatus
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator(
        "predecessor_primitive_version_ids",
        "dependency_primitive_version_ids",
        "measurement_ids",
    )
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _require_unique_references(
            value,
            str(getattr(info, "field_name", "references")),
        )

    @model_validator(mode="after")
    def require_exact_proposer_identity(self) -> Self:
        if self.proposer_id != self.proposer.actor_id:
            raise ValueError("proposer_id must match the retained proposer identity")
        return self


class PrimitiveEvaluationRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    primitive_evaluation_id: StableIdentifier
    primitive_version_id: StableIdentifier
    frame: PrimitiveEvaluationFrame
    verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    criteria: tuple[NonBlankText, ...] = Field(min_length=1)
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    outcome: EvaluationOutcome
    evaluator_id: StableIdentifier
    evaluated_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("verification_result_ids", "evidence_ids")
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _require_unique_references(
            value,
            str(getattr(info, "field_name", "references")),
        )


class HypothesisVersionRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    hypothesis_version_id: StableIdentifier
    hypothesis_id: StableIdentifier
    version: int = Field(ge=1)
    statement: NonBlankText
    assumptions: tuple[NonBlankText, ...] = Field(min_length=1)
    scope: tuple[NonBlankText, ...] = Field(min_length=1)
    variables: tuple[NonBlankText, ...] = Field(min_length=1)
    predictions: tuple[NonBlankText, ...] = Field(min_length=1)
    falsification_conditions: tuple[NonBlankText, ...] = Field(min_length=1)
    primitive_version_ids: tuple[StableIdentifier, ...]
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    admission_status: HypothesisAdmissionStatus
    proposer_id: StableIdentifier
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("primitive_version_ids", "evidence_ids")
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _require_unique_references(
            value,
            str(getattr(info, "field_name", "references")),
        )


class ExecutableModelSpecRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    model_spec_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    model_type: ModelType
    execution_mode: ModelExecutionMode
    artifact_hash: Sha256Hex | None
    artifact_media_type: NonBlankText | None
    artifact_size_bytes: int | None = Field(ge=0)
    artifact_name: NonBlankText
    builtin_simulator_id: BuiltinSimulatorId | None
    input_schema_id: StableIdentifier
    output_schema_id: StableIdentifier
    deterministic_seed: int
    max_steps: int = Field(ge=1, le=100_000)
    max_state_bytes: int = Field(ge=1, le=10_000_000)
    registered_by: StableIdentifier
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_safe_execution_shape(self) -> Self:
        artifact_fields = (
            self.artifact_hash,
            self.artifact_media_type,
            self.artifact_size_bytes,
        )
        if self.execution_mode is ModelExecutionMode.METADATA_ONLY:
            if any(value is None for value in artifact_fields):
                raise ValueError("metadata-only model requires complete artifact metadata")
            if self.builtin_simulator_id is not None:
                raise ValueError("metadata-only model cannot name builtin_simulator_id")
        else:
            if any(value is not None for value in artifact_fields):
                raise ValueError("builtin simulator cannot carry an untrusted artifact")
            if self.builtin_simulator_id is None:
                raise ValueError("builtin_simulator_id is required for builtin execution mode")
        return self


class VerificationMechanismSpecRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    mechanism_spec_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    mechanism_category: VerificationMechanismCategory
    name: NonBlankText
    description: NonBlankText
    specification_hash: Sha256Hex
    input_schema_id: StableIdentifier
    output_schema_id: StableIdentifier
    created_by: StableIdentifier
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class SimulationResultRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    simulation_result_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    model_spec_id: StableIdentifier
    execution_mode: ModelExecutionMode
    input_hash: Sha256Hex
    output_hash: Sha256Hex
    deterministic_seed: int
    steps: int = Field(ge=0, le=100_000)
    state_bytes: int = Field(ge=0, le=10_000_000)
    completed_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("execution_mode")
    @classmethod
    def require_builtin_execution(cls, value: ModelExecutionMode) -> ModelExecutionMode:
        if value is not ModelExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR:
            raise ValueError("execution_mode must use the closed builtin simulator")
        return value


class VerificationResultRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    verification_result_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    mechanism_spec_id: StableIdentifier
    mechanism_category: VerificationMechanismCategory
    result_category: VerificationResultCategory
    model_spec_id: StableIdentifier | None
    model_execution_mode: ModelExecutionMode | None
    simulation_result_ids: tuple[StableIdentifier, ...]
    outcome: VerificationOutcome
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    verified_by: StableIdentifier
    completed_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("simulation_result_ids")
    @classmethod
    def require_unique_simulation_results(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_references(value, "simulation_result_ids")

    @model_validator(mode="after")
    def require_exact_category_and_model_pairs(self) -> Self:
        expected_result_category = {
            VerificationMechanismCategory.FORMAL_VERIFIER: (
                VerificationResultCategory.FORMAL_VERIFICATION_RESULT
            ),
            VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER: (
                VerificationResultCategory.DETERMINISTIC_CHECK_RESULT
            ),
            VerificationMechanismCategory.LEARNED_JUDGE: (
                VerificationResultCategory.LEARNED_JUDGE_RESULT
            ),
        }[self.mechanism_category]
        if self.result_category is not expected_result_category:
            raise ValueError("result_category must match mechanism_category")
        if (self.model_spec_id is None) != (self.model_execution_mode is None):
            raise ValueError("model_spec_id and model_execution_mode must both be set or null")
        if self.simulation_result_ids and (
            self.model_spec_id is None
            or self.model_execution_mode is not ModelExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR
        ):
            raise ValueError(
                "simulation_result_ids require a model_spec_id with builtin execution mode"
            )
        return self


class CounterexampleRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    counterexample_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    model_spec_id: StableIdentifier | None
    model_execution_mode: ModelExecutionMode | None
    simulation_result_ids: tuple[StableIdentifier, ...]
    verification_result_ids: tuple[StableIdentifier, ...]
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    description: NonBlankText
    input_hash: Sha256Hex
    observed_output_hash: Sha256Hex
    expected_output_hash: Sha256Hex
    discovered_by: StableIdentifier
    discovered_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("simulation_result_ids", "verification_result_ids", "evidence_ids")
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _require_unique_references(
            value,
            str(getattr(info, "field_name", "references")),
        )

    @model_validator(mode="after")
    def require_complete_model_pair(self) -> Self:
        if (self.model_spec_id is None) != (self.model_execution_mode is None):
            raise ValueError("model_spec_id and model_execution_mode must both be set or null")
        if self.simulation_result_ids and (
            self.model_spec_id is None
            or self.model_execution_mode is not ModelExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR
        ):
            raise ValueError(
                "simulation_result_ids require a model_spec_id with builtin execution mode"
            )
        if self.verification_result_ids and self.model_spec_id is None:
            raise ValueError("verification_result_ids require a model_spec_id")
        return self


class HypothesisRevisionRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    revision_id: StableIdentifier
    hypothesis_id: StableIdentifier
    prior_hypothesis_version_id: StableIdentifier
    prior_version: int = Field(ge=1)
    resulting_hypothesis_version_id: StableIdentifier
    resulting_version: int = Field(ge=2)
    triggering_verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    considered_counterexample_ids: tuple[StableIdentifier, ...]
    assumptions_added: tuple[NonBlankText, ...]
    assumptions_removed: tuple[NonBlankText, ...]
    assumptions_changed: tuple[NonBlankText, ...]
    variables_added: tuple[NonBlankText, ...]
    variables_removed: tuple[NonBlankText, ...]
    variables_changed: tuple[NonBlankText, ...]
    mechanism_changes: tuple[NonBlankText, ...]
    preserved_elements: tuple[NonBlankText, ...] = Field(min_length=1)
    changed_predictions: tuple[NonBlankText, ...] = Field(min_length=1)
    changed_falsification_conditions: tuple[NonBlankText, ...] = Field(min_length=1)
    author_id: StableIdentifier
    revised_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("triggering_verification_result_ids", "considered_counterexample_ids")
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _require_unique_references(
            value,
            str(getattr(info, "field_name", "references")),
        )

    @model_validator(mode="after")
    def require_contiguous_distinct_versions(self) -> Self:
        if self.resulting_version != self.prior_version + 1:
            raise ValueError("resulting_version must immediately follow prior_version")
        if self.resulting_hypothesis_version_id == self.prior_hypothesis_version_id:
            raise ValueError("resulting hypothesis version must differ from prior version")
        return self


class HypothesisAdmissionDecisionRecord(StrictFrozenStorageRecord):
    schema_version: Literal[1] = 1
    admission_decision_id: StableIdentifier
    hypothesis_version_id: StableIdentifier
    hypothesis_id: StableIdentifier
    version: int = Field(ge=1)
    admission_status: HypothesisAdmissionStatus
    model_spec_ids: tuple[StableIdentifier, ...]
    verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    counterexample_ids: tuple[StableIdentifier, ...]
    revision_ids: tuple[StableIdentifier, ...]
    outcome: AdmissionDecisionOutcome
    rationale: NonBlankText
    decided_by: StableIdentifier
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator(
        "model_spec_ids",
        "verification_result_ids",
        "counterexample_ids",
        "revision_ids",
    )
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _require_unique_references(
            value,
            str(getattr(info, "field_name", "references")),
        )


__all__ = [
    "AdmissionDecisionOutcome",
    "BehaviorRuleLinkVersionRecord",
    "BehaviorRuleLinkVersionRepository",
    "BehavioralRuleHeadRepository",
    "BehavioralRuleVersionRepository",
    "BuiltinSimulatorId",
    "CompletionDecisionRepository",
    "ConfigurationVersionRepository",
    "CounterexampleRecord",
    "CounterexampleRecordRepository",
    "EvaluationOutcome",
    "EvaluatorAuditRepository",
    "EvaluatorCollapseRepository",
    "EvaluatorHeadRepository",
    "EvaluatorSuccessionRepository",
    "EvaluatorVersionRepository",
    "EvidenceTrailAssessmentRepository",
    "EvidenceTrailCheckRepository",
    "EvidenceTrailHeadRepository",
    "EvidenceTrailNodeRepository",
    "EvidenceTrailRelationRepository",
    "EvidenceTrailVersionRepository",
    "ExecutableModelSpecRecord",
    "ExecutableModelSpecRepository",
    "HandbookVerificationRecord",
    "HandbookVerificationRepository",
    "HarnessBudgetRecord",
    "HarnessBudgetRepository",
    "HarnessCampaignHeadRepository",
    "HarnessCampaignRecord",
    "HarnessCampaignRepository",
    "HarnessConfoundRecord",
    "HarnessConfoundRepository",
    "HarnessDecisionRecord",
    "HarnessDecisionRepository",
    "HarnessDecisionStatus",
    "HarnessMetricRecord",
    "HarnessMetricRepository",
    "HarnessObservationRecord",
    "HarnessObservationRepository",
    "HarnessPartition",
    "HarnessPartitionManifestRecord",
    "HarnessPartitionManifestRepository",
    "HarnessVariant",
    "HypothesisAdmissionDecisionRecord",
    "HypothesisAdmissionDecisionRepository",
    "HypothesisAdmissionStatus",
    "HypothesisHeadRepository",
    "HypothesisRevisionRecord",
    "HypothesisRevisionRepository",
    "HypothesisVersionRecord",
    "HypothesisVersionRepository",
    "MetricValueRecord",
    "ModelExecutionMode",
    "ModelType",
    "PrimitiveEvaluationFrame",
    "PrimitiveEvaluationRecord",
    "PrimitiveEvaluationRepository",
    "PrimitiveHeadRepository",
    "PrimitiveStatus",
    "PrimitiveVersionRecord",
    "PrimitiveVersionRepository",
    "ProgressEventRepository",
    "ProgressHeadRepository",
    "ProgressPlanRepository",
    "ProgressSubtaskRepository",
    "ReportSentenceBindingRepository",
    "ResearchRunEventRepository",
    "ResearchRunHeadRepository",
    "ResearchRunRepository",
    "ReviewerAssessmentRepository",
    "RuleConsolidationDecisionRepository",
    "RuleIncidentRepository",
    "RuleRegressionCaseRepository",
    "RunBudgetRepository",
    "RunCheckpointRepository",
    "SelfImprovementMeasurementRepository",
    "SimulationResultRecord",
    "SimulationResultRepository",
    "VerificationMechanismCategory",
    "VerificationMechanismSpecRecord",
    "VerificationMechanismSpecRepository",
    "VerificationOutcome",
    "VerificationResultCategory",
    "VerificationResultRecord",
    "VerificationResultRepository",
]


class BehaviorRuleLinkVersionRepository(AppendOnlyRecordRepository[BehaviorRuleLinkVersionRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=behavior_rule_link_versions,
            model_type=BehaviorRuleLinkVersionRecord,
            identifier_field="link_version_id",
            relationship_fields={
                "behavior_id": "behavior_id",
                "version": "version",
                "rule_version_id": "rule_version_id",
            },
            relationship_types={"version": int},
        )


class HandbookVerificationRepository(AppendOnlyRecordRepository[HandbookVerificationRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=handbook_verification_records,
            model_type=HandbookVerificationRecord,
            identifier_field="verification_id",
            relationship_fields={
                "manifest_hash": "manifest_hash",
                "outcome": "outcome",
            },
        )


class HarnessCampaignRepository(AppendOnlyRecordRepository[HarnessCampaignRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_campaigns,
            model_type=HarnessCampaignRecord,
            identifier_field="campaign_id",
            relationship_fields={"version": "version"},
            relationship_types={"version": int},
        )


class HarnessPartitionManifestRepository(
    AppendOnlyRecordRepository[HarnessPartitionManifestRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_partition_manifests,
            model_type=HarnessPartitionManifestRecord,
            identifier_field="partition_manifest_id",
            relationship_fields={
                "campaign_id": "campaign_id",
                "partition": "partition",
                "manifest_hash": "manifest_hash",
                "protected_content_hash": "protected_content_hash",
            },
            nullable_relationship_fields={"protected_content_hash"},
        )


class HarnessBudgetRepository(AppendOnlyRecordRepository[HarnessBudgetRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_budgets,
            model_type=HarnessBudgetRecord,
            identifier_field="budget_id",
            relationship_fields={
                "campaign_id": "campaign_id",
                "variant": "variant",
            },
        )


class HarnessObservationRepository(AppendOnlyRecordRepository[HarnessObservationRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_observations,
            model_type=HarnessObservationRecord,
            identifier_field="observation_id",
            relationship_fields={
                "campaign_id": "campaign_id",
                "partition_manifest_id": "partition_manifest_id",
                "task_id": "task_id",
                "variant": "variant",
                "candidate_output_hash": "candidate_output_hash",
            },
        )


class HarnessMetricRepository(AppendOnlyRecordRepository[HarnessMetricRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_metrics,
            model_type=HarnessMetricRecord,
            identifier_field="result_id",
            relationship_fields={
                "campaign_id": "campaign_id",
                "task_id": "task_id",
                "expected_output_hash": "expected_output_hash",
                "candidate_output_hash": "candidate_output_hash",
                "checker_id": "checker_id",
                "checker_version": "checker_version",
                "outcome": "outcome",
            },
        )


class HarnessConfoundRepository(AppendOnlyRecordRepository[HarnessConfoundRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_confounds,
            model_type=HarnessConfoundRecord,
            identifier_field="confound_id",
            relationship_fields={
                "campaign_id": "campaign_id",
                "code": "code",
            },
        )


class HarnessDecisionRepository(AppendOnlyRecordRepository[HarnessDecisionRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=harness_decisions,
            model_type=HarnessDecisionRecord,
            identifier_field="decision_id",
            relationship_fields={
                "campaign_id": "campaign_id",
                "status": "status",
            },
        )


class RuleIncidentRepository(AppendOnlyRecordRepository[RuleIncident]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=rule_incidents,
            model_type=RuleIncident,
            identifier_field="incident_id",
        )


class BehavioralRuleVersionRepository(ReferencedAppendOnlyRecordRepository[BehavioralRuleVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=behavioral_rule_versions,
            model_type=BehavioralRuleVersion,
            identifier_field="rule_version_id",
            relationship_fields={
                "rule_id": "rule_id",
                "semantic_version": "semantic_version",
                "status": "status",
            },
            reference_bindings=(
                OrderedReferenceBinding(
                    table=behavioral_rule_version_incidents,
                    owner_column="rule_version_id",
                    record_field="source_incident_ids",
                    reference_column="incident_id",
                ),
                OrderedReferenceBinding(
                    table=behavioral_rule_version_supersessions,
                    owner_column="rule_version_id",
                    record_field="supersedes_rule_version_ids",
                    reference_column="predecessor_rule_version_id",
                ),
            ),
        )


class ReviewerAssessmentRepository(ReferencedAppendOnlyRecordRepository[ReviewerAssessment]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=reviewer_assessments,
            model_type=ReviewerAssessment,
            identifier_field="assessment_id",
            reference_bindings=(
                OrderedReferenceBinding(
                    table=reviewer_assessment_rule_versions,
                    owner_column="assessment_id",
                    record_field="rule_version_ids",
                    reference_column="rule_version_id",
                ),
                OrderedReferenceBinding(
                    table=reviewer_assessment_incidents,
                    owner_column="assessment_id",
                    record_field="incident_ids",
                    reference_column="incident_id",
                ),
            ),
        )


class RuleConsolidationDecisionRepository(
    ReferencedAppendOnlyRecordRepository[RuleConsolidationDecision]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=rule_consolidation_decisions,
            model_type=RuleConsolidationDecision,
            identifier_field="consolidation_decision_id",
            relationship_fields={
                "resulting_rule_version_id": "resulting_rule_version_id",
            },
            nullable_relationship_fields={"resulting_rule_version_id"},
            reference_bindings=(
                OrderedReferenceBinding(
                    table=rule_consolidation_assessments,
                    owner_column="consolidation_decision_id",
                    record_field="consumed_assessment_ids",
                    reference_column="assessment_id",
                ),
                OrderedReferenceBinding(
                    table=rule_consolidation_incidents,
                    owner_column="consolidation_decision_id",
                    record_field="consumed_incident_ids",
                    reference_column="incident_id",
                ),
            ),
        )


class RuleRegressionCaseRepository(ReferencedAppendOnlyRecordRepository[RuleRegressionCase]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=rule_regression_cases,
            model_type=RuleRegressionCase,
            identifier_field="regression_case_id",
            relationship_fields={"rule_version_id": "rule_version_id"},
            reference_bindings=(
                OrderedReferenceBinding(
                    table=rule_regression_case_incidents,
                    owner_column="regression_case_id",
                    record_field="incident_ids",
                    reference_column="incident_id",
                ),
            ),
        )


class PrimitiveVersionRepository(ReferencedAppendOnlyRecordRepository[PrimitiveVersionRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=primitive_versions,
            model_type=PrimitiveVersionRecord,
            identifier_field="primitive_version_id",
            relationship_fields={
                "primitive_id": "primitive_id",
                "semantic_version": "semantic_version",
                "status": "status",
            },
            reference_bindings=(
                OrderedReferenceBinding(
                    table=primitive_version_predecessors,
                    owner_column="primitive_version_id",
                    record_field="predecessor_primitive_version_ids",
                    reference_column="predecessor_primitive_version_id",
                ),
                OrderedReferenceBinding(
                    table=primitive_version_dependencies,
                    owner_column="primitive_version_id",
                    record_field="dependency_primitive_version_ids",
                    reference_column="dependency_primitive_version_id",
                ),
                OrderedReferenceBinding(
                    table=primitive_version_measurements,
                    owner_column="primitive_version_id",
                    record_field="measurement_ids",
                    reference_column="measurement_id",
                ),
            ),
        )


class PrimitiveEvaluationRepository(
    ReferencedAppendOnlyRecordRepository[PrimitiveEvaluationRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=primitive_evaluations,
            model_type=PrimitiveEvaluationRecord,
            identifier_field="primitive_evaluation_id",
            relationship_fields={
                "primitive_version_id": "primitive_version_id",
                "frame": "frame",
            },
            reference_bindings=(
                OrderedReferenceBinding(
                    table=primitive_evaluation_verification_results,
                    owner_column="primitive_evaluation_id",
                    record_field="verification_result_ids",
                    reference_column="verification_result_id",
                ),
                OrderedReferenceBinding(
                    table=primitive_evaluation_evidence,
                    owner_column="primitive_evaluation_id",
                    record_field="evidence_ids",
                    reference_column="evidence_id",
                ),
            ),
        )


class HypothesisVersionRepository(ReferencedAppendOnlyRecordRepository[HypothesisVersionRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=hypothesis_versions,
            model_type=HypothesisVersionRecord,
            identifier_field="hypothesis_version_id",
            relationship_fields={
                "hypothesis_id": "hypothesis_id",
                "version": "version",
                "admission_status": "admission_status",
            },
            relationship_types={"version": int},
            reference_bindings=(
                OrderedReferenceBinding(
                    table=hypothesis_version_primitives,
                    owner_column="hypothesis_version_id",
                    record_field="primitive_version_ids",
                    reference_column="primitive_version_id",
                ),
                OrderedReferenceBinding(
                    table=hypothesis_version_evidence,
                    owner_column="hypothesis_version_id",
                    record_field="evidence_ids",
                    reference_column="evidence_id",
                ),
            ),
        )


class ExecutableModelSpecRepository(AppendOnlyRecordRepository[ExecutableModelSpecRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=executable_model_specs,
            model_type=ExecutableModelSpecRecord,
            identifier_field="model_spec_id",
            relationship_fields={
                "hypothesis_version_id": "hypothesis_version_id",
                "execution_mode": "execution_mode",
                "artifact_hash": "artifact_hash",
                "artifact_media_type": "artifact_media_type",
                "artifact_size_bytes": "artifact_size_bytes",
                "builtin_simulator_id": "builtin_simulator_id",
            },
            relationship_types={"artifact_size_bytes": int},
            nullable_relationship_fields={
                "artifact_hash",
                "artifact_media_type",
                "artifact_size_bytes",
                "builtin_simulator_id",
            },
            hypothesis_scope_field="hypothesis_version_id",
        )


class VerificationMechanismSpecRepository(
    AppendOnlyRecordRepository[VerificationMechanismSpecRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=verification_mechanism_specs,
            model_type=VerificationMechanismSpecRecord,
            identifier_field="mechanism_spec_id",
            relationship_fields={
                "hypothesis_version_id": "hypothesis_version_id",
                "mechanism_category": "mechanism_category",
            },
            hypothesis_scope_field="hypothesis_version_id",
        )


class SimulationResultRepository(AppendOnlyRecordRepository[SimulationResultRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=simulation_results,
            model_type=SimulationResultRecord,
            identifier_field="simulation_result_id",
            relationship_fields={
                "hypothesis_version_id": "hypothesis_version_id",
                "model_spec_id": "model_spec_id",
                "execution_mode": "execution_mode",
            },
            hypothesis_scope_field="hypothesis_version_id",
        )


class VerificationResultRepository(ReferencedAppendOnlyRecordRepository[VerificationResultRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=verification_results,
            model_type=VerificationResultRecord,
            identifier_field="verification_result_id",
            relationship_fields={
                "hypothesis_version_id": "hypothesis_version_id",
                "mechanism_spec_id": "mechanism_spec_id",
                "mechanism_category": "mechanism_category",
                "result_category": "result_category",
                "model_spec_id": "model_spec_id",
                "model_execution_mode": "model_execution_mode",
            },
            nullable_relationship_fields={"model_spec_id", "model_execution_mode"},
            hypothesis_scope_field="hypothesis_version_id",
            reference_bindings=(
                OrderedReferenceBinding(
                    table=verification_result_simulations,
                    owner_column="verification_result_id",
                    record_field="simulation_result_ids",
                    reference_column="simulation_result_id",
                    scope_columns=(
                        "hypothesis_id",
                        "hypothesis_version_id",
                        "model_spec_id",
                        "model_execution_mode",
                    ),
                ),
            ),
        )


class CounterexampleRecordRepository(ReferencedAppendOnlyRecordRepository[CounterexampleRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=counterexample_records,
            model_type=CounterexampleRecord,
            identifier_field="counterexample_id",
            relationship_fields={
                "hypothesis_version_id": "hypothesis_version_id",
                "model_spec_id": "model_spec_id",
                "model_execution_mode": "model_execution_mode",
            },
            nullable_relationship_fields={"model_spec_id", "model_execution_mode"},
            hypothesis_scope_field="hypothesis_version_id",
            reference_bindings=(
                OrderedReferenceBinding(
                    table=counterexample_simulations,
                    owner_column="counterexample_id",
                    record_field="simulation_result_ids",
                    reference_column="simulation_result_id",
                    scope_columns=(
                        "hypothesis_id",
                        "hypothesis_version_id",
                        "model_spec_id",
                        "model_execution_mode",
                    ),
                ),
                OrderedReferenceBinding(
                    table=counterexample_verification_results,
                    owner_column="counterexample_id",
                    record_field="verification_result_ids",
                    reference_column="verification_result_id",
                    scope_columns=(
                        "hypothesis_id",
                        "hypothesis_version_id",
                        "model_spec_id",
                        "model_execution_mode",
                    ),
                ),
                OrderedReferenceBinding(
                    table=counterexample_evidence,
                    owner_column="counterexample_id",
                    record_field="evidence_ids",
                    reference_column="evidence_id",
                ),
            ),
        )


class HypothesisRevisionRepository(ReferencedAppendOnlyRecordRepository[HypothesisRevisionRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=hypothesis_revisions,
            model_type=HypothesisRevisionRecord,
            identifier_field="revision_id",
            relationship_fields={
                "hypothesis_id": "hypothesis_id",
                "prior_hypothesis_version_id": "prior_hypothesis_version_id",
                "prior_version": "prior_version",
                "resulting_hypothesis_version_id": "resulting_hypothesis_version_id",
                "resulting_version": "resulting_version",
            },
            relationship_types={"prior_version": int, "resulting_version": int},
            reference_bindings=(
                OrderedReferenceBinding(
                    table=hypothesis_revision_verification_results,
                    owner_column="revision_id",
                    record_field="triggering_verification_result_ids",
                    reference_column="verification_result_id",
                    scope_columns=("hypothesis_id",),
                ),
                OrderedReferenceBinding(
                    table=hypothesis_revision_counterexamples,
                    owner_column="revision_id",
                    record_field="considered_counterexample_ids",
                    reference_column="counterexample_id",
                    scope_columns=("hypothesis_id",),
                ),
            ),
        )


class HypothesisAdmissionDecisionRepository(
    ReferencedAppendOnlyRecordRepository[HypothesisAdmissionDecisionRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=hypothesis_admission_decisions,
            model_type=HypothesisAdmissionDecisionRecord,
            identifier_field="admission_decision_id",
            relationship_fields={
                "hypothesis_version_id": "hypothesis_version_id",
                "hypothesis_id": "hypothesis_id",
                "version": "version",
                "admission_status": "admission_status",
            },
            relationship_types={"version": int},
            pre_parent_reference_fields={"revision_ids"},
            reference_bindings=(
                OrderedReferenceBinding(
                    table=hypothesis_admission_models,
                    owner_column="admission_decision_id",
                    record_field="model_spec_ids",
                    reference_column="model_spec_id",
                    scope_columns=("hypothesis_id",),
                ),
                OrderedReferenceBinding(
                    table=hypothesis_admission_verification_results,
                    owner_column="admission_decision_id",
                    record_field="verification_result_ids",
                    reference_column="verification_result_id",
                    scope_columns=("hypothesis_id",),
                ),
                OrderedReferenceBinding(
                    table=hypothesis_admission_counterexamples,
                    owner_column="admission_decision_id",
                    record_field="counterexample_ids",
                    reference_column="counterexample_id",
                    scope_columns=("hypothesis_id",),
                ),
                OrderedReferenceBinding(
                    table=hypothesis_admission_revisions,
                    owner_column="admission_decision_id",
                    record_field="revision_ids",
                    reference_column="revision_id",
                    scope_columns=("hypothesis_id",),
                ),
            ),
        )

    def _derive_storage_values(
        self,
        record: HypothesisAdmissionDecisionRecord,
    ) -> dict[str, object]:
        values = super()._derive_storage_values(record)
        if record.revision_ids:
            values.update(
                terminal_revision_id=record.revision_ids[-1],
                terminal_revision_position=len(record.revision_ids) - 1,
            )
        else:
            values.update(terminal_revision_id=None, terminal_revision_position=None)
        return values

    def _verify_derived_storage_values(
        self,
        row: Mapping[str, object],
        record: HypothesisAdmissionDecisionRecord,
    ) -> None:
        super()._verify_derived_storage_values(row, record)
        expected_revision_id = record.revision_ids[-1] if record.revision_ids else None
        expected_position = len(record.revision_ids) - 1 if record.revision_ids else None
        _require_integrity(
            _stored_relationship_value(
                row,
                "terminal_revision_id",
                str,
                nullable=True,
            )
            == expected_revision_id,
            "terminal_revision_id does not match revision_ids",
        )
        _require_integrity(
            _stored_relationship_value(
                row,
                "terminal_revision_position",
                int,
                nullable=True,
            )
            == expected_position,
            "terminal_revision_position does not match revision_ids",
        )


class ResearchRunRepository(AppendOnlyRecordRepository[ResearchRun]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=research_runs,
            model_type=ResearchRun,
            identifier_field="run_id",
        )


class ResearchRunEventRepository(AppendOnlyRecordRepository[ResearchRunEvent]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=research_run_events,
            model_type=ResearchRunEvent,
            identifier_field="run_event_id",
            relationship_fields={"run_id": "run_id"},
        )


class ConfigurationVersionRepository(AppendOnlyRecordRepository[ConfigurationVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=configuration_versions,
            model_type=ConfigurationVersion,
            identifier_field="configuration_version_id",
        )


class SelfImprovementMeasurementRepository(
    AppendOnlyRecordRepository[SelfImprovementMeasurementRecord]
):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=self_improvement_measurements,
            model_type=SelfImprovementMeasurementRecord,
            identifier_field="measurement_id",
            relationship_fields={
                "run_id": "run_id",
                "evaluator_audit_id": "evaluator_audit_id",
            },
        )


class EvaluatorAuditRepository(AppendOnlyRecordRepository[EvaluatorAuditRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_audits,
            model_type=EvaluatorAuditRecord,
            identifier_field="evaluator_audit_id",
        )


class EvaluatorVersionRepository(AppendOnlyRecordRepository[EvaluatorVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_versions,
            model_type=EvaluatorVersion,
            identifier_field="evaluator_version_id",
        )


class EvaluatorSuccessionRepository(AppendOnlyRecordRepository[EvaluatorSuccessionDecision]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_succession_decisions,
            model_type=EvaluatorSuccessionDecision,
            identifier_field="evaluator_succession_decision_id",
            relationship_fields={
                "predecessor_evaluator_version_id": "predecessor_evaluator_version_id",
                "candidate_evaluator_version_id": "candidate_evaluator_version_id",
                "evaluator_audit_id": "evaluator_audit_id",
            },
        )


class EvaluatorCollapseRepository(AppendOnlyRecordRepository[EvaluatorCollapseRecord]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evaluator_collapse_records,
            model_type=EvaluatorCollapseRecord,
            identifier_field="evaluator_collapse_record_id",
            relationship_fields={"evaluator_version_id": "evaluator_version_id"},
        )


class ProgressPlanRepository(AppendOnlyRecordRepository[ProgressPlan]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=progress_plans,
            model_type=ProgressPlan,
            identifier_field="plan_version_id",
            relationship_fields={"run_id": "run_id"},
        )

    def list_for_run(self, run_id: str) -> tuple[ProgressPlan, ...]:
        return self._list_by_relationship("run_id", run_id)


class ProgressSubtaskRepository(AppendOnlyRecordRepository[ProgressSubtask]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=progress_subtasks,
            model_type=ProgressSubtask,
            identifier_field="subtask_id",
            relationship_fields={"plan_version_id": "plan_version_id"},
        )

    def get_many(self, subtask_ids: tuple[str, ...]) -> tuple[ProgressSubtask, ...]:
        return self._get_many(subtask_ids)


class ProgressEventRepository(AppendOnlyRecordRepository[ProgressValidationEvent]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=progress_events,
            model_type=ProgressValidationEvent,
            identifier_field="event_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
                "subtask_id": "subtask_id",
            },
        )

    def list_for_plan(self, plan_version_id: str) -> tuple[ProgressValidationEvent, ...]:
        return self._list_by_relationship("plan_version_id", plan_version_id)


class RunBudgetRepository(AppendOnlyRecordRepository[BudgetAllocation]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=run_budgets,
            model_type=BudgetAllocation,
            identifier_field="budget_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
            },
        )

    def list_for_plan(self, plan_version_id: str) -> tuple[BudgetAllocation, ...]:
        return self._list_by_relationship("plan_version_id", plan_version_id)


class RunCheckpointRepository(AppendOnlyRecordRepository[RunCheckpoint]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=run_checkpoints,
            model_type=RunCheckpoint,
            identifier_field="checkpoint_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
            },
        )


class CompletionDecisionRepository(AppendOnlyRecordRepository[CompletionDecision]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=completion_decisions,
            model_type=CompletionDecision,
            identifier_field="completion_decision_id",
            relationship_fields={
                "run_id": "run_id",
                "plan_version_id": "plan_version_id",
            },
        )


class EvidenceTrailVersionRepository(AppendOnlyRecordRepository[EvidenceTrailVersion]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_versions,
            model_type=EvidenceTrailVersion,
            identifier_field="trail_version_id",
            relationship_fields={
                "trail_id": "trail_id",
                "claim_version_id": "claim_version_id",
                "version": "version",
            },
            relationship_types={"version": int},
        )


class EvidenceTrailNodeRepository(AppendOnlyRecordRepository[EvidenceTrailNode]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_nodes,
            model_type=EvidenceTrailNode,
            identifier_field="node_id",
            relationship_fields={
                "trail_version_id": "trail_version_id",
                "evidence_id": "evidence_id",
            },
        )


class EvidenceTrailRelationRepository(AppendOnlyRecordRepository[EvidenceTrailRelation]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_relations,
            model_type=EvidenceTrailRelation,
            identifier_field="relation_id",
            relationship_fields={
                "trail_version_id": "trail_version_id",
                "source_node_id": "source_node_id",
                "target_node_id": "target_node_id",
            },
        )


class EvidenceTrailCheckRepository(AppendOnlyRecordRepository[TrailCheckResult]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_checks,
            model_type=TrailCheckResult,
            identifier_field="check_id",
            relationship_fields={"trail_version_id": "trail_version_id"},
        )


class EvidenceTrailAssessmentRepository(AppendOnlyRecordRepository[TrailAssessment]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=evidence_trail_assessments,
            model_type=TrailAssessment,
            identifier_field="assessment_id",
            relationship_fields={"trail_version_id": "trail_version_id"},
        )


class ReportSentenceBindingRepository(AppendOnlyRecordRepository[ReportSentenceBinding]):
    def __init__(self, connection: Connection) -> None:
        super().__init__(
            connection,
            table=report_sentence_bindings,
            model_type=ReportSentenceBinding,
            identifier_field="binding_id",
            relationship_fields={
                "trail_version_id": "trail_version_id",
                "claim_version_id": "claim_version_id",
            },
        )


class HarnessCampaignHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, campaign_id: str) -> tuple[str, HarnessDecisionStatus] | None:
        row = (
            self._connection.execute(
                select(harness_campaign_heads).where(
                    harness_campaign_heads.c.campaign_id == campaign_id
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, HarnessDecisionStatus], ...]:
        rows = self._connection.execute(
            select(harness_campaign_heads).order_by(harness_campaign_heads.c.campaign_id)
        ).mappings()
        return tuple(
            (
                _stored_string(dict(row), "campaign_id"),
                *self._decode_row(dict(row)),
            )
            for row in rows
        )

    def set(
        self,
        campaign_id: str,
        decision_id: str,
        status: HarnessDecisionStatus,
    ) -> None:
        validated_campaign_id = STABLE_IDENTIFIER_ADAPTER.validate_python(campaign_id)
        validated_decision_id = STABLE_IDENTIFIER_ADAPTER.validate_python(decision_id)
        validated_status = HarnessDecisionStatus(status)
        decision = HarnessDecisionRepository(self._connection).get(validated_decision_id)
        if decision is None:
            raise StorageIntegrityError(
                "storage integrity error: campaign head decision does not exist"
            )
        _require_integrity(
            decision.campaign_id == validated_campaign_id,
            "campaign head decision belongs to another campaign",
        )
        _require_integrity(
            decision.status is validated_status,
            "campaign head status does not match decision",
        )
        statement = sqlite_insert(harness_campaign_heads).values(
            campaign_id=validated_campaign_id,
            decision_id=validated_decision_id,
            status=validated_status.value,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[harness_campaign_heads.c.campaign_id],
                set_={
                    "decision_id": statement.excluded.decision_id,
                    "status": statement.excluded.status,
                },
            )
        )

    @staticmethod
    def _decode_row(row: Mapping[str, object]) -> tuple[str, HarnessDecisionStatus]:
        decision_id = _stored_string(row, "decision_id")
        try:
            status = HarnessDecisionStatus(_stored_string(row, "status"))
        except ValueError as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid harness campaign head status"
            ) from error
        return decision_id, status


class BehavioralRuleHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, rule_id: str) -> tuple[str, str, RuleStatus] | None:
        row = (
            self._connection.execute(
                select(
                    behavioral_rule_heads.c.rule_id,
                    behavioral_rule_heads.c.rule_version_id,
                    behavioral_rule_heads.c.semantic_version,
                    behavioral_rule_heads.c.status,
                ).where(behavioral_rule_heads.c.rule_id == rule_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, str, RuleStatus], ...]:
        rows = self._connection.execute(
            select(
                behavioral_rule_heads.c.rule_id,
                behavioral_rule_heads.c.rule_version_id,
                behavioral_rule_heads.c.semantic_version,
                behavioral_rule_heads.c.status,
            ).order_by(behavioral_rule_heads.c.rule_id)
        ).mappings()
        heads: list[tuple[str, str, str, RuleStatus]] = []
        for row in rows:
            stored_row = dict(row)
            rule_id = _stored_string(stored_row, "rule_id")
            rule_version_id, semantic_version, status = self._decode_row(stored_row)
            heads.append((rule_id, rule_version_id, semantic_version, status))
        return tuple(heads)

    def set(
        self,
        rule_id: str,
        rule_version_id: str,
        semantic_version: str,
        status: RuleStatus,
    ) -> None:
        try:
            validated_rule_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_id)
            validated_rule_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_version_id)
            validated_semantic_version = SEMANTIC_VERSION_ADAPTER.validate_python(semantic_version)
            validated_status = RuleStatus(status)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid behavioral rule head"
            ) from error
        stored_identity = self._connection.execute(
            select(
                behavioral_rule_versions.c.rule_id,
                behavioral_rule_versions.c.semantic_version,
                behavioral_rule_versions.c.status,
            ).where(behavioral_rule_versions.c.rule_version_id == validated_rule_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity
            == (
                validated_rule_id,
                validated_semantic_version,
                validated_status.value,
            ),
            "rule version does not match rule_id, semantic_version, and status",
        )
        statement = sqlite_insert(behavioral_rule_heads).values(
            rule_id=validated_rule_id,
            rule_version_id=validated_rule_version_id,
            semantic_version=validated_semantic_version,
            status=validated_status.value,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[behavioral_rule_heads.c.rule_id],
                set_={
                    "rule_version_id": validated_rule_version_id,
                    "semantic_version": validated_semantic_version,
                    "status": validated_status.value,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, str, RuleStatus]:
        rule_id = _stored_string(row, "rule_id")
        rule_version_id = _stored_string(row, "rule_version_id")
        semantic_version = _stored_string(row, "semantic_version")
        status_text = _stored_string(row, "status")
        try:
            validated_rule_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_id)
            validated_rule_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(rule_version_id)
            validated_semantic_version = SEMANTIC_VERSION_ADAPTER.validate_python(semantic_version)
            status = RuleStatus(status_text)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid behavioral rule head"
            ) from error
        _require_integrity(validated_rule_id == rule_id, "rule_id must be canonical")
        _require_integrity(
            validated_rule_version_id == rule_version_id,
            "rule_version_id must be canonical",
        )
        _require_integrity(
            validated_semantic_version == semantic_version,
            "semantic_version must be canonical",
        )
        stored_identity = self._connection.execute(
            select(
                behavioral_rule_versions.c.rule_id,
                behavioral_rule_versions.c.semantic_version,
                behavioral_rule_versions.c.status,
            ).where(behavioral_rule_versions.c.rule_version_id == rule_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (rule_id, semantic_version, status.value),
            "behavioral rule head references an incoherent version",
        )
        return rule_version_id, semantic_version, status


class PrimitiveHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None:
        row = (
            self._connection.execute(
                select(
                    primitive_heads.c.primitive_id,
                    primitive_heads.c.primitive_version_id,
                    primitive_heads.c.semantic_version,
                    primitive_heads.c.status,
                ).where(primitive_heads.c.primitive_id == primitive_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, str, PrimitiveStatus], ...]:
        rows = self._connection.execute(
            select(
                primitive_heads.c.primitive_id,
                primitive_heads.c.primitive_version_id,
                primitive_heads.c.semantic_version,
                primitive_heads.c.status,
            ).order_by(primitive_heads.c.primitive_id)
        ).mappings()
        heads: list[tuple[str, str, str, PrimitiveStatus]] = []
        for row in rows:
            stored_row = dict(row)
            primitive_id = _stored_string(stored_row, "primitive_id")
            primitive_version_id, semantic_version, status = self._decode_row(stored_row)
            heads.append((primitive_id, primitive_version_id, semantic_version, status))
        return tuple(heads)

    def set(
        self,
        primitive_id: str,
        primitive_version_id: str,
        semantic_version: str,
        status: PrimitiveStatus,
    ) -> None:
        try:
            validated_primitive_id = STABLE_IDENTIFIER_ADAPTER.validate_python(primitive_id)
            validated_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(primitive_version_id)
            validated_semantic_version = SEMANTIC_VERSION_ADAPTER.validate_python(semantic_version)
            validated_status = PrimitiveStatus(status)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid primitive head"
            ) from error
        stored_identity = self._connection.execute(
            select(
                primitive_versions.c.primitive_id,
                primitive_versions.c.semantic_version,
                primitive_versions.c.status,
            ).where(primitive_versions.c.primitive_version_id == validated_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity
            == (
                validated_primitive_id,
                validated_semantic_version,
                validated_status.value,
            ),
            "primitive version does not match primitive_id, semantic_version, and status",
        )
        statement = sqlite_insert(primitive_heads).values(
            primitive_id=validated_primitive_id,
            primitive_version_id=validated_version_id,
            semantic_version=validated_semantic_version,
            status=validated_status.value,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[primitive_heads.c.primitive_id],
                set_={
                    "primitive_version_id": validated_version_id,
                    "semantic_version": validated_semantic_version,
                    "status": validated_status.value,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, str, PrimitiveStatus]:
        primitive_id = _stored_string(row, "primitive_id")
        primitive_version_id = _stored_string(row, "primitive_version_id")
        semantic_version = _stored_string(row, "semantic_version")
        status_text = _stored_string(row, "status")
        try:
            validated_primitive_id = STABLE_IDENTIFIER_ADAPTER.validate_python(primitive_id)
            validated_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(primitive_version_id)
            validated_semantic_version = SEMANTIC_VERSION_ADAPTER.validate_python(semantic_version)
            status = PrimitiveStatus(status_text)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid primitive head"
            ) from error
        _require_integrity(validated_primitive_id == primitive_id, "primitive_id must be canonical")
        _require_integrity(
            validated_version_id == primitive_version_id,
            "primitive_version_id must be canonical",
        )
        _require_integrity(
            validated_semantic_version == semantic_version,
            "semantic_version must be canonical",
        )
        stored_identity = self._connection.execute(
            select(
                primitive_versions.c.primitive_id,
                primitive_versions.c.semantic_version,
                primitive_versions.c.status,
            ).where(primitive_versions.c.primitive_version_id == primitive_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (primitive_id, semantic_version, status.value),
            "primitive head references an incoherent version",
        )
        return primitive_version_id, semantic_version, status


class HypothesisHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, hypothesis_id: str) -> tuple[str, int, HypothesisAdmissionStatus] | None:
        row = (
            self._connection.execute(
                select(
                    hypothesis_heads.c.hypothesis_id,
                    hypothesis_heads.c.hypothesis_version_id,
                    hypothesis_heads.c.version,
                    hypothesis_heads.c.admission_status,
                ).where(hypothesis_heads.c.hypothesis_id == hypothesis_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, int, HypothesisAdmissionStatus], ...]:
        rows = self._connection.execute(
            select(
                hypothesis_heads.c.hypothesis_id,
                hypothesis_heads.c.hypothesis_version_id,
                hypothesis_heads.c.version,
                hypothesis_heads.c.admission_status,
            ).order_by(hypothesis_heads.c.hypothesis_id)
        ).mappings()
        heads: list[tuple[str, str, int, HypothesisAdmissionStatus]] = []
        for row in rows:
            stored_row = dict(row)
            hypothesis_id = _stored_string(stored_row, "hypothesis_id")
            version_id, version, admission_status = self._decode_row(stored_row)
            heads.append((hypothesis_id, version_id, version, admission_status))
        return tuple(heads)

    def set(
        self,
        hypothesis_id: str,
        hypothesis_version_id: str,
        version: int,
        admission_status: HypothesisAdmissionStatus,
    ) -> None:
        try:
            validated_hypothesis_id = STABLE_IDENTIFIER_ADAPTER.validate_python(hypothesis_id)
            validated_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(hypothesis_version_id)
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise ValueError("version must be a positive integer")
            validated_status = HypothesisAdmissionStatus(admission_status)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid hypothesis head"
            ) from error
        stored_identity = self._connection.execute(
            select(
                hypothesis_versions.c.hypothesis_id,
                hypothesis_versions.c.version,
                hypothesis_versions.c.admission_status,
            ).where(hypothesis_versions.c.hypothesis_version_id == validated_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (validated_hypothesis_id, version, validated_status.value),
            "hypothesis version does not match hypothesis_id, version, and admission_status",
        )
        statement = sqlite_insert(hypothesis_heads).values(
            hypothesis_id=validated_hypothesis_id,
            hypothesis_version_id=validated_version_id,
            version=version,
            admission_status=validated_status.value,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[hypothesis_heads.c.hypothesis_id],
                set_={
                    "hypothesis_version_id": validated_version_id,
                    "version": version,
                    "admission_status": validated_status.value,
                },
            )
        )

    def _decode_row(
        self,
        row: Mapping[str, object],
    ) -> tuple[str, int, HypothesisAdmissionStatus]:
        hypothesis_id = _stored_string(row, "hypothesis_id")
        hypothesis_version_id = _stored_string(row, "hypothesis_version_id")
        version = _stored_integer(row, "version")
        admission_status_text = _stored_string(row, "admission_status")
        try:
            validated_hypothesis_id = STABLE_IDENTIFIER_ADAPTER.validate_python(hypothesis_id)
            validated_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(hypothesis_version_id)
            if version < 1:
                raise ValueError("version must be positive")
            admission_status = HypothesisAdmissionStatus(admission_status_text)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid hypothesis head"
            ) from error
        _require_integrity(
            validated_hypothesis_id == hypothesis_id,
            "hypothesis_id must be canonical",
        )
        _require_integrity(
            validated_version_id == hypothesis_version_id,
            "hypothesis_version_id must be canonical",
        )
        stored_identity = self._connection.execute(
            select(
                hypothesis_versions.c.hypothesis_id,
                hypothesis_versions.c.version,
                hypothesis_versions.c.admission_status,
            ).where(hypothesis_versions.c.hypothesis_version_id == hypothesis_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (hypothesis_id, version, admission_status.value),
            "hypothesis head references an incoherent version",
        )
        return hypothesis_version_id, version, admission_status


class ResearchRunHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, run_id: str) -> str | None:
        return self._connection.execute(
            select(research_run_heads.c.run_event_id).where(research_run_heads.c.run_id == run_id)
        ).scalar_one_or_none()

    def list_all(self) -> tuple[tuple[str, str], ...]:
        rows = self._connection.execute(
            select(
                research_run_heads.c.run_id,
                research_run_heads.c.run_event_id,
            ).order_by(research_run_heads.c.run_id)
        ).mappings()
        return tuple(
            (
                _stored_string(dict(row), "run_id"),
                _stored_string(dict(row), "run_event_id"),
            )
            for row in rows
        )

    def set(self, run_id: str, run_event_id: str) -> None:
        event_run_id = self._connection.execute(
            select(research_run_events.c.run_id).where(
                research_run_events.c.run_event_id == run_event_id
            )
        ).scalar_one_or_none()
        _require_integrity(
            event_run_id == run_id,
            "run_event_id does not belong to run_id",
        )
        statement = sqlite_insert(research_run_heads).values(
            run_id=run_id,
            run_event_id=run_event_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[research_run_heads.c.run_id],
                set_={"run_event_id": run_event_id},
            )
        )


class EvaluatorHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self) -> str | None:
        rows = tuple(
            self._connection.execute(
                select(
                    evaluator_heads.c.singleton_id,
                    evaluator_heads.c.evaluator_version_id,
                )
            ).mappings()
        )
        if not rows:
            return None
        _require_integrity(len(rows) == 1, "evaluator head must contain one singleton row")
        row = rows[0]
        _require_integrity(row["singleton_id"] == 1, "evaluator head singleton_id must equal 1")
        evaluator_version_id = _stored_string(dict(row), "evaluator_version_id")
        _require_integrity(
            self._connection.execute(
                select(evaluator_versions.c.evaluator_version_id).where(
                    evaluator_versions.c.evaluator_version_id == evaluator_version_id
                )
            ).scalar_one_or_none()
            == evaluator_version_id,
            "evaluator head references a missing evaluator version",
        )
        return evaluator_version_id

    def set(self, evaluator_version_id: str) -> None:
        try:
            validated_version_id = STABLE_IDENTIFIER_ADAPTER.validate_python(evaluator_version_id)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid evaluator head"
            ) from error
        _require_integrity(
            self._connection.execute(
                select(evaluator_versions.c.evaluator_version_id).where(
                    evaluator_versions.c.evaluator_version_id == validated_version_id
                )
            ).scalar_one_or_none()
            == validated_version_id,
            "evaluator head references a missing evaluator version",
        )
        statement = sqlite_insert(evaluator_heads).values(
            singleton_id=1,
            evaluator_version_id=validated_version_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[evaluator_heads.c.singleton_id],
                set_={"evaluator_version_id": validated_version_id},
            )
        )


class ProgressHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, run_id: str) -> tuple[str, str] | None:
        row = (
            self._connection.execute(
                select(
                    progress_heads.c.run_id,
                    progress_heads.c.plan_version_id,
                    progress_heads.c.last_event_id,
                ).where(progress_heads.c.run_id == run_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, str], ...]:
        rows = self._connection.execute(
            select(
                progress_heads.c.run_id,
                progress_heads.c.plan_version_id,
                progress_heads.c.last_event_id,
            ).order_by(progress_heads.c.run_id)
        ).mappings()
        heads: list[tuple[str, str, str]] = []
        for row in rows:
            stored_row = dict(row)
            run_id = _stored_string(stored_row, "run_id")
            plan_version_id, last_event_id = self._decode_row(stored_row)
            heads.append((run_id, plan_version_id, last_event_id))
        return tuple(heads)

    def set(self, run_id: str, plan_version_id: str, last_event_id: str) -> None:
        plan_run_id = self._connection.execute(
            select(progress_plans.c.run_id).where(
                progress_plans.c.plan_version_id == plan_version_id
            )
        ).scalar_one_or_none()
        _require_integrity(plan_run_id == run_id, "plan_version_id does not belong to run_id")
        event_relationship = self._connection.execute(
            select(progress_events.c.run_id, progress_events.c.plan_version_id).where(
                progress_events.c.event_id == last_event_id
            )
        ).one_or_none()
        _require_integrity(event_relationship is not None, "last_event_id does not exist")
        _require_integrity(
            event_relationship == (run_id, plan_version_id),
            "last_event_id does not belong to run_id and plan_version_id",
        )
        statement = sqlite_insert(progress_heads).values(
            run_id=run_id,
            plan_version_id=plan_version_id,
            last_event_id=last_event_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[progress_heads.c.run_id],
                set_={
                    "plan_version_id": plan_version_id,
                    "last_event_id": last_event_id,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, str]:
        run_id = _stored_string(row, "run_id")
        plan_version_id = _stored_string(row, "plan_version_id")
        last_event_id = _stored_string(row, "last_event_id")
        plan_run_id = self._connection.execute(
            select(progress_plans.c.run_id).where(
                progress_plans.c.plan_version_id == plan_version_id
            )
        ).scalar_one_or_none()
        _require_integrity(plan_run_id == run_id, "progress head references an incoherent plan")
        event_relationship = self._connection.execute(
            select(progress_events.c.run_id, progress_events.c.plan_version_id).where(
                progress_events.c.event_id == last_event_id
            )
        ).one_or_none()
        _require_integrity(
            event_relationship == (run_id, plan_version_id),
            "progress head references an incoherent event",
        )
        return plan_version_id, last_event_id


class EvidenceTrailHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, trail_id: str) -> tuple[str, int] | None:
        row = (
            self._connection.execute(
                select(
                    evidence_trail_heads.c.trail_id,
                    evidence_trail_heads.c.trail_version_id,
                    evidence_trail_heads.c.version,
                ).where(evidence_trail_heads.c.trail_id == trail_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[tuple[str, str, int], ...]:
        rows = self._connection.execute(
            select(
                evidence_trail_heads.c.trail_id,
                evidence_trail_heads.c.trail_version_id,
                evidence_trail_heads.c.version,
            ).order_by(evidence_trail_heads.c.trail_id)
        ).mappings()
        heads: list[tuple[str, str, int]] = []
        for row in rows:
            stored_row = dict(row)
            trail_id = _stored_string(stored_row, "trail_id")
            trail_version_id, version = self._decode_row(stored_row)
            heads.append((trail_id, trail_version_id, version))
        return tuple(heads)

    def set(self, trail_id: str, trail_version_id: str, version: int) -> None:
        validated_version = _stored_integer({"version": version}, "version")
        stored_identity = self._connection.execute(
            select(
                evidence_trail_versions.c.trail_id,
                evidence_trail_versions.c.version,
            ).where(evidence_trail_versions.c.trail_version_id == trail_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (trail_id, validated_version),
            "trail_version_id does not match trail_id and version",
        )
        current = self.get(trail_id)
        if current == (trail_version_id, validated_version):
            return
        if current is None:
            _require_integrity(
                validated_version == 1,
                "evidence trail head must begin at version 1",
            )
        else:
            _require_integrity(
                validated_version == current[1] + 1,
                "evidence trail head requires the exact successor of the current version",
            )
        statement = sqlite_insert(evidence_trail_heads).values(
            trail_id=trail_id,
            trail_version_id=trail_version_id,
            version=validated_version,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[evidence_trail_heads.c.trail_id],
                set_={
                    "trail_version_id": trail_version_id,
                    "version": validated_version,
                },
            )
        )

    def _decode_row(self, row: Mapping[str, object]) -> tuple[str, int]:
        trail_id = _stored_string(row, "trail_id")
        trail_version_id = _stored_string(row, "trail_version_id")
        version = _stored_integer(row, "version")
        stored_identity = self._connection.execute(
            select(
                evidence_trail_versions.c.trail_id,
                evidence_trail_versions.c.version,
            ).where(evidence_trail_versions.c.trail_version_id == trail_version_id)
        ).one_or_none()
        _require_integrity(
            stored_identity == (trail_id, version),
            "evidence trail head references an incoherent version",
        )
        return trail_version_id, version
