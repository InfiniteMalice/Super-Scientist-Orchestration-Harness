from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

import super_scientist.domain.harness_eval as harness_eval
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import (
    ResolvedRewardHackingDiagnostic,
    ResolvedVerificationResultSnapshot,
    RewardHackingCoverageAttestation,
    RewardHackingFamily,
    RewardHackingFinding,
    RewardHackingFindingStatus,
    RewardInvalidationReason,
    RewardValidityAssessment,
    RewardValidityStatus,
    VerificationOutcomeEvidence,
    VerificationOutcomeStatus,
    reward_assessment_hash,
    reward_hacking_diagnostic_status_snapshot_hash,
    valid_reward_evidence,
    verification_result_status_snapshot_hash,
)
from super_scientist.domain.harness_eval.rewards import (
    assess_reward_validity as assess_reward_validity_contract,
)
from super_scientist.domain.harness_eval.traces import (
    AvailableValue,
    CaptureRewardValidityStatus,
    EnvironmentEventKind,
    ExecutionStatus,
    MetadataAvailability,
    ObservableArtifactRef,
    ToolObservationStatus,
    TraceBinding,
    artifact_collection_hash,
)
from tests.unit.harness_eval.test_traces import (
    HASH_D,
    available,
    binding,
    reward_observation,
    trace_expectation,
    valid_trace,
)


def _verification_evidence(
    trace: object,
    succeeded: bool | None,
) -> VerificationOutcomeEvidence:
    status = (
        VerificationOutcomeStatus.UNKNOWN
        if succeeded is None
        else VerificationOutcomeStatus.SUCCEEDED
        if succeeded
        else VerificationOutcomeStatus.FAILED
    )
    observed = trace.observed_binding  # type: ignore[union-attr]
    verifier = EvidenceReceipt(
        record_id=observed.validator_id,
        schema_version=1,
        content_hash=observed.validator_hash,
    )
    checker = EvidenceReceipt(
        record_id=observed.checker_id,
        schema_version=1,
        content_hash=observed.checker_hash,
    )
    verifier_result_values: dict[str, object] = {
        "snapshot_id": "resolved-verifier-result-1",
        "executor": verifier,
        "result": EvidenceReceipt(
            record_id=trace.verifier_result_id,  # type: ignore[union-attr]
            schema_version=1,
            content_hash=trace.verifier_result_hash,  # type: ignore[union-attr]
        ),
        "status": status,
        "observable_evidence": (
            EvidenceReceipt(
                record_id="verifier-evidence",
                schema_version=1,
                content_hash="a" * 64,
            ),
        ),
        "resolver": EvidenceReceipt(
            record_id="verification-result-resolver",
            schema_version=1,
            content_hash="b" * 64,
        ),
    }
    verifier_result_values["source"] = EvidenceReceipt(
        record_id="resolved-verifier-result-1",
        schema_version=1,
        content_hash=verification_result_status_snapshot_hash(verifier_result_values),
    )
    checker_result_values: dict[str, object] = {
        "snapshot_id": "resolved-checker-result-1",
        "executor": checker,
        "result": EvidenceReceipt(
            record_id=trace.checker_result_id,  # type: ignore[union-attr]
            schema_version=1,
            content_hash=trace.checker_result_hash,  # type: ignore[union-attr]
        ),
        "status": status,
        "observable_evidence": (
            EvidenceReceipt(
                record_id="checker-evidence",
                schema_version=1,
                content_hash="c" * 64,
            ),
        ),
        "resolver": EvidenceReceipt(
            record_id="verification-result-resolver",
            schema_version=1,
            content_hash="b" * 64,
        ),
    }
    checker_result_values["source"] = EvidenceReceipt(
        record_id="resolved-checker-result-1",
        schema_version=1,
        content_hash=verification_result_status_snapshot_hash(checker_result_values),
    )
    return VerificationOutcomeEvidence.build(
        outcome_id="verification-outcome-1",
        verifier=verifier,
        verifier_result=ResolvedVerificationResultSnapshot.build(**verifier_result_values),
        checker=checker,
        checker_result=ResolvedVerificationResultSnapshot.build(**checker_result_values),
    )


