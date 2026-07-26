from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


class HarnessVariant(StrEnum):
    UNCHANGED_HARNESS_SINGLE_ATTEMPT = "UNCHANGED_HARNESS_SINGLE_ATTEMPT"
    UNCHANGED_HARNESS_BEST_OF_N = "UNCHANGED_HARNESS_BEST_OF_N"
    UNCHANGED_HARNESS_RETRY_WITH_FEEDBACK = "UNCHANGED_HARNESS_RETRY_WITH_FEEDBACK"
    UNCHANGED_HARNESS_TASK_LEVEL_SEARCH = "UNCHANGED_HARNESS_TASK_LEVEL_SEARCH"
    RANDOM_HARNESS_SEARCH = "RANDOM_HARNESS_SEARCH"
    SIMPLE_PARAMETER_SEARCH = "SIMPLE_PARAMETER_SEARCH"
    EVOLVED_HARNESS = "EVOLVED_HARNESS"


class HarnessPartition(StrEnum):
    HARNESS_DISCOVERY_TASKS = "HARNESS_DISCOVERY_TASKS"
    HARNESS_VALIDATION_TASKS = "HARNESS_VALIDATION_TASKS"
    HARNESS_TRANSFER_TASKS = "HARNESS_TRANSFER_TASKS"
    HARNESS_REGRESSION_TASKS = "HARNESS_REGRESSION_TASKS"
    HARNESS_SAFETY_TASKS = "HARNESS_SAFETY_TASKS"


class HarnessDecisionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    DISCOVERY_GAIN = "DISCOVERY_GAIN"
    VALIDATION_GAIN = "VALIDATION_GAIN"
    TRANSFER_VALIDATED = "TRANSFER_VALIDATED"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    BENCHMARK_SPECIFIC = "BENCHMARK_SPECIFIC"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"
    ADMITTED = "ADMITTED"
    ROLLED_BACK = "ROLLED_BACK"


class FeedbackMode(StrEnum):
    NONE = "NONE"
    PER_ATTEMPT = "PER_ATTEMPT"
    RETRY_WITH_FEEDBACK = "RETRY_WITH_FEEDBACK"
    TASK_LEVEL = "TASK_LEVEL"


class FixedCheckerKind(StrEnum):
    EXACT_BYTES = "EXACT_BYTES"


class HarnessConfoundCode(StrEnum):
    MODEL_ID_MISMATCH = "MODEL_ID_MISMATCH"
    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    ADAPTER_ID_MISMATCH = "ADAPTER_ID_MISMATCH"
    FEEDBACK_MODE_MISMATCH = "FEEDBACK_MODE_MISMATCH"
    TOOL_IDS_MISMATCH = "TOOL_IDS_MISMATCH"
    ATTEMPTS_MISMATCH = "ATTEMPTS_MISMATCH"
    TOKEN_LIMIT_MISMATCH = "TOKEN_LIMIT_MISMATCH"
    REASONING_LIMIT_MISMATCH = "REASONING_LIMIT_MISMATCH"
    EVALUATOR_CALL_LIMIT_MISMATCH = "EVALUATOR_CALL_LIMIT_MISMATCH"
    WALL_CLOCK_SECONDS_MISMATCH = "WALL_CLOCK_SECONDS_MISMATCH"
    COST_LIMIT_MISMATCH = "COST_LIMIT_MISMATCH"
    HUMAN_INTERVENTION_LIMIT_MISMATCH = "HUMAN_INTERVENTION_LIMIT_MISMATCH"
    TASK_SET_MISMATCH = "TASK_SET_MISMATCH"
    EVALUATOR_CHANGED = "EVALUATOR_CHANGED"
    EXTRA_INFERENCE = "EXTRA_INFERENCE"
    FEEDBACK_DIFFERENCE = "FEEDBACK_DIFFERENCE"
    LEAKAGE = "LEAKAGE"
    STOPPING_DIFFERENCE = "STOPPING_DIFFERENCE"


BUDGET_COMPARISON_FIELDS = (
    "model_id",
    "model_version",
    "adapter_id",
    "feedback_mode",
    "tool_ids",
    "attempts",
    "token_limit",
    "reasoning_limit",
    "evaluator_call_limit",
    "wall_clock_seconds",
    "cost_limit",
    "human_intervention_limit",
)


