from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from time import perf_counter

import pytest
from pydantic import ValidationError

import super_scientist.domain.harness_eval as harness_eval
import super_scientist.domain.harness_eval.matrix as matrix_module
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    HarnessEvidenceSnapshotIndex,
    HarnessEvidenceSnapshotRecord,
    harness_cell_evidence_chain_receipt,
    project_harness_evidence_snapshots,
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
from super_scientist.domain.harness_eval.receipts import (
    EvidenceReceipt,
    ResolvedEvidenceInventory,
    ResolvedEvidenceKind,
)
from super_scientist.domain.harness_eval.rewards import (
    ResolvedRewardHackingDiagnostic,
    ResolvedVerificationResultSnapshot,
    RewardHackingCoverageAttestation,
    RewardHackingFamily,
    RewardHackingFinding,
    RewardHackingFindingStatus,
    RewardValidityAssessment,
    RewardValidityStatus,
    VerificationOutcomeEvidence,
    VerificationOutcomeStatus,
    assess_reward_validity,
    reward_assessment_hash,
    reward_hacking_diagnostic_status_snapshot_hash,
    verification_result_status_snapshot_hash,
)
from super_scientist.domain.harness_eval.traces import (
    AvailableValue,
    EnvironmentEvent,
    EnvironmentEventKind,
    GenerationMetadata,
    HarnessExecutionTrace,
    MetadataAvailability,
    ObservableArtifactRef,
    RewardObservation,
    TraceBinding,
    TraceBindingMismatch,
    TraceExpectation,
    TraceExpectationResolutionAttestation,
    TraceFreshness,
    TraceFreshnessStatus,
    artifact_collection_hash,
    trace_expectation_snapshot_hash,
    trace_freshness,
    trace_freshness_hash,
)
from super_scientist.domain.improvement.models import ResourceUsage
from tests.unit.harness_eval.test_model_harness_matrix import _budget, _metrics
from tests.unit.harness_eval.test_traces import (
    HASH_A,
    HASH_B,
    HASH_C,
    HASH_D,
    attested_trace_expectation_bundle,
    resolved_evidence_inventory,
    reward_observation,
    trace_expectation,
    trace_expectation_bundle,
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


def _accepted_inventory(
    expectation_inventory: ResolvedEvidenceInventory,
    verification: VerificationOutcomeEvidence,
    coverage: RewardHackingCoverageAttestation,
    observation: RewardObservation,
    *,
    suffix: str = "1",
) -> ResolvedEvidenceInventory:
    assert observation.evidence_id is not None
    entries: list[tuple[EvidenceReceipt, ResolvedEvidenceKind]] = [
        (item.receipt, item.kind) for item in expectation_inventory.records
    ]
    for snapshot in (verification.verifier_result, verification.checker_result):
        entries.extend(
            (
                (snapshot.source, ResolvedEvidenceKind.VERIFICATION_RESULT_SOURCE),
                (snapshot.result, ResolvedEvidenceKind.VERIFICATION_RESULT),
                (snapshot.resolver, ResolvedEvidenceKind.RESOLVER),
            )
        )
        entries.extend(
            (item, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
            for item in snapshot.observable_evidence
        )
    for diagnostic in coverage.diagnostics:
        entries.extend(
            (
                (diagnostic.source, ResolvedEvidenceKind.DIAGNOSTIC_SOURCE),
                (diagnostic.resolver, ResolvedEvidenceKind.RESOLVER),
            )
        )
        entries.extend(
            (item, ResolvedEvidenceKind.OBSERVABLE_EVIDENCE)
            for item in diagnostic.observable_evidence
        )
    entries.extend((item, ResolvedEvidenceKind.PROVENANCE) for item in coverage.provenance)
    entries.append(
        (
            _receipt(observation.evidence_id, observation.content_hash),
            ResolvedEvidenceKind.OBSERVABLE_EVIDENCE,
        )
    )
    return resolved_evidence_inventory(
        tuple(entries),
        inventory_id=f"accepted-evidence-{suffix}",
    )


def test_trace_expectation_rejects_an_unattested_resolved_snapshot() -> None:
    values = _expectation_for_trace().model_dump(
        mode="python",
        exclude={"content_hash", "resolution"},
    )

    with pytest.raises(ValidationError, match="resolution"):
        TraceExpectation.build(**values)


def test_trace_freshness_requires_separately_supplied_resolved_inventory() -> None:
    with pytest.raises(TypeError, match="inventory"):
        trace_freshness(_expectation_for_trace(), valid_trace())  # type: ignore[call-arg]


def test_trace_freshness_rejects_same_record_expectation_source() -> None:
    trace = valid_trace()
    expectation, inventory = trace_expectation_bundle()
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

    with pytest.raises(ValueError, match="does not accept EXPECTATION_SOURCE"):
        trace_freshness(reconstructed, trace, inventory=inventory)


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
    expectation, inventory = trace_expectation_bundle()
    attacked = valid_trace(
        observed_binding_updates={"environment_hash": HASH_D},
    )

    freshness = trace_freshness(expectation, attacked, inventory=inventory)

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


def test_coordinated_expectation_remint_fails_against_original_inventory() -> None:
    attacked = valid_trace(observed_binding_updates={"environment_hash": HASH_D})
    expectation, original_inventory = trace_expectation_bundle()
    values = expectation.model_dump(
        mode="python",
        exclude={"content_hash", "resolution"},
    )
    values["environment"] = _receipt(
        attacked.observed_binding.environment_id,
        attacked.observed_binding.environment_hash,
    )
    snapshot_hash = trace_expectation_snapshot_hash(values)
    resolution_values = expectation.resolution.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    resolution_values["resolved_snapshot_hash"] = snapshot_hash
    resolution_values["expectation_source"] = _receipt(
        expectation.resolution.expectation_source.record_id,
        snapshot_hash,
    )
    values["resolution"] = TraceExpectationResolutionAttestation.build(**resolution_values)
    reminted = TraceExpectation.build(**values)

    with pytest.raises(ValueError, match="does not accept EXPECTATION_SOURCE"):
        trace_freshness(reminted, attacked, inventory=original_inventory)


def test_freshness_binds_stable_context_and_output_schema_ids() -> None:
    expectation, inventory = trace_expectation_bundle()
    attacked = valid_trace(
        observed_binding_updates={
            "context_id": "context-attacker",
            "output_schema_id": "output-schema-attacker",
        }
    )

    freshness = trace_freshness(expectation, attacked, inventory=inventory)

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

    complete_findings = _completed_diagnostics(trace)
    expectation, expectation_inventory = trace_expectation_bundle()
    verification = _verification_evidence(trace)
    coverage = _diagnostic_coverage(trace, complete_findings)
    inventory = _accepted_inventory(expectation_inventory, verification, coverage, observation)
    with pytest.raises(ValueError, match="every reward-hacking family"):
        assess_reward_validity(
            observation,
            trace,
            (),
            expectation=expectation,
            verification=verification,
            diagnostic_coverage=coverage,
            inventory=inventory,
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


def test_reward_validity_requires_separately_supplied_resolved_inventory() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    findings = _completed_diagnostics(trace)

    with pytest.raises(TypeError, match="inventory"):
        assess_reward_validity(
            observation,
            trace,
            findings,
            expectation=trace_expectation(),
            verification=_verification_evidence(trace),
            diagnostic_coverage=_diagnostic_coverage(trace, findings),
        )  # type: ignore[call-arg]

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
    expectation, expectation_inventory = trace_expectation_bundle()
    verification = _verification_evidence(trace)
    inventory = _accepted_inventory(expectation_inventory, verification, coverage, observation)
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
            expectation=expectation,
            verification=verification,
            diagnostic_coverage=coverage,
            inventory=inventory,
        )


def test_coordinated_diagnostic_status_remint_fails_original_inventory() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    cleared = _completed_diagnostics(trace)
    first_values = cleared[0].model_dump(mode="python", exclude={"content_hash"})
    invalidating = (
        RewardHackingFinding.build(
            **(first_values | {"status": RewardHackingFindingStatus.INVALIDATING})
        ),
        *cleared[1:],
    )
    expectation, expectation_inventory = trace_expectation_bundle()
    verification = _verification_evidence(trace)
    original_coverage = _diagnostic_coverage(trace, invalidating)
    original_inventory = _accepted_inventory(
        expectation_inventory,
        verification,
        original_coverage,
        observation,
    )
    reminted_coverage = _diagnostic_coverage(trace, cleared)

    with pytest.raises(ValueError, match="does not accept DIAGNOSTIC_SOURCE"):
        assess_reward_validity(
            observation,
            trace,
            cleared,
            expectation=expectation,
            verification=verification,
            diagnostic_coverage=reminted_coverage,
            inventory=original_inventory,
        )


def test_coordinated_verifier_status_remint_fails_original_inventory() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    findings = _completed_diagnostics(trace)
    expectation, expectation_inventory = trace_expectation_bundle()
    verification = _verification_evidence(trace)
    coverage = _diagnostic_coverage(trace, findings)
    original_inventory = _accepted_inventory(
        expectation_inventory,
        verification,
        coverage,
        observation,
    )
    result_values = verification.verifier_result.model_dump(
        mode="python",
        exclude={"content_hash", "source"},
    )
    result_values["status"] = VerificationOutcomeStatus.FAILED
    result_values["source"] = _receipt(
        verification.verifier_result.source.record_id,
        verification_result_status_snapshot_hash(result_values),
    )
    reminted_result = ResolvedVerificationResultSnapshot.build(**result_values)
    verification_values = verification.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    verification_values["verifier_result"] = reminted_result
    reminted_verification = VerificationOutcomeEvidence.build(**verification_values)

    with pytest.raises(ValueError, match="does not accept VERIFICATION_RESULT_SOURCE"):
        assess_reward_validity(
            observation,
            trace,
            findings,
            expectation=expectation,
            verification=reminted_verification,
            diagnostic_coverage=coverage,
            inventory=original_inventory,
        )


def test_reward_evidence_components_compose_to_260_receipts() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    findings = tuple(
        RewardHackingFinding.build(
            finding_id=f"wide-diagnostic-{index:02d}",
            family=family,
            status=RewardHackingFindingStatus.CLEARED,
            trace_id=trace.trace_id,
            trace_hash=trace.content_hash,
            observation_id=observation.observation_id,
            observation_hash=observation.content_hash,
            evidence_ids=tuple(
                f"wide-evidence-{index:02d}-{evidence_index:02d}" for evidence_index in range(24)
            ),
        )
        for index, family in enumerate(RewardHackingFamily)
    )
    expectation, expectation_inventory = trace_expectation_bundle()
    verification = _verification_evidence(trace)
    coverage = _diagnostic_coverage(trace, findings)
    inventory = _accepted_inventory(
        expectation_inventory,
        verification,
        coverage,
        observation,
    )

    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=coverage,
        inventory=inventory,
    )

    assert len(assessment.evidence_receipts) == 260


def test_reward_evidence_accepts_exact_3355_receipt_worst_case_and_rejects_overflow() -> None:
    trace = valid_trace()
    observation = trace.reward_observation
    assert observation is not None
    findings = tuple(
        RewardHackingFinding.build(
            finding_id=f"max-diagnostic-{index:02d}",
            family=family,
            status=RewardHackingFindingStatus.CLEARED,
            trace_id=trace.trace_id,
            trace_hash=trace.content_hash,
            observation_id=observation.observation_id,
            observation_hash=observation.content_hash,
            evidence_ids=tuple(
                f"m{index:02d}-e{evidence_index:03d}" for evidence_index in range(256)
            ),
        )
        for index, family in enumerate(RewardHackingFamily)
    )
    diagnostics: list[ResolvedRewardHackingDiagnostic] = []
    for index, finding in enumerate(findings):
        values: dict[str, object] = {
            "family": finding.family,
            "status": finding.status,
            "observable_evidence": tuple(
                _receipt(evidence_id, HASH_C) for evidence_id in finding.evidence_ids
            ),
            "resolver": _receipt(f"max-diagnostic-resolver-{index:02d}", HASH_B),
        }
        values["source"] = _receipt(
            f"max-diagnostic-source-{index:02d}",
            reward_hacking_diagnostic_status_snapshot_hash(values),
        )
        diagnostics.append(ResolvedRewardHackingDiagnostic.build(**values))
    coverage = RewardHackingCoverageAttestation.build(
        attestation_id="max-diagnostic-coverage",
        trace=_receipt(trace.trace_id, trace.content_hash),
        observation=_receipt(observation.observation_id, observation.content_hash),
        diagnostics=tuple(diagnostics),
        provenance=tuple(
            _receipt(f"max-coverage-provenance-{index:03d}", HASH_D) for index in range(256)
        ),
    )
    observed = trace.observed_binding
    verifier = _receipt(observed.validator_id, observed.validator_hash)
    checker = _receipt(observed.checker_id, observed.checker_hash)
    verification_results: list[ResolvedVerificationResultSnapshot] = []
    for label, executor, result_id, result_hash in (
        ("verifier", verifier, trace.verifier_result_id, trace.verifier_result_hash),
        ("checker", checker, trace.checker_result_id, trace.checker_result_hash),
    ):
        values = {
            "snapshot_id": f"max-{label}-result",
            "executor": executor,
            "result": _receipt(result_id, result_hash),
            "status": VerificationOutcomeStatus.SUCCEEDED,
            "observable_evidence": tuple(
                _receipt(f"max-{label}-evidence-{index:03d}", HASH_A) for index in range(256)
            ),
            "resolver": _receipt(f"max-{label}-resolver", HASH_D),
        }
        values["source"] = _receipt(
            f"max-{label}-result",
            verification_result_status_snapshot_hash(values),
        )
        verification_results.append(ResolvedVerificationResultSnapshot.build(**values))
    verification = VerificationOutcomeEvidence.build(
        outcome_id="max-verification-outcome",
        verifier=verifier,
        verifier_result=verification_results[0],
        checker=checker,
        checker_result=verification_results[1],
    )
    expectation, expectation_inventory = trace_expectation_bundle()
    inventory = _accepted_inventory(
        expectation_inventory,
        verification,
        coverage,
        observation,
        suffix="max-evidence",
    )

    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=coverage,
        inventory=inventory,
    )
    assert len(assessment.evidence_receipts) == 3_355

    overflow = findings[0].model_dump(mode="python", exclude={"content_hash"})
    overflow["evidence_ids"] = (*findings[0].evidence_ids, "overflow-evidence")
    with pytest.raises(ValidationError):
        RewardHackingFinding.build(**overflow)