def _complete_diagnostics(
    trace: object,
    findings: tuple[RewardHackingFinding, ...],
) -> tuple[RewardHackingFinding, ...]:
    by_family = {item.family: item for item in findings}
    return tuple(
        by_family.get(family)
        or reward_hacking_finding(
            trace,
            finding_id=f"diagnostic-{index:02d}",
            family=family,
            status=RewardHackingFindingStatus.CLEARED,
        )
        for index, family in enumerate(RewardHackingFamily)
    )


def assess_reward_validity(
    observation: object,
    trace: object,
    findings: tuple[RewardHackingFinding, ...],
    *,
    verifier_succeeded: bool | None,
) -> RewardValidityAssessment:
    completed = _complete_diagnostics(trace, findings)
    return assess_reward_validity_contract(
        observation,  # type: ignore[arg-type]
        trace,  # type: ignore[arg-type]
        completed,
        expectation=trace_expectation(),
        verification=_verification_evidence(trace, verifier_succeeded),
        diagnostic_coverage=_diagnostic_coverage(trace, completed),
    )


def _diagnostic_coverage(
    trace: object,
    findings: tuple[RewardHackingFinding, ...],
) -> RewardHackingCoverageAttestation:
    return RewardHackingCoverageAttestation.build(
        attestation_id="diagnostic-coverage-1",
        trace=EvidenceReceipt(
            record_id=trace.trace_id,  # type: ignore[union-attr]
            schema_version=1,
            content_hash=trace.content_hash,  # type: ignore[union-attr]
        ),
        observation=EvidenceReceipt(
            record_id=trace.reward_observation.observation_id,  # type: ignore[union-attr]
            schema_version=1,
            content_hash=trace.reward_observation.content_hash,  # type: ignore[union-attr]
        ),
        diagnostics=tuple(
            _resolved_diagnostic(finding, index) for index, finding in enumerate(findings)
        ),
        provenance=(
            EvidenceReceipt(
                record_id="diagnostic-coverage-provenance",
                schema_version=1,
                content_hash="c" * 64,
            ),
        ),
    )


def _resolved_diagnostic(
    finding: RewardHackingFinding,
    index: int,
) -> ResolvedRewardHackingDiagnostic:
    values: dict[str, object] = {
        "family": finding.family,
        "status": finding.status,
        "observable_evidence": tuple(
            EvidenceReceipt(
                record_id=evidence_id,
                schema_version=1,
                content_hash="d" * 64,
            )
            for evidence_id in finding.evidence_ids
        ),
        "resolver": EvidenceReceipt(
            record_id="diagnostic-resolver",
            schema_version=1,
            content_hash="b" * 64,
        ),
    }
    values["source"] = EvidenceReceipt(
        record_id=f"diagnostic-source-{index:02d}",
        schema_version=1,
        content_hash=reward_hacking_diagnostic_status_snapshot_hash(values),
    )
    return ResolvedRewardHackingDiagnostic.build(**values)


def test_valid_reward_requires_current_complete_observable_evidence() -> None:
    trace = valid_trace()
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=True,
    )

    assert assessment.status is RewardValidityStatus.VALID
    assert assessment.reasons == ()
    assert assessment.trace_id == trace.trace_id
    assert assessment.trace_hash == trace.content_hash
    assert valid_reward_evidence((assessment,)) == (trace.reward_observation,)


