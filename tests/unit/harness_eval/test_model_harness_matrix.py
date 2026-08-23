from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from itertools import product

import pytest
from pydantic import ValidationError

from super_scientist.domain.harness_eval.guidance import EvaluationMetricVector
from super_scientist.domain.harness_eval.matrix import (
    HarnessIdentity,
    ModelBudgetBinding,
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessComparison,
    ModelHarnessComparisonKind,
    ModelHarnessConfoundCode,
    ModelHarnessCoordinate,
    ModelHarnessProtocol,
    ModelIdentity,
    evaluation_resource_envelope_hash,
    model_harness_analysis_hash,
    model_harness_cell_hash,
    model_harness_protocol_hash,
)
from super_scientist.domain.harness_eval.matrix import (
    analyze_model_harness as analyze_model_harness_contract,
)
from super_scientist.domain.harness_eval.models import (
    EvaluationBudget,
    FeedbackMode,
    HarnessPartition,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.improvement.models import AssessmentOutcome, ResourceUsage
from super_scientist.domain.procedures.models import (
    MethodDirectionStatus,
    ProcedureValidationStatus,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _budget(model: ModelIdentity, **updates: object) -> EvaluationBudget:
    values: dict[str, object] = {
        "model_id": model.model_id,
        "model_version": model.model_version,
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


def _metrics(score: str = "0.5") -> EvaluationMetricVector:
    return EvaluationMetricVector(
        task_score=Decimal(score),
        procedure_compilation_status=ProcedureValidationStatus.VALID,
        procedure_execution_success=True,
        method_selection_result=MethodDirectionStatus.SUPPORTED,
        execution_failure_events=(),
        recovery_attempt_events=(),
        resource_usage=ResourceUsage(
            cost_usd=0.1,
            compute_units=1.0,
            tokens=10,
            elapsed_seconds=1.0,
            tool_calls=1,
            human_interventions=0,
        ),
        final_validation=AssessmentOutcome.PASSED,
        missingness=(),
    )


MODELS = (
    ModelIdentity(model_id="model-a", model_version="v1"),
    ModelIdentity(model_id="model-b", model_version="v1"),
)
HARNESSES = (
    HarnessIdentity(harness_id="harness-a", harness_version="v1"),
    HarnessIdentity(harness_id="harness-b", harness_version="v1"),
)
PARTITIONS = (
    HarnessPartition.HARNESS_DISCOVERY_TASKS,
    HarnessPartition.HARNESS_TRANSFER_TASKS,
)


def _model_budgets(
    *,
    second_token_limit: int = 100,
) -> tuple[ModelBudgetBinding, ...]:
    return tuple(
        ModelBudgetBinding.build(
            model=model,
            budget=_budget(
                model,
                token_limit=second_token_limit if model == MODELS[1] else 100,
            ),
        )
        for model in MODELS
    )


def _grid(
    models: tuple[ModelIdentity, ...] = MODELS,
    harnesses: tuple[HarnessIdentity, ...] = HARNESSES,
    partitions: tuple[HarnessPartition, ...] = PARTITIONS,
) -> tuple[ModelHarnessCoordinate, ...]:
    return tuple(
        ModelHarnessCoordinate(model=model, harness=harness, partition=partition)
        for model, harness, partition in product(models, harnesses, partitions)
    )


def _protocol(**updates: object) -> ModelHarnessProtocol:
    values: dict[str, object] = {
        "protocol_id": "matrix-protocol",
        "version": 1,
        "models": MODELS,
        "harnesses": HARNESSES,
        "partitions": PARTITIONS,
        "task_set_id": "task-set-a",
        "task_set_hash": HASH_A,
        "verifier_id": "verifier-a",
        "verifier_version": "v1",
        "checker_id": "checker-a",
        "checker_version": "v1",
        "artifact_ids": ("artifact-a",),
        "random_seed": 7,
        "output_schema_hash": HASH_A,
        "model_budgets": _model_budgets(),
        "matched_resource_envelope_hash": evaluation_resource_envelope_hash(_budget(MODELS[0])),
        "expected_grid": _grid(),
        "comparison_kinds": tuple(ModelHarnessComparisonKind),
        "governing_policy_hash": HASH_A,
    }
    values.update(updates)
    return ModelHarnessProtocol.build(**values)


def _cell(
    protocol: ModelHarnessProtocol,
    coordinate: ModelHarnessCoordinate,
    *,
    score: str = "0.5",
    trace_current: bool = True,
    reward_valid: bool = True,
) -> ModelHarnessCell:
    freshness, assessment = _validated_evidence()
    from super_scientist.domain.harness_eval.rewards import reward_validity_receipt
    from super_scientist.domain.harness_eval.traces import trace_freshness_receipt

    trace_receipt = trace_freshness_receipt(freshness)
    reward_receipt = reward_validity_receipt(assessment)
    if not trace_current:
        trace_receipt = EvidenceReceipt(
            record_id=trace_receipt.record_id,
            schema_version=trace_receipt.schema_version,
            content_hash=HASH_B,
        )
    if not reward_valid:
        reward_receipt = EvidenceReceipt(
            record_id=reward_receipt.record_id,
            schema_version=reward_receipt.schema_version,
            content_hash=HASH_B,
        )
    return ModelHarnessCell.from_protocol(
        cell_id=(
            f"cell-{coordinate.model.model_id}-{coordinate.harness.harness_id}"
            f"-{coordinate.partition.value}"
        ),
        protocol=protocol,
        coordinate=coordinate,
        metrics=_metrics(score),
        trace_freshness_receipt=trace_receipt,
        reward_validity_receipt=reward_receipt,
        observed_at=NOW,
    )


def _cells(protocol: ModelHarnessProtocol) -> tuple[ModelHarnessCell, ...]:
    return tuple(_cell(protocol, coordinate) for coordinate in protocol.expected_grid)


@lru_cache(maxsize=1)
def _validated_evidence() -> tuple[object, object]:
    from tests.unit.harness_eval.test_harness_security_contracts import (
        _valid_evaluation_snapshots,
    )

    return _valid_evaluation_snapshots()


def analyze_model_harness(
    protocol: ModelHarnessProtocol,
    cells: tuple[ModelHarnessCell, ...],
) -> ModelHarnessAnalysis:
    freshness, assessment = _validated_evidence()
    return analyze_model_harness_contract(
        protocol,
        cells,
        trace_freshness=(freshness,),  # type: ignore[arg-type]
        reward_assessments=(assessment,),  # type: ignore[arg-type]
    )


def test_protocol_requires_a_complete_model_by_harness_partition_grid() -> None:
    with pytest.raises(ValidationError, match="complete Cartesian grid"):
        _protocol(expected_grid=_grid()[:-1])


def test_protocol_rejects_duplicate_grid_cells_and_requires_two_axes() -> None:
    with pytest.raises(ValidationError, match="unique and canonically ordered"):
        _protocol(expected_grid=(*_grid()[:-1], _grid()[0]))
    with pytest.raises(ValidationError):
        _protocol(models=MODELS[:1], expected_grid=_grid(models=MODELS[:1]))
    with pytest.raises(ValidationError):
        _protocol(harnesses=HARNESSES[:1], expected_grid=_grid(harnesses=HARNESSES[:1]))


def test_each_matrix_model_has_one_exact_model_bearing_budget() -> None:
    protocol = _protocol()
    assert tuple(binding.model for binding in protocol.model_budgets) == MODELS
    assert all(
        binding.budget.model_id == binding.model.model_id
        and binding.budget.model_version == binding.model.model_version
        for binding in protocol.model_budgets
    )
    assert {binding.resource_envelope_hash for binding in protocol.model_budgets} == {
        protocol.matched_resource_envelope_hash
    }

    with pytest.raises(ValidationError, match="budget must bind its exact matrix model"):
        ModelBudgetBinding.build(model=MODELS[0], budget=_budget(MODELS[1]))
    with pytest.raises(ValidationError, match="exactly one budget for every model"):
        _protocol(model_budgets=_model_budgets()[:-1])


def test_matrix_rejects_resource_envelope_drift_without_equating_model_identity() -> None:
    with pytest.raises(ValidationError, match="same resource envelope"):
        _protocol(model_budgets=_model_budgets(second_token_limit=101))


def test_complete_grid_emits_every_declared_descriptive_comparison_kind() -> None:
    protocol = _protocol()
    analysis = analyze_model_harness(protocol, _cells(protocol))

    assert analysis.confounds == ()
    assert {comparison.kind for comparison in analysis.comparisons} == set(
        ModelHarnessComparisonKind
    )
    assert analysis.causal_claim_permitted is False


def test_incomplete_or_duplicate_observed_grid_blocks_all_comparisons() -> None:
    protocol = _protocol()
    cells = _cells(protocol)
    incomplete = analyze_model_harness(protocol, cells[:-1])
    duplicate = analyze_model_harness(protocol, (*cells, cells[0]))

    assert incomplete.comparisons == ()
    assert ModelHarnessConfoundCode.INCOMPLETE_GRID in incomplete.confounds
    assert duplicate.comparisons == ()
    assert ModelHarnessConfoundCode.DUPLICATE_CELL in duplicate.confounds


def test_observed_cells_are_canonicalized_independent_of_input_order() -> None:
    protocol = _protocol()
    forward = analyze_model_harness(protocol, _cells(protocol))
    reverse = analyze_model_harness(protocol, tuple(reversed(_cells(protocol))))

    assert forward.cell_ids == reverse.cell_ids
    assert forward.comparisons == reverse.comparisons
    assert forward.content_hash == reverse.content_hash


def test_duplicate_cell_ids_with_different_hashes_have_stable_analysis_order() -> None:
    protocol = _protocol()
    cells = _cells(protocol)
    values = cells[0].model_dump(mode="python", exclude={"content_hash"})
    values["metrics"] = _metrics("0.9")
    duplicate = ModelHarnessCell.build(**values)
    observations = (*cells, duplicate)

    forward = analyze_model_harness(protocol, observations)
    reverse = analyze_model_harness(protocol, tuple(reversed(observations)))

    assert ModelHarnessConfoundCode.DUPLICATE_CELL in forward.confounds
    assert forward.cell_ids == reverse.cell_ids
    assert forward.cell_hashes == reverse.cell_hashes
    assert forward.content_hash == reverse.content_hash


def test_declared_comparison_kinds_are_the_only_outputs() -> None:
    protocol = _protocol(comparison_kinds=(ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,))
    analysis = analyze_model_harness(protocol, _cells(protocol))
    assert {item.kind for item in analysis.comparisons} == {
        ModelHarnessComparisonKind.MODEL_HELD_CONSTANT
    }


def test_discovery_and_transfer_are_retained_as_separate_transfer_coordinates() -> None:
    protocol = _protocol(comparison_kinds=(ModelHarnessComparisonKind.TRAIN_TEST_TRANSFER,))
    analysis = analyze_model_harness(protocol, _cells(protocol))

    assert analysis.comparisons
    for comparison in analysis.comparisons:
        assert comparison.partitions == (
            HarnessPartition.HARNESS_DISCOVERY_TASKS,
            HarnessPartition.HARNESS_TRANSFER_TASKS,
        )
        assert len(comparison.cell_ids) == 2


@pytest.mark.parametrize(
    ("field", "replacement", "expected"),
    [
        ("task_set_id", "task-set-b", ModelHarnessConfoundCode.TASK_SET_MISMATCH),
        ("task_set_hash", HASH_B, ModelHarnessConfoundCode.TASK_SET_MISMATCH),
        ("verifier_id", "verifier-b", ModelHarnessConfoundCode.VERIFIER_MISMATCH),
        ("verifier_version", "v2", ModelHarnessConfoundCode.VERIFIER_MISMATCH),
        ("checker_id", "checker-b", ModelHarnessConfoundCode.CHECKER_MISMATCH),
        ("checker_version", "v2", ModelHarnessConfoundCode.CHECKER_MISMATCH),
        ("artifact_ids", ("artifact-b",), ModelHarnessConfoundCode.ARTIFACTS_MISMATCH),
        ("random_seed", 8, ModelHarnessConfoundCode.SEED_MISMATCH),
        ("output_schema_hash", HASH_B, ModelHarnessConfoundCode.OUTPUT_SCHEMA_MISMATCH),
        ("governing_policy_hash", HASH_B, ModelHarnessConfoundCode.POLICY_MISMATCH),
    ],
)
def test_cell_identity_or_policy_drift_blocks_analysis(
    field: str,
    replacement: object,
    expected: ModelHarnessConfoundCode,
) -> None:
    protocol = _protocol()
    cells = list(_cells(protocol))
    values = cells[0].model_dump(mode="python", exclude={"content_hash"})
    values[field] = replacement
    cells[0] = ModelHarnessCell.build(**values)

    analysis = analyze_model_harness(protocol, tuple(cells))
    assert analysis.comparisons == ()
    assert expected in analysis.confounds


def test_exact_budget_drift_blocks_matrix_analysis() -> None:
    protocol = _protocol()
    cells = list(_cells(protocol))
    values = cells[0].model_dump(mode="python", exclude={"content_hash"})
    values["evaluation_budget"] = _budget(cells[0].coordinate.model, token_limit=101)
    cells[0] = ModelHarnessCell.build(**values)
    analysis = analyze_model_harness(protocol, tuple(cells))
    assert ModelHarnessConfoundCode.BUDGET_MISMATCH in analysis.confounds


def test_model_harness_and_partition_drift_are_typed_confounds() -> None:
    protocol = _protocol()
    cells = list(_cells(protocol))
    original = cells[0]
    coordinate = ModelHarnessCoordinate(
        model=ModelIdentity(model_id="model-x", model_version="v9"),
        harness=original.coordinate.harness,
        partition=HarnessPartition.HARNESS_VALIDATION_TASKS,
    )
    values = original.model_dump(mode="python", exclude={"content_hash"})
    values["coordinate"] = coordinate
    cells[0] = ModelHarnessCell.build(**values)
    analysis = analyze_model_harness(protocol, tuple(cells))
    assert ModelHarnessConfoundCode.MODEL_IDENTITY_MISMATCH in analysis.confounds
    assert ModelHarnessConfoundCode.PARTITION_MISMATCH in analysis.confounds
    assert ModelHarnessConfoundCode.UNEXPECTED_CELL in analysis.confounds


def test_harness_identity_drift_is_a_typed_confound() -> None:
    protocol = _protocol()
    cells = list(_cells(protocol))
    original = cells[0]
    coordinate = ModelHarnessCoordinate(
        model=original.coordinate.model,
        harness=HarnessIdentity(harness_id="harness-x", harness_version="v9"),
        partition=original.coordinate.partition,
    )
    values = original.model_dump(mode="python", exclude={"content_hash"})
    values["coordinate"] = coordinate
    cells[0] = ModelHarnessCell.build(**values)
    analysis = analyze_model_harness(protocol, tuple(cells))
    assert ModelHarnessConfoundCode.HARNESS_IDENTITY_MISMATCH in analysis.confounds


def test_stale_trace_and_invalid_reward_block_descriptive_analysis() -> None:
    protocol = _protocol()
    cells = list(_cells(protocol))
    cells[0] = _cell(protocol, protocol.expected_grid[0], trace_current=False)
    cells[1] = _cell(protocol, protocol.expected_grid[1], reward_valid=False)
    analysis = analyze_model_harness(protocol, tuple(cells))
    assert ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH in analysis.confounds
    assert ModelHarnessConfoundCode.REWARD_RECEIPT_MISMATCH in analysis.confounds
    assert analysis.comparisons == ()


def test_protocol_cell_and_analysis_hashes_reject_tampering() -> None:
    protocol = _protocol()
    cell = _cells(protocol)[0]
    analysis = analyze_model_harness(protocol, _cells(protocol))
    assert protocol.content_hash == model_harness_protocol_hash(protocol)
    assert cell.content_hash == model_harness_cell_hash(cell)
    assert analysis.content_hash == model_harness_analysis_hash(analysis)

    for record in (protocol, cell, analysis):
        values = record.model_dump(mode="python")
        values["content_hash"] = HASH_B
        with pytest.raises(ValidationError, match="content_hash must canonically address"):
            type(record).model_validate(values)


def test_rehashed_cell_cannot_conceal_a_contradictory_protocol_hash() -> None:
    protocol = _protocol()
    cell = _cells(protocol)[0]
    values = cell.model_dump(mode="python")
    values["protocol_hash"] = HASH_B
    values["content_hash"] = model_harness_cell_hash(values, exclude_fields={"content_hash"})
    with pytest.raises(ValidationError, match="protocol hash"):
        ModelHarnessCell.model_validate(values)


def test_analysis_cannot_be_rehashed_into_a_causal_or_promotion_record() -> None:
    protocol = _protocol()
    analysis = analyze_model_harness(protocol, _cells(protocol))
    values = analysis.model_dump(mode="python", exclude={"content_hash"})
    values["causal_claim_permitted"] = True
    values["content_hash"] = model_harness_analysis_hash(values)
    with pytest.raises(ValidationError):
        ModelHarnessAnalysis.model_validate(values)

    with pytest.raises(ValidationError):
        ModelHarnessAnalysis.model_validate(
            analysis.model_dump(mode="python") | {"promotion_authorized": True}
        )


def test_rehashed_analysis_cannot_conceal_a_contradictory_protocol_identity() -> None:
    protocol = _protocol()
    analysis = analyze_model_harness(protocol, _cells(protocol))
    values = analysis.model_dump(mode="python")
    values["protocol_id"] = "another-protocol"
    values["content_hash"] = model_harness_analysis_hash(values)
    with pytest.raises(ValidationError, match="exact protocol identifier"):
        ModelHarnessAnalysis.model_validate(values)


def test_rehashed_analysis_rejects_an_undeclared_comparison_kind() -> None:
    protocol = _protocol(comparison_kinds=(ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,))
    analysis = analyze_model_harness(protocol, _cells(protocol))
    comparison_values = analysis.comparisons[0].model_dump(mode="python", exclude={"content_hash"})
    comparison_values["kind"] = ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT
    replacement = ModelHarnessComparison.build(**comparison_values)
    values = analysis.model_dump(mode="python")
    values["comparisons"] = (replacement, *analysis.comparisons[1:])
    values["content_hash"] = model_harness_analysis_hash(values)

    with pytest.raises(ValidationError, match="declared by the protocol"):
        ModelHarnessAnalysis.model_validate(values)


@pytest.mark.parametrize("tamper", ["outsider_id", "inconsistent_hash"])
def test_rehashed_analysis_rejects_comparison_cells_outside_its_inventory(
    tamper: str,
) -> None:
    protocol = _protocol()
    analysis = analyze_model_harness(protocol, _cells(protocol))
    comparison_values = analysis.comparisons[0].model_dump(mode="python", exclude={"content_hash"})
    if tamper == "outsider_id":
        comparison_values["cell_ids"] = (
            "outsider-cell",
            *analysis.comparisons[0].cell_ids[1:],
        )
    else:
        comparison_values["cell_hashes"] = (
            HASH_B,
            *analysis.comparisons[0].cell_hashes[1:],
        )
    replacement = ModelHarnessComparison.build(**comparison_values)
    values = analysis.model_dump(mode="python")
    values["comparisons"] = (replacement, *analysis.comparisons[1:])
    values["content_hash"] = model_harness_analysis_hash(values)

    with pytest.raises(ValidationError, match="analysis cell inventory"):
        ModelHarnessAnalysis.model_validate(values)


def test_model_harness_public_api_is_exported_from_harness_eval_package() -> None:
    from super_scientist.domain import harness_eval

    assert harness_eval.ModelHarnessProtocol is ModelHarnessProtocol
    assert harness_eval.analyze_model_harness is analyze_model_harness_contract
    assert not hasattr(harness_eval, "build_declared_comparisons")

    protocol = _protocol()
    cells = list(_cells(protocol))
    cells[0] = _cell(protocol, protocol.expected_grid[0], trace_current=False)
    assert analyze_model_harness(protocol, tuple(cells)).comparisons == ()
