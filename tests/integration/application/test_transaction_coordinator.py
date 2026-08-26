from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import Connection, Engine

from super_scientist.application.kernel_service import KernelService
from super_scientist.application.transactions import coordinator as coordinator_module
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.transactions.router import ProposalRouter
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    Approval,
    BindCompiledProgressPlan,
    HarnessExecutionTraceEnvelope,
    HarnessTraceRecordMetadata,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RecordDiversityAssessment,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordMethodDirectionOutcome,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordProcedureCompilation,
    RecordRewardAssessment,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from tests.unit.harness_eval.test_rewards import assess_reward_validity
from tests.unit.harness_eval.test_traces import valid_trace

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

NEW_COGNITIVE_PROPOSAL_CLASSES = (
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordDiversityAssessment,
    RecordCollaborationSession,
    AppendPeerRequest,
    AppendPeerContribution,
    AppendTopologyEvent,
    RecordCollaborationTermination,
    RecordProcedureCompilation,
    RecordMethodDirectionOutcome,
    BindCompiledProgressPlan,
    RecordGuidanceEvaluationProtocol,
    AppendGuidanceEvaluationCell,
    RecordModelHarnessProtocol,
    AppendModelHarnessCell,
    RecordModelHarnessAnalysis,
    RecordHarnessExecutionTrace,
    RecordRewardAssessment,
)
NEW_COGNITIVE_PROPOSAL_TYPES = tuple(
    proposal_class.model_fields["proposal_type"].default
    for proposal_class in NEW_COGNITIVE_PROPOSAL_CLASSES
)
COGNITIVE_CAPABILITY_GROUPS = (
    (
        "cognition_capabilities",
        (RecordCapabilityProfile, RecordCohortPlan, RecordDiversityAssessment),
    ),
    (
        "collaboration_capabilities",
        (
            RecordCollaborationSession,
            AppendPeerRequest,
            AppendPeerContribution,
            AppendTopologyEvent,
            RecordCollaborationTermination,
        ),
    ),
    (
        "procedure_capabilities",
        (RecordProcedureCompilation, RecordMethodDirectionOutcome, BindCompiledProgressPlan),
    ),
    (
        "harness_extension_capabilities",
        (
            RecordGuidanceEvaluationProtocol,
            AppendGuidanceEvaluationCell,
            RecordModelHarnessProtocol,
            AppendModelHarnessCell,
            RecordModelHarnessAnalysis,
            RecordHarnessExecutionTrace,
            RecordRewardAssessment,
        ),
    ),
)

HOSTILE_GOVERNED_REJECTION_CASES = (
    *(
        (proposal_type, RejectionCode.DERIVATION_MISMATCH)
        for proposal_type in (
            RecordCapabilityProfile,
            RecordCohortPlan,
            RecordDiversityAssessment,
            RecordCollaborationSession,
            AppendPeerRequest,
            AppendPeerContribution,
            AppendTopologyEvent,
            RecordCollaborationTermination,
        )
    ),
    *(
        (proposal_type, RejectionCode.INVALID_PROCEDURE)
        for proposal_type in (
            RecordProcedureCompilation,
            RecordMethodDirectionOutcome,
            BindCompiledProgressPlan,
        )
    ),
    *(
        (proposal_type, RejectionCode.UNMATCHED_EVALUATION)
        for proposal_type in (
            RecordGuidanceEvaluationProtocol,
            AppendGuidanceEvaluationCell,
            RecordModelHarnessProtocol,
            AppendModelHarnessCell,
            RecordModelHarnessAnalysis,
            RecordHarnessExecutionTrace,
        )
    ),
    (RecordRewardAssessment, RejectionCode.INVALID_REWARD),
)


class _CapabilityObserved(RuntimeError):
    pass


class _CapabilityProbeHandler:
    def __init__(self, proposal_type: str) -> None:
        self.proposal_type = proposal_type

    def build_context(self, proposal: object, reads: object) -> object:
        del proposal, reads
        raise _CapabilityObserved

    def decide(self, proposal: object, context: object) -> object:
        raise AssertionError("probe handler must stop during context construction")

    def project(self, proposal: object, decision: object, writes: object) -> None:
        raise AssertionError("probe handler must not project")


