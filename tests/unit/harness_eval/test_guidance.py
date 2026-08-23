from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from super_scientist.domain.harness_eval.guidance import (
    EvaluationConfoundCode,
    EvaluationMetricComponent,
    EvaluationMetricVector,
    EvaluationReferenceComponent,
    ExecutionFailureEvent,
    ExecutionFailureKind,
    GuidanceCondition,
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
    MetricMissingness,
    MetricMissingnessDelta,
    MetricMissingReason,
    MissingnessSide,
    RecoveryAttemptEvent,
    RecoveryOutcome,
    ReferenceMissingness,
    compare_guidance_cells,
    guidance_cell_hash,
    guidance_comparison_hash,
    guidance_protocol_hash,
)
from super_scientist.domain.harness_eval.models import EvaluationBudget, FeedbackMode
from super_scientist.domain.improvement.models import AssessmentOutcome, ResourceUsage
from super_scientist.domain.procedures.models import (
    MethodDirectionStatus,
    ProcedureValidationStatus,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _budget(**updates: object) -> EvaluationBudget:
    values: dict[str, object] = {
        "model_id": "model-a",
        "model_version": "model-v1",
        "adapter_id": None,
        "feedback_mode": FeedbackMode.NONE,
        "tool_ids": ("fixture",),
        "attempts": 1,
        "token_limit": 100,
        "reasoning_limit": 50,
        "evaluator_call_limit": 1,
        "wall_clock_seconds": Decimal("5"),
        "cost_limit": Decimal("1"),
        "human_intervention_limit": 0,
    }
    values.update(updates)
    return EvaluationBudget.model_validate(values)


def _usage(*, tokens: int = 20) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.2,
        compute_units=1.0,
        tokens=tokens,
        elapsed_seconds=2.0,
        tool_calls=1,
        human_interventions=0,
    )


def _metrics(*, score: str = "0.8", tokens: int = 20) -> EvaluationMetricVector:
    return EvaluationMetricVector(
        task_score=Decimal(score),
        procedure_compilation_status=ProcedureValidationStatus.VALID,
        procedure_execution_success=True,
        method_selection_result=MethodDirectionStatus.SUPPORTED,
        execution_failure_events=(
            ExecutionFailureEvent(
                event_id="failure-1",
                kind=ExecutionFailureKind.VALIDATION_FAILURE,
                procedure_step_id="step-1",
            ),
        ),
        recovery_attempt_events=(
            RecoveryAttemptEvent(
                event_id="recovery-1",
                attempt=1,
                target_step_id="step-1",
                outcome=RecoveryOutcome.SUCCEEDED,
            ),
        ),
        resource_usage=_usage(tokens=tokens),
        final_validation=AssessmentOutcome.PASSED,
        missingness=(),
    )


def _protocol(**updates: object) -> GuidanceEvaluationProtocol:
    values: dict[str, object] = {
        "protocol_id": "guidance-protocol",
        "version": 1,
        "objective_hash": HASH_A,
        "task_id": "task-a",
        "task_input_hash": HASH_A,
        "output_schema_hash": HASH_A,
        "model_id": "model-a",
        "model_version": "model-v1",
        "harness_id": "harness-a",
        "harness_version": "harness-v1",
        "verifier_id": "verifier-a",
        "verifier_version": "verifier-v1",
        "checker_id": "checker-a",
        "checker_version": "checker-v1",
        "artifact_ids": ("artifact-a",),
        "declared_distractor_artifact_ids": ("distractor-a",),
        "random_seed": 7,
        "evaluation_budget": _budget(),
    }
    values.update(updates)
    if "evaluation_budget" not in updates and ("model_id" in updates or "model_version" in updates):
        values["evaluation_budget"] = _budget(
            model_id=values["model_id"],
            model_version=values["model_version"],
        )
    return GuidanceEvaluationProtocol.build(**values)


def _cell(
    *,
    condition: GuidanceCondition = GuidanceCondition.FULL_PROCEDURE_GUIDANCE,
    protocol: GuidanceEvaluationProtocol | None = None,
    metrics: EvaluationMetricVector | None = None,
) -> GuidanceEvaluationCell:
    selected = protocol or _protocol()
    return GuidanceEvaluationCell.build(
        cell_id=f"cell-{condition.value.lower()}-{selected.protocol_id}",
        protocol=selected,
        condition=condition,
        distractor_artifact_ids=(
            selected.declared_distractor_artifact_ids
            if condition is GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS
            else ()
        ),
        metrics=metrics or _metrics(),
        output_artifact_id="output-a",
        trace_id="trace-a",
        verifier_result_id="verifier-result-a",
        reward_assessment_id="reward-a",
        observed_at=NOW,
    )


