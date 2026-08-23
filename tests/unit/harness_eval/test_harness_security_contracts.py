from __future__ import annotations

from decimal import Decimal
from itertools import product

import pytest
from pydantic import ValidationError

from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    harness_cell_evidence_chain_receipt,
)
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
    ResolvedRewardHackingDiagnostic,
    ResolvedVerificationResultSnapshot,
    RewardHackingCoverageAttestation,
    RewardHackingFamily,
    RewardHackingFinding,
    RewardHackingFindingStatus,
    RewardValidityAssessment,
    VerificationOutcomeEvidence,
    VerificationOutcomeStatus,
    assess_reward_validity,
    reward_hacking_diagnostic_status_snapshot_hash,
    verification_result_status_snapshot_hash,
)
from super_scientist.domain.harness_eval.traces import (
    EnvironmentEvent,
    EnvironmentEventKind,
    HarnessExecutionTrace,
    ObservableArtifactRef,
    RewardObservation,
    TraceBinding,
    TraceBindingMismatch,
    TraceExpectation,
    TraceExpectationResolutionAttestation,
    TraceFreshness,
    TraceFreshnessStatus,
    artifact_collection_hash,
    trace_freshness,
)
from super_scientist.domain.improvement.models import ResourceUsage
from tests.unit.harness_eval.test_model_harness_matrix import _budget, _metrics
from tests.unit.harness_eval.test_traces import (
    HASH_A,
    HASH_B,
    HASH_C,
    HASH_D,
    attested_trace_expectation,
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
    return trace_expectation()


def test_trace_expectation_rejects_an_unattested_resolved_snapshot() -> None:
    values = _expectation_for_trace().model_dump(
        mode="python",
        exclude={"content_hash", "resolution"},
    )

    with pytest.raises(ValidationError, match="resolution"):
        TraceExpectation.build(**values)


def test_trace_freshness_rejects_same_record_expectation_source() -> None:
    trace = valid_trace()
    expectation = _expectation_for_trace()
    values = expectation.model_dump(mode="python", exclude={"content_hash"})
    resolution_values = expectation.resolution.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    resolution_values["expectation_source"] = _receipt(
        trace.trace_id,
        expectation.resolution.resolved_snapshot_hash,
    )
    values["resolution"] = TraceExpectationResolutionAttestation.build(**resolution_values)
    reconstructed = TraceExpectation.build(**values)

    freshness = trace_freshness(reconstructed, trace)

    assert freshness.status is TraceFreshnessStatus.STALE
    assert freshness.mismatches == (TraceBindingMismatch.EXPECTATION_SOURCE,)


def test_expectation_source_receipt_must_address_resolved_snapshot() -> None:
    resolution = _expectation_for_trace().resolution
    values = resolution.model_dump(mode="python", exclude={"content_hash"})
    values["expectation_source"] = _receipt(
        resolution.expectation_source.record_id,
        HASH_D,
    )

    with pytest.raises(ValidationError, match="source receipt must address"):
        TraceExpectationResolutionAttestation.build(**values)


def test_equal_attacker_mutation_cannot_self_authenticate_trace_freshness() -> None:
    expectation = _expectation_for_trace()
    attacked = valid_trace(
        observed_binding_updates={"environment_hash": HASH_D},
    )

    freshness = trace_freshness(expectation, attacked)

    assert freshness.status is TraceFreshnessStatus.STALE
    assert freshness.mismatches == (TraceBindingMismatch.ENVIRONMENT,)
    assert "expected_binding" not in type(attacked).model_fields


def test_equal_trace_and_expectation_mutation_breaks_source_attestation() -> None:
    attacked = valid_trace(observed_binding_updates={"environment_hash": HASH_D})
    expectation = _expectation_for_trace()
    values = expectation.model_dump(mode="python", exclude={"content_hash"})
    values["environment"] = _receipt(
        attacked.observed_binding.environment_id,
        attacked.observed_binding.environment_hash,
    )

    with pytest.raises(ValidationError, match="exact resolved expectation snapshot"):
        TraceExpectation.build(**values)


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


def test_trace_rejects_1001_bit_embedded_resource_usage_integers() -> None:
    trace = valid_trace()
    values = trace.model_dump(
        mode="python",
        exclude={"content_hash", "resource_usage_hash"},
    )
    values["resource_usage"] = ResourceUsage(
        cost_usd=0.0,
        compute_units=0.0,
        tokens=1 << 1000,
        elapsed_seconds=0.0,
        tool_calls=0,
        human_interventions=0,
    )

    with pytest.raises(ValidationError, match="1000-bit"):
        HarnessExecutionTrace.build(**values)

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
    verifier = _receipt(observed.validator_id, observed.validator_hash)
    checker = _receipt(observed.checker_id, observed.checker_hash)
    verifier_result = _resolved_verification_result(
        snapshot_id="resolved-verifier-result-1",
        executor=verifier,
        result=_receipt(trace.verifier_result_id, trace.verifier_result_hash),
        evidence=_receipt("verifier-evidence", HASH_C),
    )
    checker_result = _resolved_verification_result(
        snapshot_id="resolved-checker-result-1",
        executor=checker,
        result=_receipt(trace.checker_result_id, trace.checker_result_hash),
        evidence=_receipt("checker-evidence", HASH_C),
    )
    return VerificationOutcomeEvidence.build(
        outcome_id="verification-outcome-1",
        verifier=verifier,
        verifier_result=verifier_result,
        checker=checker,
        checker_result=checker_result,
    )


def _resolved_verification_result(
    *,
    snapshot_id: str,
    executor: EvidenceReceipt,
    result: EvidenceReceipt,
    evidence: EvidenceReceipt,
) -> ResolvedVerificationResultSnapshot:
    values: dict[str, object] = {
        "snapshot_id": snapshot_id,
        "executor": executor,
        "result": result,
        "status": VerificationOutcomeStatus.SUCCEEDED,
        "observable_evidence": (evidence,),
        "resolver": _receipt("verification-result-resolver", HASH_D),
    }
    values["source"] = _receipt(
        snapshot_id,
        verification_result_status_snapshot_hash(values),
    )
    return ResolvedVerificationResultSnapshot.build(**values)


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


def _diagnostic_coverage(
    trace: HarnessExecutionTrace,
    findings: tuple[RewardHackingFinding, ...],
) -> RewardHackingCoverageAttestation:
    observation = trace.reward_observation
    assert observation is not None
    return RewardHackingCoverageAttestation.build(
        attestation_id=f"coverage-{trace.trace_id}",
        trace=_receipt(trace.trace_id, trace.content_hash),
        observation=_receipt(observation.observation_id, observation.content_hash),
        diagnostics=tuple(
            _resolved_diagnostic(finding, index) for index, finding in enumerate(findings)
        ),
        provenance=(_receipt("diagnostic-coverage-provenance", HASH_D),),
    )


def _resolved_diagnostic(
    finding: RewardHackingFinding,
    index: int,
) -> ResolvedRewardHackingDiagnostic:
    values: dict[str, object] = {
        "family": finding.family,
        "status": finding.status,
        "observable_evidence": tuple(
            _receipt(evidence_id, HASH_C) for evidence_id in finding.evidence_ids
        ),
        "resolver": _receipt("diagnostic-resolver", HASH_B),
    }
    values["source"] = _receipt(
        f"diagnostic-source-{index:02d}",
        reward_hacking_diagnostic_status_snapshot_hash(values),
    )
    return ResolvedRewardHackingDiagnostic.build(**values)


def test_bare_verifier_boolean_cannot_spoof_reward_validity() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None

    with pytest.raises(TypeError):
        findings = _completed_diagnostics(trace)
        assess_reward_validity(
            observation,
            trace,
            findings,
            expectation=trace_expectation(),
            verification=_verification_evidence(),
            diagnostic_coverage=_diagnostic_coverage(trace, findings),
            verifier_succeeded=True,  # type: ignore[call-arg]
        )


def test_verification_status_is_derived_from_resolved_result_snapshots() -> None:
    assert "verifier_status" not in VerificationOutcomeEvidence.model_fields
    assert "checker_status" not in VerificationOutcomeEvidence.model_fields
    assert "source" in ResolvedVerificationResultSnapshot.model_fields

    verification = _verification_evidence()
    substituted = verification.verifier_result.model_dump(mode="python")
    substituted["status"] = VerificationOutcomeStatus.FAILED
    with pytest.raises(ValidationError, match="exact status snapshot"):
        ResolvedVerificationResultSnapshot.model_validate(substituted)


def test_omitted_reward_hacking_diagnostic_families_fail_closed() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None

    with pytest.raises(ValueError, match="every reward-hacking family"):
        complete_findings = _completed_diagnostics(trace)
        assess_reward_validity(
            observation,
            trace,
            (),
            expectation=trace_expectation(),
            verification=_verification_evidence(),
            diagnostic_coverage=_diagnostic_coverage(trace, complete_findings),
        )


def test_reward_validity_requires_resolved_diagnostic_coverage_attestation() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    assert "source" in ResolvedRewardHackingDiagnostic.model_fields

    with pytest.raises(TypeError, match="diagnostic_coverage"):
        assess_reward_validity(
            observation,
            trace,
            _completed_diagnostics(trace),
            expectation=trace_expectation(),
            verification=_verification_evidence(trace),
        )

    with pytest.raises(ValidationError):
        ResolvedRewardHackingDiagnostic.build(
            family=RewardHackingFamily.PROXY_GAMING,
            status=RewardHackingFindingStatus.CLEARED,
            observable_evidence=(),
            source=_receipt("diagnostic-source", HASH_A),
            resolver=_receipt("diagnostic-resolver", HASH_B),
        )

    finding = _completed_diagnostics(trace)[0]
    resolved = _resolved_diagnostic(finding, 0)
    substituted = resolved.model_dump(mode="python")
    substituted["source"] = _receipt(resolved.source.record_id, HASH_D)
    with pytest.raises(ValidationError, match="exact status and evidence snapshot"):
        ResolvedRewardHackingDiagnostic.model_validate(substituted)


@pytest.mark.parametrize(
    "finding_update",
    (
        {"status": RewardHackingFindingStatus.INVALIDATING},
        {"evidence_ids": ("substituted-diagnostic-evidence",)},
    ),
)
def test_finding_status_or_evidence_substitution_fails_closed(
    finding_update: dict[str, object],
) -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    findings = _completed_diagnostics(trace)
    coverage = _diagnostic_coverage(trace, findings)
    first_values = findings[0].model_dump(mode="python", exclude={"content_hash"})
    substituted = (
        RewardHackingFinding.build(**(first_values | finding_update)),
        *findings[1:],
    )

    with pytest.raises(ValueError, match="resolved diagnostic coverage"):
        assess_reward_validity(
            observation,
            trace,
            substituted,
            expectation=trace_expectation(),
            verification=_verification_evidence(trace),
            diagnostic_coverage=coverage,
        )


def test_reward_assessment_binds_the_exact_freshness_hash() -> None:
    trace = valid_trace()
    expectation = trace_expectation()
    freshness = trace_freshness(expectation, trace)
    observation = trace.reward_observation
    assert observation is not None

    findings = _completed_diagnostics(trace)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=_verification_evidence(trace),
        diagnostic_coverage=_diagnostic_coverage(trace, findings),
    )

    assert assessment.freshness_hash == freshness.content_hash


