from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

import super_scientist.application.transactions.coordinator as coordinator_module
import tests.unit.harness_eval.test_traces as trace_fixtures
from super_scientist.application.harness_eval.extensions import (
    HarnessTraceProposalAdapter,
    RecordHarnessExecutionTraceHandler,
)
from super_scientist.application.transactions.harness_extensions import (
    _trace_hash_bound_evidence,
    _trace_id_only_evidence,
)
from super_scientist.domain.evidence.models import EvidenceRecord
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
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.chain import json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    HarnessTraceRecordMetadata,
    RecordGuidanceEvaluationProtocol,
    RejectionCode,
)
from tests.integration.application.test_cognitive_workspace_exchange import (
    _approval,
    _governed_policy,
    _service_actor,
)
from tests.integration.application.test_harness_eval_extensions import (
    _actor,
    _Capabilities,
    _run,
)
from tests.integration.application.test_workspace_exchange import ExchangeRuntime, _runtime
from tests.unit.harness_eval.test_rewards import assess_reward_validity, reward_hacking_finding
from tests.unit.harness_eval.test_traces import (
    HASH_D,
    NOW,
    available,
    reward_observation,
    valid_trace,
)


def _authority_heads(runtime: ExchangeRuntime) -> tuple[object, ...]:
    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        return (
            repositories.claims.list_heads(),
            repositories.policies.list_all(),
            repositories.harness_integrity_snapshot().heads,
            repositories.progress_integrity_snapshot().heads,
        )


def _retain_trace_prerequisites(
    runtime: ExchangeRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> HarnessExecutionTrace:
    fixture_bytes = b"task-18-owned-trace-evidence"
    fixture_hash = sha256_hex(fixture_bytes)
    for name in ("HASH_A", "HASH_B", "HASH_C", "HASH_D"):
        monkeypatch.setattr(trace_fixtures, name, fixture_hash)
    trace = valid_trace(trace_id="owned-snapshot-trace")
    protocol = trace.observed_binding.guidance_protocol
    assert protocol is not None
    assert runtime.coordinator.submit(
        RecordGuidanceEvaluationProtocol(
            proposal_id="owned-snapshot-guidance",
            idempotency_key="owned-snapshot-guidance",
            proposer=_service_actor(),
            approval=_approval(runtime),
            protocol=protocol,
        )
    ).accepted

    evidence_bytes: dict[str, bytes] = {}
    observation = trace.reward_observation
    assert observation is not None
    context_bytes = canonical_json_bytes(
        {
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "sha256": item.sha256,
                    "content_hash": item.content_hash,
                }
                for item in trace.context_artifacts
            ]
        }
    )
    observation_bytes = canonical_json_bytes(
        observation.model_dump(mode="json", exclude={"content_hash"})
    )
    for record_id, content_hash in _trace_hash_bound_evidence(trace):
        data = (
            fixture_bytes
            if content_hash == fixture_hash
            else context_bytes
            if record_id == trace.observed_binding.context_id
            else observation_bytes
            if record_id == observation.observation_id
            else b""
        )
        assert sha256_hex(data) == content_hash, (record_id, content_hash)
        evidence_bytes[record_id] = data
    for record_id in _trace_id_only_evidence(trace):
        evidence_bytes.setdefault(record_id, fixture_bytes)

    proposals = []
    for index, (record_id, data) in enumerate(sorted(evidence_bytes.items())):
        artifact = runtime.artifact_store.put(data, "application/json")
        proposals.append(
            AddEvidence(
                proposal_id=f"owned-snapshot-evidence-{index:02d}",
                idempotency_key=f"owned-snapshot-evidence-{index:02d}",
                proposer=_service_actor(),
                evidence=EvidenceRecord(
                    evidence_id=record_id,
                    evidence_type="task-18-trace-prerequisite",
                    source_locator=f"fixture:{record_id}",
                    retrieved_at=NOW,
                    artifact=artifact,
                    provenance={"fixture": "task-18-owned-snapshot"},
                    ingestion_actor_id="complete-slice-service",
                ),
            )
        )
    assert all(decision.accepted for decision in runtime.coordinator.submit_batch(tuple(proposals)))
    return trace


def _hostile_trace(trace: HarnessExecutionTrace, attack: str) -> HarnessExecutionTrace:
    if attack in {"log_probabilities", "token_ids", "availability_evidence"}:
        field_name = "token_count" if attack == "availability_evidence" else attack
        value: object = 1 if attack == "availability_evidence" else (1,)
        metadata = AvailableValue.model_construct(
            status=(
                MetadataAvailability.AVAILABLE
                if attack == "availability_evidence"
                else MetadataAvailability.UNAVAILABLE
            ),
            value=value,
            evidence_id=None,
        )
        generation = trace.generation_metadata.model_copy(update={field_name: metadata})
        return trace.model_copy(update={"generation_metadata": generation})
    values = trace.model_dump(mode="python", exclude={"content_hash"})
    values["final_context_hash"] = HASH_D
    return HarnessExecutionTrace.model_construct(
        **values,
        content_hash=trace_hash(values),
    )