@pytest.mark.parametrize(
    ("trace", "verifier_succeeded", "reason"),
    (
        (
            valid_trace(execution_status=ExecutionStatus.CRASHED),
            True,
            RewardInvalidationReason.ENVIRONMENT_CRASH,
        ),
        (
            valid_trace(execution_status=ExecutionStatus.INCOMPLETE),
            True,
            RewardInvalidationReason.INCOMPLETE_EXECUTION,
        ),
        (
            valid_trace(observation=reward_observation(verifier_version="v2")),
            True,
            RewardInvalidationReason.VERIFIER_MISMATCH,
        ),
        (
            valid_trace(),
            False,
            RewardInvalidationReason.VERIFIER_FAILURE,
        ),
        (
            valid_trace(
                artifact_integrity=available(False, "corrupt-artifact"),
                event_kinds=(
                    EnvironmentEventKind.STARTED,
                    EnvironmentEventKind.ARTIFACT_CORRUPTION_DETECTED,
                    EnvironmentEventKind.COMPLETED,
                ),
            ),
            True,
            RewardInvalidationReason.CORRUPTED_ARTIFACT,
        ),
        (
            valid_trace(
                protected_boundary_crossed=available(True, "boundary-crossing"),
                event_kinds=(
                    EnvironmentEventKind.STARTED,
                    EnvironmentEventKind.PROTECTED_BOUNDARY_CROSSED,
                    EnvironmentEventKind.COMPLETED,
                ),
            ),
            True,
            RewardInvalidationReason.PROTECTED_ANSWER_LEAKAGE,
        ),
        (
            valid_trace(
                evaluator_succeeded=available(False, "evaluator-failure"),
                event_kinds=(
                    EnvironmentEventKind.STARTED,
                    EnvironmentEventKind.EVALUATOR_FAILED,
                    EnvironmentEventKind.COMPLETED,
                ),
            ),
            True,
            RewardInvalidationReason.EVALUATOR_FAILURE,
        ),
        (
            valid_trace(observed_binding_updates={"harness_hash": HASH_D}),
            True,
            RewardInvalidationReason.STALE_HARNESS_TRACE,
        ),
        (
            valid_trace(observed_binding_updates={"environment_hash": HASH_D}),
            True,
            RewardInvalidationReason.TASK_RUNTIME_MISMATCH,
        ),
        (
            valid_trace(tool_status=ToolObservationStatus.FAILED),
            True,
            RewardInvalidationReason.INCOMPLETE_EXECUTION,
        ),
    ),
)
def test_fail_closed_invalid_reward_conditions(
    trace: object,
    verifier_succeeded: bool,
    reason: RewardInvalidationReason,
) -> None:
    assessment = assess_reward_validity(
        trace.reward_observation,  # type: ignore[union-attr]
        trace,  # type: ignore[arg-type]
        findings=(),
        verifier_succeeded=verifier_succeeded,
    )

    assert assessment.status is RewardValidityStatus.INVALID
    assert reason in assessment.reasons
    assert valid_reward_evidence((assessment,)) == ()


def test_high_invalid_reward_is_excluded() -> None:
    observation = reward_observation(value=Decimal("1000"))
    trace = valid_trace(
        observation=observation,
        observed_binding_updates={"harness_hash": HASH_D},
    )
    assessment = assess_reward_validity(
        observation,
        trace,
        findings=(),
        verifier_succeeded=True,
    )
    assert assessment.status is RewardValidityStatus.INVALID
    assert RewardInvalidationReason.STALE_HARNESS_TRACE in assessment.reasons
    assert valid_reward_evidence((assessment,)) == ()


def test_checker_identity_mismatch_is_invalid_verifier_evidence() -> None:
    observation = reward_observation(checker_version="v2")
    trace = valid_trace(observation=observation)

    assessment = assess_reward_validity(
        observation,
        trace,
        (),
        verifier_succeeded=True,
    )

    assert assessment.status is RewardValidityStatus.INVALID
    assert RewardInvalidationReason.VERIFIER_MISMATCH in assessment.reasons


def test_undeclared_context_artifact_identity_is_stale_and_invalid() -> None:
    base = valid_trace()
    rogue_artifacts = (
        ObservableArtifactRef.build(
            artifact_id="rogue-context",
            sha256="a" * 64,
            size_bytes=12,
            media_type="application/json",
        ),
    )
    rogue_context_hash = artifact_collection_hash(rogue_artifacts)
    authorized = binding(
        context_hash=rogue_context_hash,
        artifact_ids=("authorized-context",),
        artifact_hashes=("a" * 64,),
    )
    observed = TraceBinding.build(
        **(
            {
                key: value
                for key, value in authorized.model_dump(mode="python").items()
                if key != "content_hash"
            }
            | {
                "artifact_ids": ("rogue-context",),
                "context_hash": rogue_context_hash,
            }
        )
    )
    trace_payload = {
        key: value for key, value in base.model_dump(mode="python").items() if key != "content_hash"
    } | {
        "observed_binding": observed,
        "context_artifacts": rogue_artifacts,
        "initial_context_hash": rogue_context_hash,
        "final_context_hash": rogue_context_hash,
    }
    trace = type(base).build(**trace_payload)

    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )

    assert assessment.status is RewardValidityStatus.INVALID
    assert RewardInvalidationReason.STALE_HARNESS_TRACE in assessment.reasons