def test_numeric_reward_coefficient_has_an_exact_digit_bound() -> None:
    accepted = reward_observation(value=Decimal("9" * 256))
    assert accepted.value == Decimal("9" * 256)

    with pytest.raises(ValidationError, match="decimal coefficient exceeds bound"):
        reward_observation(value=Decimal("9" * 257))


def test_numeric_reward_exponent_and_canonical_bytes_are_bounded() -> None:
    assert reward_observation(value=Decimal("1e1024")).value == Decimal("1e1024")

    with pytest.raises(ValidationError, match="decimal exponent exceeds bound"):
        reward_observation(value=Decimal("1e1025"))

    wide = Decimal((0, tuple(range(1, 10)) * 28 + (1, 2, 3, 4), -1024))
    assert len(wide.as_tuple().digits) == 256
    with pytest.raises(ValidationError, match="decimal canonical bytes exceed bound"):
        reward_observation(value=wide)


def test_generation_log_probabilities_reject_oversized_decimal() -> None:
    with pytest.raises(ValidationError, match="decimal coefficient exceeds bound"):
        GenerationMetadata.build(
            token_ids=AvailableValue(
                status=MetadataAvailability.AVAILABLE,
                value=(1,),
                evidence_id="token-evidence",
            ),
            token_count=AvailableValue(
                status=MetadataAvailability.AVAILABLE,
                value=1,
                evidence_id="count-evidence",
            ),
            log_probabilities=AvailableValue(
                status=MetadataAvailability.AVAILABLE,
                value=(Decimal("9" * 257),),
                evidence_id="logprob-evidence",
            ),
            sampling_parameters_hash=AvailableValue(
                status=MetadataAvailability.UNAVAILABLE, value=None, evidence_id=None
            ),
            stop_reason=AvailableValue(
                status=MetadataAvailability.UNAVAILABLE, value=None, evidence_id=None
            ),
            provider_request_id=AvailableValue(
                status=MetadataAvailability.UNAVAILABLE, value=None, evidence_id=None
            ),
        )


