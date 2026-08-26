from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from super_scientist.domain.harness_eval.rewards import (
    RewardHackingFamily,
    RewardInvalidationReason,
    RewardValidityStatus,
    valid_reward_evidence,
)
from super_scientist.domain.harness_eval.traces import (
    AvailableValue,
    EnvironmentEventKind,
    GenerationMetadata,
    HarnessExecutionTrace,
    MetadataAvailability,
    RewardObservation,
    ToolObservationStatus,
    trace_hash,
)
from tests.integration.application.test_transaction_coordinator import Runtime
from tests.unit.harness_eval.test_rewards import assess_reward_validity, reward_hacking_finding
from tests.unit.harness_eval.test_traces import (
    HASH_D,
    available,
    reward_observation,
    valid_trace,
)

pytest_plugins = ("tests.integration.application.test_transaction_coordinator",)


def _authority_heads(runtime: Runtime) -> tuple[object, ...]:
    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        return (
            repositories.claims.list_heads(),
            repositories.policies.list_all(),
            repositories.harness_integrity_snapshot().heads,
            repositories.progress_integrity_snapshot().heads,
        )


def test_context_hash_tampering_fails_closed_without_head_changes(runtime: Runtime) -> None:
    before = _authority_heads(runtime)
    trace = valid_trace()
    payload = trace.model_dump(mode="python")
    payload["final_context_hash"] = HASH_D
    payload["content_hash"] = trace_hash(payload)

    with pytest.raises(ValidationError, match="context"):
        HarnessExecutionTrace.model_validate(payload, strict=True)

    assert _authority_heads(runtime) == before


@pytest.mark.parametrize(
    ("value", "evidence_id"),
    (
        ((1, 2, 3), None),
        ((Decimal("-0.1"), Decimal("-0.2")), None),
    ),
)
def test_fabricated_token_and_logprob_metadata_cannot_claim_unavailability(
    runtime: Runtime,
    value: object,
    evidence_id: str | None,
) -> None:
    before = _authority_heads(runtime)

    with pytest.raises(ValidationError, match="metadata"):
        AvailableValue[tuple[object, ...]](
            status=MetadataAvailability.UNAVAILABLE,
            value=value,
            evidence_id=evidence_id,
        )

    assert _authority_heads(runtime) == before


def test_generation_metadata_rejects_fabricated_nested_values(runtime: Runtime) -> None:
    before = _authority_heads(runtime)
    valid = valid_trace().generation_metadata
    payload = valid.model_dump(mode="python")
    payload["token_ids"] = {
        "status": MetadataAvailability.UNAVAILABLE,
        "value": (999,),
        "evidence_id": None,
    }

    with pytest.raises(ValidationError, match="metadata"):
        GenerationMetadata.model_validate(payload, strict=True)

    assert _authority_heads(runtime) == before


@pytest.mark.parametrize(
    "forbidden_field",
    ("protected_answer", "evaluator_payload", "raw_provider_log", "chain_of_thought"),
)
def test_trace_schema_cannot_leak_protected_answers_or_evaluator_state(
    runtime: Runtime,
    forbidden_field: str,
) -> None:
    before = _authority_heads(runtime)
    payload = valid_trace().model_dump(mode="python")
    payload[forbidden_field] = "literal-held-out-answer"

    with pytest.raises(ValidationError) as caught:
        HarnessExecutionTrace.model_validate(payload, strict=True)

    assert "literal-held-out-answer" not in str(caught.value)
    assert forbidden_field not in HarnessExecutionTrace.model_fields
    assert _authority_heads(runtime) == before


def test_reward_observation_cannot_embed_a_reward_channel_or_answer() -> None:
    payload = reward_observation().model_dump(mode="python")
    payload["protected_answer"] = "literal-held-out-answer"
    payload["reward_channel"] = "attacker-channel"

    with pytest.raises(ValidationError) as caught:
        RewardObservation.model_validate(payload, strict=True)

    assert "literal-held-out-answer" not in str(caught.value)
    assert not {"protected_answer", "reward_channel"} & set(RewardObservation.model_fields)


@pytest.mark.parametrize(
    "family",
    (
        RewardHackingFamily.REWARD_CHANNEL_MANIPULATION,
        RewardHackingFamily.PROXY_GAMING,
        RewardHackingFamily.METRIC_CHERRY_PICKING,
        RewardHackingFamily.PREMATURE_TERMINATION,
        RewardHackingFamily.RESOURCE_ACCOUNTING_EVASION,
        RewardHackingFamily.DISTRIBUTION_OR_PARTITION_CONTAMINATION,
    ),
)
def test_reward_spoof_families_never_produce_promotion_evidence(
    runtime: Runtime,
    family: RewardHackingFamily,
) -> None:
    before = _authority_heads(runtime)
    observation = reward_observation(value=Decimal("1000"))
    trace = valid_trace(observation=observation)
    assessment = assess_reward_validity(
        observation,
        trace,
        findings=(reward_hacking_finding(trace, family=family),),
        verifier_succeeded=True,
    )

    assert assessment.status is RewardValidityStatus.INVALID
    assert RewardInvalidationReason.REWARD_HACKING_FINDING in assessment.reasons
    assert valid_reward_evidence((assessment,)) == ()
    assert _authority_heads(runtime) == before


@pytest.mark.parametrize(
    ("trace", "verifier_succeeded", "reason"),
    (
        (
            valid_trace(observed_binding_updates={"environment_hash": HASH_D}),
            True,
            RewardInvalidationReason.TASK_RUNTIME_MISMATCH,
        ),
        (
            valid_trace(),
            False,
            RewardInvalidationReason.VERIFIER_FAILURE,
        ),
        (
            valid_trace(tool_status=ToolObservationStatus.FAILED),
            True,
            RewardInvalidationReason.INCOMPLETE_EXECUTION,
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
    ),
)
def test_environment_verifier_and_runtime_spoofs_fail_closed(
    runtime: Runtime,
    trace: HarnessExecutionTrace,
    verifier_succeeded: bool,
    reason: RewardInvalidationReason,
) -> None:
    before = _authority_heads(runtime)

    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=verifier_succeeded,
    )

    assert assessment.status is RewardValidityStatus.INVALID
    assert reason in assessment.reasons
    assert valid_reward_evidence((assessment,)) == ()
    assert _authority_heads(runtime) == before


def test_trace_log_surfaces_are_hash_only_and_have_no_provider_escape() -> None:
    trace = valid_trace()
    forbidden = {
        "command",
        "arguments",
        "raw_request",
        "raw_response",
        "provider_payload",
        "protected_answer",
        "exception_text",
        "logprobs_raw",
    }
    nested_types = {
        type(trace.tool_observations[0]),
        type(trace.environment_events[0]),
        type(trace.generation_metadata),
    }

    assert all(not forbidden & set(model_type.model_fields) for model_type in nested_types)
    dumped = trace.model_dump_json()
    assert "protected://" not in dumped
    assert "literal-held-out-answer" not in dumped
