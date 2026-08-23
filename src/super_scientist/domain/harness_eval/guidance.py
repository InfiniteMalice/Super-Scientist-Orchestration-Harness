from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from super_scientist.domain.harness_eval.models import EvaluationBudget
from super_scientist.domain.improvement.models import AssessmentOutcome, ResourceUsage
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.domain.procedures.models import (
    MethodDirectionStatus,
    ProcedureValidationStatus,
)

MAX_EVALUATION_ITEMS = 256
MAX_EVALUATION_IDENTIFIER_LENGTH = 200

BoundedIdentifier = Annotated[
    StableIdentifier,
    Field(max_length=MAX_EVALUATION_IDENTIFIER_LENGTH),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )


def _canonical_record_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    if isinstance(record, BaseModel):
        payload = record.model_dump(mode="json")
    else:
        payload = to_jsonable_python(dict(record))
    for field_name in {"content_hash", *(exclude_fields or set())}:
        payload.pop(field_name, None)
    return sha256_hex(canonical_json_bytes(payload))


def _require_canonical_unique(
    values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if len(values) != len(set(values)) or values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must be unique and sorted in canonical order")
    return values


class GuidanceCondition(StrEnum):
    FULL_PROCEDURE_GUIDANCE = "FULL_PROCEDURE_GUIDANCE"
    METHOD_ONLY = "METHOD_ONLY"
    OBJECTIVE_AND_DATA_ONLY = "OBJECTIVE_AND_DATA_ONLY"
    OBJECTIVE_DATA_WITH_DISTRACTORS = "OBJECTIVE_DATA_WITH_DISTRACTORS"


class EvaluationMetricComponent(StrEnum):
    TASK_SCORE = "TASK_SCORE"
    PROCEDURE_COMPILATION_STATUS = "PROCEDURE_COMPILATION_STATUS"
    PROCEDURE_EXECUTION_SUCCESS = "PROCEDURE_EXECUTION_SUCCESS"
    METHOD_SELECTION_RESULT = "METHOD_SELECTION_RESULT"
    RESOURCE_USAGE = "RESOURCE_USAGE"
    FINAL_VALIDATION = "FINAL_VALIDATION"


class EvaluationReferenceComponent(StrEnum):
    OUTPUT_ARTIFACT = "OUTPUT_ARTIFACT"
    TRACE = "TRACE"
    VERIFIER_RESULT = "VERIFIER_RESULT"
    REWARD_ASSESSMENT = "REWARD_ASSESSMENT"


class MetricMissingReason(StrEnum):
    NOT_OBSERVED = "NOT_OBSERVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    EXECUTION_NOT_REACHED = "EXECUTION_NOT_REACHED"
    INSTRUMENTATION_UNAVAILABLE = "INSTRUMENTATION_UNAVAILABLE"
    VALIDATION_NOT_RUN = "VALIDATION_NOT_RUN"


class MissingnessSide(StrEnum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    BOTH = "BOTH"


class ExecutionFailureKind(StrEnum):
    COMPILATION_FAILURE = "COMPILATION_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    RESOURCE_EXHAUSTION = "RESOURCE_EXHAUSTION"
    TERMINATION_FAILURE = "TERMINATION_FAILURE"


class RecoveryOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class EvaluationConfoundCode(StrEnum):
    PROTOCOL_ID_MISMATCH = "PROTOCOL_ID_MISMATCH"
    PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
    OBJECTIVE_MISMATCH = "OBJECTIVE_MISMATCH"
    TASK_ID_MISMATCH = "TASK_ID_MISMATCH"
    TASK_INPUT_MISMATCH = "TASK_INPUT_MISMATCH"
    OUTPUT_SCHEMA_MISMATCH = "OUTPUT_SCHEMA_MISMATCH"
    MODEL_ID_MISMATCH = "MODEL_ID_MISMATCH"
    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    HARNESS_ID_MISMATCH = "HARNESS_ID_MISMATCH"
    HARNESS_VERSION_MISMATCH = "HARNESS_VERSION_MISMATCH"
    VERIFIER_ID_MISMATCH = "VERIFIER_ID_MISMATCH"
    VERIFIER_VERSION_MISMATCH = "VERIFIER_VERSION_MISMATCH"
    CHECKER_ID_MISMATCH = "CHECKER_ID_MISMATCH"
    CHECKER_VERSION_MISMATCH = "CHECKER_VERSION_MISMATCH"
    ARTIFACTS_MISMATCH = "ARTIFACTS_MISMATCH"
    DISTRACTOR_DECLARATION_MISMATCH = "DISTRACTOR_DECLARATION_MISMATCH"
    SEED_MISMATCH = "SEED_MISMATCH"
    EVALUATION_BUDGET_MISMATCH = "EVALUATION_BUDGET_MISMATCH"
    SAME_GUIDANCE_CONDITION = "SAME_GUIDANCE_CONDITION"


class MetricMissingness(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    component: EvaluationMetricComponent
    reason: MetricMissingReason


class MetricMissingnessDelta(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    component: EvaluationMetricComponent
    affected_side: MissingnessSide
    left_reason: MetricMissingReason | None
    right_reason: MetricMissingReason | None

    @model_validator(mode="after")
    def require_exact_side_and_reasons(self) -> Self:
        expected_side = (
            MissingnessSide.BOTH
            if self.left_reason is not None and self.right_reason is not None
            else MissingnessSide.LEFT
            if self.left_reason is not None
            else MissingnessSide.RIGHT
            if self.right_reason is not None
            else None
        )
        if expected_side is None or self.affected_side is not expected_side:
            raise ValueError("missingness delta side must exactly match its typed reasons")
        return self


class ReferenceMissingness(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    component: EvaluationReferenceComponent
    reason: MetricMissingReason


class ExecutionFailureEvent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: BoundedIdentifier
    kind: ExecutionFailureKind
    procedure_step_id: BoundedIdentifier | None


class RecoveryAttemptEvent(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    event_id: BoundedIdentifier
    attempt: int = Field(strict=True, ge=1)
    target_step_id: BoundedIdentifier
    outcome: RecoveryOutcome


_NULLABLE_METRIC_FIELDS: tuple[tuple[str, EvaluationMetricComponent], ...] = (
    ("task_score", EvaluationMetricComponent.TASK_SCORE),
    ("procedure_compilation_status", EvaluationMetricComponent.PROCEDURE_COMPILATION_STATUS),
    ("procedure_execution_success", EvaluationMetricComponent.PROCEDURE_EXECUTION_SUCCESS),
    ("method_selection_result", EvaluationMetricComponent.METHOD_SELECTION_RESULT),
    ("resource_usage", EvaluationMetricComponent.RESOURCE_USAGE),
    ("final_validation", EvaluationMetricComponent.FINAL_VALIDATION),
)


class EvaluationMetricVector(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task_score: Decimal | None = Field(default=None, ge=0, le=1)
    procedure_compilation_status: ProcedureValidationStatus | None
    procedure_execution_success: bool | None
    method_selection_result: MethodDirectionStatus | None
    execution_failure_events: tuple[ExecutionFailureEvent, ...] = Field(
        max_length=MAX_EVALUATION_ITEMS
    )
    recovery_attempt_events: tuple[RecoveryAttemptEvent, ...] = Field(
        max_length=MAX_EVALUATION_ITEMS
    )
    resource_usage: ResourceUsage | None
    final_validation: AssessmentOutcome | None
    missingness: tuple[MetricMissingness, ...] = Field(max_length=len(_NULLABLE_METRIC_FIELDS))

    @field_validator("task_score")
    @classmethod
    def require_finite_task_score(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("task_score must be finite")
        return value

    @field_validator("execution_failure_events", "recovery_attempt_events")
    @classmethod
    def require_unique_event_ids(
        cls,
        values: tuple[ExecutionFailureEvent, ...] | tuple[RecoveryAttemptEvent, ...],
    ) -> tuple[ExecutionFailureEvent, ...] | tuple[RecoveryAttemptEvent, ...]:
        event_ids = tuple(item.event_id for item in values)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("evaluation event identifiers must be unique")
        return values

    @model_validator(mode="after")
    def require_exact_missingness(self) -> Self:
        expected = tuple(
            component
            for field_name, component in _NULLABLE_METRIC_FIELDS
            if getattr(self, field_name) is None
        )
        actual = tuple(item.component for item in self.missingness)
        if actual != expected:
            raise ValueError("missingness must exactly describe every missing metric component")
        return self


class ResourceUsageDelta(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    cost_usd: float = Field(strict=True, allow_inf_nan=False)
    compute_units: float = Field(strict=True, allow_inf_nan=False)
    tokens: int = Field(strict=True)
    elapsed_seconds: float = Field(strict=True, allow_inf_nan=False)
    tool_calls: int = Field(strict=True)
    human_interventions: int = Field(strict=True)


class EvaluationMetricDeltaVector(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    task_score_delta: Decimal | None
    procedure_compilation_changed: bool | None
    procedure_execution_changed: bool | None
    method_selection_changed: bool | None
    execution_failure_event_count_delta: int = Field(strict=True)
    recovery_attempt_event_count_delta: int = Field(strict=True)
    resource_usage_delta: ResourceUsageDelta | None
    final_validation_changed: bool | None
    missingness_deltas: tuple[MetricMissingnessDelta, ...] = Field(
        max_length=len(_NULLABLE_METRIC_FIELDS)
    )


class _GuidanceEvaluationProtocolPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: BoundedIdentifier
    version: int = Field(strict=True, ge=1)
    objective_hash: Sha256Hex
    task_id: BoundedIdentifier
    task_input_hash: Sha256Hex
    output_schema_hash: Sha256Hex
    model_id: BoundedIdentifier
    model_version: BoundedIdentifier
    harness_id: BoundedIdentifier
    harness_version: BoundedIdentifier
    verifier_id: BoundedIdentifier
    verifier_version: BoundedIdentifier
    checker_id: BoundedIdentifier
    checker_version: BoundedIdentifier
    artifact_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_EVALUATION_ITEMS)
    declared_distractor_artifact_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_EVALUATION_ITEMS
    )
    random_seed: int | None = Field(default=None, strict=True, ge=0)
    evaluation_budget: EvaluationBudget

    @field_validator("artifact_ids", "declared_distractor_artifact_ids")
    @classmethod
    def require_canonical_artifact_ids(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, info.field_name)

    @model_validator(mode="after")
    def require_disjoint_artifact_families(self) -> Self:
        if set(self.artifact_ids) & set(self.declared_distractor_artifact_ids):
            raise ValueError("base and distractor artifact identifiers must be disjoint")
        if (
            self.evaluation_budget.model_id != self.model_id
            or self.evaluation_budget.model_version != self.model_version
        ):
            raise ValueError("evaluation budget must bind the exact guidance model")
        return self


class GuidanceEvaluationProtocol(_GuidanceEvaluationProtocolPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _GuidanceEvaluationProtocolPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=guidance_protocol_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != guidance_protocol_hash(self):
            raise ValueError("content_hash must canonically address the guidance protocol")
        return self


class _GuidanceEvaluationCellPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    cell_id: BoundedIdentifier
    protocol: GuidanceEvaluationProtocol
    protocol_id: BoundedIdentifier
    protocol_version: int = Field(strict=True, ge=1)
    protocol_hash: Sha256Hex
    condition: GuidanceCondition
    distractor_artifact_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_EVALUATION_ITEMS
    )
    metrics: EvaluationMetricVector
    output_artifact_id: BoundedIdentifier | None
    trace_id: BoundedIdentifier | None
    verifier_result_id: BoundedIdentifier | None
    reward_assessment_id: BoundedIdentifier | None
    observed_at: UtcTimestamp
    reference_missingness: tuple[ReferenceMissingness, ...] = Field(
        default=(),
        max_length=len(EvaluationReferenceComponent),
    )

    @field_validator("distractor_artifact_ids")
    @classmethod
    def require_canonical_distractors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, "distractor_artifact_ids")

    @model_validator(mode="after")
    def require_exact_protocol_and_condition_binding(self) -> Self:
        if self.protocol_id != self.protocol.protocol_id:
            raise ValueError("guidance cell must bind the exact protocol identifier")
        if self.protocol_version != self.protocol.version:
            raise ValueError("guidance cell must bind the exact protocol version")
        if self.protocol_hash != self.protocol.content_hash:
            raise ValueError("guidance cell must bind the exact protocol hash")
        if self.condition is GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS:
            if (
                not self.distractor_artifact_ids
                or self.distractor_artifact_ids
                != self.protocol.declared_distractor_artifact_ids
            ):
                raise ValueError(
                    "distractor condition must add exactly the declared distractor artifacts"
                )
        elif self.distractor_artifact_ids:
            raise ValueError("only the distractor condition may add distractor artifacts")
        reference_fields = (
            ("output_artifact_id", EvaluationReferenceComponent.OUTPUT_ARTIFACT),
            ("trace_id", EvaluationReferenceComponent.TRACE),
            ("verifier_result_id", EvaluationReferenceComponent.VERIFIER_RESULT),
            ("reward_assessment_id", EvaluationReferenceComponent.REWARD_ASSESSMENT),
        )
        expected_missing = tuple(
            component
            for field_name, component in reference_fields
            if getattr(self, field_name) is None
        )
        if tuple(item.component for item in self.reference_missingness) != expected_missing:
            raise ValueError(
                "reference_missingness must exactly describe every missing cell reference"
            )
        return self


class GuidanceEvaluationCell(_GuidanceEvaluationCellPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        supplied = dict(values)
        protocol = GuidanceEvaluationProtocol.model_validate(supplied["protocol"])
        supplied.setdefault("protocol_id", protocol.protocol_id)
        supplied.setdefault("protocol_version", protocol.version)
        supplied.setdefault("protocol_hash", protocol.content_hash)
        payload = _GuidanceEvaluationCellPayload(**supplied)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=guidance_cell_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != guidance_cell_hash(self):
            raise ValueError("content_hash must canonically address the guidance cell")
        return self


class _GuidanceComparisonPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    comparable: bool
    left_cell_id: BoundedIdentifier
    left_cell_hash: Sha256Hex
    right_cell_id: BoundedIdentifier
    right_cell_hash: Sha256Hex
    component_deltas: EvaluationMetricDeltaVector
    confounds: tuple[EvaluationConfoundCode, ...] = Field(
        max_length=len(EvaluationConfoundCode)
    )

    @model_validator(mode="after")
    def require_exact_comparability_state(self) -> Self:
        if self.comparable != (not self.confounds):
            raise ValueError("guidance comparability must exactly match confounds")
        if len(self.confounds) != len(set(self.confounds)):
            raise ValueError("guidance confounds must be unique")
        return self


class GuidanceComparison(_GuidanceComparisonPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> Self:
        payload = _GuidanceComparisonPayload(**values)
        return cls(
            **payload.model_dump(mode="python"),
            content_hash=guidance_comparison_hash(payload),
        )

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != guidance_comparison_hash(self):
            raise ValueError("content_hash must canonically address the guidance comparison")
        return self


def guidance_protocol_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return _canonical_record_hash(record, exclude_fields=exclude_fields)


def guidance_cell_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return _canonical_record_hash(record, exclude_fields=exclude_fields)


def guidance_comparison_hash(
    record: BaseModel | Mapping[str, object],
    *,
    exclude_fields: set[str] | None = None,
) -> str:
    return _canonical_record_hash(record, exclude_fields=exclude_fields)


def guidance_identity_confounds(
    left: GuidanceEvaluationCell,
    right: GuidanceEvaluationCell,
) -> tuple[EvaluationConfoundCode, ...]:
    left_cell = GuidanceEvaluationCell.model_validate(left)
    right_cell = GuidanceEvaluationCell.model_validate(right)
    pairs: tuple[tuple[object, object, EvaluationConfoundCode], ...] = (
        (
            left_cell.protocol_id,
            right_cell.protocol_id,
            EvaluationConfoundCode.PROTOCOL_ID_MISMATCH,
        ),
        (
            left_cell.protocol_version,
            right_cell.protocol_version,
            EvaluationConfoundCode.PROTOCOL_VERSION_MISMATCH,
        ),
        (
            left_cell.protocol.objective_hash,
            right_cell.protocol.objective_hash,
            EvaluationConfoundCode.OBJECTIVE_MISMATCH,
        ),
        (
            left_cell.protocol.task_id,
            right_cell.protocol.task_id,
            EvaluationConfoundCode.TASK_ID_MISMATCH,
        ),
        (
            left_cell.protocol.task_input_hash,
            right_cell.protocol.task_input_hash,
            EvaluationConfoundCode.TASK_INPUT_MISMATCH,
        ),
        (
            left_cell.protocol.output_schema_hash,
            right_cell.protocol.output_schema_hash,
            EvaluationConfoundCode.OUTPUT_SCHEMA_MISMATCH,
        ),
        (
            left_cell.protocol.model_id,
            right_cell.protocol.model_id,
            EvaluationConfoundCode.MODEL_ID_MISMATCH,
        ),
        (
            left_cell.protocol.model_version,
            right_cell.protocol.model_version,
            EvaluationConfoundCode.MODEL_VERSION_MISMATCH,
        ),
        (
            left_cell.protocol.harness_id,
            right_cell.protocol.harness_id,
            EvaluationConfoundCode.HARNESS_ID_MISMATCH,
        ),
        (
            left_cell.protocol.harness_version,
            right_cell.protocol.harness_version,
            EvaluationConfoundCode.HARNESS_VERSION_MISMATCH,
        ),
        (
            left_cell.protocol.verifier_id,
            right_cell.protocol.verifier_id,
            EvaluationConfoundCode.VERIFIER_ID_MISMATCH,
        ),
        (
            left_cell.protocol.verifier_version,
            right_cell.protocol.verifier_version,
            EvaluationConfoundCode.VERIFIER_VERSION_MISMATCH,
        ),
        (
            left_cell.protocol.checker_id,
            right_cell.protocol.checker_id,
            EvaluationConfoundCode.CHECKER_ID_MISMATCH,
        ),
        (
            left_cell.protocol.checker_version,
            right_cell.protocol.checker_version,
            EvaluationConfoundCode.CHECKER_VERSION_MISMATCH,
        ),
        (
            left_cell.protocol.artifact_ids,
            right_cell.protocol.artifact_ids,
            EvaluationConfoundCode.ARTIFACTS_MISMATCH,
        ),
        (
            left_cell.protocol.declared_distractor_artifact_ids,
            right_cell.protocol.declared_distractor_artifact_ids,
            EvaluationConfoundCode.DISTRACTOR_DECLARATION_MISMATCH,
        ),
        (
            left_cell.protocol.random_seed,
            right_cell.protocol.random_seed,
            EvaluationConfoundCode.SEED_MISMATCH,
        ),
        (
            left_cell.protocol.evaluation_budget,
            right_cell.protocol.evaluation_budget,
            EvaluationConfoundCode.EVALUATION_BUDGET_MISMATCH,
        ),
    )
    confounds = tuple(code for left_value, right_value, code in pairs if left_value != right_value)
    if left_cell.condition is right_cell.condition:
        confounds += (EvaluationConfoundCode.SAME_GUIDANCE_CONDITION,)
    return confounds


def _changed(left: object | None, right: object | None) -> bool | None:
    return None if left is None or right is None else left != right


def _resource_usage_delta(
    left: ResourceUsage | None,
    right: ResourceUsage | None,
) -> ResourceUsageDelta | None:
    if left is None or right is None:
        return None
    return ResourceUsageDelta(
        cost_usd=right.cost_usd - left.cost_usd,
        compute_units=right.compute_units - left.compute_units,
        tokens=right.tokens - left.tokens,
        elapsed_seconds=right.elapsed_seconds - left.elapsed_seconds,
        tool_calls=right.tool_calls - left.tool_calls,
        human_interventions=right.human_interventions - left.human_interventions,
    )


def metric_component_deltas(
    left: EvaluationMetricVector,
    right: EvaluationMetricVector,
) -> EvaluationMetricDeltaVector:
    left_metrics = EvaluationMetricVector.model_validate(left)
    right_metrics = EvaluationMetricVector.model_validate(right)
    left_missing = {item.component: item.reason for item in left_metrics.missingness}
    right_missing = {item.component: item.reason for item in right_metrics.missingness}
    missingness_deltas = tuple(
        MetricMissingnessDelta(
            component=component,
            affected_side=(
                MissingnessSide.BOTH
                if component in left_missing and component in right_missing
                else MissingnessSide.LEFT
                if component in left_missing
                else MissingnessSide.RIGHT
            ),
            left_reason=left_missing.get(component),
            right_reason=right_missing.get(component),
        )
        for _, component in _NULLABLE_METRIC_FIELDS
        if component in left_missing or component in right_missing
    )
    return EvaluationMetricDeltaVector(
        task_score_delta=(
            None
            if left_metrics.task_score is None or right_metrics.task_score is None
            else right_metrics.task_score - left_metrics.task_score
        ),
        procedure_compilation_changed=_changed(
            left_metrics.procedure_compilation_status,
            right_metrics.procedure_compilation_status,
        ),
        procedure_execution_changed=_changed(
            left_metrics.procedure_execution_success,
            right_metrics.procedure_execution_success,
        ),
        method_selection_changed=_changed(
            left_metrics.method_selection_result,
            right_metrics.method_selection_result,
        ),
        execution_failure_event_count_delta=(
            len(right_metrics.execution_failure_events)
            - len(left_metrics.execution_failure_events)
        ),
        recovery_attempt_event_count_delta=(
            len(right_metrics.recovery_attempt_events)
            - len(left_metrics.recovery_attempt_events)
        ),
        resource_usage_delta=_resource_usage_delta(
            left_metrics.resource_usage,
            right_metrics.resource_usage,
        ),
        final_validation_changed=_changed(
            left_metrics.final_validation,
            right_metrics.final_validation,
        ),
        missingness_deltas=missingness_deltas,
    )


def compare_guidance_cells(
    left: GuidanceEvaluationCell,
    right: GuidanceEvaluationCell,
) -> GuidanceComparison:
    left_cell = GuidanceEvaluationCell.model_validate(left)
    right_cell = GuidanceEvaluationCell.model_validate(right)
    confounds = guidance_identity_confounds(left_cell, right_cell)
    return GuidanceComparison.build(
        comparable=not confounds,
        left_cell_id=left_cell.cell_id,
        left_cell_hash=left_cell.content_hash,
        right_cell_id=right_cell.cell_id,
        right_cell_hash=right_cell.content_hash,
        component_deltas=metric_component_deltas(left_cell.metrics, right_cell.metrics),
        confounds=confounds,
    )


__all__ = [
    "EvaluationConfoundCode",
    "EvaluationMetricComponent",
    "EvaluationMetricDeltaVector",
    "EvaluationMetricVector",
    "EvaluationReferenceComponent",
    "ExecutionFailureEvent",
    "ExecutionFailureKind",
    "GuidanceComparison",
    "GuidanceCondition",
    "GuidanceEvaluationCell",
    "GuidanceEvaluationProtocol",
    "MetricMissingReason",
    "MetricMissingness",
    "MetricMissingnessDelta",
    "MissingnessSide",
    "RecoveryAttemptEvent",
    "RecoveryOutcome",
    "ReferenceMissingness",
    "ResourceUsageDelta",
    "compare_guidance_cells",
    "guidance_cell_hash",
    "guidance_comparison_hash",
    "guidance_identity_confounds",
    "guidance_protocol_hash",
    "metric_component_deltas",
]