def _valid_evaluation_snapshots() -> tuple[TraceFreshness, RewardValidityAssessment]:
    trace = valid_trace()
    expectation = trace_expectation()
    freshness = trace_freshness(expectation, trace)
    observation = trace.reward_observation
    assert observation is not None
    findings = _completed_diagnostics(trace)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=_verification_evidence(),
        diagnostic_coverage=_diagnostic_coverage(trace, findings),
    )
    return freshness, assessment


def _matrix_evidence_chain(
    protocol: ModelHarnessProtocol,
    coordinate: ModelHarnessCoordinate,
    index: int,
    *,
    stale_environment: bool = False,
) -> HarnessCellEvidenceChain:
    artifact = ObservableArtifactRef.build(
        artifact_id="artifact-a",
        sha256=HASH_A,
        size_bytes=1,
        media_type="application/json",
    )
    context_hash = artifact_collection_hash((artifact,))
    expected_binding = TraceBinding.from_model_harness_protocol(
        protocol,
        coordinate,
        artifacts=(artifact,),
        model_hash=HASH_B,
        harness_hash=HASH_C,
        procedure_id="procedure-1",
        procedure_version="v1",
        procedure_hash=HASH_A,
        environment_id="environment",
        environment_version="v1",
        environment_hash=HASH_B,
        context_id=f"matrix-context-{index:03d}",
        context_hash=context_hash,
        output_schema_id="output-schema-a",
        validator_hash=HASH_C,
        checker_hash=HASH_D,
    )
    if stale_environment:
        binding_values = expected_binding.model_dump(mode="python", exclude={"content_hash"})
        binding_values["environment_hash"] = HASH_D
        binding = TraceBinding.build(**binding_values)
    else:
        binding = expected_binding
    observation = RewardObservation.build(
        observation_id=f"matrix-reward-{index:03d}",
        task_id=protocol.task_set_id,
        task_input_hash=protocol.task_set_hash,
        verifier_id=protocol.verifier_id,
        verifier_version=protocol.verifier_version,
        checker_id=protocol.checker_id,
        checker_version=protocol.checker_version,
        checker_result_id="checker-result-1",
        checker_result_hash=HASH_C,
        evaluator_id="evaluator",
        evaluator_version="v1",
        value=Decimal("0.9"),
        evidence_id=f"matrix-reward-evidence-{index:03d}",
        observed_at=valid_trace().observed_at,
    )
    trace = valid_trace(
        trace_id=f"matrix-trace-{index:03d}",
        observed_binding=binding,
        context_artifacts_override=(artifact,),
        observation=observation,
    )
    expectation = attested_trace_expectation(
        expected_binding,
        suffix=f"matrix-{index:03d}",
    )
    freshness = trace_freshness(expectation, trace)
    findings = _completed_diagnostics(trace)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=_verification_evidence(trace),
        diagnostic_coverage=_diagnostic_coverage(trace, findings),
    )
    return HarnessCellEvidenceChain.build(
        protocol=protocol,
        coordinate=coordinate,
        trace=trace,
        freshness=freshness,
        assessment=assessment,
    )


