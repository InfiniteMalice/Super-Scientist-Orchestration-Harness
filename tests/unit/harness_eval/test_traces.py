from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

import super_scientist.domain.harness_eval as harness_eval
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.harness_eval.guidance import GuidanceCondition
from super_scientist.domain.harness_eval.matrix import HarnessIdentity, ModelIdentity
from super_scientist.domain.harness_eval.traces import (
    AvailableValue,
    CaptureRewardValidityStatus,
    ContextTransformation,
    ContextTransformationKind,
    EnvironmentEvent,
    EnvironmentEventKind,
    ExecutionStatus,
    GenerationMetadata,
    GenerationStopReason,
    HarnessExecutionTrace,
    MetadataAvailability,
    ObservableArtifactRef,
    RewardObservation,
    ToolObservation,
    ToolObservationStatus,
    TraceBinding,
    TraceBindingMismatch,
    TraceFreshnessStatus,
    artifact_collection_hash,
    context_transformation_hash,
    generation_metadata_hash,
    trace_binding_hash,
    trace_freshness,
    trace_hash,
)
from super_scientist.domain.improvement.models import ResourceUsage
from tests.unit.harness_eval.test_guidance import _protocol as guidance_protocol
from tests.unit.harness_eval.test_model_harness_matrix import _protocol as matrix_protocol

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def test_available_value_truthfully_couples_status_value_and_evidence() -> None:
    available = AvailableValue[int](
        status=MetadataAvailability.AVAILABLE,
        value=0,
        evidence_id="counter-evidence",
    )
    assert available.value == 0

    for payload in (
        {
            "status": MetadataAvailability.UNAVAILABLE,
            "value": 1,
            "evidence_id": None,
        },
        {
            "status": MetadataAvailability.NOT_APPLICABLE,
            "value": None,
            "evidence_id": "fabricated-evidence",
        },
        {
            "status": MetadataAvailability.AVAILABLE,
            "value": None,
            "evidence_id": "partial-evidence",
        },
    ):
        with pytest.raises(ValidationError, match="metadata"):
            AvailableValue[int].model_validate(payload)


def test_unavailable_log_probabilities_cannot_carry_values() -> None:
    with pytest.raises(ValidationError):
        AvailableValue[tuple[Decimal, ...]](
            status=MetadataAvailability.UNAVAILABLE,
            value=(Decimal("0.5"),),
            evidence_id=None,
        )


def test_trace_identifiers_cannot_encode_reversible_locations() -> None:
    with pytest.raises(ValidationError):
        AvailableValue[str](
            status=MetadataAvailability.AVAILABLE,
            value=HASH_A,
            evidence_id="protected://store/answer",
        )


def test_observable_artifact_conversion_discards_reversible_location() -> None:
    artifact = ArtifactRef(
        sha256=HASH_A,
        size_bytes=12,
        media_type="Application/JSON",
        relative_path="protected/store/answer.json",
    )

    observable = ObservableArtifactRef.from_artifact_ref("public-context", artifact)

    assert observable.sha256 == HASH_A
    assert observable.media_type == "application/json"
    serialized = observable.model_dump_json()
    assert "relative_path" not in serialized
    assert "protected/store" not in serialized


def test_context_transformations_require_an_ordered_contiguous_hash_chain() -> None:
    trace = valid_trace(with_transformations=True)
    first, second = trace.context_transformations

    assert first.kind is ContextTransformationKind.CONTEXT_COMPACTION
    assert second.kind is ContextTransformationKind.RESERIALIZATION
    assert first.output_context_hash == second.input_context_hash
    assert trace.final_context_hash == second.output_context_hash

    broken = second.model_dump(mode="python") | {"input_context_hash": HASH_D}
    broken["content_hash"] = context_transformation_hash(broken)
    payload = trace.model_dump(mode="python") | {
        "context_transformations": (first, broken),
    }
    payload["content_hash"] = trace_hash(payload)
    with pytest.raises(ValidationError, match="context transformation chain"):
        HarnessExecutionTrace.model_validate(payload)


def test_tool_and_environment_observations_are_typed_hash_only_surfaces() -> None:
    trace = valid_trace()
    tool_fields = set(ToolObservation.model_fields)
    event_fields = set(EnvironmentEvent.model_fields)

    assert not {
        "command",
        "arguments",
        "raw_request",
        "raw_response",
        "provider_payload",
        "protected_answer",
        "exception_text",
    } & (tool_fields | event_fields)
    dumped = trace.model_dump_json()
    assert "command" not in dumped
    assert "protected://" not in dumped
    assert "literal-held-out-answer" not in dumped