class EvaluationBudget(_StrictFrozenModel):
    model_id: StableIdentifier
    model_version: StableIdentifier
    adapter_id: StableIdentifier | None
    feedback_mode: FeedbackMode
    tool_ids: tuple[StableIdentifier, ...]
    attempts: int = Field(strict=True, ge=1)
    token_limit: int = Field(strict=True, ge=1)
    reasoning_limit: int = Field(strict=True, ge=1)
    evaluator_call_limit: int = Field(strict=True, ge=1)
    wall_clock_seconds: Decimal = Field(gt=0)
    cost_limit: Decimal = Field(ge=0)
    human_intervention_limit: int = Field(strict=True, ge=0)

    @field_validator("tool_ids")
    @classmethod
    def require_unique_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("tool_ids must contain unique identifiers")
        return value

    @field_validator("wall_clock_seconds", "cost_limit")
    @classmethod
    def require_finite_decimals(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("budget values must be finite")
        return value


class BudgetComparison(_StrictFrozenModel):
    comparable: bool
    mismatches: tuple[StableIdentifier, ...]

    @model_validator(mode="after")
    def require_exact_state(self) -> Self:
        if self.comparable != (not self.mismatches):
            raise ValueError("budget comparability must exactly match mismatches")
        if len(set(self.mismatches)) != len(self.mismatches):
            raise ValueError("budget mismatch dimensions must be unique")
        if any(item not in BUDGET_COMPARISON_FIELDS for item in self.mismatches):
            raise ValueError("unknown budget mismatch dimension")
        return self


def compare_evaluation_budgets(
    baseline: EvaluationBudget,
    candidate: EvaluationBudget,
) -> BudgetComparison:
    left = EvaluationBudget.model_validate(baseline)
    right = EvaluationBudget.model_validate(candidate)
    mismatches = tuple(
        field_name
        for field_name in BUDGET_COMPARISON_FIELDS
        if getattr(left, field_name) != getattr(right, field_name)
    )
    return BudgetComparison(comparable=not mismatches, mismatches=mismatches)


class VariantEvaluationBudget(_StrictFrozenModel):
    budget_id: StableIdentifier
    variant: HarnessVariant
    budget: EvaluationBudget


def partition_manifest_hash(
    *,
    campaign_id: str,
    campaign_version: int,
    partition: HarnessPartition,
    task_ids: tuple[str, ...],
) -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "campaign_id": campaign_id,
                "campaign_version": campaign_version,
                "partition": partition.value,
                "task_ids": list(task_ids),
            }
        )
    )


class CampaignPartitionManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    partition_manifest_id: StableIdentifier
    campaign_id: StableIdentifier
    campaign_version: int = Field(strict=True, ge=1)
    partition: HarnessPartition
    task_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    manifest_hash: Sha256Hex
    protected_content_hash: Sha256Hex | None
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_content_addressed_unique_tasks(self) -> Self:
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task_ids must contain unique identifiers")
        expected = partition_manifest_hash(
            campaign_id=self.campaign_id,
            campaign_version=self.campaign_version,
            partition=self.partition,
            task_ids=self.task_ids,
        )
        if self.manifest_hash != expected:
            raise ValueError("manifest_hash must content-address exact partition membership")
        return self