def test_unknown_required_evidence_is_inconclusive_never_valid() -> None:
    unavailable_integrity = AvailableValue[bool](
        status=MetadataAvailability.UNAVAILABLE,
        value=None,
        evidence_id=None,
    )
    trace = valid_trace(artifact_integrity=unavailable_integrity)

    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=None,
    )

    assert assessment.status is RewardValidityStatus.INCONCLUSIVE
    assert assessment.reasons == (RewardInvalidationReason.UNKNOWN_EVIDENCE,)
    assert valid_reward_evidence((assessment,)) == ()


def test_reward_hacking_families_are_closed_observable_findings() -> None:
    trace = valid_trace()
    for family in RewardHackingFamily:
        finding = reward_hacking_finding(trace, family=family)
        assessment = assess_reward_validity(
            trace.reward_observation,
            trace,
            findings=(finding,),
            verifier_succeeded=True,
        )
        assert assessment.status is RewardValidityStatus.INVALID
        assert RewardInvalidationReason.REWARD_HACKING_FINDING in assessment.reasons
        assert finding.evidence_ids
        assert not {"motive", "intent", "reasoning", "scratchpad"} & set(
            RewardHackingFinding.model_fields
        )


def test_inconclusive_and_cleared_hacking_findings_follow_closed_policy() -> None:
    trace = valid_trace()
    inconclusive = reward_hacking_finding(
        trace,
        status=RewardHackingFindingStatus.INCONCLUSIVE,
    )
    cleared = reward_hacking_finding(
        trace,
        status=RewardHackingFindingStatus.CLEARED,
    )

    uncertain = assess_reward_validity(
        trace.reward_observation,
        trace,
        (inconclusive,),
        verifier_succeeded=True,
    )
    valid = assess_reward_validity(
        trace.reward_observation,
        trace,
        (cleared,),
        verifier_succeeded=True,
    )

    assert uncertain.status is RewardValidityStatus.INCONCLUSIVE
    assert uncertain.reasons == (RewardInvalidationReason.UNKNOWN_EVIDENCE,)
    assert valid.status is RewardValidityStatus.VALID


def test_reason_order_is_canonical_and_independent_of_finding_input_order() -> None:
    trace = valid_trace(
        execution_status=ExecutionStatus.CRASHED,
        artifact_integrity=available(False, "corruption"),
        protected_boundary_crossed=available(True, "leakage"),
        evaluator_succeeded=available(False, "evaluator"),
        observed_binding_updates={"environment_hash": HASH_D, "harness_hash": HASH_D},
        event_kinds=(
            EnvironmentEventKind.STARTED,
            EnvironmentEventKind.ARTIFACT_CORRUPTION_DETECTED,
            EnvironmentEventKind.PROTECTED_BOUNDARY_CROSSED,
            EnvironmentEventKind.EVALUATOR_FAILED,
            EnvironmentEventKind.CRASHED,
        ),
    )
    first = reward_hacking_finding(trace, finding_id="finding-a")
    second = reward_hacking_finding(
        trace,
        finding_id="finding-b",
        family=RewardHackingFamily.TRACE_INCONSISTENCY,
    )

    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (second, first),
        verifier_succeeded=False,
    )

    assert assessment.reasons == (
        RewardInvalidationReason.ENVIRONMENT_CRASH,
        RewardInvalidationReason.INCOMPLETE_EXECUTION,
        RewardInvalidationReason.VERIFIER_FAILURE,
        RewardInvalidationReason.CORRUPTED_ARTIFACT,
        RewardInvalidationReason.PROTECTED_ANSWER_LEAKAGE,
        RewardInvalidationReason.REWARD_HACKING_FINDING,
        RewardInvalidationReason.EVALUATOR_FAILURE,
        RewardInvalidationReason.STALE_HARNESS_TRACE,
        RewardInvalidationReason.TASK_RUNTIME_MISMATCH,
    )
    assert "finding-a" in assessment.finding_ids
    assert "finding-b" in assessment.finding_ids