def test_reward_observation_rejects_oversized_canonical_payload() -> None:
    long_id = "x" * 200
    with pytest.raises(ValidationError, match="reward observation canonical bytes exceed bound"):
        RewardObservation.build(
            observation_id=long_id,
            task_id=long_id,
            task_input_hash=HASH_A,
            verifier_id=long_id,
            verifier_version=long_id,
            checker_id=long_id,
            checker_version=long_id,
            checker_result_id=long_id,
            checker_result_hash=HASH_B,
            evaluator_id=long_id,
            evaluator_version=long_id,
            value="x" * 200,
            evidence_id=long_id,
            observed_at=valid_trace().observed_at,
        )


def test_harness_trace_rejects_oversized_canonical_payload() -> None:
    artifacts = tuple(
        ObservableArtifactRef.build(
            artifact_id=f"a{index:03d}-" + "x" * 195,
            sha256=HASH_A,
            size_bytes=1,
            media_type="m" * 200,
        )
        for index in range(192)
    )
    trace = valid_trace(context_artifacts_override=artifacts)
    values = trace.model_dump(
        mode="python",
        exclude={"content_hash", "provenance_hash"},
    )
    values["provenance_evidence_ids"] = tuple(f"p{index:03d}-" + "x" * 195 for index in range(256))

    with pytest.raises(ValidationError, match="harness trace canonical bytes exceed bound"):
        HarnessExecutionTrace.build(**values)