def test_succeeded_tool_requires_observed_response_evidence() -> None:
    with pytest.raises(ValidationError, match="successful tool observation"):
        ToolObservation.build(
            sequence=0,
            tool_id="fixture",
            tool_version="v1",
            request_hash=HASH_A,
            response_hash=unavailable(),
            status=ToolObservationStatus.SUCCEEDED,
            evidence_id="tool-call-1",
        )


def test_tool_response_hash_evidence_must_match_the_observation() -> None:
    with pytest.raises(ValidationError, match="response hash evidence"):
        ToolObservation.build(
            sequence=0,
            tool_id="fixture",
            tool_version="v1",
            request_hash=HASH_A,
            response_hash=available(HASH_B, "different-evidence"),
            status=ToolObservationStatus.SUCCEEDED,
            evidence_id="tool-call-1",
        )


def test_environment_crash_and_completion_state_cannot_contradict_each_other() -> None:
    with pytest.raises(ValidationError, match="crash event"):
        valid_trace(
            execution_status=ExecutionStatus.COMPLETED,
            event_kinds=(EnvironmentEventKind.STARTED, EnvironmentEventKind.CRASHED),
        )


def test_environment_history_rejects_repeated_terminal_events() -> None:
    with pytest.raises(ValidationError, match="exactly one terminal"):
        valid_trace(
            event_kinds=(
                EnvironmentEventKind.STARTED,
                EnvironmentEventKind.COMPLETED,
                EnvironmentEventKind.COMPLETED,
            )
        )


def test_trace_rejects_duplicate_artifact_identifiers_even_with_distinct_hashes() -> None:
    first = ObservableArtifactRef.build(
        artifact_id="same-artifact",
        sha256=HASH_A,
        size_bytes=1,
        media_type="application/json",
    )
    second = ObservableArtifactRef.build(
        artifact_id="same-artifact",
        sha256=HASH_B,
        size_bytes=2,
        media_type="application/json",
    )
    trace = valid_trace()
    payload = trace.model_dump(mode="python") | {
        "output_artifacts": (first, second),
    }

    with pytest.raises(ValidationError, match="artifact identifiers"):
        HarnessExecutionTrace.build(
            **{key: value for key, value in payload.items() if key != "content_hash"}
        )


def test_trace_hash_binds_every_observable_hash_family() -> None:
    trace = valid_trace()
    baseline = trace.content_hash

    mutations = (
        {"tool_observations_hash": HASH_D},
        {"environment_events_hash": HASH_D},
        {"output_artifacts_hash": HASH_D},
        {"resource_usage_hash": HASH_D},
        {"provenance_hash": HASH_D},
        {
            "reward_observation_hash": AvailableValue[str](
                status=MetadataAvailability.AVAILABLE,
                value=HASH_D,
                evidence_id=trace.reward_observation.observation_id,
            )
        },
    )
    for mutation in mutations:
        with pytest.raises(ValidationError):
            HarnessExecutionTrace.model_validate(
                trace.model_dump(mode="python") | mutation
            )
    assert trace.content_hash == baseline


def test_direct_parse_rejects_rehashed_contradictory_trace_state() -> None:
    trace = valid_trace()
    payload = trace.model_dump(mode="python") | {"final_context_hash": HASH_D}
    observed = trace.observed_binding.model_dump(mode="python") | {"context_hash": HASH_D}
    observed["content_hash"] = trace_binding_hash(observed)
    payload["observed_binding"] = observed
    payload["content_hash"] = trace_hash(payload)

    with pytest.raises(ValidationError, match="final context hash"):
        HarnessExecutionTrace.model_validate(payload)


def test_trace_freshness_is_exact_hash_identity_not_time() -> None:
    current = valid_trace()
    later = valid_trace(observed_at=datetime(2099, 1, 1, tzinfo=UTC))

    assert trace_freshness(current).status is TraceFreshnessStatus.CURRENT
    assert trace_freshness(later).status is TraceFreshnessStatus.CURRENT

    stale = valid_trace(observed_binding_updates={"harness_hash": HASH_D})
    freshness = trace_freshness(stale)
    assert freshness.status is TraceFreshnessStatus.STALE
    assert freshness.mismatches == (TraceBindingMismatch.HARNESS,)