class HarnessCampaign(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    campaign_id: StableIdentifier
    version: int = Field(strict=True, ge=1)
    variants: tuple[HarnessVariant, ...] = Field(min_length=2)
    baseline_variant: HarnessVariant
    candidate_variant: HarnessVariant
    baseline_harness_version_id: StableIdentifier
    candidate_harness_version_id: StableIdentifier
    rollback_harness_version_id: StableIdentifier
    model_id: StableIdentifier
    model_version: StableIdentifier
    adapter_id: StableIdentifier | None
    evaluator: ActorIdentity
    evaluator_version_id: StableIdentifier
    candidate_producer: ActorIdentity
    coordinator: ActorIdentity
    partitions: tuple[CampaignPartitionManifest, ...] = Field(min_length=1)
    budgets: tuple[VariantEvaluationBudget, ...]
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_immutable_exclusive_campaign_version(self) -> Self:
        if len(set(self.variants)) != len(self.variants):
            raise ValueError("campaign variants must be unique")
        if (
            self.baseline_variant == self.candidate_variant
            or self.baseline_variant not in self.variants
            or self.candidate_variant not in self.variants
        ):
            raise ValueError("campaign requires distinct included baseline and candidate variants")
        if self.baseline_harness_version_id == self.candidate_harness_version_id:
            raise ValueError("candidate harness version must differ from baseline")
        if self.rollback_harness_version_id != self.baseline_harness_version_id:
            raise ValueError("campaign rollback target must be the baseline harness version")
        partitions = tuple(item.partition for item in self.partitions)
        if len(partitions) != len(set(partitions)) or set(partitions) != set(HarnessPartition):
            raise ValueError("campaign requires exactly one of all partition manifests")
        all_tasks = tuple(task for item in self.partitions for task in item.task_ids)
        if len(all_tasks) != len(set(all_tasks)):
            raise ValueError("each task must belong to exactly one partition")
        for item in self.partitions:
            if item.campaign_id != self.campaign_id or item.campaign_version != self.version:
                raise ValueError("partition manifest must bind the exact campaign version")
            if item.governing_policy_hash != self.governing_policy_hash:
                raise ValueError("partition manifest must bind the campaign policy")
        budget_variants = tuple(item.variant for item in self.budgets)
        if len(budget_variants) != len(set(budget_variants)) or set(budget_variants) != set(
            self.variants
        ):
            raise ValueError("campaign requires exactly one of all variant budgets")
        if any(
            item.budget.model_id != self.model_id
            or item.budget.model_version != self.model_version
            or item.budget.adapter_id != self.adapter_id
            for item in self.budgets
        ):
            raise ValueError("variant budgets must bind the campaign model identity")
        if self.coordinator.kind is not ActorKind.HUMAN or not are_independent(
            self.coordinator, self.candidate_producer
        ):
            raise ValueError("campaign coordinator must be an independent human")
        if not are_independent(self.evaluator, self.candidate_producer):
            raise ValueError("campaign evaluator must be independent of the candidate producer")
        return self


class PublicTaskInput(_StrictFrozenModel):
    campaign_id: StableIdentifier
    campaign_version: int = Field(strict=True, ge=1)
    task_id: StableIdentifier
    partition: HarnessPartition
    payload: bytes
    payload_hash: Sha256Hex

    @model_validator(mode="after")
    def require_payload_hash(self) -> Self:
        if sha256_hex(self.payload) != self.payload_hash:
            raise ValueError("public task payload hash mismatch")
        return self


class FixedCheckerConfiguration(_StrictFrozenModel):
    checker_id: StableIdentifier
    checker_version: StableIdentifier
    checker_kind: FixedCheckerKind
    configuration_hash: Sha256Hex
    metric_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evaluator_version_id: StableIdentifier

    @field_validator("metric_ids")
    @classmethod
    def require_unique_metrics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metric_ids must contain unique identifiers")
        return value


class MetricValue(_StrictFrozenModel):
    metric_id: StableIdentifier
    value: Decimal

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("metric value must be finite")
        return value


class ProtectedCheckerResult(_StrictFrozenModel):
    result_id: StableIdentifier
    campaign_id: StableIdentifier
    task_id: StableIdentifier
    expected_output_hash: Sha256Hex
    candidate_output_hash: Sha256Hex
    checker_id: StableIdentifier
    checker_version: StableIdentifier
    outcome: AssessmentOutcome
    metric_values: tuple[MetricValue, ...]
    evaluated_at: UtcTimestamp

    @field_validator("metric_values")
    @classmethod
    def require_unique_metric_ids(
        cls,
        value: tuple[MetricValue, ...],
    ) -> tuple[MetricValue, ...]:
        metric_ids = tuple(item.metric_id for item in value)
        if len(set(metric_ids)) != len(metric_ids):
            raise ValueError("metric_values must have unique metric identifiers")
        return value


class CampaignIteration(_StrictFrozenModel):
    iteration_index: int = Field(strict=True, ge=0)
    observation_id: StableIdentifier
    partition_manifest_id: StableIdentifier
    task_id: StableIdentifier
    partition: HarnessPartition
    variant: HarnessVariant
    budget_id: StableIdentifier
    attempt: int = Field(strict=True, ge=1)
    candidate_output_hash: Sha256Hex
    result_id: StableIdentifier | None
    outcome: AssessmentOutcome | None
    negative_result: bool
    evaluator_version_id: StableIdentifier
    observed_at: UtcTimestamp

    @model_validator(mode="after")
    def require_result_outcome_pair(self) -> Self:
        if (self.result_id is None) != (self.outcome is None):
            raise ValueError("iteration result_id and outcome must be present together")
        if self.negative_result and self.outcome is AssessmentOutcome.PASSED:
            raise ValueError("negative iteration cannot retain a passed outcome")
        return self


class PartitionMetric(_StrictFrozenModel):
    partition: HarnessPartition
    metric_id: StableIdentifier
    baseline_value: Decimal
    candidate_value: Decimal
    higher_is_better: bool
    catastrophic_regression: bool
    result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evaluator_version_id: StableIdentifier

    @field_validator("baseline_value", "candidate_value")
    @classmethod
    def require_finite_metrics(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("partition metric values must be finite")
        return value

    @field_validator("result_ids")
    @classmethod
    def require_unique_results(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metric result_ids must be unique")
        return value

    @property
    def improved(self) -> bool:
        comparison = self.candidate_value.compare(self.baseline_value)
        return comparison > 0 if self.higher_is_better else comparison < 0

    @property
    def regressed(self) -> bool:
        comparison = self.candidate_value.compare(self.baseline_value)
        return comparison < 0 if self.higher_is_better else comparison > 0


class HarnessConfound(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    confound_id: StableIdentifier
    campaign_id: StableIdentifier
    code: HarnessConfoundCode
    description: NonBlankText
    affected_variant: HarnessVariant | None
    resolved: bool
    independent_analysis_id: StableIdentifier | None
    recorded_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_resolution_evidence(self) -> Self:
        if self.resolved != (self.independent_analysis_id is not None):
            raise ValueError("resolved confounds require an independent analysis identifier")
        return self


class HarnessRollback(_StrictFrozenModel):
    rollback_event_id: StableIdentifier
    target_harness_version_id: StableIdentifier
    reason: NonBlankText
    rolled_back_at: UtcTimestamp


class HarnessCampaignReport(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    campaign: HarnessCampaign
    expected_iteration_count: int = Field(strict=True, ge=0)
    iterations: tuple[CampaignIteration, ...]
    negative_observation_ids: tuple[StableIdentifier, ...]
    budget_comparisons: tuple[BudgetComparison, ...]
    metrics: tuple[PartitionMetric, ...] = Field(min_length=1)
    confounds: tuple[HarnessConfound, ...]
    evaluator_audit_id: StableIdentifier
    evaluator_audit_passed: bool
    measurement_id: StableIdentifier
    measurement_accepted: bool
    rollback: HarnessRollback | None
    admission_requested: bool
    decision_authority: ActorIdentity
    reported_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_complete_noncollapsed_report(self) -> Self:
        if len(self.iterations) != self.expected_iteration_count or tuple(
            item.iteration_index for item in self.iterations
        ) != tuple(range(self.expected_iteration_count)):
            raise ValueError("report must retain the complete iteration history")
        observation_ids = tuple(item.observation_id for item in self.iterations)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("report iteration observations must be unique")
        expected_negatives = tuple(
            item.observation_id for item in self.iterations if item.negative_result
        )
        if self.negative_observation_ids != expected_negatives:
            raise ValueError("report must exactly retain every negative observation")
        manifest_by_partition = {
            manifest.partition: manifest for manifest in self.campaign.partitions
        }
        budget_by_variant = {item.variant: item for item in self.campaign.budgets}
        for iteration in self.iterations:
            manifest = manifest_by_partition[iteration.partition]
            budget = budget_by_variant.get(iteration.variant)
            if (
                iteration.partition_manifest_id != manifest.partition_manifest_id
                or iteration.task_id not in manifest.task_ids
                or budget is None
                or iteration.budget_id != budget.budget_id
                or iteration.attempt > budget.budget.attempts
            ):
                raise ValueError("iteration must bind exact campaign partition and budget")
        baseline_budget = budget_by_variant[self.campaign.baseline_variant].budget
        expected_comparisons = tuple(
            compare_evaluation_budgets(baseline_budget, item.budget)
            for item in self.campaign.budgets
            if item.variant is not self.campaign.baseline_variant
        )
        if self.budget_comparisons != expected_comparisons:
            raise ValueError("report budget comparisons must be complete and exact")
        metric_keys = tuple((item.partition, item.metric_id) for item in self.metrics)
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("partition metrics must be unique")
        if set(item.partition for item in self.metrics) != set(HarnessPartition):
            raise ValueError("report must preserve all five partition metric families")
        evaluator_versions = {
            self.campaign.evaluator_version_id,
            *(item.evaluator_version_id for item in self.iterations),
            *(item.evaluator_version_id for item in self.metrics),
        }
        confound_codes = {item.code for item in self.confounds}
        if (
            len(evaluator_versions) > 1
            and HarnessConfoundCode.EVALUATOR_CHANGED not in confound_codes
        ):
            raise ValueError("evaluator changes require an EVALUATOR_CHANGED confound")
        confound_ids = tuple(item.confound_id for item in self.confounds)
        if len(confound_ids) != len(set(confound_ids)):
            raise ValueError("report confounds must be unique")
        if any(
            item.campaign_id != self.campaign.campaign_id
            or item.governing_policy_hash != self.governing_policy_hash
            for item in self.confounds
        ):
            raise ValueError("report confounds must bind the campaign and policy")
        if self.rollback is not None and (
            self.rollback.target_harness_version_id != self.campaign.rollback_harness_version_id
        ):
            raise ValueError("report rollback must use the campaign rollback target")
        if (
            self.governing_policy_hash != self.campaign.governing_policy_hash
            or self.decision_authority.kind is not ActorKind.HUMAN
            or not are_independent(self.decision_authority, self.campaign.candidate_producer)
        ):
            raise ValueError(
                "report requires the campaign policy and an independent human authority"
            )
        return self


class HarnessDecision(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    decision_id: StableIdentifier
    campaign_id: StableIdentifier
    status: HarnessDecisionStatus
    admitted: bool
    rationale: tuple[NonBlankText, ...] = Field(min_length=1)
    authority: ActorIdentity
    rollback_target_id: StableIdentifier | None
    evaluator_audit_id: StableIdentifier
    measurement_id: StableIdentifier
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def require_exact_admission_state(self) -> Self:
        if self.admitted != (self.status is HarnessDecisionStatus.ADMITTED):
            raise ValueError("admitted must be true exactly for ADMITTED status")
        if self.status is HarnessDecisionStatus.ROLLED_BACK and self.rollback_target_id is None:
            raise ValueError("rolled-back decision requires the exact rollback target")
        if (
            self.status is not HarnessDecisionStatus.ROLLED_BACK
            and self.rollback_target_id is not None
        ):
            raise ValueError("only a rolled-back decision may carry a rollback target")
        return self


__all__ = [
    "BUDGET_COMPARISON_FIELDS",
    "BudgetComparison",
    "CampaignIteration",
    "CampaignPartitionManifest",
    "EvaluationBudget",
    "FeedbackMode",
    "FixedCheckerConfiguration",
    "FixedCheckerKind",
    "HarnessCampaign",
    "HarnessCampaignReport",
    "HarnessConfound",
    "HarnessConfoundCode",
    "HarnessDecision",
    "HarnessDecisionStatus",
    "HarnessPartition",
    "HarnessRollback",
    "HarnessVariant",
    "MetricValue",
    "PartitionMetric",
    "ProtectedCheckerResult",
    "PublicTaskInput",
    "VariantEvaluationBudget",
    "compare_evaluation_budgets",
    "partition_manifest_hash",
]
