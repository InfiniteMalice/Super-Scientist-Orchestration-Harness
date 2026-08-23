from __future__ import annotations

from itertools import product

import pytest
from pydantic import ValidationError

from super_scientist.domain.harness_eval.guidance import (
    RecoveryAttemptEvent,
    RecoveryOutcome,
)
from super_scientist.domain.harness_eval.matrix import (
    HarnessIdentity,
    ModelBudgetBinding,
    ModelHarnessCell,
    ModelHarnessComparisonKind,
    ModelHarnessConfoundCode,
    ModelHarnessCoordinate,
    ModelHarnessProtocol,
    ModelIdentity,
    analyze_model_harness,
    evaluation_resource_envelope_hash,
)
from super_scientist.domain.harness_eval.models import HarnessPartition
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import (
    RewardHackingFamily,
    RewardHackingFinding,
    RewardHackingFindingStatus,
    RewardValidityAssessment,
    VerificationOutcomeEvidence,
    VerificationOutcomeStatus,
    assess_reward_validity,
    reward_validity_receipt,
)
from super_scientist.domain.harness_eval.traces import (
    EnvironmentEvent,
    EnvironmentEventKind,
    HarnessExecutionTrace,
    ObservableArtifactRef,
    TraceBindingMismatch,
    TraceExpectation,
    TraceFreshness,
    TraceFreshnessStatus,
    trace_freshness,
    trace_freshness_receipt,
)
from tests.unit.harness_eval.test_model_harness_matrix import _budget, _metrics
from tests.unit.harness_eval.test_traces import (
    HASH_C,
    HASH_D,
    trace_expectation,
    valid_trace,
)


def _receipt(record_id: str, content_hash: str) -> EvidenceReceipt:
    return EvidenceReceipt(
        record_id=record_id,
        schema_version=1,
        content_hash=content_hash,
    )


def _expectation_for_trace() -> TraceExpectation:
    trace = valid_trace()
    observed = trace.observed_binding
    return TraceExpectation.build(
        protocol=_receipt(observed.protocol_id, observed.protocol_hash),
        task=_receipt(observed.task_id, observed.task_input_hash),
        model=_receipt(observed.model.model_id, observed.model_hash),
        harness=_receipt(observed.harness.harness_id, observed.harness_hash),
        procedure=_receipt(observed.procedure_id, observed.procedure_hash),
        environment=_receipt(observed.environment_id, observed.environment_hash),
        context=_receipt("context-a", observed.context_hash),
        validator=_receipt(observed.validator_id, observed.validator_hash),
        checker=_receipt(observed.checker_id, observed.checker_hash),
        artifacts=tuple(
            _receipt(artifact_id, artifact_hash)
            for artifact_id, artifact_hash in zip(
                observed.artifact_ids,
                observed.artifact_hashes,
                strict=True,
            )
        ),
        output_schema=_receipt("output-schema-a", observed.output_schema_hash),
    )


def test_equal_attacker_mutation_cannot_self_authenticate_trace_freshness() -> None:
    expectation = _expectation_for_trace()
    attacked = valid_trace(
        observed_binding_updates={"environment_hash": HASH_D},
    )

    freshness = trace_freshness(expectation, attacked)

    assert freshness.status is TraceFreshnessStatus.STALE
    assert freshness.mismatches == (TraceBindingMismatch.ENVIRONMENT,)
    assert "expected_binding" not in type(attacked).model_fields


def test_freshness_binds_stable_context_and_output_schema_ids() -> None:
    expectation = _expectation_for_trace()
    attacked = valid_trace(
        observed_binding_updates={
            "context_id": "context-attacker",
            "output_schema_id": "output-schema-attacker",
        }
    )

    freshness = trace_freshness(expectation, attacked)

    assert TraceBindingMismatch.CONTEXT in freshness.mismatches
    assert TraceBindingMismatch.OUTPUT_SCHEMA in freshness.mismatches


def test_trace_numeric_bounds_fail_closed() -> None:
    with pytest.raises(ValidationError):
        ObservableArtifactRef.build(
            artifact_id="oversized-artifact",
            sha256=HASH_D,
            size_bytes=1_073_741_825,
            media_type="application/octet-stream",
        )

    with pytest.raises(ValidationError):
        EnvironmentEvent.build(
            sequence=256,
            environment_id="environment-a",
            environment_version="v1",
            kind=EnvironmentEventKind.STARTED,
            evidence_id="environment-evidence",
        )