def test_reward_assessment_binds_the_exact_freshness_hash() -> None:
    trace = valid_trace()
    expectation, expectation_inventory = trace_expectation_bundle()
    observation = trace.reward_observation
    assert observation is not None

    findings = _completed_diagnostics(trace)
    verification = _verification_evidence(trace)
    coverage = _diagnostic_coverage(trace, findings)
    inventory = _accepted_inventory(expectation_inventory, verification, coverage, observation)
    freshness = trace_freshness(expectation, trace, inventory=inventory)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=coverage,
        inventory=inventory,
    )

    assert assessment.freshness_hash == freshness.content_hash


def _valid_evaluation_snapshots() -> tuple[TraceFreshness, RewardValidityAssessment]:
    trace = valid_trace()
    expectation, expectation_inventory = trace_expectation_bundle()
    observation = trace.reward_observation
    assert observation is not None
    findings = _completed_diagnostics(trace)
    verification = _verification_evidence(trace)
    coverage = _diagnostic_coverage(trace, findings)
    inventory = _accepted_inventory(expectation_inventory, verification, coverage, observation)
    freshness = trace_freshness(expectation, trace, inventory=inventory)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=coverage,
        inventory=inventory,
    )
    return freshness, assessment


def test_direct_parse_revalidates_inventory_bound_snapshots() -> None:
    freshness, assessment = _valid_evaluation_snapshots()
    freshness_payload = freshness.model_dump(mode="python")
    freshness_payload["resolution_inventory_hash"] = HASH_D
    freshness_payload["content_hash"] = trace_freshness_hash(freshness_payload)
    with pytest.raises(ValidationError, match="exact resolved evidence inventory"):
        TraceFreshness.model_validate(freshness_payload)

    assessment_payload = assessment.model_dump(mode="python")
    assessment_payload["evidence_receipts"] = assessment.evidence_receipts[1:]
    assessment_payload["content_hash"] = reward_assessment_hash(assessment_payload)
    with pytest.raises(ValidationError, match="exact accepted evidence receipts"):
        RewardValidityAssessment.model_validate(assessment_payload)