def test_freshness_compares_task_model_procedure_environment_context_validator_artifacts() -> None:
    expected = {
        "task_input_hash": TraceBindingMismatch.TASK,
        "model_hash": TraceBindingMismatch.MODEL,
        "procedure_hash": TraceBindingMismatch.PROCEDURE,
        "environment_hash": TraceBindingMismatch.ENVIRONMENT,
        "context_hash": TraceBindingMismatch.CONTEXT,
        "validator_hash": TraceBindingMismatch.VALIDATOR,
        "artifact_hashes": TraceBindingMismatch.ARTIFACTS,
    }
    for field, mismatch in expected.items():
        value: object = (HASH_D,) if field == "artifact_hashes" else HASH_D
        stale = valid_trace(observed_binding_updates={field: value})
        assert mismatch in trace_freshness(stale).mismatches


def test_capture_reward_validity_is_diagnostic_and_bound_into_trace() -> None:
    invalid_at_capture = valid_trace(
        capture_status=CaptureRewardValidityStatus.INVALID,
    )
    valid_at_capture = valid_trace(
        capture_status=CaptureRewardValidityStatus.VALID,
    )

    assert invalid_at_capture.capture_reward_validity.value is CaptureRewardValidityStatus.INVALID
    assert invalid_at_capture.content_hash != valid_at_capture.content_hash
    assert "reward_assessment" not in HarnessExecutionTrace.model_fields


def test_categorical_reward_must_be_bounded_nonblank_metadata() -> None:
    with pytest.raises(ValidationError, match="categorical reward"):
        reward_observation(value="   ")


def test_generation_metadata_rejects_unbounded_or_synthesized_evidence_shapes() -> None:
    metadata = generation_metadata()
    assert metadata.log_probabilities.status is MetadataAvailability.UNAVAILABLE
    assert metadata.token_ids.status is MetadataAvailability.UNAVAILABLE

    payload = metadata.model_dump(mode="python") | {
        "provider_payload": {"reasoning": "hidden scratchpad"}
    }
    with pytest.raises(ValidationError):
        GenerationMetadata.model_validate(payload)


def test_trace_can_truthfully_record_an_unavailable_reward_observation() -> None:
    trace = valid_trace(include_reward=False)

    assert trace.reward_observation is None
    assert trace.reward_observation_hash.status is MetadataAvailability.UNAVAILABLE
    assert trace.reward_observation_hash.value is None
    assert trace.reward_observation_hash.evidence_id is None


def test_absent_reward_rejects_rehashed_not_applicable_observation_hash() -> None:
    trace = valid_trace(include_reward=False)
    payload = trace.model_dump(mode="python") | {
        "reward_observation_hash": not_applicable(),
    }
    payload["content_hash"] = trace_hash(payload)

    with pytest.raises(ValidationError, match="UNAVAILABLE"):
        HarnessExecutionTrace.model_validate(payload)


def test_available_generation_token_count_is_strict_nonnegative_without_token_ids() -> None:
    metadata = generation_metadata()
    payload = metadata.model_dump(mode="python") | {
        "token_count": available(-1, "usage-meter"),
    }
    payload["content_hash"] = generation_metadata_hash(payload)

    with pytest.raises(ValidationError, match="non-negative"):
        GenerationMetadata.model_validate(payload)


def test_rehashed_trace_rejects_expected_artifact_outside_authorized_set() -> None:
    trace = valid_trace()
    expected_payload = {
        key: value
        for key, value in trace.expected_binding.model_dump(mode="python").items()
        if key != "content_hash"
    } | {"artifact_ids": ("rogue-context",)}
    rogue_expected = TraceBinding.build(**expected_payload)
    trace_payload = {
        key: value
        for key, value in trace.model_dump(mode="python").items()
        if key != "content_hash"
    } | {"expected_binding": rogue_expected}
    trace_payload["content_hash"] = trace_hash(trace_payload)

    with pytest.raises(ValidationError, match="authorized artifact identities"):
        HarnessExecutionTrace.model_validate(trace_payload)


def test_trace_public_api_is_exported_from_harness_eval_package() -> None:
    assert harness_eval.HarnessExecutionTrace is HarnessExecutionTrace
    assert harness_eval.AvailableValue is AvailableValue
    assert harness_eval.trace_freshness is trace_freshness