def test_guidance_recovery_attempt_has_an_exact_upper_bound() -> None:
    accepted = RecoveryAttemptEvent(
        event_id="recovery-256",
        attempt=256,
        target_step_id="step-a",
        outcome=RecoveryOutcome.SUCCEEDED,
    )
    assert accepted.attempt == 256

    with pytest.raises(ValidationError):
        RecoveryAttemptEvent(
            event_id="recovery-257",
            attempt=257,
            target_step_id="step-a",
            outcome=RecoveryOutcome.SUCCEEDED,
        )


def test_evaluation_protocol_versions_and_seeds_are_bounded() -> None:
    from tests.unit.harness_eval.test_guidance import _protocol as guidance_protocol
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol as matrix_protocol

    with pytest.raises(ValidationError):
        guidance_protocol(version=2_147_483_648)
    with pytest.raises(ValidationError):
        guidance_protocol(random_seed=9_223_372_036_854_775_808)
    with pytest.raises(ValidationError):
        matrix_protocol(version=2_147_483_648)
    with pytest.raises(ValidationError):
        matrix_protocol(random_seed=9_223_372_036_854_775_808)


def _verification_evidence(
    trace: HarnessExecutionTrace | None = None,
) -> VerificationOutcomeEvidence:
    trace = valid_trace() if trace is None else trace
    observed = trace.observed_binding
    return VerificationOutcomeEvidence.build(
        outcome_id="verification-outcome-1",
        verifier=_receipt(observed.validator_id, observed.validator_hash),
        verifier_result=_receipt("verifier-result-1", HASH_C),
        verifier_status=VerificationOutcomeStatus.SUCCEEDED,
        checker=_receipt(observed.checker_id, observed.checker_hash),
        checker_result=_receipt(trace.checker_result_id, trace.checker_result_hash),
        checker_status=VerificationOutcomeStatus.SUCCEEDED,
        evidence_ids=("checker-evidence", "verifier-evidence"),
    )


def _completed_diagnostics(
    trace: HarnessExecutionTrace | None = None,
) -> tuple[RewardHackingFinding, ...]:
    trace = valid_trace() if trace is None else trace
    observation = trace.reward_observation
    assert observation is not None
    return tuple(
        RewardHackingFinding.build(
            finding_id=f"diagnostic-{index:02d}",
            family=family,
            status=RewardHackingFindingStatus.CLEARED,
            trace_id=trace.trace_id,
            trace_hash=trace.content_hash,
            observation_id=observation.observation_id,
            observation_hash=observation.content_hash,
            evidence_ids=(f"diagnostic-evidence-{index:02d}",),
        )
        for index, family in enumerate(RewardHackingFamily)
    )


def test_bare_verifier_boolean_cannot_spoof_reward_validity() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None

    with pytest.raises(TypeError):
        assess_reward_validity(
            observation,
            trace,
            _completed_diagnostics(),
            expectation=trace_expectation(trace),
            verification=_verification_evidence(),
            verifier_succeeded=True,  # type: ignore[call-arg]
        )


def test_omitted_reward_hacking_diagnostic_families_fail_closed() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None

    with pytest.raises(ValueError, match="every reward-hacking family"):
        assess_reward_validity(
            observation,
            trace,
            (),
            expectation=trace_expectation(trace),
            verification=_verification_evidence(),
        )


def test_reward_assessment_binds_the_exact_freshness_hash() -> None:
    trace = valid_trace()
    expectation = trace_expectation(trace)
    freshness = trace_freshness(expectation, trace)
    observation = trace.reward_observation
    assert observation is not None

    assessment = assess_reward_validity(
        observation,
        trace,
        _completed_diagnostics(trace),
        expectation=expectation,
        verification=_verification_evidence(trace),
    )

    assert assessment.freshness_hash == freshness.content_hash