@pytest.mark.parametrize(
    "attack",
    ("log_probabilities", "token_ids", "availability_evidence", "context_hash"),
)
def test_trace_handler_fresh_validates_every_nested_hostile_copy(attack: str) -> None:
    trace = valid_trace()
    proposal = HarnessTraceProposalAdapter().from_untrusted_payload(
        trace.model_dump_json(),
        HarnessTraceRecordMetadata(received_at=NOW, source_id="handler-runtime"),
        f"handler-{attack}",
        f"handler-{attack}",
        _actor(),
    )
    hostile = proposal.model_copy(
        update={
            "envelope": proposal.envelope.model_copy(
                update={"trace": _hostile_trace(trace, attack)}
            )
        }
    )
    capabilities = _Capabilities(
        guidance_protocol=trace.observed_binding.guidance_protocol,
        current=True,
    )

    decision = _run(RecordHarnessExecutionTraceHandler(), hostile, capabilities)

    assert decision.reasons[0].code is RejectionCode.UNMATCHED_EVALUATION
    assert capabilities.projected == []
    assert capabilities.trace_repository_reads == 0


def test_trace_handler_still_accepts_a_fresh_valid_trace() -> None:
    trace = valid_trace()
    proposal = HarnessTraceProposalAdapter().from_untrusted_payload(
        trace.model_dump_json(),
        HarnessTraceRecordMetadata(received_at=NOW, source_id="handler-runtime-valid"),
        "handler-valid-trace",
        "handler-valid-trace",
        _actor(),
    )
    capabilities = _Capabilities(
        guidance_protocol=trace.observed_binding.guidance_protocol,
        admitted_trace_proposal=proposal,
        current=True,
    )

    decision = _run(RecordHarnessExecutionTraceHandler(), proposal, capabilities)

    assert decision.accepted
    assert capabilities.projected == [trace]