class _HookedMapping(Mapping[str, object]):
    def __init__(self) -> None:
        self.hook_calls = 0

    def __getitem__(self, key: str) -> object:
        del key
        self.hook_calls += 1
        raise AssertionError("mapping hook must not run")

    def __iter__(self) -> Iterator[str]:
        self.hook_calls += 1
        raise AssertionError("mapping hook must not run")

    def __len__(self) -> int:
        self.hook_calls += 1
        raise AssertionError("mapping hook must not run")


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class Runtime:
    service: KernelService
    coordinator: TransactionCoordinator
    uow_factory: Callable[[], DatabaseUnitOfWork]
    artifact_store: FileArtifactStore
    actor: ActorIdentity

    def add_evidence_proposal(self, proposal_id: str, key: str) -> AddEvidence:
        artifact = self.artifact_store.put(b"coordinator characterization", "text/plain")
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            evidence=EvidenceRecord(
                evidence_id=f"evidence-{proposal_id}",
                evidence_type="observation",
                source_locator=f"fixture://{proposal_id}",
                retrieved_at=NOW,
                artifact=artifact,
                provenance={"collector": "transaction-coordinator-test"},
                ingestion_actor_id=self.actor.actor_id,
            ),
        )

    def transaction_and_audit_counts(self) -> tuple[int, int]:
        with self.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            return (
                len(repositories.transactions.list_all()),
                len(repositories.audit.list_all()),
            )


def _policy_snapshot() -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    policy_data = policy.model_dump(mode="json")
    policy_data["human_approval_for"] = sorted(policy.human_approval_for)
    return PolicySnapshot(
        policy_hash=sha256_hex(canonical_json_bytes(policy_data)),
        policy=policy,
    )


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    database_url = f"sqlite:///{(tmp_path / 'kernel.db').as_posix()}"
    upgrade_database(database_url)
    engine: Engine = create_database_engine(database_url)
    policy = _policy_snapshot()
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="scientist-1", kind=ActorKind.HUMAN, created_at=NOW)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(policy, NOW)
    coordinator = TransactionCoordinator(uow_factory, policy, FixedClock(), artifact_store)
    yield Runtime(
        service=KernelService(uow_factory, policy, FixedClock(), artifact_store),
        coordinator=coordinator,
        uow_factory=uow_factory,
        artifact_store=artifact_store,
        actor=actor,
    )
    engine.dispose()


@pytest.mark.integration
def test_coordinator_preserves_one_decision_and_audit_event_per_new_attempt(
    runtime: Runtime,
) -> None:
    assert not hasattr(runtime.service, "coordinator")

    decision = runtime.coordinator.submit(runtime.add_evidence_proposal("proposal-1", "key-1"))

    assert decision.accepted is True
    assert runtime.transaction_and_audit_counts() == (1, 1)


