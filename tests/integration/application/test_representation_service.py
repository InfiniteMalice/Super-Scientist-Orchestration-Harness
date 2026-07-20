from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, update

from super_scientist.application.representations.service import (
    FIXED_PRIMITIVE_CLASSIFICATION,
    RepresentationService,
    primitive_use_rejection,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.transactions.representations import (
    fixed_representation_handlers,
    representation_capabilities,
    stored_primitive_resolver,
)
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
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
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.representations.models import (
    AcceptedPrimitiveReceiptRef,
    EvaluatorAuditReceiptRef,
    NewFrameEvaluation,
    OldFrameEvaluation,
    PrimitiveEvaluation,
    PrimitiveEvaluationReceiptRef,
    PrimitiveStatus,
    PrimitiveUse,
    PrimitiveVersion,
    PrimitiveVersionReceiptRef,
    SelfImprovementMeasurementReceiptRef,
    TransformationKind,
)
from super_scientist.kernel.audit.models import json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AdmitPrimitiveVersion,
    Approval,
    CreateResearchRun,
    ProposePrimitiveVersion,
    RecordEvaluatorAudit,
    RecordPrimitiveEvaluation,
    RecordSelfImprovementMeasurement,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    HypothesisAdmissionStatus,
    HypothesisVersionRecord,
    HypothesisVersionRepository,
    PrimitiveEvaluationRepository,
    PrimitiveHeadRepository,
    PrimitiveVersionRepository,
    VerificationMechanismCategory,
    VerificationMechanismSpecRecord,
    VerificationMechanismSpecRepository,
    VerificationOutcome,
    VerificationResultCategory,
    VerificationResultRecord,
    VerificationResultRepository,
)
from super_scientist.providers.storage.repositories import PolicyRepository
from super_scientist.providers.storage.schema import primitive_heads, primitive_versions
from tests.integration.application.test_adaptation_foundation import (
    _audit as base_audit,
)
from tests.integration.application.test_adaptation_foundation import (
    _measurement as base_measurement,
)
from tests.integration.application.test_adaptation_foundation import _run as base_run

BASE = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class SettableClock:
    def __init__(self) -> None:
        self.current = BASE

    def now(self):  # type: ignore[no-untyped-def]
        value = self.current
        self.current += timedelta(seconds=1)
        return value

    def advance_to(self, value: datetime) -> None:
        assert value >= self.current
        self.current = value


def _actor(actor_id: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=kind,
        provider_id=f"provider-{actor_id}" if kind is ActorKind.MODEL else None,
        model_id=f"model-{actor_id}" if kind is ActorKind.MODEL else None,
        adapter_id=f"adapter-{actor_id}" if kind is ActorKind.MODEL else None,
        configuration_hash=(
            sha256_hex(f"configuration-{actor_id}".encode()) if kind is ActorKind.MODEL else None
        ),
        created_at=BASE,
    )


@dataclass(frozen=True)
class RepresentationRuntime:
    engine: Engine
    uow_factory: Callable[[], DatabaseUnitOfWork]
    policy: PolicySnapshot
    coordinator: TransactionCoordinator
    service: RepresentationService
    artifacts: FileArtifactStore
    clock: SettableClock
    primitive_author: ActorIdentity
    stage_approver: ActorIdentity
    integrator: ActorIdentity
    admission_approver: ActorIdentity


@pytest.fixture
def representation_runtime(tmp_path: Path) -> Iterator[RepresentationRuntime]:
    policy = _policy()
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    database_url = f"sqlite:///{(tmp_path / 'representations.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        PolicyRepository(connection).add_and_activate(snapshot, BASE)
    clock = SettableClock()
    artifacts = FileArtifactStore(tmp_path / "artifacts")

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    coordinator = TransactionCoordinator(uow_factory, snapshot, clock, artifacts)
    runtime = RepresentationRuntime(
        engine=engine,
        uow_factory=uow_factory,
        policy=snapshot,
        coordinator=coordinator,
        service=RepresentationService(coordinator),
        artifacts=artifacts,
        clock=clock,
        primitive_author=_actor("primitive-author", ActorKind.MODEL),
        stage_approver=_actor("stage-approver"),
        integrator=_actor("primitive-integrator"),
        admission_approver=_actor("admission-approver"),
    )
    try:
        yield runtime
    finally:
        engine.dispose()


def test_representation_handlers_are_fixed_and_focused() -> None:
    handlers = fixed_representation_handlers()

    assert tuple(handler.proposal_type for handler in handlers) == (
        "propose_primitive_version",
        "record_primitive_evaluation",
        "admit_primitive_version",
    )
    assert FIXED_PRIMITIVE_CLASSIFICATION.target is ChangeTarget.SKILL
    assert FIXED_PRIMITIVE_CLASSIFICATION.loop_closure is LoopClosure.HUMAN_IN_LOOP
    assert FIXED_PRIMITIVE_CLASSIFICATION.persistence is PersistenceScope.PERSISTENT_SKILL
    assert (
        FIXED_PRIMITIVE_CLASSIFICATION.verification_level
        is VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
    )
    assert FIXED_PRIMITIVE_CLASSIFICATION.grounding is ExternalGrounding.CONTROLLED_EXPERIMENT
    assert FIXED_PRIMITIVE_CLASSIFICATION.signal is ImprovementSignal.EMPIRICAL_MEASUREMENT