def test_coordinator_rejects_fabricated_generation_and_context_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _governed_policy()
    runtime = _runtime(tmp_path, "trace-tamper", policy_snapshot=policy)
    try:
        trace = valid_trace()
        guidance_protocol = trace.observed_binding.guidance_protocol
        assert guidance_protocol is not None
        prerequisite = runtime.coordinator.submit(
            RecordGuidanceEvaluationProtocol(
                proposal_id="trace-tamper-guidance-protocol",
                idempotency_key="trace-tamper-guidance-protocol",
                proposer=_service_actor(),
                approval=_approval(runtime),
                protocol=guidance_protocol,
            )
        )
        assert prerequisite.accepted
        fabricated_logprobs = AvailableValue[tuple[Decimal, ...]].model_construct(
            status=MetadataAvailability.UNAVAILABLE,
            value=(Decimal("-0.1"),),
            evidence_id=None,
        )
        fabricated_generation = trace.generation_metadata.model_copy(
            update={"log_probabilities": fabricated_logprobs}
        )
        fabricated_trace = trace.model_copy(update={"generation_metadata": fabricated_generation})
        context_values = trace.model_dump(mode="python", exclude={"content_hash"})
        context_values["final_context_hash"] = HASH_D
        context_trace = HarnessExecutionTrace.model_construct(
            **context_values,
            content_hash=trace_hash(context_values),
        )
        adapter = HarnessTraceProposalAdapter()
        valid_proposal = adapter.from_untrusted_payload(
            trace.model_dump_json(),
            HarnessTraceRecordMetadata(received_at=NOW, source_id="adversarial-runtime"),
            "trace-tamper-template",
            "trace-tamper-template",
            _service_actor(),
        ).model_copy(update={"approval": _approval(runtime)})
        attacks = (
            valid_proposal.model_copy(
                update={
                    "proposal_id": "trace-fabricated-logprobs",
                    "idempotency_key": "trace-fabricated-logprobs",
                    "envelope": valid_proposal.envelope.model_copy(
                        update={"trace": fabricated_trace}
                    ),
                }
            ),
            valid_proposal.model_copy(
                update={
                    "proposal_id": "trace-forged-context-hash",
                    "idempotency_key": "trace-forged-context-hash",
                    "envelope": valid_proposal.envelope.model_copy(update={"trace": context_trace}),
                }
            ),
        )
        before = _authority_heads(runtime)
        with runtime.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            assert (
                repositories.evaluation_extension_integrity_snapshot().harness_execution_traces
                == ()
            )
            before_counts = (
                len(repositories.transactions.list_all()),
                len(repositories.audit.list_all()),
            )

        def reject_capability_reads(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise AssertionError("invalid trace state reached capability construction")

        monkeypatch.setattr(
            coordinator_module,
            "harness_extension_capabilities",
            reject_capability_reads,
        )

        decisions = tuple(runtime.coordinator.submit(proposal) for proposal in attacks)

        assert all(not decision.accepted for decision in decisions)
        assert tuple(decision.reasons[0].code for decision in decisions) == (
            RejectionCode.UNMATCHED_EVALUATION,
            RejectionCode.UNMATCHED_EVALUATION,
        )
        assert _authority_heads(runtime) == before
        with runtime.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            assert (
                repositories.evaluation_extension_integrity_snapshot().harness_execution_traces
                == ()
            )
            assert (
                len(repositories.transactions.list_all()),
                len(repositories.audit.list_all()),
            ) == (before_counts[0] + 2, before_counts[1] + 2)
    finally:
        runtime.engine.dispose()


def test_coordinator_uses_one_owned_trace_snapshot_across_reentrant_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _governed_policy()
    runtime = _runtime(tmp_path, "owned-trace-snapshot", policy_snapshot=policy)
    try:
        trace = _retain_trace_prerequisites(runtime, monkeypatch)
        caller = (
            HarnessTraceProposalAdapter()
            .from_untrusted_payload(
                trace.model_dump_json(),
                HarnessTraceRecordMetadata(received_at=NOW, source_id="owned-snapshot-runtime"),
                "proposal-owned-snapshot-trace",
                "key-owned-snapshot-trace",
                _service_actor(),
            )
            .model_copy(update={"approval": _approval(runtime)})
        )
        expected = caller.model_copy(deep=True)
        capability_proposals: list[object] = []
        real_factory = coordinator_module.harness_extension_capabilities

        def mutate_caller_then_build_capabilities(
            admitted: object,
            *args: object,
            **kwargs: object,
        ) -> object:
            object.__setattr__(caller, "proposal_id", "proposal-mutated-after-admission")
            object.__setattr__(caller, "idempotency_key", "key-mutated-after-admission")
            capability_proposals.append(admitted)
            return real_factory(admitted, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            coordinator_module,
            "harness_extension_capabilities",
            mutate_caller_then_build_capabilities,
        )

        decision = runtime.coordinator.submit(caller)

        assert decision.accepted
        assert decision.proposal_id == "proposal-owned-snapshot-trace"
        assert len(capability_proposals) == 1
        admitted = capability_proposals[0]
        assert admitted is not caller
        assert admitted == expected
        with runtime.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            transaction = repositories.transactions.get_by_idempotency_key(expected.idempotency_key)
            assert transaction is not None
            assert transaction.proposal == expected
            assert transaction.decision.proposal_id == expected.proposal_id
            assert (
                repositories.evaluation_extension_integrity_snapshot().harness_execution_traces
                == (trace,)
            )
            event = repositories.audit.last()
            assert event is not None
            payload = json_compatible_payload(event.payload)
            assert payload["proposal"]["proposal_id"] == expected.proposal_id
            assert payload["decision"]["proposal_id"] == expected.proposal_id
            projection_transaction_id = unit_of_work.connection.execute(
                text(
                    "SELECT transaction_id FROM harness_execution_traces WHERE trace_id = :trace_id"
                ),
                {"trace_id": trace.trace_id},
            ).scalar_one()
            assert projection_transaction_id == expected.proposal_id
        replay = runtime.coordinator.submit(expected.model_copy(deep=True))
        assert replay.accepted and replay.replayed
        assert replay.proposal_id == expected.proposal_id
        assert len(capability_proposals) == 1
    finally:
        runtime.engine.dispose()


@pytest.mark.parametrize(
    ("value", "evidence_id"),
    (
        ((1, 2, 3), None),
        ((Decimal("-0.1"), Decimal("-0.2")), None),
    ),
)
def test_fabricated_token_and_logprob_metadata_cannot_claim_unavailability(
    value: object,
    evidence_id: str | None,
) -> None:
    with pytest.raises(ValidationError, match="metadata"):
        AvailableValue[tuple[object, ...]](
            status=MetadataAvailability.UNAVAILABLE,
            value=value,
            evidence_id=evidence_id,
        )


def test_generation_metadata_rejects_fabricated_nested_values() -> None:
    valid = valid_trace().generation_metadata
    payload = valid.model_dump(mode="python")
    payload["token_ids"] = {
        "status": MetadataAvailability.UNAVAILABLE,
        "value": (999,),
        "evidence_id": None,
    }

    with pytest.raises(ValidationError, match="metadata"):
        GenerationMetadata.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "forbidden_field",
    ("protected_answer", "evaluator_payload", "raw_provider_log", "chain_of_thought"),
)
def test_trace_schema_cannot_leak_protected_answers_or_evaluator_state(
    forbidden_field: str,
) -> None:
    payload = valid_trace().model_dump(mode="python")
    payload[forbidden_field] = "literal-held-out-answer"

    with pytest.raises(ValidationError) as caught:
        HarnessExecutionTrace.model_validate(payload, strict=True)

    assert "literal-held-out-answer" not in str(caught.value)
    assert forbidden_field not in HarnessExecutionTrace.model_fields


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
    tuple(RewardHackingFamily),
)
def test_reward_spoof_families_never_produce_promotion_evidence(
    family: RewardHackingFamily,
) -> None:
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
    trace: HarnessExecutionTrace,
    verifier_succeeded: bool,
    reason: RewardInvalidationReason,
) -> None:
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        findings=(),
        verifier_succeeded=verifier_succeeded,
    )

    assert assessment.status is RewardValidityStatus.INVALID
    assert reason in assessment.reasons
    assert valid_reward_evidence((assessment,)) == ()


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