@pytest.mark.integration
def test_coordinator_submits_an_ordered_batch_after_one_integrity_check(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integrity_checks = 0
    real_integrity_check = coordinator_module.require_workspace_integrity

    def count_integrity_checks(*args: object, **kwargs: object) -> None:
        nonlocal integrity_checks
        integrity_checks += 1
        real_integrity_check(*args, **kwargs)

    monkeypatch.setattr(
        coordinator_module,
        "require_workspace_integrity",
        count_integrity_checks,
    )
    proposals = (
        runtime.add_evidence_proposal("proposal-1", "key-1"),
        runtime.add_evidence_proposal("proposal-2", "key-2"),
    )

    decisions = runtime.coordinator.submit_batch(proposals)

    assert tuple(decision.accepted for decision in decisions) == (True, True)
    assert integrity_checks == 1
    assert runtime.transaction_and_audit_counts() == (2, 2)
    with runtime.uow_factory() as unit_of_work:
        transaction_ids = tuple(
            transaction.proposal.proposal_id
            for transaction in unit_of_work.repositories().transactions.list_all()
        )
    assert transaction_ids == ("proposal-1", "proposal-2")


@pytest.mark.integration
def test_coordinator_batch_handles_empty_and_unstorable_inputs(runtime: Runtime) -> None:
    assert runtime.coordinator.submit_batch(()) == ()

    (decision,) = runtime.coordinator.submit_batch(({},))

    assert decision.accepted is False
    assert decision.reasons[0].code.value == "INVALID_PROPOSAL"
    assert runtime.transaction_and_audit_counts() == (0, 0)


@pytest.mark.integration
def test_coordinator_batch_rolls_back_prior_writes_when_a_later_submit_raises(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposals = (
        runtime.add_evidence_proposal("proposal-1", "key-1"),
        runtime.add_evidence_proposal("proposal-2", "key-2"),
    )
    real_submit_locked = runtime.coordinator._submit_locked
    call_count = 0

    def fail_second_submit(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("second submit failed")
        return real_submit_locked(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime.coordinator, "_submit_locked", fail_second_submit)

    with pytest.raises(RuntimeError, match="second submit failed"):
        runtime.coordinator.submit_batch(proposals)

    assert runtime.transaction_and_audit_counts() == (0, 0)


@pytest.mark.integration
def test_compatibility_router_declares_the_resolved_proposal_type(runtime: Runtime) -> None:
    proposal_types = ("add_evidence", "propose_claim", "transition_claim")

    assert (
        tuple(
            runtime.coordinator.router.resolve(proposal_type).proposal_type
            for proposal_type in proposal_types
        )
        == proposal_types
    )


@pytest.mark.integration
def test_every_new_cognitive_proposal_has_one_fixed_route(runtime: Runtime) -> None:
    assert len(NEW_COGNITIVE_PROPOSAL_TYPES) == 18
    assert not hasattr(coordinator_module, "RetainedIntentProposalFactory")

    assert (
        tuple(
            runtime.coordinator.router.resolve(proposal_type).proposal_type
            for proposal_type in NEW_COGNITIVE_PROPOSAL_TYPES
        )
        == NEW_COGNITIVE_PROPOSAL_TYPES
    )


@pytest.mark.integration
def test_each_new_proposal_uses_only_its_focused_capability_factory(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, type[Any]]] = []

    def capability_factory(factory_name: str) -> Callable[..., object]:
        def observe(proposal: object, *args: object, **kwargs: object) -> object:
            del args, kwargs
            observed.append((factory_name, type(proposal)))
            return object()

        return observe

    for factory_name, _ in COGNITIVE_CAPABILITY_GROUPS:
        monkeypatch.setattr(
            coordinator_module,
            factory_name,
            capability_factory(factory_name),
        )

    with runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        connection = unit_of_work.connection
        assert isinstance(connection, Connection)
        for _, proposal_classes in COGNITIVE_CAPABILITY_GROUPS:
            for proposal_class in proposal_classes:
                proposal_type = proposal_class.model_fields["proposal_type"].default
                runtime.coordinator._router = ProposalRouter(
                    (  # type: ignore[arg-type]
                        (proposal_type, _CapabilityProbeHandler(proposal_type)),
                    )
                )
                proposal = (
                    RecordHarnessExecutionTrace(
                        proposal_id=f"probe-{proposal_type}",
                        idempotency_key=f"probe-key-{proposal_type}",
                        proposer=runtime.actor,
                        approval=Approval(approver=runtime.actor, approved_at=NOW),
                        envelope=HarnessExecutionTraceEnvelope(
                            metadata=HarnessTraceRecordMetadata(
                                received_at=NOW,
                                source_id="capability-probe",
                            ),
                            trace=valid_trace(),
                        ),
                    )
                    if proposal_class is RecordHarnessExecutionTrace
                    else proposal_class.model_construct(
                        proposal_id=f"probe-{proposal_type}",
                        idempotency_key=f"probe-key-{proposal_type}",
                        proposer=runtime.actor,
                    )
                )
                with pytest.raises(_CapabilityObserved):
                    runtime.coordinator._submit_locked(
                        proposal,
                        repositories,
                        connection,
                    )

    assert observed == [
        (factory_name, proposal_class)
        for factory_name, proposal_classes in COGNITIVE_CAPABILITY_GROUPS
        for proposal_class in proposal_classes
    ]


@pytest.mark.integration
def test_governed_reward_copies_and_hostile_subclasses_reach_fixed_handler_decision(
    runtime: Runtime,
) -> None:
    trace = valid_trace()
    assert trace.reward_observation is not None
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    proposal = RecordRewardAssessment(
        proposal_id="proposal-hostile-reward",
        idempotency_key="key-hostile-reward",
        proposer=runtime.actor,
        observation=trace.reward_observation,
        findings=assessment.findings,
        assessment=assessment,
    )
    copied = proposal.model_copy(update={"findings": ()})
    serializer_calls = 0

    def injected_serializer(*args: object, **kwargs: object) -> object:
        del args, kwargs
        nonlocal serializer_calls
        serializer_calls += 1
        raise AssertionError("injected serializer must not run")

    object.__setattr__(copied, "model_dump", injected_serializer)
    copied_decision = runtime.coordinator.submit(copied)

    attribute_calls = 0

    class HostileReward(RecordRewardAssessment):
        def __getattribute__(self, name: str) -> object:
            del name
            nonlocal attribute_calls
            attribute_calls += 1
            raise AssertionError("subclass attribute hook must not run")

    hostile_values = BaseModel.model_dump(proposal, mode="python", warnings=False) | {
        "proposal_id": "proposal-hostile-reward-subclass",
        "idempotency_key": "key-hostile-reward-subclass",
    }
    hostile = HostileReward.model_construct(**hostile_values)
    hostile_decision = runtime.coordinator.submit(hostile)

    assert copied_decision.reasons[0].code is RejectionCode.INVALID_REWARD
    assert hostile_decision.reasons[0].code is RejectionCode.INVALID_REWARD
    assert serializer_calls == 0
    assert attribute_calls == 0


@pytest.mark.integration
def test_other_nonvalidating_governed_copies_fail_closed_without_crashing(
    runtime: Runtime,
) -> None:
    copied = RecordCapabilityProfile.model_construct(
        proposal_id="proposal-invalid-capability-copy",
        idempotency_key="key-invalid-capability-copy",
        proposer=runtime.actor,
    )

    decision = runtime.coordinator.submit(copied)

    assert decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH
    assert runtime.transaction_and_audit_counts() == (1, 1)


@pytest.mark.integration
@pytest.mark.parametrize(("proposal_type", "expected_code"), HOSTILE_GOVERNED_REJECTION_CASES)
def test_every_hostile_governed_subclass_is_rejected_before_capability_attribute_access(
    runtime: Runtime,
    proposal_type: type[BaseModel],
    expected_code: RejectionCode,
) -> None:
    attribute_calls = 0
    serializer_calls = 0

    def hostile_attribute(self: object, name: str) -> object:
        del self, name
        nonlocal attribute_calls
        attribute_calls += 1
        raise AssertionError("hostile governed attribute hook must not run")

    hostile_type = type(
        f"Hostile{proposal_type.__name__}",
        (proposal_type,),
        {"__getattribute__": hostile_attribute},
    )

    def injected_serializer(*args: object, **kwargs: object) -> object:
        del args, kwargs
        nonlocal serializer_calls
        serializer_calls += 1
        raise AssertionError("injected governed serializer must not run")

    hostile = hostile_type.model_construct(
        proposal_id=f"hostile-{proposal_type.__name__}",
        idempotency_key=f"hostile-key-{proposal_type.__name__}",
        proposer=runtime.actor,
    )
    object.__setattr__(hostile, "model_dump", injected_serializer)

    decision = runtime.coordinator.submit(hostile)

    assert decision.reasons[0].code is expected_code
    assert attribute_calls == 0
    assert serializer_calls == 0
    with runtime.uow_factory() as unit_of_work:
        retained = unit_of_work.repositories().transactions.get_by_proposal_id(
            f"hostile-{proposal_type.__name__}"
        )
        assert retained is not None
        assert retained.proposal.proposal_type == "invalid_proposal"


@pytest.mark.integration
@pytest.mark.parametrize(("proposal_type", "expected_code"), HOSTILE_GOVERNED_REJECTION_CASES)
@pytest.mark.parametrize("error_type", (AssertionError, RuntimeError))
def test_exact_governed_constructs_reject_hostile_nested_values_without_hooks(
    runtime: Runtime,
    proposal_type: type[BaseModel],
    expected_code: RejectionCode,
    error_type: type[Exception],
) -> None:
    hook_calls: list[str] = []

    class HostileNestedValue:
        def __getattribute__(self, name: str) -> object:
            del self
            hook_calls.append(name)
            raise error_type("nested value hook must not run")

    base_fields = {
        "proposal_id",
        "idempotency_key",
        "proposer",
        "approval",
        "proposal_type",
    }
    payload_field = next(name for name in proposal_type.model_fields if name not in base_fields)
    suffix = error_type.__name__.lower()
    proposal_id = f"hostile-nested-{proposal_type.__name__}-{suffix}"
    hostile_value = HostileNestedValue()
    hostile_proposer = runtime.actor.model_copy(update={"created_at": hostile_value})
    proposal = proposal_type.model_construct(
        proposal_id=proposal_id,
        idempotency_key=f"hostile-nested-key-{proposal_type.__name__}-{suffix}",
        proposer=hostile_proposer,
        **{payload_field: hostile_value},
    )

    decision = runtime.coordinator.submit(proposal)

    assert decision.reasons[0].code is expected_code
    assert hook_calls == []
    with runtime.uow_factory() as unit_of_work:
        retained = unit_of_work.repositories().transactions.get_by_proposal_id(proposal_id)
        assert retained is not None
        assert retained.proposal.proposal_type == "invalid_proposal"


@pytest.mark.integration
def test_declared_maximum_model_harness_analysis_is_safe_and_reaches_its_handler(
    runtime: Runtime,
) -> None:
    from tests.unit.harness_eval.test_harness_security_contracts import (
        _maximum_shape_model_harness_analysis,
    )

    analysis, _ = _maximum_shape_model_harness_analysis()
    assert len(analysis.comparisons) == 24_512
    proposal = RecordModelHarnessAnalysis(
        proposal_id="proposal-maximum-model-harness-analysis",
        idempotency_key="key-maximum-model-harness-analysis",
        proposer=runtime.actor,
        analysis=analysis,
    )
    proposal_type = RecordModelHarnessAnalysis.model_fields["proposal_type"].default
    runtime.coordinator._router = ProposalRouter(
        ((proposal_type, _CapabilityProbeHandler(proposal_type)),)  # type: ignore[arg-type]
    )

    assert coordinator_module._governed_proposal_state_is_safe(
        proposal,
        RecordModelHarnessAnalysis,
    )
    with pytest.raises(_CapabilityObserved):
        runtime.coordinator.submit(proposal)

    values = analysis.model_dump(mode="python")
    values["comparisons"] = (*analysis.comparisons, analysis.comparisons[-1])
    with pytest.raises(ValidationError, match="comparisons"):
        type(analysis).model_validate(values, strict=True)


@pytest.mark.integration
def test_mapping_ingress_preflights_deep_exact_builtins_without_hooks(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = _HookedMapping()

    hostile_decision = runtime.coordinator.submit(hostile)

    assert hostile_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    assert hostile.hook_calls == 0

    deeply_nested: object = None
    for _ in range(10_000):
        deeply_nested = {"nested": [deeply_nested]}
    deep_decision = runtime.coordinator.submit(deeply_nested)

    assert deep_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    assert deep_decision.reasons[0].message == (
        "proposal failed service boundary validation and could not be stored"
    )

    maximum_nodes = {"items": [None] * (coordinator_module.MAX_PROPOSAL_JSON_NODES - 2)}
    excessive_nodes = {"items": [None] * (coordinator_module.MAX_PROPOSAL_JSON_NODES - 1)}
    assert coordinator_module._mapping_is_within_proposal_bounds(maximum_nodes) is True
    assert coordinator_module._mapping_is_within_proposal_bounds(excessive_nodes) is False

    oversized = {"padding": "x" * coordinator_module.MAX_PROPOSAL_BYTES}
    oversized_decision = runtime.coordinator.submit(oversized)
    assert oversized_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL

    byte_limit = coordinator_module.MAX_PROPOSAL_BYTES
    value_allowance = byte_limit - len(canonical_json_bytes({"": ""}))
    assert coordinator_module._mapping_is_within_proposal_bounds({"": "x" * value_allowance})
    assert not coordinator_module._mapping_is_within_proposal_bounds(
        {"": "x" * (value_allowance + 1)}
    )
    key_allowance = byte_limit - len(canonical_json_bytes({"": None}))
    assert coordinator_module._mapping_is_within_proposal_bounds({"x" * key_allowance: None})
    assert not coordinator_module._mapping_is_within_proposal_bounds(
        {"x" * (key_allowance + 1): None}
    )
    assert not coordinator_module._mapping_is_within_proposal_bounds(
        {"é" * (byte_limit // 2 + 1): None}
    )
    assert not coordinator_module._mapping_is_within_proposal_bounds(
        {"parts": ["x" * (byte_limit // 2), "y" * (byte_limit // 2)]}
    )
    assert not coordinator_module._mapping_is_within_proposal_bounds(
        {"integer": 1 << (byte_limit * 4)}
    )
    assert not coordinator_module._mapping_is_within_proposal_bounds({"float": float("inf")})

    serialization_calls = 0

    def forbidden_serialization(value: object) -> bytes:
        del value
        nonlocal serialization_calls
        serialization_calls += 1
        raise AssertionError("oversized mapping must fail before canonical serialization")

    monkeypatch.setattr(coordinator_module, "canonical_json_bytes", forbidden_serialization)
    multibyte_decision = runtime.coordinator.submit({"": "é" * (byte_limit // 2 + 1)})
    assert multibyte_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    nul_decision = runtime.coordinator.submit({"": "\0" * (byte_limit // 2)})
    assert nul_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    quote_decision = runtime.coordinator.submit({"": '"' * (byte_limit * 3 // 4)})
    assert quote_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    assert serialization_calls == 0


@pytest.mark.integration
def test_exact_replay_does_not_readmit_or_append(runtime: Runtime) -> None:
    proposal = runtime.add_evidence_proposal("proposal-1", "key-1")

    first = runtime.service.submit(proposal)
    second = runtime.service.submit(proposal)

    assert first.accepted is True
    assert second == first.model_copy(update={"replayed": True})
    assert runtime.transaction_and_audit_counts() == (1, 1)