@pytest.mark.integration
def test_live_handler_capabilities_expose_only_role_specific_authority(
    representation_runtime: RepresentationRuntime,
) -> None:
    runtime = representation_runtime
    candidate = _primitive(runtime, "capability-version", "capability-primitive")
    stage = _stage_proposal(runtime, candidate, "capability-stage")
    candidate_receipt = PrimitiveVersionReceiptRef(
        proposal_id=stage.proposal_id,
        proposal_hash="1" * 64,
        audit_event_id="capability-stage-audit",
        audit_event_hash="2" * 64,
    )
    evaluation = _evaluation(runtime, candidate, "old", BASE + timedelta(seconds=15))
    evaluate = _evaluation_proposal(runtime, candidate_receipt, evaluation)
    admit = AdmitPrimitiveVersion(
        proposal_id="capability-admission",
        idempotency_key="intent-capability-admission",
        proposer=runtime.integrator,
        approval=Approval(
            approver=runtime.admission_approver,
            approved_at=BASE + timedelta(seconds=31),
        ),
        classification=FIXED_PRIMITIVE_CLASSIFICATION,
        candidate_receipt=candidate_receipt,
        old_frame_evaluation_receipt=PrimitiveEvaluationReceiptRef(
            proposal_id="capability-old-evaluation",
            proposal_hash="3" * 64,
            audit_event_id="capability-old-audit",
            audit_event_hash="4" * 64,
        ),
        new_frame_evaluation_receipt=PrimitiveEvaluationReceiptRef(
            proposal_id="capability-new-evaluation",
            proposal_hash="5" * 64,
            audit_event_id="capability-new-audit",
            audit_event_hash="6" * 64,
        ),
        evaluator_audit_receipt=EvaluatorAuditReceiptRef(
            proposal_id="capability-evaluator-audit",
            proposal_hash="7" * 64,
            audit_event_id="capability-evaluator-audit-event",
            audit_event_hash="8" * 64,
        ),
        measurement_receipt=SelfImprovementMeasurementReceiptRef(
            proposal_id="capability-measurement",
            proposal_hash="9" * 64,
            audit_event_id="capability-measurement-audit",
            audit_event_hash="a" * 64,
        ),
        rollback_primitive_version_id="capability-rollback",
        integrated_at=BASE + timedelta(seconds=30),
    )
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        capability_sets = (
            (
                representation_capabilities(
                    stage,
                    uow.connection,
                    runtime.policy,
                    runtime.artifacts,
                ),
                {"append_version"},
            ),
            (
                representation_capabilities(
                    evaluate,
                    uow.connection,
                    runtime.policy,
                    runtime.artifacts,
                ),
                {"append_evaluation"},
            ),
            (
                representation_capabilities(
                    admit,
                    uow.connection,
                    runtime.policy,
                    runtime.artifacts,
                ),
                {"set_head_from_candidate_receipt"},
            ),
        )

        for capabilities, expected_writer_methods in capability_sets:
            reads = getattr(capabilities, "reads", None)
            writes = getattr(capabilities, "writes", None)
            assert reads is not None
            assert writes is not None
            assert _public_callable_names(writes) == expected_writer_methods
            for item in (*_public_object_graph(reads), *_public_object_graph(writes)):
                assert not ({"add", "set", "add_and_activate"} & _public_callable_names(item))


def _public_callable_names(value: object) -> set[str]:
    return {
        name
        for name in dir(value)
        if not name.startswith("_") and callable(getattr(value, name, None))
    }


def _public_object_graph(root: object) -> tuple[object, ...]:
    pending = [root]
    seen: set[int] = set()
    values: list[object] = []
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, BaseModel):
            continue
        values.append(value)
        if is_dataclass(value) and not isinstance(value, type):
            pending.extend(
                getattr(value, field.name)
                for field in fields(value)
                if not field.name.startswith("_")
            )
            continue
        attributes = getattr(value, "__dict__", {})
        if isinstance(attributes, Mapping):
            pending.extend(item for name, item in attributes.items() if not name.startswith("_"))
    return tuple(values)


@pytest.mark.integration
def test_staging_retains_history_and_derives_duplicate_suspected_status(
    representation_runtime: RepresentationRuntime,
) -> None:
    original = _primitive(representation_runtime, "primitive-gradient-v0", "primitive-gradient")
    duplicate = _primitive(
        representation_runtime,
        "primitive-gradient-copy-v0",
        "primitive-gradient-copy",
    )

    assert representation_runtime.service.propose(
        _stage_proposal(representation_runtime, original, "stage-original")
    ).accepted
    assert representation_runtime.service.propose(
        _stage_proposal(representation_runtime, duplicate, "stage-duplicate")
    ).accepted

    with representation_runtime.uow_factory() as uow:
        assert uow.connection is not None
        versions = PrimitiveVersionRepository(uow.connection).list_all()
        by_id = {record.primitive_version_id: record for record in versions}
        assert set(by_id) == {
            original.primitive_version_id,
            duplicate.primitive_version_id,
        }
        assert by_id[original.primitive_version_id].status.value == (
            PrimitiveStatus.EXPERIMENTAL.value
        )
        assert by_id[duplicate.primitive_version_id].status.value == (
            PrimitiveStatus.DUPLICATE_SUSPECTED.value
        )
        assert PrimitiveHeadRepository(uow.connection).list_all() == ()