def test_resolved_inventory_contracts_are_publicly_exported() -> None:
    assert harness_eval.ResolvedEvidenceInventory is ResolvedEvidenceInventory
    assert harness_eval.ResolvedEvidenceKind is ResolvedEvidenceKind
    assert harness_eval.ResolvedEvidenceRecord.__name__ == "ResolvedEvidenceRecord"


@dataclass(frozen=True)
class _MatrixEvidenceFixture:
    chain: HarnessCellEvidenceChain
    record: HarnessEvidenceSnapshotRecord
    trace: HarnessExecutionTrace
    freshness: TraceFreshness
    assessment: RewardValidityAssessment


def _snapshot_index(
    evidence: tuple[_MatrixEvidenceFixture, ...],
) -> HarnessEvidenceSnapshotIndex:
    return HarnessEvidenceSnapshotIndex.build(
        records=tuple(
            sorted(
                (item.record for item in evidence),
                key=lambda item: item.chain_receipt.record_id,
            )
        ),
    )


def _matrix_evidence_chain(
    protocol: ModelHarnessProtocol,
    coordinate: ModelHarnessCoordinate,
    index: int,
    *,
    stale_environment: bool = False,
    binding_updates: dict[str, object] | None = None,
) -> _MatrixEvidenceFixture:
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
    if stale_environment or binding_updates:
        binding_values = expected_binding.model_dump(mode="python", exclude={"content_hash"})
        if stale_environment:
            binding_values["environment_hash"] = HASH_D
        if binding_updates:
            binding_values.update(binding_updates)
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
    expectation, expectation_inventory = attested_trace_expectation_bundle(
        expected_binding,
        suffix=f"matrix-{index:03d}",
    )
    findings = _completed_diagnostics(trace)
    verification = _verification_evidence(trace)
    coverage = _diagnostic_coverage(trace, findings)
    inventory = _accepted_inventory(
        expectation_inventory,
        verification,
        coverage,
        observation,
        suffix=f"matrix-{index:03d}",
    )
    freshness = trace_freshness(expectation, trace, inventory=inventory)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings,
        expectation=expectation,
        verification=verification,
        diagnostic_coverage=coverage,
        inventory=inventory,
    )
    chain, record = project_harness_evidence_snapshots(
        protocol=protocol,
        coordinate=coordinate,
        trace=trace,
        freshness=freshness,
        assessment=assessment,
    )
    return _MatrixEvidenceFixture(
        chain=chain,
        record=record,
        trace=trace,
        freshness=freshness,
        assessment=assessment,
    )


