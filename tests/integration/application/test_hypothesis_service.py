from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, delete

from super_scientist.application.hypothesis_testing.service import (
    FIXED_HYPOTHESIS_CLASSIFICATION,
    HypothesisTestingService,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.transactions.hypotheses import (
    admission_to_storage,
    counterexample_to_storage,
    fixed_hypothesis_handlers,
    hypothesis_capabilities,
    hypothesis_to_storage,
    mechanism_to_storage,
    model_to_storage,
    revision_to_storage,
    simulation_to_storage,
    verification_to_storage,
)
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import AdaptationRequirement, GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.hypotheses.models import (
    AcceptedHypothesisReceiptRef,
    AdmissionOutcome,
    CounterexampleReceiptRef,
    CounterexampleRecord,
    DeterministicCheckerSpec,
    DeterministicCheckResult,
    EvaluatorAuditReceiptRef,
    ExecutableModelSpec,
    ExecutionMode,
    HypothesisAdmissionDecision,
    HypothesisCandidateReceiptRef,
    HypothesisRevisionReceiptRef,
    HypothesisSpec,
    HypothesisVersionReceiptRef,
    ImportedPatternStatus,
    ModelInput,
    ModelSpecReceiptRef,
    ModelType,
    NumericField,
    RevisionRecord,
    SelfImprovementMeasurementReceiptRef,
    SimulationResultReceiptRef,
    VerificationMechanismReceiptRef,
    VerificationOutcome,
    VerificationResultReceiptRef,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.domain.primitives import sha256_hex
from super_scientist.kernel.audit.models import json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AdmitHypothesis,
    Approval,
    CreateResearchRun,
    ProposeHypothesisVersion,
    RecordCounterexample,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
    RecordSimulationResult,
    RecordVerificationResult,
    RegisterExecutableModel,
    RegisterVerificationMechanism,
    RejectionCode,
    ReviseHypothesis,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    CounterexampleRecordRepository,
    ExecutableModelSpecRepository,
    HypothesisAdmissionDecisionRepository,
    HypothesisHeadRepository,
    HypothesisRevisionRepository,
    HypothesisVersionRepository,
    SimulationResultRepository,
    VerificationMechanismSpecRepository,
    VerificationResultRepository,
)
from super_scientist.providers.storage.repositories import PolicyRepository
from super_scientist.providers.storage.schema import hypothesis_heads
from tests.integration.application.test_adaptation_foundation import (
    _audit as base_audit,
)
from tests.integration.application.test_adaptation_foundation import (
    _measurement as base_measurement,
)
from tests.integration.application.test_adaptation_foundation import _run as base_run
from tests.integration.application.test_representation_service import (
    RepresentationRuntime,
    RepresentationService,
    SettableClock,
    _prepare_evaluated_candidate,
    _record_promotion_support,
)
from tests.integration.application.test_representation_service import (
    _actor as representation_actor,
)
from tests.integration.application.test_representation_service import (
    _admission_proposal as primitive_admission_proposal,
)

BASE = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class HypothesisRuntime:
    engine: Engine
    uow_factory: Callable[[], DatabaseUnitOfWork]
    policy: PolicySnapshot
    coordinator: TransactionCoordinator
    service: HypothesisTestingService
    artifacts: FileArtifactStore
    clock: SettableClock
    author: ActorIdentity
    checker: ActorIdentity
    integrator: ActorIdentity
    approver: ActorIdentity
    primitive_version_id: str


@pytest.fixture
def hypothesis_runtime(tmp_path: Path) -> Iterator[HypothesisRuntime]:
    policy = _policy()
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    database_url = f"sqlite:///{(tmp_path / 'hypotheses.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        PolicyRepository(connection).add_and_activate(snapshot, BASE)
    clock = SettableClock()
    artifacts = FileArtifactStore(tmp_path / "artifacts")

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    coordinator = TransactionCoordinator(uow_factory, snapshot, clock, artifacts)
    representation_runtime = RepresentationRuntime(
        engine=engine,
        uow_factory=uow_factory,
        policy=snapshot,
        coordinator=coordinator,
        service=RepresentationService(coordinator),
        artifacts=artifacts,
        clock=clock,
        primitive_author=representation_actor("task12-primitive-author", ActorKind.MODEL),
        stage_approver=representation_actor("task12-primitive-stage-approver"),
        integrator=representation_actor("task12-primitive-integrator"),
        admission_approver=representation_actor("task12-primitive-admission-approver"),
    )
    prepared_primitive = _prepare_evaluated_candidate(representation_runtime)
    primitive_support = _record_promotion_support(representation_runtime, prepared_primitive)
    assert representation_runtime.service.admit(
        primitive_admission_proposal(representation_runtime, prepared_primitive, primitive_support)
    ).accepted
    runtime = HypothesisRuntime(
        engine=engine,
        uow_factory=uow_factory,
        policy=snapshot,
        coordinator=coordinator,
        service=HypothesisTestingService(coordinator),
        artifacts=artifacts,
        clock=clock,
        author=_actor("hypothesis-author", ActorKind.MODEL),
        checker=_actor("hypothesis-checker", ActorKind.MODEL),
        integrator=_actor("hypothesis-integrator"),
        approver=_actor("hypothesis-admission-approver"),
        primitive_version_id=prepared_primitive.candidate.primitive_version_id,
    )
    try:
        _seed_evidence(runtime, "hypothesis-evidence")
        yield runtime
    finally:
        engine.dispose()


def test_hypothesis_handlers_and_classification_are_closed_and_exact() -> None:
    assert tuple(handler.proposal_type for handler in fixed_hypothesis_handlers()) == (
        "propose_hypothesis_version",
        "register_executable_model",
        "register_verification_mechanism",
        "record_simulation_result",
        "record_verification_result",
        "record_counterexample",
        "revise_hypothesis",
        "admit_hypothesis",
    )
    assert FIXED_HYPOTHESIS_CLASSIFICATION.target is ChangeTarget.RESEARCH_PROCESS
    assert FIXED_HYPOTHESIS_CLASSIFICATION.loop_closure is LoopClosure.HUMAN_IN_LOOP
    assert FIXED_HYPOTHESIS_CLASSIFICATION.persistence is PersistenceScope.RUN_LOCAL
    assert (
        FIXED_HYPOTHESIS_CLASSIFICATION.verification_level
        is VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
    )
    assert FIXED_HYPOTHESIS_CLASSIFICATION.grounding is ExternalGrounding.CONTROLLED_EXPERIMENT
    assert FIXED_HYPOTHESIS_CLASSIFICATION.signal is ImprovementSignal.EMPIRICAL_MEASUREMENT


@pytest.mark.integration
def test_live_capabilities_expose_one_narrow_writer_per_stage(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis = _hypothesis(runtime, 1)
    proposal = _hypothesis_proposal(runtime, hypothesis)
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        capabilities = hypothesis_capabilities(
            proposal,
            uow.connection,
            runtime.policy,
            runtime.artifacts,
            current_transaction_created_at=runtime.clock.current,
        )
        assert _public_methods(capabilities.writes) == {"append_hypothesis"}
        assert "append" not in _public_methods(capabilities.reads)
        assert "set" not in _public_methods(capabilities.reads)


@pytest.mark.integration
def test_every_live_hypothesis_stage_has_only_its_one_typed_writer(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis = _hypothesis(runtime, 1)
    candidate_receipt = _placeholder_receipt(HypothesisVersionReceiptRef, "candidate")
    model = _model(runtime, hypothesis, 1)
    model_receipt = _placeholder_receipt(ModelSpecReceiptRef, "model")
    mechanism = _mechanism(runtime, hypothesis, "capability")
    mechanism_receipt = _placeholder_receipt(
        VerificationMechanismReceiptRef,
        "mechanism",
    )
    simulation = runtime.service.simulate(
        model,
        _thermal_input(model.deterministic_seed),
        simulation_result_id="capability-simulation",
        output_id="capability-output",
        governing_policy_hash=runtime.policy.policy_hash,
        completed_at=runtime.clock.current,
    )
    simulation_receipt = _placeholder_receipt(
        SimulationResultReceiptRef,
        "simulation",
    )
    result = _result(
        runtime,
        hypothesis,
        model,
        mechanism,
        simulation.simulation_result_id,
        "capability",
    )
    result_receipt = _placeholder_receipt(VerificationResultReceiptRef, "result")
    failed = result.model_copy(
        update={
            "outcome": VerificationOutcome.FAIL,
            "provenance": _provenance(
                runtime,
                mechanism.mechanism_spec_id,
                outcome=AssessmentOutcome.FAILED,
            ),
            "counterexample_found": True,
        }
    )
    counterexample = CounterexampleRecord(
        counterexample_id="capability-counterexample",
        hypothesis_version_id=hypothesis.hypothesis_version_id,
        model_spec_id=model.model_spec_id,
        simulation_result_ids=(simulation.simulation_result_id,),
        verification_result_ids=(failed.verification_result_id,),
        evidence_ids=("hypothesis-evidence",),
        description="A bounded fixture contradicted the prediction.",
        input_hash=sha256_hex(b"capability-input"),
        observed_output_hash=sha256_hex(b"capability-observed"),
        expected_output_hash=sha256_hex(b"capability-expected"),
        discovered_by=runtime.checker,
        discovered_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    counterexample_receipt = _placeholder_receipt(
        CounterexampleReceiptRef,
        "counterexample",
    )
    resulting = _hypothesis(runtime, 2)
    revision = _revision(runtime, hypothesis, resulting, failed).model_copy(
        update={"considered_counterexample_ids": (counterexample.counterexample_id,)}
    )
    prepared = PreparedCandidate(
        hypothesis=hypothesis,
        hypothesis_receipt=candidate_receipt,
        model=model,
        model_receipt=model_receipt,
        mechanism=mechanism,
        mechanism_receipt=mechanism_receipt,
        simulation_receipt=simulation_receipt,
        result=result,
        result_receipt=result_receipt,
    )
    support = AdmissionSupport(
        audit_receipt=_placeholder_receipt(EvaluatorAuditReceiptRef, "audit"),
        measurement_receipt=_placeholder_receipt(
            SelfImprovementMeasurementReceiptRef,
            "measurement",
        ),
    )
    proposals: tuple[tuple[BaseModel, str], ...] = (
        (_hypothesis_proposal(runtime, hypothesis), "append_hypothesis"),
        (_model_proposal(runtime, candidate_receipt, model, "capability"), "append_model"),
        (
            RegisterVerificationMechanism(
                proposal_id="capability-mechanism",
                idempotency_key="intent-capability-mechanism",
                proposer=mechanism.created_by,
                approval=_approval(runtime, "capability-mechanism-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=candidate_receipt,
                mechanism_spec=mechanism,
            ),
            "append_mechanism",
        ),
        (
            RecordSimulationResult(
                proposal_id="capability-simulation",
                idempotency_key="intent-capability-simulation",
                proposer=model.registered_by,
                approval=_approval(runtime, "capability-simulation-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=candidate_receipt,
                model_receipt=model_receipt,
                simulation_result=simulation,
            ),
            "append_simulation",
        ),
        (
            RecordVerificationResult(
                proposal_id="capability-result",
                idempotency_key="intent-capability-result",
                proposer=result.provenance.actor,
                approval=_approval(runtime, "capability-result-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=candidate_receipt,
                mechanism_receipt=mechanism_receipt,
                model_receipt=model_receipt,
                simulation_receipts=(simulation_receipt,),
                verification_result=result,
            ),
            "append_result",
        ),
        (
            RecordCounterexample(
                proposal_id="capability-counterexample",
                idempotency_key="intent-capability-counterexample",
                proposer=counterexample.discovered_by,
                approval=_approval(runtime, "capability-counterexample-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=candidate_receipt,
                model_receipt=model_receipt,
                simulation_receipts=(simulation_receipt,),
                verification_result_receipts=(result_receipt,),
                counterexample=counterexample,
            ),
            "append_counterexample",
        ),
        (
            ReviseHypothesis(
                proposal_id="capability-revision",
                idempotency_key="intent-capability-revision",
                proposer=revision.author,
                approval=_approval(runtime, "capability-revision-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                prior_hypothesis_receipt=candidate_receipt,
                triggering_result_receipts=(result_receipt,),
                counterexample_receipts=(counterexample_receipt,),
                resulting_hypothesis=resulting,
                revision=revision,
            ),
            "append_revision",
        ),
        (
            _admission_proposal(
                runtime,
                prepared,
                support,
                rollback=None,
                suffix="capability",
            ),
            "admit_hypothesis",
        ),
    )
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        for proposal, expected_writer in proposals:
            capabilities = hypothesis_capabilities(
                proposal,
                uow.connection,
                runtime.policy,
                runtime.artifacts,
                current_transaction_created_at=runtime.clock.current,
            )
            assert _public_methods(capabilities.writes) == {expected_writer}
            assert not {
                "append_authoritative",
                "update_projection",
                "set_head",
            }.intersection(_public_methods(capabilities.writes))


@pytest.mark.integration
def test_metadata_only_model_is_retained_but_service_refuses_execution(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, hypothesis_receipt = _stage_hypothesis(runtime, 1)
    model = _model(runtime, hypothesis, version=1).model_copy(
        update={
            "model_type": ModelType.SOURCE_CONTROLLED_METADATA,
            "execution_mode": ExecutionMode.METADATA_ONLY,
            "artifact_hash": sha256_hex(b"inert artifact"),
            "artifact_media_type": "application/octet-stream",
            "artifact_size_bytes": 14,
            "artifact_name": "do-not-execute.py",
            "builtin_simulator_id": None,
        }
    )
    proposal = _model_proposal(runtime, hypothesis_receipt, model)
    assert runtime.service.register_model(proposal).accepted

    with pytest.raises(ValueError, match="metadata-only"):
        runtime.service.simulate(
            model,
            _thermal_input(model.deterministic_seed),
            simulation_result_id="forbidden-simulation",
            output_id="forbidden-output",
            governing_policy_hash=runtime.policy.policy_hash,
            completed_at=runtime.clock.current,
        )


@pytest.mark.integration
def test_full_failed_check_revision_and_successor_admission_preserves_history(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    v1, v1_receipt = _stage_hypothesis(runtime, 1)
    prepared_v1 = _prepare_checked_candidate(runtime, v1, v1_receipt)
    _admit_candidate(runtime, prepared_v1, rollback=None)

    failed_result, failed_receipt, counterexample_receipt = _record_failed_counterexample(
        runtime,
        prepared_v1,
    )
    resulting = _hypothesis(runtime, 2)
    revision = _revision(runtime, v1, resulting, failed_result)
    revision_proposal = ReviseHypothesis(
        proposal_id="revise-hypothesis-v2",
        idempotency_key="intent-revise-hypothesis-v2",
        proposer=revision.author,
        approval=_approval(runtime, "revision-approver"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        prior_hypothesis_receipt=v1_receipt,
        triggering_result_receipts=(failed_receipt,),
        counterexample_receipts=(counterexample_receipt,),
        resulting_hypothesis=resulting,
        revision=revision,
    )
    assert runtime.service.revise(revision_proposal).accepted
    revision_receipt = _receipt(
        runtime,
        revision_proposal.proposal_id,
        HypothesisRevisionReceiptRef,
    )
    prepared_v2 = _prepare_checked_candidate(runtime, resulting, revision_receipt)
    stale_support = _record_admission_support(
        runtime,
        prepared_v2,
        rollback=v1.hypothesis_version_id,
        suffix="wrong-rollback",
    )
    stale_proposal = _admission_proposal(
        runtime,
        prepared_v2,
        stale_support,
        rollback=v1.hypothesis_version_id,
        suffix="wrong-rollback",
    )
    wrong_rollback = "unrelated-hypothesis-v1"
    stale_proposal = stale_proposal.model_copy(
        update={
            "rollback_hypothesis_version_id": wrong_rollback,
            "admission_decision": stale_proposal.admission_decision.model_copy(
                update={"rollback_hypothesis_version_id": wrong_rollback}
            ),
        }
    )
    stale_decision = runtime.service.admit(stale_proposal)
    assert stale_decision.accepted is False
    assert stale_decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    _admit_candidate(runtime, prepared_v2, rollback=v1.hypothesis_version_id)

    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        versions = HypothesisVersionRepository(uow.connection)
        assert versions.get(v1.hypothesis_version_id) is not None
        assert versions.get(resulting.hypothesis_version_id) is not None
        assert HypothesisRevisionRepository(uow.connection).get(revision.revision_id) is not None
        assert CounterexampleRecordRepository(uow.connection).list_all()
        assert HypothesisHeadRepository(uow.connection).get(resulting.hypothesis_id) == (
            resulting.hypothesis_version_id,
            2,
            # Compare the exact stored status by value below.
            HypothesisHeadRepository(uow.connection).get(resulting.hypothesis_id)[2],  # type: ignore[index]
        )
        assert (
            HypothesisHeadRepository(uow.connection).get(resulting.hypothesis_id)[2].value  # type: ignore[index]
            == ImportedPatternStatus.TRANSFER_VALIDATED.value
        )
        verification = verify_workspace(uow.repositories(), runtime.artifacts)
        assert verification.valid, verification.reason


@pytest.mark.integration
def test_unresolved_counterexample_blocks_admission_until_revision(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, hypothesis, receipt)
    _record_failed_counterexample(runtime, prepared)
    support = _record_admission_support(runtime, prepared, rollback=None, suffix="blocked")
    proposal = _admission_proposal(runtime, prepared, support, rollback=None, suffix="blocked")

    decision = runtime.service.admit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert HypothesisHeadRepository(uow.connection).get(hypothesis.hypothesis_id) is None


@pytest.mark.integration
def test_fake_receipt_and_stable_key_changed_content_are_audited_conflicts(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    fake = receipt.model_copy(update={"proposal_hash": "f" * 64})
    model = _model(runtime, hypothesis, 1)
    fake_decision = runtime.service.register_model(_model_proposal(runtime, fake, model, "fake"))
    assert fake_decision.accepted is False
    assert fake_decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    assert runtime.service.register_model(
        _model_proposal(runtime, receipt, model, "first")
    ).accepted
    changed = model.model_copy(update={"max_steps": model.max_steps + 1})
    changed_decision = runtime.service.register_model(
        _model_proposal(runtime, receipt, changed, "changed")
    )
    assert changed_decision.accepted is False
    assert changed_decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    with runtime.uow_factory() as uow:
        rejected = tuple(
            item
            for item in uow.repositories().transactions.list_all()
            if item.proposal.proposal_id in {"register-model-fake", "register-model-changed"}
        )
        assert len(rejected) == 2
        assert all(not item.decision.accepted for item in rejected)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("forgery", "expected_code"),
    [
        ("classification", RejectionCode.PERMISSION_DENIED),
        ("policy", RejectionCode.POLICY_HASH_MISMATCH),
        ("self-approval", RejectionCode.INDEPENDENT_REVIEW_REQUIRED),
    ],
)
def test_forged_stage_authority_fails_closed_without_projection(
    hypothesis_runtime: HypothesisRuntime,
    forgery: str,
    expected_code: RejectionCode,
) -> None:
    runtime = hypothesis_runtime
    hypothesis = _hypothesis(runtime, 1)
    proposal = _hypothesis_proposal(runtime, hypothesis).model_copy(
        update={
            "proposal_id": f"forged-{forgery}",
            "idempotency_key": f"intent-forged-{forgery}",
        }
    )
    if forgery == "classification":
        proposal = proposal.model_copy(
            update={
                "classification": FIXED_HYPOTHESIS_CLASSIFICATION.model_copy(
                    update={"signal": ImprovementSignal.HUMAN_CORRECTION}
                )
            }
        )
    elif forgery == "policy":
        proposal = proposal.model_copy(
            update={"hypothesis": hypothesis.model_copy(update={"governing_policy_hash": "f" * 64})}
        )
    else:
        proposal = proposal.model_copy(
            update={"approval": Approval(approver=runtime.author, approved_at=BASE)}
        )

    decision = runtime.service.propose(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert (
            HypothesisVersionRepository(uow.connection).get(hypothesis.hypothesis_version_id)
            is None
        )


@pytest.mark.integration
def test_cross_scope_model_lineage_is_rejected(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    model = _model(runtime, hypothesis, 1).model_copy(
        update={"hypothesis_version_id": "unrelated-hypothesis-v1"}
    )

    decision = runtime.service.register_model(
        _model_proposal(runtime, receipt, model, "cross-scope")
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert ExecutableModelSpecRepository(uow.connection).get(model.model_spec_id) is None


@pytest.mark.integration
def test_simulation_timestamp_cannot_predate_committed_dependencies(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    model = _model(runtime, hypothesis, 1)
    model_proposal = _model_proposal(runtime, receipt, model, "backdated")
    assert runtime.service.register_model(model_proposal).accepted
    model_receipt = _receipt(runtime, model_proposal.proposal_id, ModelSpecReceiptRef)
    simulation = runtime.service.simulate(
        model,
        _thermal_input(model.deterministic_seed),
        simulation_result_id="backdated-simulation",
        output_id="backdated-output",
        governing_policy_hash=runtime.policy.policy_hash,
        completed_at=runtime.clock.current,
    ).model_copy(update={"completed_at": BASE - timedelta(days=365)})
    proposal = RecordSimulationResult(
        proposal_id="record-backdated-simulation",
        idempotency_key="intent-record-backdated-simulation",
        proposer=model.registered_by,
        approval=Approval(
            approver=_actor("backdated-approval"),
            approved_at=runtime.clock.current,
        ),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        model_receipt=model_receipt,
        simulation_result=simulation,
    )

    decision = runtime.service.record_simulation(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert (
            SimulationResultRepository(uow.connection).get(simulation.simulation_result_id) is None
        )


@pytest.mark.integration
def test_stage_approval_cannot_predate_committed_dependencies(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    model = _model(runtime, hypothesis, 1)
    model_proposal = _model_proposal(runtime, receipt, model, "approval-chronology")
    assert runtime.service.register_model(model_proposal).accepted
    model_receipt = _receipt(runtime, model_proposal.proposal_id, ModelSpecReceiptRef)
    simulation = runtime.service.simulate(
        model,
        _thermal_input(model.deterministic_seed),
        simulation_result_id="approval-chronology-simulation",
        output_id="approval-chronology-output",
        governing_policy_hash=runtime.policy.policy_hash,
        completed_at=runtime.clock.current,
    ).model_copy(update={"completed_at": runtime.clock.current})
    proposal = RecordSimulationResult(
        proposal_id="record-approval-chronology-simulation",
        idempotency_key="intent-record-approval-chronology-simulation",
        proposer=model.registered_by,
        approval=Approval(
            approver=_actor("approval-chronology-reviewer"),
            approved_at=BASE - timedelta(days=365),
        ),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        model_receipt=model_receipt,
        simulation_result=simulation,
    )

    decision = runtime.service.record_simulation(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
@pytest.mark.parametrize("future_field", ["record", "approval"])
def test_caller_timestamp_cannot_follow_current_transaction_time(
    hypothesis_runtime: HypothesisRuntime,
    future_field: str,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    model = _model(runtime, hypothesis, 1)
    model_proposal = _model_proposal(runtime, receipt, model, f"future-{future_field}")
    assert runtime.service.register_model(model_proposal).accepted
    model_receipt = _receipt(runtime, model_proposal.proposal_id, ModelSpecReceiptRef)
    current = runtime.clock.current
    simulation = runtime.service.simulate(
        model,
        _thermal_input(model.deterministic_seed),
        simulation_result_id=f"future-{future_field}-simulation",
        output_id=f"future-{future_field}-output",
        governing_policy_hash=runtime.policy.policy_hash,
        completed_at=current,
    ).model_copy(
        update={
            "completed_at": current + timedelta(days=1) if future_field == "record" else current
        }
    )
    proposal = RecordSimulationResult(
        proposal_id=f"record-future-{future_field}-simulation",
        idempotency_key=f"intent-record-future-{future_field}-simulation",
        proposer=model.registered_by,
        approval=Approval(
            approver=_actor(f"future-{future_field}-reviewer"),
            approved_at=current + timedelta(days=1) if future_field == "approval" else current,
        ),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        model_receipt=model_receipt,
        simulation_result=simulation,
    )

    decision = runtime.service.record_simulation(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["model", "mechanism"])
def test_registered_stage_timestamp_cannot_predate_hypothesis_receipt(
    hypothesis_runtime: HypothesisRuntime,
    stage: str,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    if stage == "model":
        model = _model(runtime, hypothesis, 1).model_copy(
            update={"created_at": BASE - timedelta(days=365)}
        )
        decision = runtime.service.register_model(
            _model_proposal(runtime, receipt, model, "predated-model")
        )
    else:
        mechanism = _mechanism(runtime, hypothesis, "predated").model_copy(
            update={"created_at": BASE - timedelta(days=365)}
        )
        decision = runtime.service.register_verification_mechanism(
            RegisterVerificationMechanism(
                proposal_id="register-predated-mechanism",
                idempotency_key="intent-register-predated-mechanism",
                proposer=mechanism.created_by,
                approval=Approval(
                    approver=_actor("predated-mechanism-reviewer"),
                    approved_at=runtime.clock.current,
                ),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=receipt,
                mechanism_spec=mechanism,
            )
        )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_verification_result_cannot_predate_its_retained_inputs(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, hypothesis, receipt)
    result = prepared.result.model_copy(
        update={
            "verification_result_id": "predated-verification-result",
            "provenance": prepared.result.provenance.model_copy(
                update={"assessed_at": BASE - timedelta(days=365)}
            ),
        }
    )
    proposal = RecordVerificationResult(
        proposal_id="record-predated-verification-result",
        idempotency_key="intent-record-predated-verification-result",
        proposer=result.provenance.actor,
        approval=Approval(
            approver=_actor("predated-result-reviewer"),
            approved_at=runtime.clock.current,
        ),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=prepared.hypothesis_receipt,
        mechanism_receipt=prepared.mechanism_receipt,
        model_receipt=prepared.model_receipt,
        simulation_receipts=(prepared.simulation_receipt,),
        verification_result=result,
    )

    decision = runtime.service.record_verification(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_revision_timestamp_cannot_predate_triggering_history(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    prior, prior_receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, prior, prior_receipt)
    _admit_candidate(runtime, prepared, rollback=None)
    failed, failed_receipt, counterexample_receipt = _record_failed_counterexample(
        runtime,
        prepared,
    )
    resulting = _hypothesis(runtime, 2).model_copy(update={"created_at": runtime.clock.current})
    revision = _revision(runtime, prior, resulting, failed).model_copy(
        update={"revised_at": BASE - timedelta(days=365)}
    )
    proposal = ReviseHypothesis(
        proposal_id="revise-predated-hypothesis",
        idempotency_key="intent-revise-predated-hypothesis",
        proposer=revision.author,
        approval=Approval(
            approver=_actor("predated-revision-reviewer"),
            approved_at=runtime.clock.current,
        ),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        prior_hypothesis_receipt=prior_receipt,
        triggering_result_receipts=(failed_receipt,),
        counterexample_receipts=(counterexample_receipt,),
        resulting_hypothesis=resulting,
        revision=revision,
    )

    decision = runtime.service.revise(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
@pytest.mark.parametrize("timestamp_field", ["integrated_at", "decided_at", "approved_at"])
def test_admission_timestamps_cannot_predate_supporting_history(
    hypothesis_runtime: HypothesisRuntime,
    timestamp_field: str,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, hypothesis, receipt)
    support = _record_admission_support(
        runtime,
        prepared,
        rollback=None,
        suffix=f"chronology-{timestamp_field}",
    )
    current = runtime.clock.current
    proposal = _admission_proposal(
        runtime,
        prepared,
        support,
        rollback=None,
        suffix=f"chronology-{timestamp_field}",
    ).model_copy(
        update={
            "integrated_at": current,
            "approval": Approval(
                approver=runtime.approver,
                approved_at=current,
            ),
            "admission_decision": _admission_proposal(
                runtime,
                prepared,
                support,
                rollback=None,
                suffix=f"chronology-{timestamp_field}",
            ).admission_decision.model_copy(update={"decided_at": current}),
        }
    )
    backdated = BASE - timedelta(days=365)
    if timestamp_field == "integrated_at":
        proposal = proposal.model_copy(update={"integrated_at": backdated})
    elif timestamp_field == "decided_at":
        proposal = proposal.model_copy(
            update={
                "admission_decision": proposal.admission_decision.model_copy(
                    update={"decided_at": backdated}
                )
            }
        )
    else:
        assert proposal.approval is not None
        proposal = proposal.model_copy(
            update={"approval": proposal.approval.model_copy(update={"approved_at": backdated})}
        )

    decision = runtime.service.admit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_stale_admission_cannot_reuse_an_already_advanced_head(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, hypothesis, receipt)
    _admit_candidate(runtime, prepared, rollback=None)
    support = _record_admission_support(runtime, prepared, rollback=None, suffix="stale")

    decision = runtime.service.admit(
        _admission_proposal(
            runtime,
            prepared,
            support,
            rollback=None,
            suffix="stale",
        )
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_workspace_replay_detects_a_deleted_hypothesis_head(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    hypothesis, receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, hypothesis, receipt)
    _admit_candidate(runtime, prepared, rollback=None)
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        uow.connection.execute(
            delete(hypothesis_heads).where(
                hypothesis_heads.c.hypothesis_id == hypothesis.hypothesis_id
            )
        )
    with runtime.uow_factory() as uow:
        verification = verify_workspace(uow.repositories(), runtime.artifacts)

    assert verification.valid is False
    assert verification.reason is not None
    assert "hypothesis heads" in verification.reason


@pytest.mark.integration
@pytest.mark.parametrize(
    "family",
    [
        "versions",
        "models",
        "mechanisms",
        "simulations",
        "results",
        "counterexamples",
        "revisions",
        "admissions",
        "heads",
    ],
)
def test_workspace_replay_rejects_every_unlogged_hypothesis_snapshot_family(
    hypothesis_runtime: HypothesisRuntime,
    family: str,
) -> None:
    runtime = hypothesis_runtime
    _stage_hypothesis(runtime, 1)
    with runtime.uow_factory() as uow:
        baseline = verify_workspace(uow.repositories(), runtime.artifacts)
    assert baseline.valid, baseline.reason

    _insert_unlogged_hypothesis_scope(runtime, family)

    with runtime.uow_factory() as uow:
        verification = verify_workspace(uow.repositories(), runtime.artifacts)
    assert verification.valid is False
    assert verification.reason is not None
    assert "hypothesis" in verification.reason or family in verification.reason


@pytest.mark.integration
def test_workspace_replay_rejects_unlogged_model_independent_verification_graph(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    _stage_hypothesis(runtime, 1)
    with runtime.uow_factory() as uow:
        baseline = verify_workspace(uow.repositories(), runtime.artifacts)
    assert baseline.valid, baseline.reason

    _insert_unlogged_model_independent_verification_graph(runtime)

    with runtime.uow_factory() as uow:
        verification = verify_workspace(uow.repositories(), runtime.artifacts)
    assert verification.valid is False
    assert verification.reason is not None
    assert "hypothesis" in verification.reason


@pytest.mark.integration
def test_stage_rejections_and_exact_duplicate_projection_are_durable(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    candidate = _hypothesis(runtime, 1)

    wrong_actor = _hypothesis_proposal(runtime, candidate).model_copy(
        update={
            "proposal_id": "propose-wrong-hypothesis-actor",
            "idempotency_key": "intent-propose-wrong-hypothesis-actor",
            "proposer": _actor("wrong-hypothesis-actor"),
        }
    )
    decision = runtime.service.propose(wrong_actor)
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH

    orphan_v2 = _hypothesis(runtime, 2).model_copy(
        update={
            "hypothesis_version_id": "orphan-hypothesis-v2",
            "hypothesis_id": "orphan-hypothesis",
        }
    )
    decision = runtime.service.propose(_hypothesis_proposal(runtime, orphan_v2))
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    missing_evidence = candidate.model_copy(
        update={
            "hypothesis_version_id": "missing-evidence-hypothesis-v1",
            "hypothesis_id": "missing-evidence-hypothesis",
            "evidence_ids": ("absent-hypothesis-evidence",),
        }
    )
    decision = runtime.service.propose(_hypothesis_proposal(runtime, missing_evidence))
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE

    candidate, candidate_receipt = _stage_hypothesis(runtime, 1)
    duplicate_candidate = _hypothesis_proposal(runtime, candidate).model_copy(
        update={
            "proposal_id": "duplicate-exact-hypothesis",
            "idempotency_key": "intent-duplicate-exact-hypothesis",
        }
    )
    assert runtime.service.propose(duplicate_candidate).accepted
    changed_candidate = candidate.model_copy(
        update={"statement": "Changed content cannot reuse a hypothesis version key."}
    )
    changed_candidate_proposal = _hypothesis_proposal(runtime, changed_candidate).model_copy(
        update={
            "proposal_id": "conflicting-hypothesis",
            "idempotency_key": "intent-conflicting-hypothesis",
        }
    )
    decision = runtime.service.propose(changed_candidate_proposal)
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    valid_model = _model(runtime, candidate, 1)
    wrong_model_actor = _model_proposal(runtime, candidate_receipt, valid_model, "wrong-actor")
    wrong_model_actor = wrong_model_actor.model_copy(
        update={"proposer": _actor("wrong-model-actor")}
    )
    decision = runtime.service.register_model(wrong_model_actor)
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH

    unknown_model = valid_model.model_copy(
        update={
            "model_spec_id": "unknown-builtin-model",
            "builtin_simulator_id": "unknown-builtin-v1",
        }
    )
    decision = runtime.service.register_model(
        _model_proposal(runtime, candidate_receipt, unknown_model, "unknown-builtin")
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL

    prepared = _prepare_checked_candidate(runtime, candidate, candidate_receipt)
    assert runtime.service.register_model(
        _model_proposal(runtime, candidate_receipt, prepared.model, "duplicate-exact")
    ).accepted
    conflicting_model = prepared.model.model_copy(
        update={"max_steps": prepared.model.max_steps + 1}
    )
    decision = runtime.service.register_model(
        _model_proposal(runtime, candidate_receipt, conflicting_model, "conflicting")
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    fake_candidate_receipt = _placeholder_receipt(
        HypothesisVersionReceiptRef,
        "absent-candidate",
    )
    mechanism_template = RegisterVerificationMechanism(
        proposal_id="mechanism-template",
        idempotency_key="intent-mechanism-template",
        proposer=prepared.mechanism.created_by,
        approval=_approval(runtime, "mechanism-template-approver"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=candidate_receipt,
        mechanism_spec=prepared.mechanism,
    )
    decision = runtime.service.register_verification_mechanism(
        mechanism_template.model_copy(
            update={
                "proposal_id": "mechanism-absent-receipt",
                "idempotency_key": "intent-mechanism-absent-receipt",
                "hypothesis_receipt": fake_candidate_receipt,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    decision = runtime.service.register_verification_mechanism(
        mechanism_template.model_copy(
            update={
                "proposal_id": "mechanism-wrong-actor",
                "idempotency_key": "intent-mechanism-wrong-actor",
                "proposer": _actor("wrong-mechanism-actor"),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH
    assert runtime.service.register_verification_mechanism(
        mechanism_template.model_copy(
            update={
                "proposal_id": "mechanism-duplicate-exact",
                "idempotency_key": "intent-mechanism-duplicate-exact",
            }
        )
    ).accepted
    changed_mechanism = prepared.mechanism.model_copy(
        update={"description": "Changed checker content cannot reuse a stable key."}
    )
    decision = runtime.service.register_verification_mechanism(
        mechanism_template.model_copy(
            update={
                "proposal_id": "mechanism-conflicting",
                "idempotency_key": "intent-mechanism-conflicting",
                "mechanism_spec": changed_mechanism,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    with runtime.uow_factory() as uow:
        stored_simulation = uow.repositories().transactions.get_by_proposal_id(
            prepared.simulation_receipt.proposal_id
        )
        stored_result = uow.repositories().transactions.get_by_proposal_id(
            prepared.result_receipt.proposal_id
        )
    assert stored_simulation is not None
    assert isinstance(stored_simulation.proposal, RecordSimulationResult)
    simulation_template = stored_simulation.proposal
    assert stored_result is not None
    assert isinstance(stored_result.proposal, RecordVerificationResult)
    result_template = stored_result.proposal

    decision = runtime.service.record_simulation(
        simulation_template.model_copy(
            update={
                "proposal_id": "simulation-wrong-actor",
                "idempotency_key": "intent-simulation-wrong-actor",
                "proposer": _actor("wrong-simulation-actor"),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH
    decision = runtime.service.record_simulation(
        simulation_template.model_copy(
            update={
                "proposal_id": "simulation-absent-model-receipt",
                "idempotency_key": "intent-simulation-absent-model-receipt",
                "model_receipt": _placeholder_receipt(ModelSpecReceiptRef, "absent-model"),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    simulation = simulation_template.simulation_result
    invalid_values = tuple(
        item.model_copy(update={"value": -1.0}) if item.name == "heater_delta" else item
        for item in simulation.model_input.values
    )
    invalid_input_simulation = simulation.model_copy(
        update={
            "simulation_result_id": "invalid-input-simulation",
            "model_input": simulation.model_input.model_copy(update={"values": invalid_values}),
        }
    )
    decision = runtime.service.record_simulation(
        simulation_template.model_copy(
            update={
                "proposal_id": "record-invalid-input-simulation",
                "idempotency_key": "intent-record-invalid-input-simulation",
                "simulation_result": invalid_input_simulation,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL

    wrong_values = tuple(
        item.model_copy(update={"value": float(item.value) + 1.0})
        if item.name == "final_temperature"
        else item
        for item in simulation.model_output.values
    )
    nonreproducible = simulation.model_copy(
        update={
            "simulation_result_id": "nonreproducible-simulation",
            "model_output": simulation.model_output.model_copy(update={"values": wrong_values}),
        }
    )
    decision = runtime.service.record_simulation(
        simulation_template.model_copy(
            update={
                "proposal_id": "record-nonreproducible-simulation",
                "idempotency_key": "intent-record-nonreproducible-simulation",
                "simulation_result": nonreproducible,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING
    assert runtime.service.record_simulation(
        simulation_template.model_copy(
            update={
                "proposal_id": "record-duplicate-exact-simulation",
                "idempotency_key": "intent-record-duplicate-exact-simulation",
            }
        )
    ).accepted
    changed_completed_at = simulation.completed_at + timedelta(seconds=1)
    assert simulation_template.approval is not None
    decision = runtime.service.record_simulation(
        simulation_template.model_copy(
            update={
                "proposal_id": "record-conflicting-simulation",
                "idempotency_key": "intent-record-conflicting-simulation",
                "simulation_result": simulation.model_copy(
                    update={"completed_at": changed_completed_at}
                ),
                "approval": simulation_template.approval.model_copy(
                    update={"approved_at": changed_completed_at}
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    decision = runtime.service.record_verification(
        result_template.model_copy(
            update={
                "proposal_id": "verification-wrong-actor",
                "idempotency_key": "intent-verification-wrong-actor",
                "proposer": _actor("wrong-verification-actor"),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH
    decision = runtime.service.record_verification(
        result_template.model_copy(
            update={
                "proposal_id": "verification-absent-mechanism",
                "idempotency_key": "intent-verification-absent-mechanism",
                "mechanism_receipt": _placeholder_receipt(
                    VerificationMechanismReceiptRef,
                    "absent-mechanism",
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    wrong_lineage_result = prepared.result.model_copy(
        update={
            "verification_result_id": "verification-wrong-mechanism-lineage",
            "mechanism_spec_id": "unrelated-mechanism",
        }
    )
    decision = runtime.service.record_verification(
        result_template.model_copy(
            update={
                "proposal_id": "record-wrong-mechanism-lineage",
                "idempotency_key": "intent-record-wrong-mechanism-lineage",
                "verification_result": wrong_lineage_result,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    ungrounded_result = prepared.result.model_copy(
        update={
            "verification_result_id": "verification-ungrounded",
            "provenance": prepared.result.provenance.model_copy(
                update={"evidence_ids": ("absent-verification-evidence",)}
            ),
        }
    )
    decision = runtime.service.record_verification(
        result_template.model_copy(
            update={
                "proposal_id": "record-ungrounded-verification",
                "idempotency_key": "intent-record-ungrounded-verification",
                "verification_result": ungrounded_result,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    assert runtime.service.record_verification(
        result_template.model_copy(
            update={
                "proposal_id": "record-duplicate-exact-verification",
                "idempotency_key": "intent-record-duplicate-exact-verification",
            }
        )
    ).accepted
    changed_result = prepared.result.model_copy(
        update={"findings": ("Changed findings cannot reuse a verification key.",)}
    )
    decision = runtime.service.record_verification(
        result_template.model_copy(
            update={
                "proposal_id": "record-conflicting-verification",
                "idempotency_key": "intent-record-conflicting-verification",
                "verification_result": changed_result,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert (
            HypothesisVersionRepository(uow.connection).get(missing_evidence.hypothesis_version_id)
            is None
        )
        assert (
            ExecutableModelSpecRepository(uow.connection).get(unknown_model.model_spec_id) is None
        )
        assert (
            VerificationMechanismSpecRepository(uow.connection).get(
                prepared.mechanism.mechanism_spec_id
            )
            is not None
        )
        assert (
            SimulationResultRepository(uow.connection).get(
                invalid_input_simulation.simulation_result_id
            )
            is None
        )
        assert (
            SimulationResultRepository(uow.connection).get(nonreproducible.simulation_result_id)
            is None
        )
        assert (
            VerificationResultRepository(uow.connection).get(
                wrong_lineage_result.verification_result_id
            )
            is None
        )
        assert (
            VerificationResultRepository(uow.connection).get(
                ungrounded_result.verification_result_id
            )
            is None
        )


@pytest.mark.integration
def test_counterexample_revision_and_admission_rejections_preserve_authority(
    hypothesis_runtime: HypothesisRuntime,
) -> None:
    runtime = hypothesis_runtime
    v1, v1_receipt = _stage_hypothesis(runtime, 1)
    prepared = _prepare_checked_candidate(runtime, v1, v1_receipt)
    support = _record_admission_support(
        runtime,
        prepared,
        rollback=None,
        suffix="authority-branches",
    )
    admission = _admission_proposal(
        runtime,
        prepared,
        support,
        rollback=None,
        suffix="authority-branches",
    )

    decision = runtime.service.admit(
        admission.model_copy(
            update={
                "proposal_id": "admission-absent-audit-receipt",
                "idempotency_key": "intent-admission-absent-audit-receipt",
                "evaluator_audit_receipt": _placeholder_receipt(
                    EvaluatorAuditReceiptRef,
                    "absent-admission-audit",
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    decision = runtime.service.admit(
        admission.model_copy(
            update={
                "proposal_id": "admission-wrong-integrator",
                "idempotency_key": "intent-admission-wrong-integrator",
                "admission_decision": admission.admission_decision.model_copy(
                    update={"decided_by": _actor("wrong-admission-integrator")}
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH

    decision = runtime.service.admit(
        admission.model_copy(
            update={
                "proposal_id": "admission-wrong-decision-authority",
                "idempotency_key": "intent-admission-wrong-decision-authority",
                "approval": Approval(
                    approver=_actor("wrong-admission-decision-authority"),
                    approved_at=BASE,
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    assert runtime.service.admit(admission).accepted

    failed, failed_receipt, counterexample_receipt = _record_failed_counterexample(
        runtime,
        prepared,
    )
    with runtime.uow_factory() as uow:
        stored_counterexample = uow.repositories().transactions.get_by_proposal_id(
            counterexample_receipt.proposal_id
        )
    assert stored_counterexample is not None
    assert isinstance(stored_counterexample.proposal, RecordCounterexample)
    counterexample_template = stored_counterexample.proposal

    decision = runtime.service.record_counterexample(
        counterexample_template.model_copy(
            update={
                "proposal_id": "counterexample-wrong-actor",
                "idempotency_key": "intent-counterexample-wrong-actor",
                "proposer": _actor("wrong-counterexample-actor"),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH
    decision = runtime.service.record_counterexample(
        counterexample_template.model_copy(
            update={
                "proposal_id": "counterexample-absent-result",
                "idempotency_key": "intent-counterexample-absent-result",
                "verification_result_receipts": (
                    _placeholder_receipt(VerificationResultReceiptRef, "absent-failed-result"),
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    counterexample_from_pass = counterexample_template.counterexample.model_copy(
        update={
            "counterexample_id": "counterexample-from-passing-check",
            "verification_result_ids": (prepared.result.verification_result_id,),
        }
    )
    decision = runtime.service.record_counterexample(
        counterexample_template.model_copy(
            update={
                "proposal_id": "record-counterexample-from-passing-check",
                "idempotency_key": "intent-record-counterexample-from-passing-check",
                "verification_result_receipts": (prepared.result_receipt,),
                "counterexample": counterexample_from_pass,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    ungrounded_counterexample = counterexample_template.counterexample.model_copy(
        update={
            "counterexample_id": "ungrounded-counterexample",
            "evidence_ids": ("absent-counterexample-evidence",),
        }
    )
    decision = runtime.service.record_counterexample(
        counterexample_template.model_copy(
            update={
                "proposal_id": "record-ungrounded-counterexample",
                "idempotency_key": "intent-record-ungrounded-counterexample",
                "counterexample": ungrounded_counterexample,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE
    assert runtime.service.record_counterexample(
        counterexample_template.model_copy(
            update={
                "proposal_id": "record-duplicate-exact-counterexample",
                "idempotency_key": "intent-record-duplicate-exact-counterexample",
            }
        )
    ).accepted
    decision = runtime.service.record_counterexample(
        counterexample_template.model_copy(
            update={
                "proposal_id": "record-conflicting-counterexample",
                "idempotency_key": "intent-record-conflicting-counterexample",
                "counterexample": counterexample_template.counterexample.model_copy(
                    update={"description": "Changed content cannot reuse a counterexample key."}
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    v2 = _hypothesis(runtime, 2)
    revision = _revision(runtime, v1, v2, failed)
    revision_template = ReviseHypothesis(
        proposal_id="revision-authority-template",
        idempotency_key="intent-revision-authority-template",
        proposer=revision.author,
        approval=_approval(runtime, "revision-authority-approver"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        prior_hypothesis_receipt=v1_receipt,
        triggering_result_receipts=(failed_receipt,),
        counterexample_receipts=(counterexample_receipt,),
        resulting_hypothesis=v2,
        revision=revision,
    )
    decision = runtime.service.revise(
        revision_template.model_copy(
            update={
                "proposal_id": "revision-wrong-actor",
                "idempotency_key": "intent-revision-wrong-actor",
                "proposer": _actor("wrong-revision-actor"),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.ENTITY_ID_MISMATCH
    decision = runtime.service.revise(
        revision_template.model_copy(
            update={
                "proposal_id": "revision-absent-trigger",
                "idempotency_key": "intent-revision-absent-trigger",
                "triggering_result_receipts": (
                    _placeholder_receipt(VerificationResultReceiptRef, "absent-revision-trigger"),
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    pass_trigger_revision = revision.model_copy(
        update={
            "triggering_verification_result_ids": (prepared.result.verification_result_id,),
            "considered_counterexample_ids": (),
        }
    )
    decision = runtime.service.revise(
        revision_template.model_copy(
            update={
                "proposal_id": "revision-from-passing-check",
                "idempotency_key": "intent-revision-from-passing-check",
                "triggering_result_receipts": (prepared.result_receipt,),
                "counterexample_receipts": (),
                "revision": pass_trigger_revision,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    unchanged_v2 = v2.model_copy(
        update={
            "predictions": v1.predictions,
            "falsification_conditions": v1.falsification_conditions,
        }
    )
    unchanged_revision = revision.model_copy(
        update={
            "changed_predictions": unchanged_v2.predictions,
            "changed_falsification_conditions": unchanged_v2.falsification_conditions,
        }
    )
    decision = runtime.service.revise(
        revision_template.model_copy(
            update={
                "proposal_id": "revision-without-scientific-change",
                "idempotency_key": "intent-revision-without-scientific-change",
                "resulting_hypothesis": unchanged_v2,
                "revision": unchanged_revision,
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE

    assert runtime.service.revise(revision_template).accepted
    assert runtime.service.revise(
        revision_template.model_copy(
            update={
                "proposal_id": "revision-duplicate-exact",
                "idempotency_key": "intent-revision-duplicate-exact",
            }
        )
    ).accepted
    decision = runtime.service.revise(
        revision_template.model_copy(
            update={
                "proposal_id": "revision-conflicting",
                "idempotency_key": "intent-revision-conflicting",
                "revision": revision.model_copy(
                    update={"mechanism_changes": ("Conflicting retained mechanism change.",)}
                ),
            }
        )
    )
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT

    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        counterexamples = CounterexampleRecordRepository(uow.connection)
        revisions = HypothesisRevisionRepository(uow.connection)
        assert (
            counterexamples.get(counterexample_template.counterexample.counterexample_id)
            is not None
        )
        assert counterexamples.get(counterexample_from_pass.counterexample_id) is None
        assert counterexamples.get(ungrounded_counterexample.counterexample_id) is None
        assert revisions.get(revision.revision_id) is not None
        assert HypothesisHeadRepository(uow.connection).get(v1.hypothesis_id)[0] == (
            v1.hypothesis_version_id
        )  # type: ignore[index]


@dataclass(frozen=True)
class PreparedCandidate:
    hypothesis: HypothesisSpec
    hypothesis_receipt: HypothesisCandidateReceiptRef
    model: ExecutableModelSpec
    model_receipt: ModelSpecReceiptRef
    mechanism: DeterministicCheckerSpec
    mechanism_receipt: VerificationMechanismReceiptRef
    simulation_receipt: SimulationResultReceiptRef
    result: DeterministicCheckResult
    result_receipt: VerificationResultReceiptRef


@dataclass(frozen=True)
class AdmissionSupport:
    audit_receipt: EvaluatorAuditReceiptRef
    measurement_receipt: SelfImprovementMeasurementReceiptRef


def _stage_hypothesis(
    runtime: HypothesisRuntime,
    version: int,
) -> tuple[HypothesisSpec, HypothesisVersionReceiptRef]:
    hypothesis = _hypothesis(runtime, version)
    proposal = _hypothesis_proposal(runtime, hypothesis)
    assert runtime.service.propose(proposal).accepted
    return hypothesis, _receipt(runtime, proposal.proposal_id, HypothesisVersionReceiptRef)


def _prepare_checked_candidate(
    runtime: HypothesisRuntime,
    hypothesis: HypothesisSpec,
    receipt: HypothesisCandidateReceiptRef,
) -> PreparedCandidate:
    suffix = f"v{hypothesis.version}"
    model = _model(runtime, hypothesis, hypothesis.version)
    model_proposal = _model_proposal(runtime, receipt, model, suffix)
    assert runtime.service.register_model(model_proposal).accepted
    model_receipt = _receipt(runtime, model_proposal.proposal_id, ModelSpecReceiptRef)
    mechanism = _mechanism(runtime, hypothesis, suffix)
    mechanism_proposal = RegisterVerificationMechanism(
        proposal_id=f"register-mechanism-{suffix}",
        idempotency_key=f"intent-register-mechanism-{suffix}",
        proposer=mechanism.created_by,
        approval=_approval(runtime, f"mechanism-approver-{suffix}"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        mechanism_spec=mechanism,
    )
    assert runtime.service.register_verification_mechanism(mechanism_proposal).accepted
    mechanism_receipt = _receipt(
        runtime,
        mechanism_proposal.proposal_id,
        VerificationMechanismReceiptRef,
    )
    simulation = runtime.service.simulate(
        model,
        _thermal_input(model.deterministic_seed),
        simulation_result_id=f"simulation-{suffix}",
        output_id=f"output-{suffix}",
        governing_policy_hash=runtime.policy.policy_hash,
        completed_at=runtime.clock.current,
    )
    simulation_proposal = RecordSimulationResult(
        proposal_id=f"record-simulation-{suffix}",
        idempotency_key=f"intent-record-simulation-{suffix}",
        proposer=model.registered_by,
        approval=_approval(runtime, f"simulation-approver-{suffix}"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        model_receipt=model_receipt,
        simulation_result=simulation,
    )
    assert runtime.service.record_simulation(simulation_proposal).accepted
    simulation_receipt = _receipt(
        runtime,
        simulation_proposal.proposal_id,
        SimulationResultReceiptRef,
    )
    result = _result(runtime, hypothesis, model, mechanism, simulation.simulation_result_id, suffix)
    result_proposal = RecordVerificationResult(
        proposal_id=f"record-result-{suffix}",
        idempotency_key=f"intent-record-result-{suffix}",
        proposer=result.provenance.actor,
        approval=_approval(runtime, f"result-approver-{suffix}"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        mechanism_receipt=mechanism_receipt,
        model_receipt=model_receipt,
        simulation_receipts=(simulation_receipt,),
        verification_result=result,
    )
    assert runtime.service.record_verification(result_proposal).accepted
    return PreparedCandidate(
        hypothesis=hypothesis,
        hypothesis_receipt=receipt,
        model=model,
        model_receipt=model_receipt,
        mechanism=mechanism,
        mechanism_receipt=mechanism_receipt,
        simulation_receipt=simulation_receipt,
        result=result,
        result_receipt=_receipt(
            runtime,
            result_proposal.proposal_id,
            VerificationResultReceiptRef,
        ),
    )


def _record_failed_counterexample(
    runtime: HypothesisRuntime,
    prepared: PreparedCandidate,
) -> tuple[DeterministicCheckResult, VerificationResultReceiptRef, CounterexampleReceiptRef]:
    suffix = f"failed-v{prepared.hypothesis.version}"
    failed = _result(
        runtime,
        prepared.hypothesis,
        prepared.model,
        prepared.mechanism,
        prepared.result.simulation_result_ids[0],
        suffix,
    ).model_copy(
        update={
            "verification_result_id": f"verification-{suffix}",
            "outcome": VerificationOutcome.FAIL,
            "findings": ("A retained bounded input falsified the registered prediction.",),
            "provenance": _provenance(
                runtime,
                prepared.mechanism.mechanism_spec_id,
                outcome=AssessmentOutcome.FAILED,
            ),
            "counterexample_found": True,
        }
    )
    proposal = RecordVerificationResult(
        proposal_id=f"record-{suffix}",
        idempotency_key=f"intent-record-{suffix}",
        proposer=failed.provenance.actor,
        approval=_approval(runtime, f"{suffix}-approver"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=prepared.hypothesis_receipt,
        mechanism_receipt=prepared.mechanism_receipt,
        model_receipt=prepared.model_receipt,
        simulation_receipts=(prepared.simulation_receipt,),
        verification_result=failed,
    )
    assert runtime.service.record_verification(proposal).accepted
    result_receipt = _receipt(runtime, proposal.proposal_id, VerificationResultReceiptRef)
    counterexample = CounterexampleRecord(
        counterexample_id=f"counterexample-v{prepared.hypothesis.version}",
        hypothesis_version_id=prepared.hypothesis.hypothesis_version_id,
        model_spec_id=prepared.model.model_spec_id,
        simulation_result_ids=prepared.result.simulation_result_ids,
        verification_result_ids=(failed.verification_result_id,),
        evidence_ids=("hypothesis-evidence",),
        description="A retained bounded simulation contradicted the prediction.",
        input_hash=sha256_hex(b"counterexample-input"),
        observed_output_hash=sha256_hex(b"counterexample-observed"),
        expected_output_hash=sha256_hex(b"counterexample-expected"),
        discovered_by=runtime.checker,
        discovered_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    counterexample_proposal = RecordCounterexample(
        proposal_id=f"record-counterexample-v{prepared.hypothesis.version}",
        idempotency_key=f"intent-record-counterexample-v{prepared.hypothesis.version}",
        proposer=runtime.checker,
        approval=_approval(runtime, f"counterexample-approver-v{prepared.hypothesis.version}"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=prepared.hypothesis_receipt,
        model_receipt=prepared.model_receipt,
        simulation_receipts=(prepared.simulation_receipt,),
        verification_result_receipts=(result_receipt,),
        counterexample=counterexample,
    )
    assert runtime.service.record_counterexample(counterexample_proposal).accepted
    return (
        failed,
        result_receipt,
        _receipt(runtime, counterexample_proposal.proposal_id, CounterexampleReceiptRef),
    )


def _admit_candidate(
    runtime: HypothesisRuntime,
    prepared: PreparedCandidate,
    *,
    rollback: str | None,
) -> None:
    suffix = f"v{prepared.hypothesis.version}"
    support = _record_admission_support(runtime, prepared, rollback=rollback, suffix=suffix)
    decision = runtime.service.admit(
        _admission_proposal(runtime, prepared, support, rollback=rollback, suffix=suffix)
    )
    assert decision.accepted, decision.reasons


def _record_admission_support(
    runtime: HypothesisRuntime,
    prepared: PreparedCandidate,
    *,
    rollback: str | None,
    suffix: str,
) -> AdmissionSupport:
    run = base_run().model_copy(
        update={
            "run_id": f"hypothesis-run-{suffix}",
            "creator": runtime.integrator,
            "active_governance_policy_hash": runtime.policy.policy_hash,
        }
    )
    assert runtime.coordinator.submit(
        CreateResearchRun(
            proposal_id=f"create-hypothesis-run-{suffix}",
            idempotency_key=f"intent-create-hypothesis-run-{suffix}",
            proposer=runtime.integrator,
            approval=_approval(runtime, f"run-approver-{suffix}"),
            run=run,
        )
    ).accepted
    evaluator = _actor(f"hypothesis-evaluator-{suffix}", ActorKind.MODEL)
    auditor = _actor(f"hypothesis-auditor-{suffix}")
    audit = base_audit().model_copy(
        update={
            "evaluator_audit_id": f"hypothesis-audit-{suffix}",
            "auditor": auditor,
            "auditor_version": f"hypothesis-auditor-{suffix}-v1",
            "evaluator": evaluator,
            "evaluator_version": f"hypothesis-evaluator-{suffix}-v1",
            "proposer": runtime.author,
            "candidate_producer": runtime.author,
            "evidence_ids": ("hypothesis-evidence",),
            "checks_run": (prepared.result.verification_result_id,),
            "audited_at": runtime.clock.current,
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )
    audit_proposal = RecordEvaluatorAudit(
        proposal_id=f"record-hypothesis-audit-{suffix}",
        idempotency_key=f"intent-record-hypothesis-audit-{suffix}",
        proposer=auditor,
        approval=_approval(runtime, f"audit-approver-{suffix}"),
        evaluator_audit=audit,
    )
    assert runtime.coordinator.submit(audit_proposal).accepted
    base = base_measurement()
    measurement = base.model_copy(
        update={
            "measurement_id": f"hypothesis-measurement-{suffix}",
            "run_id": run.run_id,
            "classification": FIXED_HYPOTHESIS_CLASSIFICATION,
            "proposer": runtime.author,
            "evaluator": evaluator,
            "evaluator_version": audit.evaluator_version,
            "baseline_version_id": rollback or prepared.hypothesis.hypothesis_version_id,
            "candidate_version_id": prepared.hypothesis.hypothesis_version_id,
            "protected_metrics": tuple(
                item.model_copy(update={"source_id": "hypothesis-evidence"})
                for item in base.protected_metrics
            ),
            "countermetrics": tuple(
                item.model_copy(update={"source_id": "hypothesis-evidence"})
                for item in base.countermetrics
            ),
            "rollback_target_id": rollback or prepared.hypothesis.hypothesis_version_id,
            "evaluator_audit_id": audit.evaluator_audit_id,
            "decision_authority": runtime.approver,
            "decided_at": runtime.clock.current,
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )
    measurement_proposal = RecordSelfImprovementMeasurement(
        proposal_id=f"record-hypothesis-measurement-{suffix}",
        idempotency_key=f"intent-record-hypothesis-measurement-{suffix}",
        proposer=runtime.author,
        approval=Approval(approver=runtime.approver, approved_at=runtime.clock.current),
        measurement=measurement,
    )
    assert runtime.coordinator.submit(measurement_proposal).accepted
    return AdmissionSupport(
        audit_receipt=_receipt(runtime, audit_proposal.proposal_id, EvaluatorAuditReceiptRef),
        measurement_receipt=_receipt(
            runtime,
            measurement_proposal.proposal_id,
            SelfImprovementMeasurementReceiptRef,
        ),
    )


def _admission_proposal(
    runtime: HypothesisRuntime,
    prepared: PreparedCandidate,
    support: AdmissionSupport,
    *,
    rollback: str | None,
    suffix: str,
) -> AdmitHypothesis:
    revision_receipts = (
        (prepared.hypothesis_receipt,)
        if isinstance(prepared.hypothesis_receipt, HypothesisRevisionReceiptRef)
        else ()
    )
    decision = HypothesisAdmissionDecision(
        admission_decision_id=f"hypothesis-admission-{suffix}",
        hypothesis_version_id=prepared.hypothesis.hypothesis_version_id,
        hypothesis_id=prepared.hypothesis.hypothesis_id,
        version=prepared.hypothesis.version,
        imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
        model_spec_ids=(prepared.model.model_spec_id,),
        verification_result_ids=(prepared.result.verification_result_id,),
        counterexample_search_result_ids=(prepared.result.verification_result_id,),
        counterexample_ids=(),
        revision_ids=_revision_ids(runtime, revision_receipts),
        evaluator_audit_id=support.audit_receipt.proposal_id.replace("record-", ""),
        measurement_id=support.measurement_receipt.proposal_id.replace("record-", ""),
        rollback_hypothesis_version_id=rollback,
        outcome=AdmissionOutcome.ACCEPT,
        rationale="Exact retained deterministic, transfer, review, and policy gates passed.",
        decided_by=runtime.integrator,
        decided_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    return AdmitHypothesis(
        proposal_id=f"admit-hypothesis-{suffix}",
        idempotency_key=f"intent-admit-hypothesis-{suffix}",
        proposer=runtime.integrator,
        approval=Approval(approver=runtime.approver, approved_at=runtime.clock.current),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=prepared.hypothesis_receipt,
        model_receipts=(prepared.model_receipt,),
        verification_result_receipts=(prepared.result_receipt,),
        counterexample_search_receipts=(prepared.result_receipt,),
        revision_receipts=revision_receipts,
        evaluator_audit_receipt=support.audit_receipt,
        measurement_receipt=support.measurement_receipt,
        rollback_hypothesis_version_id=rollback,
        integrated_at=runtime.clock.current,
        admission_decision=decision,
    )


def _hypothesis(runtime: HypothesisRuntime, version: int) -> HypothesisSpec:
    return HypothesisSpec(
        hypothesis_version_id=f"thermal-hypothesis-v{version}",
        hypothesis_id="thermal-hypothesis",
        version=version,
        statement="Bounded heating exceeds calibrated cooling under registered conditions.",
        assumptions=("The source-controlled simulator receives complete numeric records.",),
        scope=("Bounded deterministic in-memory simulations.",),
        variables=("temperature", "ambient", "heater_delta", "cooling_rate"),
        predictions=(f"Version {version} predicts a bounded temperature rise.",),
        falsification_conditions=(
            f"Version {version} is falsified by a valid run with no predicted rise.",
        ),
        primitive_version_ids=(runtime.primitive_version_id,),
        evidence_ids=("hypothesis-evidence",),
        imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
        proposer=runtime.author,
        created_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _hypothesis_proposal(
    runtime: HypothesisRuntime,
    hypothesis: HypothesisSpec,
) -> ProposeHypothesisVersion:
    return ProposeHypothesisVersion(
        proposal_id=f"propose-{hypothesis.hypothesis_version_id}",
        idempotency_key=f"intent-propose-{hypothesis.hypothesis_version_id}",
        proposer=runtime.author,
        approval=_approval(runtime, f"hypothesis-stage-approver-v{hypothesis.version}"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis=hypothesis,
    )


def _model(
    runtime: HypothesisRuntime,
    hypothesis: HypothesisSpec,
    version: int,
) -> ExecutableModelSpec:
    return ExecutableModelSpec(
        model_spec_id=f"thermal-model-v{version}",
        hypothesis_version_id=hypothesis.hypothesis_version_id,
        model_type=ModelType.DETERMINISTIC_SIMULATOR,
        execution_mode=ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
        artifact_hash=None,
        artifact_media_type=None,
        artifact_size_bytes=None,
        artifact_name="source-controlled thermal simulator",
        builtin_simulator_id="thermal-chamber-v1",
        input_schema_id="thermal-chamber-input-v1",
        output_schema_id="thermal-chamber-output-v1",
        deterministic_seed=version,
        max_steps=10,
        max_state_bytes=4_096,
        registered_by=_actor(f"model-registrar-v{version}"),
        created_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _model_proposal(
    runtime: HypothesisRuntime,
    receipt: HypothesisCandidateReceiptRef,
    model: ExecutableModelSpec,
    suffix: str = "v1",
) -> RegisterExecutableModel:
    return RegisterExecutableModel(
        proposal_id=f"register-model-{suffix}",
        idempotency_key=f"intent-register-model-{suffix}",
        proposer=model.registered_by,
        approval=_approval(runtime, f"model-approver-{suffix}"),
        classification=FIXED_HYPOTHESIS_CLASSIFICATION,
        hypothesis_receipt=receipt,
        model_spec=model,
    )


def _mechanism(
    runtime: HypothesisRuntime,
    hypothesis: HypothesisSpec,
    suffix: str,
) -> DeterministicCheckerSpec:
    return DeterministicCheckerSpec(
        mechanism_type="DETERMINISTIC_CHECKER",
        mechanism_spec_id=f"thermal-checker-{suffix}",
        hypothesis_version_id=hypothesis.hypothesis_version_id,
        name="bounded thermal prediction and search checker",
        description="Checks registered predictions and bounded counterexample inputs.",
        specification_hash=sha256_hex(suffix.encode()),
        input_schema_id="thermal-chamber-output-v1",
        output_schema_id="verification-result-v1",
        created_by=runtime.checker,
        created_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
        checked_invariants=("registered-prediction", "bounded-counterexample-search"),
    )


def _result(
    runtime: HypothesisRuntime,
    hypothesis: HypothesisSpec,
    model: ExecutableModelSpec,
    mechanism: DeterministicCheckerSpec,
    simulation_id: str,
    suffix: str,
) -> DeterministicCheckResult:
    return DeterministicCheckResult(
        mechanism_type="DETERMINISTIC_CHECKER",
        verification_result_id=f"verification-{suffix}",
        hypothesis_version_id=hypothesis.hypothesis_version_id,
        mechanism_spec_id=mechanism.mechanism_spec_id,
        model_spec_id=model.model_spec_id,
        simulation_result_ids=(simulation_id,),
        outcome=VerificationOutcome.PASS,
        findings=("Registered predictions passed and no bounded counterexample was found.",),
        provenance=_provenance(runtime, mechanism.mechanism_spec_id),
        counterexample_search_performed=True,
        counterexample_found=False,
        checked_invariants=mechanism.checked_invariants,
    )


def _provenance(
    runtime: HypothesisRuntime,
    check_id: str,
    *,
    outcome: AssessmentOutcome = AssessmentOutcome.PASSED,
) -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=runtime.checker,
        actor_version="hypothesis-checker-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("Registered schemas and bounds are authoritative inputs.",),
        evidence_ids=("hypothesis-evidence",),
        checks_run=(check_id,),
        limitations=("Bounded source-controlled fixture coverage only.",),
        result=outcome,
        meaningful_confidence=None,
        assessed_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _revision(
    runtime: HypothesisRuntime,
    prior: HypothesisSpec,
    resulting: HypothesisSpec,
    failed: DeterministicCheckResult,
) -> RevisionRecord:
    return RevisionRecord(
        revision_id="thermal-revision-v2",
        hypothesis_id=prior.hypothesis_id,
        prior_hypothesis_version_id=prior.hypothesis_version_id,
        prior_version=prior.version,
        resulting_hypothesis_version_id=resulting.hypothesis_version_id,
        resulting_version=resulting.version,
        triggering_verification_result_ids=(failed.verification_result_id,),
        considered_counterexample_ids=("counterexample-v1",),
        assumptions_added=("Cooling calibration is retained before each run.",),
        assumptions_removed=(),
        assumptions_changed=(),
        variables_added=("calibration",),
        variables_removed=(),
        variables_changed=(),
        mechanism_changes=("Added calibration to the bounded mechanism.",),
        preserved_elements=("In-memory bounded deterministic transition.",),
        changed_predictions=resulting.predictions,
        changed_falsification_conditions=resulting.falsification_conditions,
        author=_actor("hypothesis-revision-author"),
        revised_at=runtime.clock.current,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _insert_unlogged_hypothesis_scope(
    runtime: HypothesisRuntime,
    family: str,
) -> None:
    suffix = f"unlogged-{family}"
    retained_at = runtime.clock.current
    hypothesis = _hypothesis(runtime, 1).model_copy(
        update={
            "hypothesis_version_id": f"{suffix}-hypothesis-v1",
            "hypothesis_id": f"{suffix}-hypothesis",
            "created_at": retained_at,
        }
    )
    hypothesis_record = hypothesis_to_storage(hypothesis)
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        versions = HypothesisVersionRepository(uow.connection)
        versions.add(
            hypothesis_record.hypothesis_version_id,
            hypothesis_record,
            hypothesis_record.created_at,
        )
        if family == "versions":
            return

        model = _model(runtime, hypothesis, 1).model_copy(
            update={
                "model_spec_id": f"{suffix}-model",
                "created_at": retained_at,
            }
        )
        model_record = model_to_storage(model)
        ExecutableModelSpecRepository(uow.connection).add(
            model_record.model_spec_id,
            model_record,
            model_record.created_at,
        )
        if family == "models":
            return

        mechanism = _mechanism(runtime, hypothesis, suffix).model_copy(
            update={
                "mechanism_spec_id": f"{suffix}-mechanism",
                "created_at": retained_at,
            }
        )
        mechanism_record = mechanism_to_storage(mechanism)
        VerificationMechanismSpecRepository(uow.connection).add(
            mechanism_record.mechanism_spec_id,
            mechanism_record,
            mechanism_record.created_at,
        )
        if family == "mechanisms":
            return

        simulation = runtime.service.simulate(
            model,
            _thermal_input(model.deterministic_seed),
            simulation_result_id=f"{suffix}-simulation",
            output_id=f"{suffix}-output",
            governing_policy_hash=runtime.policy.policy_hash,
            completed_at=retained_at,
        ).model_copy(update={"completed_at": retained_at})
        simulation_record = simulation_to_storage(simulation)
        SimulationResultRepository(uow.connection).add(
            simulation_record.simulation_result_id,
            simulation_record,
            simulation_record.completed_at,
        )
        if family == "simulations":
            return

        result = _result(
            runtime,
            hypothesis,
            model,
            mechanism,
            simulation.simulation_result_id,
            suffix,
        ).model_copy(
            update={
                "provenance": _provenance(
                    runtime,
                    mechanism.mechanism_spec_id,
                    outcome=AssessmentOutcome.FAILED,
                ).model_copy(update={"assessed_at": retained_at}),
                "outcome": VerificationOutcome.FAIL,
                "counterexample_found": True,
            }
        )
        result_record = verification_to_storage(result, model_record)
        VerificationResultRepository(uow.connection).add(
            result_record.verification_result_id,
            result_record,
            result_record.completed_at,
        )
        if family == "results":
            return

        counterexample = CounterexampleRecord(
            counterexample_id=f"{suffix}-counterexample",
            hypothesis_version_id=hypothesis.hypothesis_version_id,
            model_spec_id=model.model_spec_id,
            simulation_result_ids=(simulation.simulation_result_id,),
            verification_result_ids=(result.verification_result_id,),
            evidence_ids=("hypothesis-evidence",),
            description="An unlogged repository mutation must never evade replay.",
            input_hash=sha256_hex(f"{suffix}-input".encode()),
            observed_output_hash=sha256_hex(f"{suffix}-observed".encode()),
            expected_output_hash=sha256_hex(f"{suffix}-expected".encode()),
            discovered_by=runtime.checker,
            discovered_at=retained_at,
            governing_policy_hash=runtime.policy.policy_hash,
        )
        counterexample_record = counterexample_to_storage(counterexample, model_record)
        CounterexampleRecordRepository(uow.connection).add(
            counterexample_record.counterexample_id,
            counterexample_record,
            counterexample_record.discovered_at,
        )
        if family == "counterexamples":
            return

        resulting = _hypothesis(runtime, 2).model_copy(
            update={
                "hypothesis_version_id": f"{suffix}-hypothesis-v2",
                "hypothesis_id": hypothesis.hypothesis_id,
                "created_at": retained_at,
            }
        )
        resulting_record = hypothesis_to_storage(resulting)
        versions.add(
            resulting_record.hypothesis_version_id,
            resulting_record,
            resulting_record.created_at,
        )
        revision = _revision(runtime, hypothesis, resulting, result).model_copy(
            update={
                "revision_id": f"{suffix}-revision",
                "considered_counterexample_ids": (counterexample.counterexample_id,),
                "revised_at": retained_at,
            }
        )
        revision_record = revision_to_storage(revision)
        HypothesisRevisionRepository(uow.connection).add(
            revision_record.revision_id,
            revision_record,
            revision_record.revised_at,
        )
        if family == "revisions":
            return

        admission = HypothesisAdmissionDecision(
            admission_decision_id=f"{suffix}-admission",
            hypothesis_version_id=resulting.hypothesis_version_id,
            hypothesis_id=resulting.hypothesis_id,
            version=resulting.version,
            imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
            model_spec_ids=(model.model_spec_id,),
            verification_result_ids=(result.verification_result_id,),
            counterexample_search_result_ids=(result.verification_result_id,),
            counterexample_ids=(counterexample.counterexample_id,),
            revision_ids=(revision.revision_id,),
            evaluator_audit_id=f"{suffix}-audit",
            measurement_id=f"{suffix}-measurement",
            rollback_hypothesis_version_id=hypothesis.hypothesis_version_id,
            outcome=AdmissionOutcome.ACCEPT,
            rationale="Direct unlogged state must be reconciled as a complete snapshot.",
            decided_by=runtime.integrator,
            decided_at=retained_at,
            governing_policy_hash=runtime.policy.policy_hash,
        )
        admission_record = admission_to_storage(admission)
        HypothesisAdmissionDecisionRepository(uow.connection).add(
            admission_record.admission_decision_id,
            admission_record,
            admission_record.decided_at,
        )
        if family == "admissions":
            return

        HypothesisHeadRepository(uow.connection).set(
            resulting.hypothesis_id,
            resulting.hypothesis_version_id,
            resulting.version,
            admission_record.admission_status,
        )


def _insert_unlogged_model_independent_verification_graph(
    runtime: HypothesisRuntime,
) -> None:
    retained_at = runtime.clock.current
    hypothesis = _hypothesis(runtime, 1).model_copy(
        update={
            "hypothesis_version_id": "unlogged-model-independent-hypothesis-v1",
            "hypothesis_id": "unlogged-model-independent-hypothesis",
            "created_at": retained_at,
        }
    )
    mechanism = _mechanism(runtime, hypothesis, "unlogged-model-independent").model_copy(
        update={"created_at": retained_at}
    )
    result = DeterministicCheckResult(
        mechanism_type="DETERMINISTIC_CHECKER",
        verification_result_id="unlogged-model-independent-result",
        hypothesis_version_id=hypothesis.hypothesis_version_id,
        mechanism_spec_id=mechanism.mechanism_spec_id,
        model_spec_id=None,
        simulation_result_ids=(),
        outcome=VerificationOutcome.PASS,
        findings=("An unlogged model-independent check must not acquire Task 11 ownership.",),
        provenance=_provenance(runtime, mechanism.mechanism_spec_id).model_copy(
            update={"assessed_at": retained_at}
        ),
        counterexample_search_performed=True,
        counterexample_found=False,
        checked_invariants=mechanism.checked_invariants,
    )
    hypothesis_record = hypothesis_to_storage(hypothesis)
    mechanism_record = mechanism_to_storage(mechanism)
    result_record = verification_to_storage(result, None)
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        HypothesisVersionRepository(uow.connection).add(
            hypothesis_record.hypothesis_version_id,
            hypothesis_record,
            hypothesis_record.created_at,
        )
        VerificationMechanismSpecRepository(uow.connection).add(
            mechanism_record.mechanism_spec_id,
            mechanism_record,
            mechanism_record.created_at,
        )
        VerificationResultRepository(uow.connection).add(
            result_record.verification_result_id,
            result_record,
            result_record.completed_at,
        )


def _thermal_input(seed: int) -> ModelInput:
    return ModelInput(
        model_input_id=f"thermal-input-{seed}",
        schema_id="thermal-chamber-input-v1",
        values=(
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=5.0),
            NumericField(name="cooling_rate", value=0.1),
            NumericField(name="steps", value=3),
        ),
        deterministic_seed=seed,
    )


def _receipt[RefT: AcceptedHypothesisReceiptRef](
    runtime: HypothesisRuntime,
    proposal_id: str,
    receipt_type: type[RefT],
) -> RefT:
    with runtime.uow_factory() as uow:
        transaction = uow.repositories().transactions.get_by_proposal_id(proposal_id)
        assert transaction is not None and transaction.decision.accepted
        events = tuple(
            event
            for event in uow.repositories().audit.list_all()
            if json_compatible_payload(event.payload).get("proposal", {}).get("proposal_id")
            == proposal_id
        )
    assert len(events) == 1
    event = events[0]
    return receipt_type(
        proposal_id=proposal_id,
        proposal_hash=transaction.proposal_hash,
        audit_event_id=event.event_id,
        audit_event_hash=event.event_hash,
    )


def _placeholder_receipt[RefT: AcceptedHypothesisReceiptRef](
    receipt_type: type[RefT],
    suffix: str,
) -> RefT:
    return receipt_type(
        proposal_id=f"placeholder-{suffix}",
        proposal_hash=sha256_hex(f"proposal-{suffix}".encode()),
        audit_event_id=f"placeholder-event-{suffix}",
        audit_event_hash=sha256_hex(f"event-{suffix}".encode()),
    )


def _revision_ids(
    runtime: HypothesisRuntime,
    receipts: tuple[HypothesisRevisionReceiptRef, ...],
) -> tuple[str, ...]:
    revision_ids: list[str] = []
    with runtime.uow_factory() as uow:
        for receipt in receipts:
            transaction = uow.repositories().transactions.get_by_proposal_id(receipt.proposal_id)
            assert transaction is not None
            assert isinstance(transaction.proposal, ReviseHypothesis)
            revision_ids.append(transaction.proposal.revision.revision_id)
    return tuple(revision_ids)


def _seed_evidence(runtime: HypothesisRuntime, evidence_id: str) -> None:
    content = b"immutable hypothesis evaluation evidence"
    artifact = runtime.artifacts.put(content, "application/octet-stream")
    proposal = AddEvidence(
        proposal_id=f"add-{evidence_id}",
        idempotency_key=f"intent-add-{evidence_id}",
        proposer=runtime.integrator,
        evidence=EvidenceRecord(
            evidence_id=evidence_id,
            evidence_type="controlled-experiment-observation",
            source_locator=f"fixture://{evidence_id}",
            retrieved_at=BASE,
            artifact=artifact,
            provenance={
                "collector": "task-12-integration",
                "external_grounding": ExternalGrounding.CONTROLLED_EXPERIMENT.value,
            },
            ingestion_actor_id=runtime.integrator.actor_id,
            verification_state=VerificationState.UNVERIFIED,
        ),
    )
    assert runtime.coordinator.submit(proposal).accepted


def _approval(runtime: HypothesisRuntime, identifier: str) -> Approval:
    return Approval(approver=_actor(identifier), approved_at=runtime.clock.current)


def _actor(identifier: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    return ActorIdentity(
        actor_id=identifier,
        kind=kind,
        provider_id=f"provider-{identifier}" if kind is ActorKind.MODEL else None,
        model_id=f"model-{identifier}" if kind is ActorKind.MODEL else None,
        adapter_id=f"adapter-{identifier}" if kind is ActorKind.MODEL else None,
        configuration_hash=sha256_hex(identifier.encode()) if kind is ActorKind.MODEL else None,
        created_at=BASE,
    )


def _policy() -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset(
                    {ExternalGrounding.CONTROLLED_EXPERIMENT, ExternalGrounding.HUMAN_JUDGMENT}
                ),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
            AdaptationRequirement(
                change_target=ChangeTarget.SKILL,
                persistence=PersistenceScope.PERSISTENT_SKILL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )


def _public_methods(value: object) -> set[str]:
    return {
        name for name in dir(value) if not name.startswith("_") and callable(getattr(value, name))
    }