def test_trace_binding_consumes_exact_guidance_and_matrix_protocol_contracts() -> None:
    guidance = guidance_protocol()
    guidance_artifact = ObservableArtifactRef.build(
        artifact_id="artifact-a",
        sha256=HASH_A,
        size_bytes=1,
        media_type="application/json",
    )
    guidance_binding = TraceBinding.from_guidance_protocol(
        guidance,
        condition=GuidanceCondition.FULL_PROCEDURE_GUIDANCE,
        artifacts=(guidance_artifact,),
        model_hash=HASH_B,
        harness_hash=HASH_C,
        procedure_id="procedure-1",
        procedure_version="v1",
        procedure_hash=HASH_A,
        environment_id="environment",
        environment_version="v1",
        environment_hash=HASH_B,
        context_hash=HASH_C,
        validator_hash=HASH_C,
        checker_hash=HASH_D,
    )
    assert guidance_binding.protocol_hash == guidance.content_hash
    assert guidance_binding.task_id == guidance.task_id
    assert guidance_binding.checker_id == guidance.checker_id
    assert guidance_binding.guidance_condition is GuidanceCondition.FULL_PROCEDURE_GUIDANCE
    assert guidance_binding.artifact_ids == guidance.artifact_ids
    assert guidance_binding.artifact_hashes == (HASH_A,)
    rehashed = guidance_binding.model_dump(mode="python") | {
        "authorized_artifact_ids": ("rogue-artifact",),
    }
    rehashed["content_hash"] = trace_binding_hash(rehashed)
    with pytest.raises(ValidationError, match="authorized guidance artifacts"):
        TraceBinding.model_validate(rehashed)

    distractor = ObservableArtifactRef.build(
        artifact_id="distractor-a",
        sha256=HASH_B,
        size_bytes=1,
        media_type="application/json",
    )
    distractor_binding = TraceBinding.from_guidance_protocol(
        guidance,
        condition=GuidanceCondition.OBJECTIVE_DATA_WITH_DISTRACTORS,
        artifacts=(guidance_artifact, distractor),
        model_hash=HASH_B,
        harness_hash=HASH_C,
        procedure_id="procedure-1",
        procedure_version="v1",
        procedure_hash=HASH_A,
        environment_id="environment",
        environment_version="v1",
        environment_hash=HASH_B,
        context_hash=HASH_C,
        validator_hash=HASH_C,
        checker_hash=HASH_D,
    )
    assert distractor_binding.artifact_ids == ("artifact-a", "distractor-a")

    rogue = ObservableArtifactRef.build(
        artifact_id="rogue-artifact",
        sha256=HASH_A,
        size_bytes=1,
        media_type="application/json",
    )
    with pytest.raises(ValueError, match="authorized guidance artifacts"):
        TraceBinding.from_guidance_protocol(
            guidance,
            condition=GuidanceCondition.FULL_PROCEDURE_GUIDANCE,
            artifacts=(rogue,),
            model_hash=HASH_B,
            harness_hash=HASH_C,
            procedure_id="procedure-1",
            procedure_version="v1",
            procedure_hash=HASH_A,
            environment_id="environment",
            environment_version="v1",
            environment_hash=HASH_B,
            context_hash=HASH_C,
            validator_hash=HASH_C,
            checker_hash=HASH_D,
        )

    matrix = matrix_protocol()
    coordinate = matrix.expected_grid[0]
    matrix_artifact = ObservableArtifactRef.build(
        artifact_id="artifact-a",
        sha256=HASH_A,
        size_bytes=1,
        media_type="application/json",
    )
    matrix_binding = TraceBinding.from_model_harness_protocol(
        matrix,
        coordinate,
        artifacts=(matrix_artifact,),
        model_hash=HASH_B,
        harness_hash=HASH_C,
        procedure_id="procedure-1",
        procedure_version="v1",
        procedure_hash=HASH_A,
        environment_id="environment",
        environment_version="v1",
        environment_hash=HASH_B,
        context_hash=HASH_C,
        validator_hash=HASH_C,
        checker_hash=HASH_D,
    )
    assert matrix_binding.protocol_hash == matrix.content_hash
    assert matrix_binding.task_id == matrix.task_set_id
    assert matrix_binding.model == coordinate.model
    assert matrix_binding.partition == coordinate.partition
    assert matrix_binding.artifact_ids == matrix.artifact_ids

    with pytest.raises(ValueError, match="authorized matrix artifacts"):
        TraceBinding.from_model_harness_protocol(
            matrix,
            coordinate,
            artifacts=(
                ObservableArtifactRef.build(
                    artifact_id="rogue-artifact",
                    sha256=HASH_A,
                    size_bytes=1,
                    media_type="application/json",
                ),
            ),
            model_hash=HASH_B,
            harness_hash=HASH_C,
            procedure_id="procedure-1",
            procedure_version="v1",
            procedure_hash=HASH_A,
            environment_id="environment",
            environment_version="v1",
            environment_hash=HASH_B,
            context_hash=HASH_C,
            validator_hash=HASH_C,
            checker_hash=HASH_D,
        )