def test_findings_must_bind_exact_trace_reward_and_observable_evidence() -> None:
    trace = valid_trace()
    for updates in (
        {"trace_id": "other-trace"},
        {"trace_hash": HASH_D},
        {"observation_id": "other-reward"},
        {"observation_hash": HASH_D},
    ):
        baseline = reward_hacking_finding(trace).model_dump(mode="python")
        baseline.pop("content_hash")
        finding = RewardHackingFinding.build(**(baseline | updates))
        with pytest.raises(ValueError, match="finding must bind exact"):
            assess_reward_validity(
                trace.reward_observation,
                trace,
                (finding,),
                verifier_succeeded=True,
            )

    with pytest.raises(ValidationError, match=r"evidence_ids|observable evidence"):
        RewardHackingFinding.build(
            finding_id="finding-empty",
            family=RewardHackingFamily.PROXY_GAMING,
            status=RewardHackingFindingStatus.INVALIDATING,
            trace_id=trace.trace_id,
            trace_hash=trace.content_hash,
            observation_id=trace.reward_observation.observation_id,
            observation_hash=trace.reward_observation.content_hash,
            evidence_ids=(),
        )


def test_capture_status_never_overrides_recomputed_assessment() -> None:
    captured_valid = valid_trace(
        capture_status=CaptureRewardValidityStatus.VALID,
        observed_binding_updates={"harness_hash": HASH_D},
    )
    captured_invalid = valid_trace(capture_status=CaptureRewardValidityStatus.INVALID)

    recomputed_invalid = assess_reward_validity(
        captured_valid.reward_observation,
        captured_valid,
        (),
        verifier_succeeded=True,
    )
    recomputed_valid = assess_reward_validity(
        captured_invalid.reward_observation,
        captured_invalid,
        (),
        verifier_succeeded=True,
    )

    assert recomputed_invalid.status is RewardValidityStatus.INVALID
    assert recomputed_valid.status is RewardValidityStatus.VALID
    assert valid_reward_evidence((recomputed_invalid, recomputed_valid)) == (
        captured_invalid.reward_observation,
    )


def test_assessment_direct_parse_rejects_rehashed_contradictory_status_and_reasons() -> None:
    trace = valid_trace()
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    payload = assessment.model_dump(mode="python") | {
        "status": RewardValidityStatus.INVALID,
        "reasons": (RewardInvalidationReason.REWARD_HACKING_FINDING,),
    }
    payload["content_hash"] = reward_assessment_hash(payload)

    with pytest.raises(ValidationError, match="recomputed reward validity"):
        RewardValidityAssessment.model_validate(payload)


def test_valid_reward_filter_revalidates_nonvalidating_model_copies() -> None:
    trace = valid_trace()
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    forged = assessment.model_copy(update={"status": RewardValidityStatus.INVALID})

    with pytest.raises(ValidationError):
        valid_reward_evidence((forged,))


def test_reward_public_api_is_exported_from_harness_eval_package() -> None:
    assert harness_eval.RewardValidityAssessment is RewardValidityAssessment
    assert harness_eval.assess_reward_validity is assess_reward_validity_contract
    assert harness_eval.valid_reward_evidence is valid_reward_evidence


def reward_hacking_finding(
    trace: object,
    *,
    finding_id: str = "finding-1",
    family: RewardHackingFamily = RewardHackingFamily.PROXY_GAMING,
    status: RewardHackingFindingStatus = RewardHackingFindingStatus.INVALIDATING,
) -> RewardHackingFinding:
    return RewardHackingFinding.build(
        finding_id=finding_id,
        family=family,
        status=status,
        trace_id=trace.trace_id,  # type: ignore[union-attr]
        trace_hash=trace.content_hash,  # type: ignore[union-attr]
        observation_id=trace.reward_observation.observation_id,  # type: ignore[union-attr]
        observation_hash=trace.reward_observation.content_hash,  # type: ignore[union-attr]
        evidence_ids=(f"{finding_id}-evidence",),
    )