def test_metric_vector_has_no_composite_or_promotion_scalar() -> None:
    assert "composite_score" not in EvaluationMetricVector.model_fields
    assert "canonical_score" not in EvaluationMetricVector.model_fields
    assert "promotion_score" not in EvaluationMetricVector.model_fields
    assert "task_score" in EvaluationMetricVector.model_fields
    assert "resource_usage" in EvaluationMetricVector.model_fields


def test_all_four_guidance_conditions_are_closed_and_constructible() -> None:
    assert tuple(GuidanceCondition) == (
        GuidanceCondition.FULL_PROCEDURE_GUIDANCE,
        GuidanceCondition.METHOD_ONLY,
        GuidanceCondition.OBJECTIVE_AND_DATA_ONLY,
        GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS,
    )
    assert tuple(_cell(condition=condition).condition for condition in GuidanceCondition) == tuple(
        GuidanceCondition
    )


def test_metric_vector_keeps_typed_events_and_component_values_separate() -> None:
    metrics = _metrics()

    assert metrics.procedure_compilation_status is ProcedureValidationStatus.VALID
    assert metrics.method_selection_result is MethodDirectionStatus.SUPPORTED
    assert metrics.execution_failure_events[0].kind is ExecutionFailureKind.VALIDATION_FAILURE
    assert metrics.recovery_attempt_events[0].outcome is RecoveryOutcome.SUCCEEDED
    assert metrics.resource_usage == _usage()
    assert metrics.final_validation is AssessmentOutcome.PASSED


def test_missing_metric_requires_exact_typed_missingness() -> None:
    values = _metrics().model_dump(mode="python")
    values["task_score"] = None

    with pytest.raises(ValidationError, match="missingness must exactly describe"):
        EvaluationMetricVector.model_validate(values)

    values["missingness"] = (
        MetricMissingness(
            component=EvaluationMetricComponent.TASK_SCORE,
            reason=MetricMissingReason.NOT_OBSERVED,
        ),
    )
    parsed = EvaluationMetricVector.model_validate(values)
    assert parsed.missingness[0].reason is MetricMissingReason.NOT_OBSERVED


def test_present_metric_rejects_a_contradictory_missingness_reason() -> None:
    values = _metrics().model_dump(mode="python")
    values["missingness"] = (
        MetricMissingness(
            component=EvaluationMetricComponent.FINAL_VALIDATION,
            reason=MetricMissingReason.VALIDATION_NOT_RUN,
        ),
    )
    with pytest.raises(ValidationError, match="missingness must exactly describe"):
        EvaluationMetricVector.model_validate(values)


def test_guidance_protocol_rejects_a_budget_for_a_different_model() -> None:
    with pytest.raises(ValidationError, match="budget must bind the exact guidance model"):
        _protocol(evaluation_budget=_budget(model_id="model-b"))


def test_missing_cell_reference_requires_typed_missingness() -> None:
    values = _cell().model_dump(mode="python", exclude={"content_hash"})
    values["trace_id"] = None
    with pytest.raises(ValidationError, match="reference_missingness must exactly describe"):
        GuidanceEvaluationCell.build(**values)

    values["reference_missingness"] = (
        ReferenceMissingness(
            component=EvaluationReferenceComponent.TRACE,
            reason=MetricMissingReason.INSTRUMENTATION_UNAVAILABLE,
        ),
    )
    parsed = GuidanceEvaluationCell.build(**values)
    assert parsed.trace_id is None