def valid_trace(
    *,
    execution_status: ExecutionStatus = ExecutionStatus.COMPLETED,
    with_transformations: bool = False,
    observed_binding_updates: dict[str, object] | None = None,
    artifact_integrity: AvailableValue[bool] | None = None,
    protected_boundary_crossed: AvailableValue[bool] | None = None,
    evaluator_succeeded: AvailableValue[bool] | None = None,
    capture_status: CaptureRewardValidityStatus = CaptureRewardValidityStatus.INCONCLUSIVE,
    tool_status: ToolObservationStatus = ToolObservationStatus.SUCCEEDED,
    event_kinds: tuple[EnvironmentEventKind, ...] | None = None,
    observation: RewardObservation | None = None,
    observed_at: datetime = NOW,
    include_reward: bool = True,
) -> HarnessExecutionTrace:
    context_artifacts = (
        ObservableArtifactRef.build(
            artifact_id="public-context",
            sha256=HASH_A,
            size_bytes=12,
            media_type="application/json",
        ),
    )
    initial_context_hash = artifact_collection_hash(context_artifacts)
    transformations: tuple[ContextTransformation, ...] = ()
    final_context_hash = initial_context_hash
    if with_transformations:
        transformations = (
            ContextTransformation.build(
                sequence=0,
                kind=ContextTransformationKind.CONTEXT_COMPACTION,
                input_context_hash=initial_context_hash,
                output_context_hash=HASH_B,
                evidence_id="compaction-evidence",
            ),
            ContextTransformation.build(
                sequence=1,
                kind=ContextTransformationKind.RESERIALIZATION,
                input_context_hash=HASH_B,
                output_context_hash=HASH_C,
                evidence_id="reserialization-evidence",
            ),
        )
        final_context_hash = HASH_C
    base_binding = binding(
        context_hash=final_context_hash,
        artifact_hashes=(HASH_A,),
    )
    expected_values = base_binding.model_dump(mode="python")
    expected_values.pop("content_hash")
    if observed_binding_updates:
        expected_values.update(observed_binding_updates)
    expected_binding = TraceBinding.build(**expected_values)
    observed_binding = base_binding
    tool_observations = (
        ToolObservation.build(
            sequence=0,
            tool_id="fixture",
            tool_version="v1",
            request_hash=HASH_A,
            response_hash=(
                available(HASH_B, "tool-call-1")
                if tool_status is ToolObservationStatus.SUCCEEDED
                else unavailable()
            ),
            status=tool_status,
            evidence_id="tool-call-1",
        ),
    )
    if event_kinds is None:
        event_kinds = {
            ExecutionStatus.COMPLETED: (
                EnvironmentEventKind.STARTED,
                EnvironmentEventKind.COMPLETED,
            ),
            ExecutionStatus.INCOMPLETE: (EnvironmentEventKind.STARTED,),
            ExecutionStatus.CRASHED: (
                EnvironmentEventKind.STARTED,
                EnvironmentEventKind.CRASHED,
            ),
        }[execution_status]
    events = tuple(
        EnvironmentEvent.build(
            sequence=index,
            environment_id="environment",
            environment_version="v1",
            kind=kind,
            evidence_id=f"environment-event-{index}",
        )
        for index, kind in enumerate(event_kinds)
    )
    output_artifacts = (
        ObservableArtifactRef.build(
            artifact_id="candidate-output",
            sha256=HASH_B,
            size_bytes=8,
            media_type="application/json",
        ),
    )
    reward = reward_observation() if observation is None else observation
    embedded_reward = reward if include_reward else None
    embedded_reward_hash = (
        available(reward.content_hash, reward.observation_id)
        if include_reward
        else unavailable()
    )
    capture_validity = (
        available(capture_status, "capture-validity-evidence")
        if include_reward
        else not_applicable()
    )
    return HarnessExecutionTrace.build(
        trace_id="trace-1",
        expected_binding=expected_binding,
        observed_binding=observed_binding,
        context_artifacts=context_artifacts,
        initial_context_hash=initial_context_hash,
        context_transformations=transformations,
        final_context_hash=final_context_hash,
        tool_observations=tool_observations,
        environment_events=events,
        output_artifacts=output_artifacts,
        output_hash=artifact_collection_hash(output_artifacts),
        checker_result_id="checker-result-1",
        checker_result_hash=HASH_C,
        reward_observation=embedded_reward,
        reward_observation_hash=embedded_reward_hash,
        capture_reward_validity=capture_validity,
        generation_metadata=generation_metadata(),
        resource_usage=resource_usage(),
        execution_status=execution_status,
        artifact_integrity=artifact_integrity or available(True, "artifact-integrity"),
        protected_boundary_crossed=(
            protected_boundary_crossed or available(False, "boundary-monitor")
        ),
        evaluator_succeeded=evaluator_succeeded or available(True, "evaluator-run"),
        provenance_evidence_ids=("fixture-run", "protocol-receipt"),
        observed_at=observed_at,
    )