def _valid_evaluation_snapshots() -> tuple[TraceFreshness, RewardValidityAssessment]:
    trace = valid_trace()
    expectation = trace_expectation(trace)
    freshness = trace_freshness(expectation, trace)
    observation = trace.reward_observation
    assert observation is not None
    assessment = assess_reward_validity(
        observation,
        trace,
        _completed_diagnostics(),
        expectation=expectation,
        verification=_verification_evidence(),
    )
    return freshness, assessment


def test_matrix_cell_rejects_boolean_evidence_shortcuts() -> None:
    assert "trace_current" not in ModelHarnessCell.model_fields
    assert "reward_valid" not in ModelHarnessCell.model_fields


def test_matrix_receipt_spoof_suppresses_analysis() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    freshness, assessment = _valid_evaluation_snapshots()
    good_trace_receipt = trace_freshness_receipt(freshness)
    good_reward_receipt = reward_validity_receipt(assessment)
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"security-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            trace_freshness_receipt=(
                _receipt(good_trace_receipt.record_id, HASH_D) if index == 0 else good_trace_receipt
            ),
            reward_validity_receipt=good_reward_receipt,
            observed_at=valid_trace().observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        trace_freshness=(freshness,),
        reward_assessments=(assessment,),
    )

    assert analysis.comparisons == ()
    assert ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH in analysis.confounds


def test_matrix_rejects_exactly_receipted_stale_and_invalid_snapshots() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    trace = valid_trace(observed_binding_updates={"environment_hash": HASH_D})
    expectation = trace_expectation()
    freshness = trace_freshness(expectation, trace)
    observation = trace.reward_observation
    assert observation is not None
    assessment = assess_reward_validity(
        observation,
        trace,
        _completed_diagnostics(trace),
        expectation=expectation,
        verification=_verification_evidence(trace),
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"stale-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            trace_freshness_receipt=trace_freshness_receipt(freshness),
            reward_validity_receipt=reward_validity_receipt(assessment),
            observed_at=trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        trace_freshness=(freshness,),
        reward_assessments=(assessment,),
    )

    assert ModelHarnessConfoundCode.STALE_TRACE in analysis.confounds
    assert ModelHarnessConfoundCode.INVALID_REWARD in analysis.confounds
    assert analysis.comparisons == ()


def test_eight_by_eight_valid_matrix_fits_declared_comparison_bound() -> None:
    models = tuple(
        ModelIdentity(model_id=f"model-{index:02d}", model_version="v1") for index in range(8)
    )
    harnesses = tuple(
        HarnessIdentity(harness_id=f"harness-{index:02d}", harness_version="v1")
        for index in range(8)
    )
    partitions = (HarnessPartition.HARNESS_DISCOVERY_TASKS,)
    grid = tuple(
        ModelHarnessCoordinate(model=model, harness=harness, partition=partition)
        for model, harness, partition in product(models, harnesses, partitions)
    )
    model_budgets = tuple(
        ModelBudgetBinding.build(model=model, budget=_budget(model)) for model in models
    )
    protocol = ModelHarnessProtocol.build(
        protocol_id="matrix-eight-by-eight",
        version=1,
        models=models,
        harnesses=harnesses,
        partitions=partitions,
        task_set_id="task-set-a",
        task_set_hash="a" * 64,
        verifier_id="verifier-a",
        verifier_version="v1",
        checker_id="checker-a",
        checker_version="v1",
        artifact_ids=("artifact-a",),
        random_seed=7,
        output_schema_hash="a" * 64,
        model_budgets=model_budgets,
        matched_resource_envelope_hash=evaluation_resource_envelope_hash(_budget(models[0])),
        expected_grid=grid,
        comparison_kinds=(
            ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,
            ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT,
            ModelHarnessComparisonKind.INTERACTION_DESCRIPTIVE,
        ),
        governing_policy_hash="a" * 64,
    )
    freshness, assessment = _valid_evaluation_snapshots()
    trace_receipt = trace_freshness_receipt(freshness)
    reward_receipt = reward_validity_receipt(assessment)
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"cell-{index:03d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            trace_freshness_receipt=trace_receipt,
            reward_validity_receipt=reward_receipt,
            observed_at=valid_trace().observed_at,
        )
        for index, coordinate in enumerate(grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        trace_freshness=(freshness,),
        reward_assessments=(assessment,),
    )

    assert analysis.confounds == ()
    assert len(analysis.comparisons) == 1_232