@pytest.mark.parametrize(
    ("field", "replacement", "expected"),
    [
        ("objective_hash", HASH_B, EvaluationConfoundCode.OBJECTIVE_MISMATCH),
        ("task_id", "task-b", EvaluationConfoundCode.TASK_ID_MISMATCH),
        ("task_input_hash", HASH_B, EvaluationConfoundCode.TASK_INPUT_MISMATCH),
        ("output_schema_hash", HASH_B, EvaluationConfoundCode.OUTPUT_SCHEMA_MISMATCH),
        ("model_id", "model-b", EvaluationConfoundCode.MODEL_ID_MISMATCH),
        ("model_version", "model-v2", EvaluationConfoundCode.MODEL_VERSION_MISMATCH),
        ("harness_id", "harness-b", EvaluationConfoundCode.HARNESS_ID_MISMATCH),
        ("harness_version", "harness-v2", EvaluationConfoundCode.HARNESS_VERSION_MISMATCH),
        ("verifier_id", "verifier-b", EvaluationConfoundCode.VERIFIER_ID_MISMATCH),
        ("verifier_version", "verifier-v2", EvaluationConfoundCode.VERIFIER_VERSION_MISMATCH),
        ("checker_id", "checker-b", EvaluationConfoundCode.CHECKER_ID_MISMATCH),
        ("checker_version", "checker-v2", EvaluationConfoundCode.CHECKER_VERSION_MISMATCH),
        ("artifact_ids", ("artifact-b",), EvaluationConfoundCode.ARTIFACTS_MISMATCH),
        (
            "declared_distractor_artifact_ids",
            ("distractor-b",),
            EvaluationConfoundCode.DISTRACTOR_DECLARATION_MISMATCH,
        ),
        ("random_seed", 8, EvaluationConfoundCode.SEED_MISMATCH),
    ],
)
def test_every_held_constant_identity_drift_blocks_guidance_comparison(
    field: str,
    replacement: object,
    expected: EvaluationConfoundCode,
) -> None:
    drifted = _protocol(**{field: replacement, "protocol_id": "drifted-protocol"})

    comparison = compare_guidance_cells(
        _cell(),
        _cell(condition=GuidanceCondition.METHOD_ONLY, protocol=drifted),
    )

    assert comparison.comparable is False
    assert expected in comparison.confounds


def test_exact_budget_drift_blocks_guidance_comparison() -> None:
    drifted = _protocol(
        protocol_id="budget-drift",
        evaluation_budget=_budget(token_limit=101),
    )
    comparison = compare_guidance_cells(
        _cell(),
        _cell(condition=GuidanceCondition.METHOD_ONLY, protocol=drifted),
    )
    assert EvaluationConfoundCode.EVALUATION_BUDGET_MISMATCH in comparison.confounds
    assert comparison.comparable is False


def test_protocol_identity_drift_is_explicit_even_when_other_fields_match() -> None:
    comparison = compare_guidance_cells(
        _cell(),
        _cell(
            condition=GuidanceCondition.METHOD_ONLY,
            protocol=_protocol(protocol_id="another-protocol"),
        ),
    )
    assert comparison.confounds == (EvaluationConfoundCode.PROTOCOL_ID_MISMATCH,)


def test_protocol_version_drift_is_explicit() -> None:
    comparison = compare_guidance_cells(
        _cell(),
        _cell(
            condition=GuidanceCondition.METHOD_ONLY,
            protocol=_protocol(protocol_id="guidance-protocol", version=2),
        ),
    )
    assert comparison.confounds == (EvaluationConfoundCode.PROTOCOL_VERSION_MISMATCH,)


def test_same_condition_is_not_a_guidance_only_comparison() -> None:
    comparison = compare_guidance_cells(_cell(), _cell())
    assert comparison.comparable is False
    assert EvaluationConfoundCode.SAME_GUIDANCE_CONDITION in comparison.confounds


def test_distractor_condition_adds_exactly_the_declared_artifacts() -> None:
    distractor = _cell(condition=GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS)
    comparison = compare_guidance_cells(_cell(), distractor)

    assert distractor.distractor_artifact_ids == ("distractor-a",)
    assert comparison.comparable is True
    assert comparison.confounds == ()


def test_non_distractor_and_distractor_conditions_reject_wrong_artifact_sets() -> None:
    base = _cell().model_dump(mode="python", exclude={"content_hash"})
    base["distractor_artifact_ids"] = ("distractor-a",)
    with pytest.raises(ValidationError, match="only the distractor condition"):
        GuidanceEvaluationCell.build(**base)

    distractor = _cell(condition=GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS).model_dump(
        mode="python", exclude={"content_hash"}
    )
    distractor["distractor_artifact_ids"] = ("undeclared",)
    with pytest.raises(ValidationError, match="exactly the declared distractor"):
        GuidanceEvaluationCell.build(**distractor)


def test_comparison_retains_canonical_component_deltas_without_collapsing_metrics() -> None:
    comparison = compare_guidance_cells(
        _cell(metrics=_metrics(score="0.3", tokens=10)),
        _cell(
            condition=GuidanceCondition.METHOD_ONLY,
            metrics=_metrics(score="0.8", tokens=25),
        ),
    )

    assert comparison.component_deltas.task_score_delta == Decimal("0.5")
    assert comparison.component_deltas.resource_usage_delta is not None
    assert comparison.component_deltas.resource_usage_delta.tokens == 15
    assert "composite_delta" not in type(comparison.component_deltas).model_fields
    assert comparison.content_hash == guidance_comparison_hash(comparison)