def test_matrix_cell_rejects_boolean_evidence_shortcuts() -> None:
    assert "trace_current" not in ModelHarnessCell.model_fields
    assert "reward_valid" not in ModelHarnessCell.model_fields


def test_matrix_rejects_unrelated_trace_evidence_reused_for_every_cell() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    chain = _matrix_evidence_chain(protocol, protocol.expected_grid[0], 0)
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"unrelated-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(chain),
            observed_at=valid_trace().observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=(chain,),
    )

    assert analysis.comparisons == ()
    assert ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH in analysis.confounds


@pytest.mark.parametrize("substitute", ("freshness", "assessment"))
def test_cell_evidence_chain_rejects_freshness_or_assessment_substitution(
    substitute: str,
) -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    coordinate = protocol.expected_grid[0]
    first = _matrix_evidence_chain(protocol, coordinate, 0)
    second = _matrix_evidence_chain(protocol, coordinate, 99)
    values: dict[str, object] = {
        "protocol": protocol,
        "coordinate": coordinate,
        "trace": first.trace,
        "freshness": first.freshness,
        "assessment": first.assessment,
    }
    values[substitute] = getattr(second, substitute)

    with pytest.raises(ValueError, match="exact trace"):
        HarnessCellEvidenceChain.build(**values)


def test_matrix_receipt_spoof_suppresses_analysis() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    chains = tuple(
        _matrix_evidence_chain(protocol, coordinate, index)
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"security-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=(
                _receipt(chains[index].chain_id, HASH_D)
                if index == 0
                else harness_cell_evidence_chain_receipt(chains[index])
            ),
            observed_at=chains[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=chains,
    )

    assert analysis.comparisons == ()
    assert ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH in analysis.confounds


def test_matrix_rejects_exactly_receipted_stale_and_invalid_snapshots() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    chains = tuple(
        _matrix_evidence_chain(
            protocol,
            coordinate,
            index,
            stale_environment=True,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"stale-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(chains[index]),
            observed_at=chains[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=chains,
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
    chains = tuple(
        _matrix_evidence_chain(protocol, coordinate, index) for index, coordinate in enumerate(grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"cell-{index:03d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(chains[index]),
            observed_at=chains[index].trace.observed_at,
        )
        for index, coordinate in enumerate(grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=chains,
    )

    assert analysis.confounds == ()
    assert len(analysis.comparisons) == 1_232