@pytest.mark.integration
def test_exact_stage_resubmission_is_stable_after_later_duplicate_detection(
    representation_runtime: RepresentationRuntime,
) -> None:
    runtime = representation_runtime
    original = _primitive(runtime, "primitive-gradient-v0", "primitive-gradient")
    duplicate = _primitive(
        runtime,
        "primitive-gradient-copy-v0",
        "primitive-gradient-copy",
    )
    assert runtime.service.propose(_stage_proposal(runtime, original, "stage-original")).accepted
    assert runtime.service.propose(_stage_proposal(runtime, duplicate, "stage-duplicate")).accepted

    decision = runtime.service.propose(
        _stage_proposal(runtime, original, "stage-original-exact-resubmission")
    )

    assert decision.accepted is True
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        records = PrimitiveVersionRepository(uow.connection).list_all()
        assert len(records) == 2
        original_record = next(
            item for item in records if item.primitive_version_id == original.primitive_version_id
        )
        assert original_record.status.value == PrimitiveStatus.EXPERIMENTAL.value
        verification = verify_workspace(uow.repositories(), runtime.artifacts)
        assert verification.valid, verification.reason


@pytest.mark.integration
def test_changed_content_under_stable_primitive_version_key_is_audited_conflict(
    representation_runtime: RepresentationRuntime,
) -> None:
    original = _primitive(representation_runtime, "primitive-gradient-v0", "primitive-gradient")
    assert representation_runtime.service.propose(
        _stage_proposal(representation_runtime, original, "stage-original")
    ).accepted
    changed = original.model_copy(update={"motivation": "Changed content under a stable key."})

    decision = representation_runtime.service.propose(
        _stage_proposal(representation_runtime, changed, "stage-conflict")
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    with representation_runtime.uow_factory() as uow:
        audit = uow.repositories().audit.list_all()
        assert len(audit) == 2
        assert json_compatible_payload(audit[-1].payload)["decision"]["reasons"][0]["code"] == (
            RejectionCode.IDEMPOTENCY_CONFLICT.value
        )


@pytest.mark.integration
def test_exact_resubmission_does_not_reorder_staged_lineage(
    representation_runtime: RepresentationRuntime,
) -> None:
    runtime = representation_runtime
    original = _primitive(runtime, "primitive-lineage-v0", "primitive-lineage")
    successor = _primitive(
        runtime,
        "primitive-lineage-v1",
        "primitive-lineage",
        semantic_version="0.1.1",
        predecessors=(original.primitive_version_id,),
        created_at=BASE + timedelta(seconds=8),
    )
    next_successor = _primitive(
        runtime,
        "primitive-lineage-v2",
        "primitive-lineage",
        semantic_version="0.1.2",
        predecessors=(successor.primitive_version_id,),
        created_at=BASE + timedelta(seconds=13),
    )

    assert runtime.service.propose(
        _stage_proposal(runtime, original, "stage-lineage-original")
    ).accepted
    assert runtime.service.propose(
        _stage_proposal(runtime, successor, "stage-lineage-successor")
    ).accepted
    assert runtime.service.propose(
        _stage_proposal(runtime, original, "stage-lineage-original-resubmission")
    ).accepted

    decision = runtime.service.propose(
        _stage_proposal(runtime, next_successor, "stage-lineage-next-successor")
    )

    assert decision.accepted is True
    with runtime.uow_factory() as uow:
        verification = verify_workspace(uow.repositories(), runtime.artifacts)
        assert verification.valid, verification.reason


@pytest.mark.integration
def test_staging_enforces_fixed_classification_and_semantic_meaning_lineage(
    representation_runtime: RepresentationRuntime,
) -> None:
    runtime = representation_runtime
    original = _primitive(runtime, "primitive-lineage-v0", "primitive-lineage")
    assert runtime.service.propose(
        _stage_proposal(runtime, original, "stage-lineage-original")
    ).accepted
    wrong_classification = _stage_proposal(
        runtime,
        _primitive(runtime, "primitive-other-v0", "primitive-other"),
        "stage-wrong-classification",
    ).model_copy(
        update={
            "classification": FIXED_PRIMITIVE_CLASSIFICATION.model_copy(
                update={"grounding": ExternalGrounding.PRIMARY_SOURCE}
            )
        }
    )
    incompatible_minor = _primitive(
        runtime,
        "primitive-lineage-v1",
        "primitive-lineage",
        semantic_version="0.2.0",
        predecessors=(original.primitive_version_id,),
        created_at=BASE + timedelta(seconds=8),
    ).model_copy(update={"definition": "An incompatible causal sufficiency relation."})

    classification_decision = runtime.service.propose(wrong_classification)
    lineage_decision = runtime.service.propose(
        _stage_proposal(runtime, incompatible_minor, "stage-incompatible-minor")
    )

    assert classification_decision.accepted is False
    assert classification_decision.reasons[0].code is RejectionCode.PERMISSION_DENIED
    assert lineage_decision.accepted is False
    assert lineage_decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    assert "INCOMPATIBLE_MEANING_REQUIRES_MAJOR" in lineage_decision.reasons[0].message


@pytest.mark.integration
def test_full_two_frame_promotion_sets_only_exact_admitted_head_and_replays(
    representation_runtime: RepresentationRuntime,
) -> None:
    prepared = _prepare_evaluated_candidate(representation_runtime)
    support = _record_promotion_support(representation_runtime, prepared)
    proposal = _admission_proposal(representation_runtime, prepared, support)

    decision = representation_runtime.service.admit(proposal)

    assert decision.accepted is True
    with representation_runtime.uow_factory() as uow:
        assert uow.connection is not None
        head = PrimitiveHeadRepository(uow.connection).get(prepared.candidate.primitive_id)
        assert head == (
            prepared.candidate.primitive_version_id,
            prepared.candidate.semantic_version,
            # storage enum is intentionally compared by value below
            head[2],
        )
        assert head[2].value == prepared.candidate.status.value
        assert (
            primitive_use_rejection(
                prepared.candidate.primitive_version_id,
                resolver=stored_primitive_resolver(uow.connection),
                use=PrimitiveUse.PUBLIC_CONCLUSION,
            )
            is None
        )
        stored_evaluations = PrimitiveEvaluationRepository(uow.connection).list_all()
        assert {item.primitive_evaluation_id for item in stored_evaluations} == {
            prepared.old_evaluation.primitive_evaluation_id,
            prepared.new_evaluation.primitive_evaluation_id,
        }
        verification = verify_workspace(uow.repositories(), representation_runtime.artifacts)
        assert verification.valid, verification.reason


@pytest.mark.integration
def test_promotion_requirements_are_not_imposed_on_staging_but_fail_closed_at_admission(
    representation_runtime: RepresentationRuntime,
) -> None:
    prepared = _prepare_evaluated_candidate(representation_runtime)
    incomplete = AdmitPrimitiveVersion(
        proposal_id="admit-without-support",
        idempotency_key="intent-admit-without-support",
        proposer=representation_runtime.integrator,
        approval=Approval(
            approver=representation_runtime.admission_approver,
            approved_at=BASE + timedelta(seconds=40),
        ),
        classification=FIXED_PRIMITIVE_CLASSIFICATION,
        candidate_receipt=prepared.candidate_receipt,
        old_frame_evaluation_receipt=prepared.old_receipt,
        new_frame_evaluation_receipt=prepared.new_receipt,
        evaluator_audit_receipt=None,
        measurement_receipt=None,
        rollback_primitive_version_id=prepared.rollback.primitive_version_id,
        integrated_at=BASE + timedelta(seconds=39),
    )

    decision = representation_runtime.service.admit(incomplete)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    with representation_runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert PrimitiveHeadRepository(uow.connection).get(prepared.candidate.primitive_id) is None


@pytest.mark.integration
def test_live_evaluation_rejects_candidate_author_as_evaluator(
    representation_runtime: RepresentationRuntime,
) -> None:
    runtime = representation_runtime
    _seed_evidence(runtime, "evidence-old")
    candidate = _primitive(runtime, "primitive-circular-v0", "primitive-circular")
    stage = _stage_proposal(runtime, candidate, "stage-circular-candidate")
    assert runtime.service.propose(stage).accepted
    candidate_receipt = _receipt(runtime, stage.proposal_id, PrimitiveVersionReceiptRef)
    _seed_verification_result(
        runtime,
        candidate,
        "old",
        "evidence-old",
        completed_at=BASE + timedelta(seconds=14),
    )
    evaluation = _evaluation(runtime, candidate, "old", BASE + timedelta(seconds=15))
    circular = evaluation.model_copy(
        update={
            "provenance": evaluation.provenance.model_copy(update={"actor": candidate.proposer})
        }
    )

    decision = runtime.service.evaluate(_evaluation_proposal(runtime, candidate_receipt, circular))

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL


@pytest.mark.integration
def test_admission_rejects_cross_frame_evaluator_reuse(
    representation_runtime: RepresentationRuntime,
) -> None:
    prepared = _prepare_evaluated_candidate(
        representation_runtime,
        new_evaluator=_actor("old-evaluator", ActorKind.MODEL),
    )
    support = _record_promotion_support(representation_runtime, prepared)

    decision = representation_runtime.service.admit(
        _admission_proposal(representation_runtime, prepared, support)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL


@pytest.mark.integration
def test_experimental_candidate_remains_quarantined_with_complete_support(
    representation_runtime: RepresentationRuntime,
) -> None:
    prepared = _prepare_evaluated_candidate(
        representation_runtime,
        candidate_status=PrimitiveStatus.EXPERIMENTAL,
    )
    support = _record_promotion_support(representation_runtime, prepared)

    decision = representation_runtime.service.admit(
        _admission_proposal(representation_runtime, prepared, support)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED


@pytest.mark.integration
def test_admission_rejects_evaluations_bound_to_a_substitute_candidate_receipt(
    representation_runtime: RepresentationRuntime,
) -> None:
    prepared = _prepare_evaluated_candidate(
        representation_runtime,
        alternate_evaluation_candidate_receipt=True,
    )
    support = _record_promotion_support(representation_runtime, prepared)

    decision = representation_runtime.service.admit(
        _admission_proposal(representation_runtime, prepared, support)
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_workspace_replay_detects_primitive_head_tampering(
    representation_runtime: RepresentationRuntime,
) -> None:
    prepared = _prepare_evaluated_candidate(representation_runtime)
    support = _record_promotion_support(representation_runtime, prepared)
    assert representation_runtime.service.admit(
        _admission_proposal(representation_runtime, prepared, support)
    ).accepted
    with representation_runtime.engine.begin() as connection:
        connection.execute(
            update(primitive_heads)
            .where(primitive_heads.c.primitive_id == prepared.candidate.primitive_id)
            .values(
                primitive_version_id=prepared.rollback.primitive_version_id,
                semantic_version=prepared.rollback.semantic_version,
                status=prepared.rollback.status.value,
            )
        )

    with representation_runtime.uow_factory() as uow:
        verification = verify_workspace(uow.repositories(), representation_runtime.artifacts)
        assert verification.valid is False
        assert "primitive" in (verification.reason or "")


@pytest.mark.integration
@pytest.mark.parametrize("field", ("transformation_kind", "proposer"))
def test_workspace_replay_rejects_rehashed_primitive_identity_tampering(
    representation_runtime: RepresentationRuntime,
    field: str,
) -> None:
    runtime = representation_runtime
    primitive = _primitive(runtime, "primitive-tamper-v0", "primitive-tamper")
    assert runtime.service.propose(
        _stage_proposal(runtime, primitive, "stage-primitive-tamper")
    ).accepted
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        stored = PrimitiveVersionRepository(uow.connection).get(primitive.primitive_version_id)
        assert stored is not None
    update_value: object = TransformationKind.INTRA_SPACE_TRANSFORMATION
    if field == "proposer":
        update_value = stored.proposer.model_copy(
            update={"configuration_hash": sha256_hex(b"forged-proposer-configuration")}
        )
    tampered = stored.model_copy(update={field: update_value})
    record_json = canonical_json_bytes(tampered.model_dump(mode="json")).decode("utf-8")
    with runtime.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER primitive_versions_no_update")
        connection.execute(
            update(primitive_versions)
            .where(primitive_versions.c.primitive_version_id == primitive.primitive_version_id)
            .values(
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
            )
        )

    with runtime.uow_factory() as uow:
        verification = verify_workspace(uow.repositories(), runtime.artifacts)
        assert verification.valid is False
        assert "primitive version" in (verification.reason or "")


@dataclass(frozen=True)
class PreparedCandidate:
    rollback: PrimitiveVersion
    candidate: PrimitiveVersion
    candidate_receipt: PrimitiveVersionReceiptRef
    old_evaluation: PrimitiveEvaluation
    old_receipt: PrimitiveEvaluationReceiptRef
    new_evaluation: PrimitiveEvaluation
    new_receipt: PrimitiveEvaluationReceiptRef


@dataclass(frozen=True)
class PromotionSupport:
    evaluator_audit: EvaluatorAuditRecord
    evaluator_audit_receipt: EvaluatorAuditReceiptRef
    measurement: SelfImprovementMeasurementRecord
    measurement_receipt: SelfImprovementMeasurementReceiptRef


def _prepare_evaluated_candidate(
    runtime: RepresentationRuntime,
    *,
    new_evaluator: ActorIdentity | None = None,
    candidate_status: PrimitiveStatus = PrimitiveStatus.STABILIZED,
    alternate_evaluation_candidate_receipt: bool = False,
) -> PreparedCandidate:
    _seed_evidence(runtime, "evidence-old")
    _seed_evidence(runtime, "evidence-new")
    rollback = _primitive(runtime, "primitive-gradient-v0", "primitive-gradient")
    assert runtime.service.propose(_stage_proposal(runtime, rollback, "stage-rollback")).accepted
    candidate = _primitive(
        runtime,
        "primitive-gradient-v1",
        "primitive-gradient",
        semantic_version="0.1.1",
        status=candidate_status,
        predecessors=(rollback.primitive_version_id,),
        created_at=BASE + timedelta(seconds=8),
    )
    runtime.clock.advance_to(BASE + timedelta(seconds=10))
    candidate_stage = _stage_proposal(runtime, candidate, "stage-candidate")
    assert runtime.service.propose(candidate_stage).accepted
    candidate_receipt = _receipt(runtime, candidate_stage.proposal_id, PrimitiveVersionReceiptRef)
    evaluation_candidate_receipt = candidate_receipt
    if alternate_evaluation_candidate_receipt:
        alternate_stage = _stage_proposal(runtime, candidate, "stage-candidate-alternate")
        assert runtime.service.propose(alternate_stage).accepted
        evaluation_candidate_receipt = _receipt(
            runtime,
            alternate_stage.proposal_id,
            PrimitiveVersionReceiptRef,
        )
    _seed_verification_result(
        runtime,
        candidate,
        "old",
        "evidence-old",
        completed_at=BASE + timedelta(seconds=14),
    )
    _seed_verification_result(
        runtime,
        candidate,
        "new",
        "evidence-new",
        completed_at=BASE + timedelta(seconds=18),
    )
    old_evaluation = _evaluation(
        runtime,
        candidate,
        "old",
        BASE + timedelta(seconds=15),
    )
    runtime.clock.advance_to(BASE + timedelta(seconds=16))
    old_proposal = _evaluation_proposal(runtime, evaluation_candidate_receipt, old_evaluation)
    assert runtime.service.evaluate(old_proposal).accepted
    old_receipt = _receipt(runtime, old_proposal.proposal_id, PrimitiveEvaluationReceiptRef)
    new_evaluation = _evaluation(
        runtime,
        candidate,
        "new",
        BASE + timedelta(seconds=19),
        evaluator=new_evaluator,
    )
    runtime.clock.advance_to(BASE + timedelta(seconds=20))
    new_proposal = _evaluation_proposal(runtime, evaluation_candidate_receipt, new_evaluation)
    assert runtime.service.evaluate(new_proposal).accepted
    new_receipt = _receipt(runtime, new_proposal.proposal_id, PrimitiveEvaluationReceiptRef)
    return PreparedCandidate(
        rollback=rollback,
        candidate=candidate,
        candidate_receipt=candidate_receipt,
        old_evaluation=old_evaluation,
        old_receipt=old_receipt,
        new_evaluation=new_evaluation,
        new_receipt=new_receipt,
    )


def _record_promotion_support(
    runtime: RepresentationRuntime,
    prepared: PreparedCandidate,
) -> PromotionSupport:
    run = base_run().model_copy(
        update={
            "run_id": "primitive-run",
            "creator": runtime.integrator,
            "created_at": BASE + timedelta(seconds=21),
            "active_governance_policy_hash": runtime.policy.policy_hash,
        }
    )
    runtime.clock.advance_to(BASE + timedelta(seconds=22))
    assert runtime.coordinator.submit(
        CreateResearchRun(
            proposal_id="create-primitive-run",
            idempotency_key="intent-create-primitive-run",
            proposer=runtime.integrator,
            approval=Approval(
                approver=runtime.stage_approver,
                approved_at=BASE + timedelta(seconds=21, milliseconds=500),
            ),
            run=run,
        )
    ).accepted
    evaluator = _actor("measurement-evaluator", ActorKind.MODEL)
    auditor = _actor("evaluator-auditor")
    audit = base_audit().model_copy(
        update={
            "evaluator_audit_id": "primitive-evaluator-audit",
            "auditor": auditor,
            "auditor_version": "primitive-auditor-v1",
            "evaluator": evaluator,
            "evaluator_version": "primitive-evaluator-v1",
            "proposer": runtime.primitive_author,
            "candidate_producer": runtime.primitive_author,
            "evidence_ids": ("evidence-old", "evidence-new"),
            "checks_run": ("verification-old", "verification-new"),
            "audited_at": BASE + timedelta(seconds=25),
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )
    runtime.clock.advance_to(BASE + timedelta(seconds=26))
    audit_proposal = RecordEvaluatorAudit(
        proposal_id="record-primitive-evaluator-audit",
        idempotency_key="intent-record-primitive-evaluator-audit",
        proposer=auditor,
        approval=Approval(
            approver=_actor("audit-approver"),
            approved_at=BASE + timedelta(seconds=25, milliseconds=500),
        ),
        evaluator_audit=audit,
    )
    assert runtime.coordinator.submit(audit_proposal).accepted
    audit_receipt = _receipt(runtime, audit_proposal.proposal_id, EvaluatorAuditReceiptRef)
    base = base_measurement()
    protected_metrics = tuple(
        item.model_copy(update={"source_id": "evidence-old"}) for item in base.protected_metrics
    )
    countermetrics = tuple(
        item.model_copy(update={"source_id": "evidence-new"}) for item in base.countermetrics
    )
    measurement = base.model_copy(
        update={
            "measurement_id": "primitive-measurement",
            "run_id": run.run_id,
            "classification": FIXED_PRIMITIVE_CLASSIFICATION,
            "proposer": runtime.primitive_author,
            "evaluator": evaluator,
            "evaluator_version": "primitive-evaluator-v1",
            "baseline_version_id": prepared.rollback.primitive_version_id,
            "candidate_version_id": prepared.candidate.primitive_version_id,
            "protected_metrics": protected_metrics,
            "countermetrics": countermetrics,
            "rollback_target_id": prepared.rollback.primitive_version_id,
            "evaluator_audit_id": audit.evaluator_audit_id,
            "decision_authority": runtime.admission_approver,
            "decided_at": BASE + timedelta(seconds=29),
            "governing_policy_hash": runtime.policy.policy_hash,
        }
    )
    runtime.clock.advance_to(BASE + timedelta(seconds=30))
    measurement_proposal = RecordSelfImprovementMeasurement(
        proposal_id="record-primitive-measurement",
        idempotency_key="intent-record-primitive-measurement",
        proposer=runtime.primitive_author,
        approval=Approval(
            approver=runtime.admission_approver,
            approved_at=BASE + timedelta(seconds=29, milliseconds=500),
        ),
        measurement=measurement,
    )
    assert runtime.coordinator.submit(measurement_proposal).accepted
    measurement_receipt = _receipt(
        runtime,
        measurement_proposal.proposal_id,
        SelfImprovementMeasurementReceiptRef,
    )
    return PromotionSupport(
        evaluator_audit=audit,
        evaluator_audit_receipt=audit_receipt,
        measurement=measurement,
        measurement_receipt=measurement_receipt,
    )


def _admission_proposal(
    runtime: RepresentationRuntime,
    prepared: PreparedCandidate,
    support: PromotionSupport,
) -> AdmitPrimitiveVersion:
    runtime.clock.advance_to(BASE + timedelta(seconds=34))
    return AdmitPrimitiveVersion(
        proposal_id="admit-primitive-gradient-v1",
        idempotency_key="intent-admit-primitive-gradient-v1",
        proposer=runtime.integrator,
        approval=Approval(
            approver=runtime.admission_approver,
            approved_at=BASE + timedelta(seconds=33),
        ),
        classification=FIXED_PRIMITIVE_CLASSIFICATION,
        candidate_receipt=prepared.candidate_receipt,
        old_frame_evaluation_receipt=prepared.old_receipt,
        new_frame_evaluation_receipt=prepared.new_receipt,
        evaluator_audit_receipt=support.evaluator_audit_receipt,
        measurement_receipt=support.measurement_receipt,
        rollback_primitive_version_id=prepared.rollback.primitive_version_id,
        integrated_at=BASE + timedelta(seconds=32),
    )


def _policy() -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.SKILL,
                persistence=PersistenceScope.PERSISTENT_SKILL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
        ),
    )


def _primitive(
    runtime: RepresentationRuntime,
    primitive_version_id: str,
    primitive_id: str,
    *,
    semantic_version: str = "0.1.0",
    status: PrimitiveStatus = PrimitiveStatus.EXPERIMENTAL,
    predecessors: tuple[str, ...] = (),
    created_at: datetime = BASE + timedelta(seconds=3),
) -> PrimitiveVersion:
    return PrimitiveVersion(
        primitive_version_id=primitive_version_id,
        primitive_id=primitive_id,
        semantic_version=semantic_version,
        transformation_kind=TransformationKind.GENERATIVE_REPRESENTATION_PROPOSAL,
        definition="A directional change in a measured quantity over time.",
        motivation="Test whether a new distinction produces falsifiable predictions.",
        parent_vocabulary=("measured-quantity", "time"),
        contrasts=("constant quantity",),
        examples=("A bounded heating interval has a positive gradient.",),
        counterexamples=("A calibrated constant reading has no gradient.",),
        construction_method="Derived from retained controlled-experiment evidence.",
        expected_uses=("Construct bounded hypotheses.",),
        predecessor_primitive_version_ids=predecessors,
        dependency_primitive_version_ids=(),
        measurement_ids=(),
        falsification_tests=("gradient-zero-check",),
        ambiguity=("Sampling resolution bounds interpretation.",),
        proposer=runtime.primitive_author,
        status=status,
        created_at=created_at,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _stage_proposal(
    runtime: RepresentationRuntime,
    primitive: PrimitiveVersion,
    identifier: str,
) -> ProposePrimitiveVersion:
    approved_at = primitive.created_at + timedelta(seconds=1)
    transaction_time = approved_at + timedelta(seconds=1)
    if transaction_time > runtime.clock.current:
        runtime.clock.advance_to(transaction_time)
    return ProposePrimitiveVersion(
        proposal_id=identifier,
        idempotency_key=f"intent-{identifier}",
        proposer=primitive.proposer,
        approval=Approval(approver=runtime.stage_approver, approved_at=approved_at),
        classification=FIXED_PRIMITIVE_CLASSIFICATION,
        primitive_version=primitive,
    )


def _seed_evidence(runtime: RepresentationRuntime, evidence_id: str) -> None:
    record = EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="controlled-experiment-observation",
        source_locator=f"fixture://{evidence_id}",
        retrieved_at=BASE,
        artifact=runtime.artifacts.put(f"retained {evidence_id}".encode(), "text/plain"),
        provenance={
            "collector": "task-11-integration",
            "external_grounding": ExternalGrounding.CONTROLLED_EXPERIMENT.value,
        },
        ingestion_actor_id=runtime.integrator.actor_id,
        verification_state=VerificationState.UNVERIFIED,
    )
    assert runtime.coordinator.submit(
        AddEvidence(
            proposal_id=f"add-{evidence_id}",
            idempotency_key=f"intent-add-{evidence_id}",
            proposer=runtime.integrator,
            evidence=record,
        )
    ).accepted


def _seed_verification_result(
    runtime: RepresentationRuntime,
    primitive: PrimitiveVersion,
    frame: str,
    evidence_id: str,
    *,
    completed_at: datetime,
) -> None:
    hypothesis_id = f"primitive-evaluation-{frame}"
    hypothesis_version_id = f"{hypothesis_id}-v1"
    checker = _actor(f"{frame}-checker", ActorKind.MODEL)
    hypothesis = HypothesisVersionRecord(
        hypothesis_version_id=hypothesis_version_id,
        hypothesis_id=hypothesis_id,
        version=1,
        statement=f"The {frame} frame remains deterministically testable.",
        assumptions=("The bounded fixture is retained.",),
        scope=("Task 11 primitive evaluation fixture.",),
        variables=("observation",),
        predictions=("The declared criterion passes.",),
        falsification_conditions=("The declared criterion fails.",),
        primitive_version_ids=(primitive.primitive_version_id,),
        evidence_ids=(evidence_id,),
        admission_status=HypothesisAdmissionStatus.TRANSFER_TESTING,
        proposer_id=runtime.integrator.actor_id,
        created_at=completed_at - timedelta(seconds=2),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    mechanism = VerificationMechanismSpecRecord(
        mechanism_spec_id=f"mechanism-{frame}",
        hypothesis_version_id=hypothesis_version_id,
        mechanism_category=VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER,
        name=f"{frame}-frame-checker",
        description="A fixed deterministic fixture checker.",
        specification_hash=sha256_hex(f"mechanism-{frame}".encode()),
        input_schema_id="primitive-evaluation-input-v1",
        output_schema_id="primitive-evaluation-output-v1",
        created_by=checker.actor_id,
        created_at=completed_at - timedelta(seconds=1),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    result = VerificationResultRecord(
        verification_result_id=f"verification-{frame}",
        hypothesis_version_id=hypothesis_version_id,
        mechanism_spec_id=mechanism.mechanism_spec_id,
        mechanism_category=VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER,
        result_category=VerificationResultCategory.DETERMINISTIC_CHECK_RESULT,
        model_spec_id=None,
        model_execution_mode=None,
        simulation_result_ids=(),
        outcome=VerificationOutcome.PASS,
        findings=(f"The {frame}-frame criterion passed.",),
        verified_by=checker.actor_id,
        completed_at=completed_at,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        HypothesisVersionRepository(uow.connection).add(
            hypothesis.hypothesis_version_id,
            hypothesis,
            hypothesis.created_at,
        )
        VerificationMechanismSpecRepository(uow.connection).add(
            mechanism.mechanism_spec_id,
            mechanism,
            mechanism.created_at,
        )
        VerificationResultRepository(uow.connection).add(
            result.verification_result_id,
            result,
            result.completed_at,
        )


def _evaluation(
    runtime: RepresentationRuntime,
    primitive: PrimitiveVersion,
    frame: str,
    evaluated_at: datetime,
    *,
    evaluator: ActorIdentity | None = None,
) -> PrimitiveEvaluation:
    retained_evaluator = evaluator or _actor(f"{frame}-evaluator", ActorKind.MODEL)
    checker = _actor(f"{frame}-checker", ActorKind.MODEL)
    evidence_ids = (f"evidence-{frame}",)
    verification_ids = (f"verification-{frame}",)
    detail = (
        OldFrameEvaluation(
            preserved_constraints=("Calibration bounds remain unchanged.",),
            established_test_ids=("established-calibration-test",),
            regression_findings=("No retained baseline regression was observed.",),
        )
        if frame == "old"
        else NewFrameEvaluation(
            novel_predictions=("Heating predicts a positive bounded gradient.",),
            independent_operationalization="A separately authored finite-difference check.",
            non_circular_test_ids=("independent-gradient-test",),
            later_reuse_evidence_ids=evidence_ids,
        )
    )
    provenance = AssessmentProvenance(
        actor=retained_evaluator,
        actor_version=f"{frame}-evaluator-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("The retained fixture covers the declared bounded scope.",),
        evidence_ids=evidence_ids,
        checks_run=verification_ids,
        limitations=("The evaluation is not a proof of universal usefulness.",),
        result=AssessmentOutcome.PASSED,
        meaningful_confidence=None,
        assessed_at=evaluated_at,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    return PrimitiveEvaluation(
        primitive_evaluation_id=f"primitive-evaluation-{frame}",
        primitive_version_id=primitive.primitive_version_id,
        frame_evaluation=detail,
        verification_result_ids=verification_ids,
        evidence_ids=evidence_ids,
        check_actors=(checker,),
        provenance=provenance,
        findings=(f"The {frame}-frame criterion passed.",),
        outcome=AssessmentOutcome.PASSED,
        evaluated_at=evaluated_at,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _evaluation_proposal(
    runtime: RepresentationRuntime,
    candidate_receipt: PrimitiveVersionReceiptRef,
    evaluation: PrimitiveEvaluation,
) -> RecordPrimitiveEvaluation:
    return RecordPrimitiveEvaluation(
        proposal_id=f"record-{evaluation.primitive_evaluation_id}",
        idempotency_key=f"intent-record-{evaluation.primitive_evaluation_id}",
        proposer=evaluation.provenance.actor,
        approval=Approval(
            approver=_actor(f"{evaluation.frame_evaluation.frame.lower()}-evaluation-approver"),
            approved_at=evaluation.evaluated_at + timedelta(milliseconds=500),
        ),
        classification=FIXED_PRIMITIVE_CLASSIFICATION,
        candidate_receipt=candidate_receipt,
        evaluation=evaluation,
    )


def _receipt[ReceiptT: AcceptedPrimitiveReceiptRef](
    runtime: RepresentationRuntime,
    proposal_id: str,
    receipt_type: type[ReceiptT],
) -> ReceiptT:
    with runtime.uow_factory() as uow:
        repositories = uow.repositories()
        transaction = repositories.transactions.get_by_proposal_id(proposal_id)
        assert transaction is not None and transaction.decision.accepted
        events = tuple(
            event
            for event in repositories.audit.list_all()
            if _event_proposal_id(event.payload) == proposal_id
        )
        assert len(events) == 1
        event = events[0]
        return receipt_type(
            proposal_id=proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=event.event_id,
            audit_event_hash=event.event_hash,
        )


def _event_proposal_id(payload: Mapping[str, object]) -> str | None:
    proposal = json_compatible_payload(payload).get("proposal")
    return proposal.get("proposal_id") if isinstance(proposal, Mapping) else None