def test_matrix_cell_rejects_boolean_evidence_shortcuts() -> None:
    assert "trace_current" not in ModelHarnessCell.model_fields
    assert "reward_valid" not in ModelHarnessCell.model_fields
    assert "protocol" not in ModelHarnessCell.model_fields
    assert "protocol_receipt" in ModelHarnessCell.model_fields


def test_cell_evidence_chain_is_a_compact_receipt_join() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    full = _matrix_evidence_chain(protocol, protocol.expected_grid[0], 0)
    compact = HarnessCellEvidenceChain.build(
        protocol_receipt=_receipt(protocol.protocol_id, protocol.content_hash),
        coordinate=protocol.expected_grid[0],
        trace_receipt=_receipt(full.trace.trace_id, full.trace.content_hash),
        freshness_receipt=_receipt(
            full.freshness.freshness_id,
            full.freshness.content_hash,
        ),
        assessment_receipt=_receipt(
            full.assessment.assessment_id,
            full.assessment.content_hash,
        ),
    )

    assert compact.trace_receipt.record_id == full.trace.trace_id
    assert "trace" not in HarnessCellEvidenceChain.model_fields
    assert "freshness" not in HarnessCellEvidenceChain.model_fields
    assert "assessment" not in HarnessCellEvidenceChain.model_fields


def test_compact_chain_rejects_trace_scalar_mismatch_behind_valid_protocol_receipt() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    with pytest.raises(ValueError, match="exact protocol"):
        _matrix_evidence_chain(
            protocol,
            protocol.expected_grid[0],
            0,
            binding_updates={"validator_id": "attacker-validator"},
        )


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
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(chain.chain),
            observed_at=valid_trace().observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=(chain.chain,),
        evidence_index=_snapshot_index((chain,)),
    )

    assert analysis.comparisons == ()
    assert ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH in analysis.confounds