def test_component_deltas_preserve_left_right_and_both_missingness_reasons() -> None:
    left_values = _metrics().model_dump(mode="python")
    left_values["task_score"] = None
    left_values["resource_usage"] = None
    left_values["missingness"] = (
        MetricMissingness(
            component=EvaluationMetricComponent.TASK_SCORE,
            reason=MetricMissingReason.NOT_OBSERVED,
        ),
        MetricMissingness(
            component=EvaluationMetricComponent.RESOURCE_USAGE,
            reason=MetricMissingReason.INSTRUMENTATION_UNAVAILABLE,
        ),
    )
    right_values = _metrics().model_dump(mode="python")
    right_values["resource_usage"] = None
    right_values["final_validation"] = None
    right_values["missingness"] = (
        MetricMissingness(
            component=EvaluationMetricComponent.RESOURCE_USAGE,
            reason=MetricMissingReason.NOT_APPLICABLE,
        ),
        MetricMissingness(
            component=EvaluationMetricComponent.FINAL_VALIDATION,
            reason=MetricMissingReason.VALIDATION_NOT_RUN,
        ),
    )

    comparison = compare_guidance_cells(
        _cell(metrics=EvaluationMetricVector.model_validate(left_values)),
        _cell(
            condition=GuidanceCondition.METHOD_ONLY,
            metrics=EvaluationMetricVector.model_validate(right_values),
        ),
    )

    assert comparison.component_deltas.missingness_deltas == (
        MetricMissingnessDelta(
            component=EvaluationMetricComponent.TASK_SCORE,
            affected_side=MissingnessSide.LEFT,
            left_reason=MetricMissingReason.NOT_OBSERVED,
            right_reason=None,
        ),
        MetricMissingnessDelta(
            component=EvaluationMetricComponent.RESOURCE_USAGE,
            affected_side=MissingnessSide.BOTH,
            left_reason=MetricMissingReason.INSTRUMENTATION_UNAVAILABLE,
            right_reason=MetricMissingReason.NOT_APPLICABLE,
        ),
        MetricMissingnessDelta(
            component=EvaluationMetricComponent.FINAL_VALIDATION,
            affected_side=MissingnessSide.RIGHT,
            left_reason=None,
            right_reason=MetricMissingReason.VALIDATION_NOT_RUN,
        ),
    )
    assert "missing_components" not in type(comparison.component_deltas).model_fields
    assert comparison.content_hash == guidance_comparison_hash(comparison)


@pytest.mark.parametrize("target", ["protocol", "cell", "comparison"])
def test_direct_parsing_rejects_canonical_hash_tampering(target: str) -> None:
    protocol = _protocol()
    cell = _cell(protocol=protocol)
    comparison = compare_guidance_cells(
        cell,
        _cell(condition=GuidanceCondition.METHOD_ONLY, protocol=protocol),
    )
    record = {"protocol": protocol, "cell": cell, "comparison": comparison}[target]
    values = record.model_dump(mode="python")
    values["content_hash"] = HASH_C
    model = type(record)

    with pytest.raises(ValidationError, match="content_hash must canonically address"):
        model.model_validate(values)


def test_direct_cell_parsing_rejects_a_rehashed_protocol_binding_contradiction() -> None:
    cell = _cell()
    values = cell.model_dump(mode="python")
    values["protocol_hash"] = HASH_B
    values["content_hash"] = guidance_cell_hash(values, exclude_fields={"content_hash"})

    with pytest.raises(ValidationError, match="exact protocol hash"):
        GuidanceEvaluationCell.model_validate(values)


def test_protocol_and_cell_hashes_are_canonical_and_models_are_strict_frozen() -> None:
    protocol = _protocol()
    cell = _cell(protocol=protocol)
    assert protocol.content_hash == guidance_protocol_hash(protocol)
    assert cell.content_hash == guidance_cell_hash(cell)
    assert protocol.model_config["strict"] is True
    assert protocol.model_config["frozen"] is True
    assert protocol.model_config["extra"] == "forbid"

    with pytest.raises(ValidationError):
        GuidanceEvaluationProtocol.model_validate(
            protocol.model_dump(mode="python") | {"promotion_authorized": True}
        )


def test_guidance_public_api_is_exported_from_harness_eval_package() -> None:
    from super_scientist.domain import harness_eval

    assert harness_eval.GuidanceEvaluationProtocol is GuidanceEvaluationProtocol
    assert harness_eval.compare_guidance_cells is compare_guidance_cells