def binding(
    *,
    context_hash: str,
    artifact_hashes: tuple[str, ...],
    artifact_ids: tuple[str, ...] = ("public-context",),
) -> TraceBinding:
    return TraceBinding.build(
        protocol_id="guidance-protocol",
        protocol_version=1,
        protocol_hash=HASH_A,
        guidance_protocol=None,
        model_harness_protocol=None,
        guidance_condition=None,
        task_id="task-1",
        task_input_hash=HASH_A,
        partition=None,
        model=ModelIdentity(model_id="model-1", model_version="v1"),
        model_hash=HASH_B,
        harness=HarnessIdentity(harness_id="harness-1", harness_version="v1"),
        harness_hash=HASH_C,
        procedure_id="procedure-1",
        procedure_version="v1",
        procedure_hash=HASH_A,
        environment_id="environment",
        environment_version="v1",
        environment_hash=HASH_B,
        context_hash=context_hash,
        validator_id="validator",
        validator_version="v1",
        validator_hash=HASH_C,
        checker_id="checker",
        checker_version="v1",
        checker_hash=HASH_D,
        authorized_artifact_ids=artifact_ids,
        artifact_ids=artifact_ids,
        artifact_hashes=artifact_hashes,
        output_schema_hash=HASH_D,
    )


def reward_observation(
    *,
    value: Decimal | str | None = Decimal("0.9"),
    verifier_id: str = "validator",
    verifier_version: str = "v1",
    checker_id: str = "checker",
    checker_version: str = "v1",
    evaluator_id: str = "evaluator",
    evaluator_version: str = "v1",
) -> RewardObservation:
    return RewardObservation.build(
        observation_id="reward-observation-1",
        task_id="task-1",
        task_input_hash=HASH_A,
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        checker_id=checker_id,
        checker_version=checker_version,
        checker_result_id="checker-result-1",
        checker_result_hash=HASH_C,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        value=value,
        evidence_id="reward-evidence" if value is not None else None,
        observed_at=NOW,
    )


def generation_metadata() -> GenerationMetadata:
    return GenerationMetadata.build(
        token_ids=unavailable(),
        token_count=available(17, "usage-meter"),
        log_probabilities=unavailable(),
        sampling_parameters_hash=available(HASH_A, "request-envelope"),
        stop_reason=available(GenerationStopReason.COMPLETED, "response-envelope"),
        provider_request_id=unavailable(),
    )


def resource_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.1,
        compute_units=1.0,
        tokens=17,
        elapsed_seconds=0.5,
        tool_calls=1,
        human_interventions=0,
    )


def available[ValueT](value: ValueT, evidence_id: str) -> AvailableValue[ValueT]:
    return AvailableValue[ValueT](
        status=MetadataAvailability.AVAILABLE,
        value=value,
        evidence_id=evidence_id,
    )


def unavailable[ValueT]() -> AvailableValue[ValueT]:
    return AvailableValue[ValueT](
        status=MetadataAvailability.UNAVAILABLE,
        value=None,
        evidence_id=None,
    )


def not_applicable[ValueT]() -> AvailableValue[ValueT]:
    return AvailableValue[ValueT](
        status=MetadataAvailability.NOT_APPLICABLE,
        value=None,
        evidence_id=None,
    )