def test_matrix_bounds_surplus_evidence_chains_before_element_validation() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    evidence = tuple(
        _matrix_evidence_chain(protocol, coordinate, index)
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"bounded-chain-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(evidence[index].chain),
            observed_at=evidence[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    with pytest.raises(ValueError, match="evidence chain count exceeds expected grid"):
        analyze_model_harness(
            protocol,
            cells,
            evidence_chains=(evidence[0].chain,) * 300,
            evidence_index=_snapshot_index(evidence),
        )


def test_matrix_rejects_duplicate_chain_receipts_within_collection_bound() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    evidence = tuple(
        _matrix_evidence_chain(protocol, coordinate, index)
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"duplicate-chain-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(evidence[index].chain),
            observed_at=evidence[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    duplicated = (evidence[0].chain, evidence[0].chain, *(item.chain for item in evidence[2:]))

    with pytest.raises(ValueError, match="unique identifiers"):
        analyze_model_harness(
            protocol,
            cells,
            evidence_chains=duplicated,
            evidence_index=_snapshot_index(evidence),
        )


def test_matrix_surplus_index_receipt_suppresses_comparisons() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    evidence = tuple(
        _matrix_evidence_chain(protocol, coordinate, index)
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"exact-chain-cell-{index:02d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(evidence[index].chain),
            observed_at=evidence[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    surplus = _matrix_evidence_chain(protocol, protocol.expected_grid[0], 99)
    all_evidence = (*evidence, surplus)

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=tuple(item.chain for item in evidence),
        evidence_index=_snapshot_index(all_evidence),
    )

    assert analysis.comparisons == ()
    assert ModelHarnessConfoundCode.TRACE_RECEIPT_MISMATCH in analysis.confounds
    assert ModelHarnessConfoundCode.REWARD_RECEIPT_MISMATCH in analysis.confounds


def test_matrix_protocol_outer_bytes_bound_composed_budget_inventory() -> None:
    models = tuple(
        ModelIdentity(model_id=f"wide-model-{index:02d}", model_version="v1") for index in range(20)
    )
    harnesses = (
        HarnessIdentity(harness_id="wide-harness-a", harness_version="v1"),
        HarnessIdentity(harness_id="wide-harness-b", harness_version="v1"),
    )
    partition = HarnessPartition.HARNESS_DISCOVERY_TASKS
    tools = tuple(f"tool-{index:03d}-" + "x" * 191 for index in range(256))
    bindings = tuple(
        ModelBudgetBinding.build(model=model, budget=_budget(model, tool_ids=tools))
        for model in models
    )
    grid = tuple(
        ModelHarnessCoordinate(model=model, harness=harness, partition=partition)
        for model, harness in product(models, harnesses)
    )

    with pytest.raises(
        ValidationError,
        match="model-harness protocol canonical bytes exceed bound",
    ):
        ModelHarnessProtocol.build(
            protocol_id="wide-matrix-protocol",
            version=1,
            models=models,
            harnesses=harnesses,
            partitions=(partition,),
            task_set_id="task-set-a",
            task_set_hash=HASH_A,
            verifier_id="verifier-a",
            verifier_version="v1",
            checker_id="checker-a",
            checker_version="v1",
            artifact_ids=("artifact-a",),
            random_seed=7,
            output_schema_hash=HASH_A,
            model_budgets=bindings,
            matched_resource_envelope_hash=bindings[0].resource_envelope_hash,
            expected_grid=grid,
            comparison_kinds=(ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,),
            governing_policy_hash=HASH_A,
        )


def test_matrix_budget_binding_strictly_revalidates_copied_released_budget() -> None:
    model = ModelIdentity(model_id="copied-budget-model", model_version="v1")
    copied = _budget(model).model_copy(update={"token_limit": 1 << 10_000})

    with pytest.raises(ValidationError, match="evaluation budget integers exceed bound"):
        ModelBudgetBinding.build(model=model, budget=copied)


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
        HarnessCellEvidenceChain.from_snapshots(**values)  # type: ignore[arg-type]


def test_snapshot_projection_rejects_model_copy_freshness_status_spoof() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    evidence = _matrix_evidence_chain(
        protocol,
        protocol.expected_grid[0],
        0,
        stale_environment=True,
    )
    assert evidence.freshness.status is TraceFreshnessStatus.STALE
    copied = evidence.freshness.model_copy(update={"status": TraceFreshnessStatus.CURRENT})

    with pytest.raises(ValueError, match="canonical validated snapshots"):
        HarnessEvidenceSnapshotRecord.from_snapshots(
            chain=evidence.chain,
            trace=evidence.trace,
            freshness=copied,
            assessment=evidence.assessment,
        )


def test_snapshot_projection_rejects_model_construct_assessment_status_spoof() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    evidence = _matrix_evidence_chain(
        protocol,
        protocol.expected_grid[0],
        0,
        stale_environment=True,
    )
    assert evidence.assessment.status is RewardValidityStatus.INVALID
    constructed = RewardValidityAssessment.model_construct(
        **(evidence.assessment.__dict__ | {"status": RewardValidityStatus.VALID})
    )

    with pytest.raises(ValueError, match="canonical validated snapshots"):
        HarnessEvidenceSnapshotRecord.from_snapshots(
            chain=evidence.chain,
            trace=evidence.trace,
            freshness=evidence.freshness,
            assessment=constructed,
        )


def test_chain_projection_rejects_nested_trace_model_copy_with_retained_hash() -> None:
    from tests.unit.harness_eval.test_model_harness_matrix import _protocol

    protocol = _protocol()
    evidence = _matrix_evidence_chain(protocol, protocol.expected_grid[0], 0)
    copied = evidence.trace.model_copy(update={"context_artifacts": ()})
    assert copied.content_hash == evidence.trace.content_hash

    with pytest.raises(ValueError, match="canonical validated snapshots"):
        HarnessCellEvidenceChain.from_snapshots(
            protocol=protocol,
            coordinate=protocol.expected_grid[0],
            trace=copied,
            freshness=evidence.freshness,
            assessment=evidence.assessment,
        )


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
                _receipt(chains[index].chain.chain_id, HASH_D)
                if index == 0
                else harness_cell_evidence_chain_receipt(chains[index].chain)
            ),
            observed_at=chains[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=tuple(item.chain for item in chains),
        evidence_index=_snapshot_index(chains),
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
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(chains[index].chain),
            observed_at=chains[index].trace.observed_at,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=tuple(item.chain for item in chains),
        evidence_index=_snapshot_index(chains),
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
    started = perf_counter()
    chains = tuple(
        _matrix_evidence_chain(protocol, coordinate, index) for index, coordinate in enumerate(grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"cell-{index:03d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(chains[index].chain),
            observed_at=chains[index].trace.observed_at,
        )
        for index, coordinate in enumerate(grid)
    )

    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=tuple(item.chain for item in chains),
        evidence_index=_snapshot_index(chains),
    )

    assert analysis.confounds == ()
    assert len(analysis.comparisons) == 1_232
    assert perf_counter() - started < 60.0


def test_maximum_shape_matrix_emits_24512_comparisons_within_runtime_bound() -> None:
    models = tuple(
        ModelIdentity(model_id=f"max-model-{index:03d}", model_version="v1") for index in range(128)
    )
    harnesses = tuple(
        HarnessIdentity(harness_id=f"max-harness-{index}", harness_version="v1")
        for index in range(2)
    )
    partition = HarnessPartition.HARNESS_DISCOVERY_TASKS
    grid = tuple(
        ModelHarnessCoordinate(model=model, harness=harness, partition=partition)
        for model, harness in product(models, harnesses)
    )
    near_limit_budget_updates: dict[str, object] = {
        "tool_ids": ("tool-" + "x" * 195,),
        "token_limit": (1 << 1_000) - 1,
        "wall_clock_seconds": Decimal("1." + "1" * 255),
        "cost_limit": Decimal("0." + "1" * 256),
    }
    model_budgets = tuple(
        ModelBudgetBinding.build(
            model=model,
            budget=_budget(model, **near_limit_budget_updates),
        )
        for model in models
    )
    protocol = ModelHarnessProtocol.build(
        protocol_id="matrix-maximum-shape",
        version=1,
        models=models,
        harnesses=harnesses,
        partitions=(partition,),
        task_set_id="task-set-a",
        task_set_hash=HASH_A,
        verifier_id="verifier-a",
        verifier_version="v1",
        checker_id="checker-a",
        checker_version="v1",
        artifact_ids=("artifact-a",),
        random_seed=7,
        output_schema_hash=HASH_A,
        model_budgets=model_budgets,
        matched_resource_envelope_hash=evaluation_resource_envelope_hash(
            _budget(models[0], **near_limit_budget_updates)
        ),
        expected_grid=grid,
        comparison_kinds=(
            ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,
            ModelHarnessComparisonKind.HARNESS_HELD_CONSTANT,
            ModelHarnessComparisonKind.INTERACTION_DESCRIPTIVE,
        ),
        governing_policy_hash=HASH_A,
    )
    started = perf_counter()
    evidence = tuple(
        _matrix_evidence_chain(protocol, coordinate, index) for index, coordinate in enumerate(grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"max-cell-{index:03d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(evidence[index].chain),
            observed_at=evidence[index].trace.observed_at,
        )
        for index, coordinate in enumerate(grid)
    )
    analysis = analyze_model_harness(
        protocol,
        cells,
        evidence_chains=tuple(item.chain for item in evidence),
        evidence_index=_snapshot_index(evidence),
    )
    elapsed = perf_counter() - started

    assert analysis.confounds == ()
    assert len(analysis.comparisons) == 24_512
    assert elapsed < 180.0


def test_oversized_matrix_rejects_before_constructing_cartesian_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = tuple(
        ModelIdentity(model_id=f"overflow-model-{index:03d}", model_version="v1")
        for index in range(256)
    )
    harnesses = tuple(
        HarnessIdentity(harness_id=f"overflow-harness-{index:03d}", harness_version="v1")
        for index in range(256)
    )
    model_budgets = tuple(
        ModelBudgetBinding.build(model=model, budget=_budget(model)) for model in models
    )
    coordinate = ModelHarnessCoordinate(
        model=models[0],
        harness=harnesses[0],
        partition=HarnessPartition.HARNESS_DISCOVERY_TASKS,
    )
    values: dict[str, object] = {
        "protocol_id": "oversized-matrix",
        "version": 1,
        "models": tuple(model.model_dump(mode="python") for model in models),
        "harnesses": tuple(harness.model_dump(mode="python") for harness in harnesses),
        "partitions": tuple(HarnessPartition),
        "task_set_id": "task-set-a",
        "task_set_hash": HASH_A,
        "verifier_id": "verifier-a",
        "verifier_version": "v1",
        "checker_id": "checker-a",
        "checker_version": "v1",
        "artifact_ids": ("artifact-a",),
        "random_seed": 7,
        "output_schema_hash": HASH_A,
        "model_budgets": tuple(binding.model_dump(mode="python") for binding in model_budgets),
        "matched_resource_envelope_hash": evaluation_resource_envelope_hash(_budget(models[0])),
        "expected_grid": tuple(coordinate.model_dump(mode="python") for _ in range(4)),
        "comparison_kinds": (ModelHarnessComparisonKind.MODEL_HELD_CONSTANT,),
        "governing_policy_hash": HASH_A,
    }

    def unexpected_coordinate_construction(*args: object, **kwargs: object) -> None:
        raise AssertionError("oversized grid must reject before coordinate construction")

    monkeypatch.setattr(
        matrix_module,
        "ModelHarnessCoordinate",
        unexpected_coordinate_construction,
    )

    for construct in (
        lambda: ModelHarnessProtocol.build(**values),
        lambda: ModelHarnessProtocol.model_validate(values),
    ):
        with pytest.raises(
            ValidationError,
            match="model-harness Cartesian grid exceeds 256 cells",
        ):
            construct()
