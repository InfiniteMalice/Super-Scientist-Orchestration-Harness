from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from super_scientist.application.trails.service import EvidenceTrailVersionBuilder
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV1,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.evidence.models import VerificationState
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    ExactSourceSpan,
    RelationType,
    StructuralLocation,
    StructuralLocationKind,
    TrailNodeRole,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.primitives import sha256_hex
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    BindReportSentence,
    ProposeClaim,
    RecordEvidenceTrailVersion,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    EvidenceTrailAssessmentRepository,
    EvidenceTrailCheckRepository,
    EvidenceTrailHeadRepository,
    EvidenceTrailNodeRepository,
    EvidenceTrailRelationRepository,
    EvidenceTrailVersionRepository,
    ReportSentenceBindingRepository,
)
from super_scientist.providers.storage.repositories import PolicyRepository, RepositorySet
from tests.integration.storage.test_evidence_trail_repositories import _binding
from tests.unit.evidence_trails.conftest import (
    SOURCE_TEXT,
    TrailFixture,
    make_trail_fixture,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class TrailRuntime:
    engine: Engine
    artifact_store: FileArtifactStore
    coordinator: TransactionCoordinator
    policy: PolicySnapshot
    fixture: TrailFixture
    proposer: ActorIdentity
    approver: ActorIdentity

    def record_proposal(
        self,
        *,
        proposal_id: str = "proposal-trail-1",
    ) -> RecordEvidenceTrailVersion:
        snapshot = self.fixture.snapshot
        return RecordEvidenceTrailVersion(
            proposal_id=proposal_id,
            idempotency_key=f"intent-{proposal_id}",
            proposer=self.proposer,
            approval=Approval(approver=self.approver, approved_at=NOW),
            trail_version=snapshot.version,
            nodes=snapshot.nodes,
            relations=snapshot.relations,
            checks=snapshot.checks,
            assessments=snapshot.assessments,
        )

    def binding_proposal(self, *, proposal_id: str = "proposal-binding-1") -> BindReportSentence:
        return BindReportSentence(
            proposal_id=proposal_id,
            idempotency_key=f"intent-{proposal_id}",
            proposer=self.proposer,
            approval=Approval(approver=self.approver, approved_at=NOW),
            binding=_binding(self.fixture),
        )


@pytest.fixture
def v2_runtime(tmp_path: Path) -> Iterator[TrailRuntime]:
    yield from _runtime(tmp_path, _v2_policy())


@pytest.fixture
def v1_runtime(tmp_path: Path) -> Iterator[TrailRuntime]:
    yield from _runtime(
        tmp_path,
        GovernancePolicyV1(required_claim_checks=("hash_matches",)),
    )


@pytest.mark.integration
def test_router_registers_both_fixed_trail_handlers(v2_runtime: TrailRuntime) -> None:
    assert tuple(
        v2_runtime.coordinator.router.resolve(name).proposal_type
        for name in ("record_evidence_trail_version", "bind_report_sentence")
    ) == ("record_evidence_trail_version", "bind_report_sentence")


@pytest.mark.integration
def test_v1_both_trail_proposals_fail_closed_durably_and_audited(
    v1_runtime: TrailRuntime,
) -> None:
    decisions = (
        v1_runtime.coordinator.submit(v1_runtime.record_proposal()),
        v1_runtime.coordinator.submit(v1_runtime.binding_proposal()),
    )

    assert all(not decision.accepted for decision in decisions)
    assert all(
        decision.reasons[0].code is RejectionCode.PERMISSION_DENIED
        for decision in decisions
    )
    with v1_runtime.engine.connect() as connection:
        repositories = RepositorySet(connection)
        assert len(repositories.transactions.list_all()) == 4
        assert len(repositories.audit.list_all()) == 4
        assert EvidenceTrailVersionRepository(connection).list_all() == ()
        assert ReportSentenceBindingRepository(connection).list_all() == ()


@pytest.mark.integration
def test_v2_complete_snapshot_projects_atomically_and_exact_replay_is_stable(
    v2_runtime: TrailRuntime,
) -> None:
    proposal = v2_runtime.record_proposal()

    first = v2_runtime.coordinator.submit(proposal)
    replay = v2_runtime.coordinator.submit(proposal)

    assert first.accepted is True
    assert replay == first.model_copy(update={"replayed": True})
    snapshot = v2_runtime.fixture.snapshot
    with v2_runtime.engine.connect() as connection:
        assert EvidenceTrailVersionRepository(connection).list_all() == (snapshot.version,)
        assert set(EvidenceTrailNodeRepository(connection).list_all()) == set(snapshot.nodes)
        assert set(EvidenceTrailRelationRepository(connection).list_all()) == set(
            snapshot.relations
        )
        assert set(EvidenceTrailCheckRepository(connection).list_all()) == set(snapshot.checks)
        assert set(EvidenceTrailAssessmentRepository(connection).list_all()) == set(
            snapshot.assessments
        )
        assert EvidenceTrailHeadRepository(connection).get(snapshot.version.trail_id) == (
            snapshot.version.trail_version_id,
            snapshot.version.version,
        )


@pytest.mark.integration
def test_invalid_causal_snapshot_is_durably_rejected_and_projects_nothing(
    v2_runtime: TrailRuntime,
) -> None:
    proposal = v2_runtime.record_proposal()
    causal = proposal.relations[1].model_copy(
        update={"relation_type": RelationType.CAUSES_CANDIDATE, "causal_support": ()}
    )
    invalid = proposal.model_copy(update={"relations": (proposal.relations[0], causal)})

    decision = v2_runtime.coordinator.submit(invalid)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    with v2_runtime.engine.connect() as connection:
        assert EvidenceTrailVersionRepository(connection).list_all() == ()
        assert EvidenceTrailNodeRepository(connection).list_all() == ()
        assert EvidenceTrailRelationRepository(connection).list_all() == ()
        assert EvidenceTrailCheckRepository(connection).list_all() == ()
        assert EvidenceTrailAssessmentRepository(connection).list_all() == ()
        assert EvidenceTrailHeadRepository(connection).list_all() == ()
        assert len(RepositorySet(connection).transactions.list_all()) == 3
        assert len(RepositorySet(connection).audit.list_all()) == 3


@pytest.mark.integration
def test_projection_failure_rolls_back_version_children_head_transaction_and_audit(
    v2_runtime: TrailRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_relation_projection(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected relation projection failure")

    monkeypatch.setattr(EvidenceTrailRelationRepository, "add", fail_relation_projection)

    with pytest.raises(RuntimeError, match="injected relation projection failure"):
        v2_runtime.coordinator.submit(v2_runtime.record_proposal())

    with v2_runtime.engine.connect() as connection:
        assert EvidenceTrailVersionRepository(connection).list_all() == ()
        assert EvidenceTrailNodeRepository(connection).list_all() == ()
        assert EvidenceTrailRelationRepository(connection).list_all() == ()
        assert EvidenceTrailCheckRepository(connection).list_all() == ()
        assert EvidenceTrailAssessmentRepository(connection).list_all() == ()
        assert EvidenceTrailHeadRepository(connection).list_all() == ()
        assert len(RepositorySet(connection).transactions.list_all()) == 2
        assert len(RepositorySet(connection).audit.list_all()) == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    ("verification", "grounding", "protected", "rollback"),
    [
        (
            VerificationLevel.SELF_CRITIQUE,
            frozenset({ExternalGrounding.PRIMARY_SOURCE}),
            False,
            False,
        ),
        (
            VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
            False,
            False,
        ),
        (
            VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            frozenset({ExternalGrounding.PRIMARY_SOURCE}),
            True,
            False,
        ),
        (
            VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            frozenset({ExternalGrounding.PRIMARY_SOURCE}),
            False,
            True,
        ),
    ],
)
def test_v2_policy_verification_grounding_and_unsupported_flags_fail_closed(
    tmp_path: Path,
    verification: VerificationLevel,
    grounding: frozenset[ExternalGrounding],
    protected: bool,
    rollback: bool,
) -> None:
    policy = _v2_policy(
        verification=verification,
        grounding=grounding,
        protected=protected,
        rollback=rollback,
    )
    runtime_iterator = _runtime(tmp_path, policy)
    runtime = next(runtime_iterator)
    try:
        decision = runtime.coordinator.submit(runtime.record_proposal())
        assert decision.accepted is False
        assert decision.reasons[0].code is RejectionCode.INSUFFICIENT_GROUNDING
        with runtime.engine.connect() as connection:
            assert EvidenceTrailVersionRepository(connection).list_all() == ()
    finally:
        runtime_iterator.close()


@pytest.mark.integration
def test_add_node_and_relation_helpers_build_complete_successor_versions_without_mutation(
    v2_runtime: TrailRuntime,
) -> None:
    initial = v2_runtime.record_proposal()
    assert v2_runtime.coordinator.submit(initial).accepted is True
    alternative_text = "Alternative explanation remains"
    start = SOURCE_TEXT.index(alternative_text)
    node = EvidenceTrailNode(
        node_id="node-alternative-v2",
        trail_version_id="trail-version-2",
        source_id="source-1",
        evidence_id="evidence-1",
        exact_span=ExactSourceSpan(
            start=start,
            end=start + len(alternative_text),
            text=alternative_text,
        ),
        structural_location=StructuralLocation(
            kind=StructuralLocationKind.PARAGRAPH,
            locator="paragraph-1",
            start=start,
            end=start + len(alternative_text),
        ),
        content_hash=sha256_hex(alternative_text.encode("utf-8")),
        role=TrailNodeRole.REDUNDANT,
        temporal_position=2,
        causal_position=2,
        confidence=0.7,
        necessity=False,
    )
    second = EvidenceTrailVersionBuilder.add_node(
        current_head=initial.snapshot(),
        node=node,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-2",
        idempotency_key="intent-trail-2",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    assert v2_runtime.coordinator.submit(second).accepted is True
    second_snapshot = second.snapshot()
    assert second_snapshot.version.version == 2
    assert second_snapshot.version.parent_trail_version_id == "trail-version-1"
    assert all(node.trail_version_id == "trail-version-2" for node in second_snapshot.nodes)
    assert not ({item.node_id for item in initial.nodes} & {item.node_id for item in second.nodes})

    source, target = second.nodes[:2]
    relation = EvidenceTrailRelation(
        relation_id="relation-qualified-v3",
        trail_version_id="trail-version-2",
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relation_type=RelationType.QUALIFIES,
        evidence_ids=("evidence-1",),
        modality=initial.relations[0].modality,
    )
    third = EvidenceTrailVersionBuilder.add_relation(
        current_head=second_snapshot,
        relation=relation,
        trail_version_id="trail-version-3",
        proposal_id="proposal-trail-3",
        idempotency_key="intent-trail-3",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    assert v2_runtime.coordinator.submit(third).accepted is True

    with v2_runtime.engine.connect() as connection:
        versions = EvidenceTrailVersionRepository(connection).list_all()
        assert versions == (
            initial.trail_version,
            second.trail_version,
            third.trail_version,
        )
        stored_initial_nodes = tuple(
            item
            for item in EvidenceTrailNodeRepository(connection).list_all()
            if item.trail_version_id == initial.trail_version.trail_version_id
        )
        stored_initial_relations = tuple(
            item
            for item in EvidenceTrailRelationRepository(connection).list_all()
            if item.trail_version_id == initial.trail_version.trail_version_id
        )
        stored_initial_checks = tuple(
            item
            for item in EvidenceTrailCheckRepository(connection).list_all()
            if item.trail_version_id == initial.trail_version.trail_version_id
        )
        assert stored_initial_nodes == tuple(sorted(initial.nodes, key=lambda item: item.node_id))
        assert stored_initial_relations == tuple(
            sorted(initial.relations, key=lambda item: item.relation_id)
        )
        assert stored_initial_checks == tuple(
            sorted(initial.checks, key=lambda item: item.check_id)
        )
        assert EvidenceTrailHeadRepository(connection).get("trail-1") == (
            "trail-version-3",
            3,
        )


@pytest.mark.integration
def test_report_binding_is_derived_traceability_and_never_claim_admission_authority(
    v2_runtime: TrailRuntime,
) -> None:
    assert v2_runtime.coordinator.submit(v2_runtime.record_proposal()).accepted is True
    proposal = v2_runtime.binding_proposal()

    first = v2_runtime.coordinator.submit(proposal)
    replay = v2_runtime.coordinator.submit(proposal)

    assert first.accepted is True
    assert replay == first.model_copy(update={"replayed": True})
    with v2_runtime.engine.connect() as connection:
        repositories = RepositorySet(connection)
        claim = repositories.claims.get_head("claim-1")
        assert claim == v2_runtime.fixture.inputs.claim
        assert repositories.claims.history("claim-1") == (v2_runtime.fixture.inputs.claim,)
        assert ReportSentenceBindingRepository(connection).list_all() == (proposal.binding,)

    forged_binding = proposal.binding.model_copy(
        update={
            "binding_id": "binding-forged",
            "source_node_ids": ("unknown-node",),
        }
    )
    forged = proposal.model_copy(
        update={
            "proposal_id": "proposal-binding-forged",
            "idempotency_key": "intent-binding-forged",
            "binding": forged_binding,
        }
    )
    decision = v2_runtime.coordinator.submit(forged)
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    with v2_runtime.engine.connect() as connection:
        assert ReportSentenceBindingRepository(connection).list_all() == (proposal.binding,)


@pytest.mark.integration
def test_workspace_integrity_replay_detects_missing_trail_children(
    v2_runtime: TrailRuntime,
) -> None:
    proposal = v2_runtime.record_proposal()
    assert v2_runtime.coordinator.submit(proposal).accepted is True
    with v2_runtime.engine.connect() as connection:
        assert verify_workspace(RepositorySet(connection), v2_runtime.artifact_store).valid
    with v2_runtime.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER evidence_trail_assessments_no_delete"))
        connection.execute(
            text(
                "DELETE FROM evidence_trail_assessments "
                "WHERE assessment_id = :assessment_id"
            ),
            {"assessment_id": proposal.assessments[0].assessment_id},
        )

    with v2_runtime.engine.connect() as connection:
        verification = verify_workspace(RepositorySet(connection), v2_runtime.artifact_store)
    assert verification.valid is False
    assert "trail" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_integrity_replay_rejects_rewound_trail_head(
    v2_runtime: TrailRuntime,
) -> None:
    initial = v2_runtime.record_proposal()
    assert v2_runtime.coordinator.submit(initial).accepted is True
    source, target = initial.nodes[:2]
    relation = EvidenceTrailRelation(
        relation_id="relation-added-v2",
        trail_version_id=initial.trail_version.trail_version_id,
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relation_type=RelationType.QUALIFIES,
        evidence_ids=(source.evidence_id,),
        modality=initial.relations[0].modality,
    )
    successor = EvidenceTrailVersionBuilder.add_relation(
        current_head=initial.snapshot(),
        relation=relation,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-2",
        idempotency_key="intent-trail-2",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    assert v2_runtime.coordinator.submit(successor).accepted is True

    with v2_runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE evidence_trail_heads "
                "SET trail_version_id = :trail_version_id, version = :version "
                "WHERE trail_id = :trail_id"
            ),
            {
                "trail_id": initial.trail_version.trail_id,
                "trail_version_id": initial.trail_version.trail_version_id,
                "version": initial.trail_version.version,
            },
        )

    with v2_runtime.engine.connect() as connection:
        verification = verify_workspace(RepositorySet(connection), v2_runtime.artifact_store)
    assert verification.valid is False
    assert "trail" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_integrity_replay_rejects_binding_without_accepted_transaction(
    v2_runtime: TrailRuntime,
) -> None:
    assert v2_runtime.coordinator.submit(v2_runtime.record_proposal()).accepted is True
    binding = v2_runtime.binding_proposal().binding
    with v2_runtime.engine.begin() as connection:
        ReportSentenceBindingRepository(connection).add(
            binding.binding_id,
            binding,
            binding.created_at,
        )

    with v2_runtime.engine.connect() as connection:
        verification = verify_workspace(RepositorySet(connection), v2_runtime.artifact_store)
    assert verification.valid is False
    assert "binding" in (verification.reason or "").lower()


def _runtime(
    tmp_path: Path,
    policy: GovernancePolicyV1 | GovernancePolicyV2,
) -> Iterator[TrailRuntime]:
    database_url = f"sqlite:///{(tmp_path / f'trails-v{policy.schema_version}.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifact_store = FileArtifactStore(tmp_path / f"artifacts-v{policy.schema_version}")
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    with engine.begin() as connection:
        PolicyRepository(connection).add_and_activate(snapshot, NOW)
    fixture = _rebind_fixture(make_trail_fixture(), snapshot.policy_hash)
    source = fixture.inputs.sources[0]
    artifact = artifact_store.put(source.artifact_bytes, "text/plain")
    source = source.model_copy(
        update={"evidence": source.evidence.model_copy(update={"artifact": artifact})}
    )
    fixture = fixture.__class__(
        snapshot=fixture.snapshot,
        inputs=fixture.inputs.model_copy(update={"sources": (source,)}),
    )
    coordinator = TransactionCoordinator(
        lambda: DatabaseUnitOfWork(engine),
        snapshot,
        FixedClock(),
        artifact_store,
    )
    evidence_actor = _actor("ingestor-1", ActorKind.SERVICE)
    claim_actor = _actor("claim-author", ActorKind.HUMAN)
    assert coordinator.submit(
        AddEvidence(
            proposal_id="proposal-source",
            idempotency_key="intent-source",
            proposer=evidence_actor,
            evidence=source.evidence.model_copy(
                update={"verification_state": VerificationState.UNVERIFIED}
            ),
        )
    ).accepted
    assert coordinator.submit(
        ProposeClaim(
            proposal_id="proposal-claim",
            idempotency_key="intent-claim",
            proposer=claim_actor,
            claim=fixture.inputs.claim,
        )
    ).accepted
    runtime = TrailRuntime(
        engine=engine,
        artifact_store=artifact_store,
        coordinator=coordinator,
        policy=snapshot,
        fixture=fixture,
        proposer=fixture.snapshot.version.constructed_by,
        approver=_actor("trail-approver", ActorKind.HUMAN),
    )
    try:
        yield runtime
    finally:
        engine.dispose()


def _rebind_fixture(fixture: TrailFixture, governing_policy_hash: str) -> TrailFixture:
    assessments = tuple(
        assessment.model_copy(
            update={
                "provenance": assessment.provenance.model_copy(
                    update={"governing_policy_hash": governing_policy_hash}
                )
            }
        )
        for assessment in fixture.snapshot.assessments
    )
    version = fixture.snapshot.version.model_copy(
        update={
            "governing_policy_hash": governing_policy_hash,
            "assessment_ids": tuple(item.assessment_id for item in assessments),
        }
    )
    snapshot = fixture.snapshot.model_copy(
        update={"version": version, "assessments": assessments}
    )
    return fixture.__class__(snapshot=snapshot, inputs=fixture.inputs)


def _v2_policy(
    *,
    verification: VerificationLevel = VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    grounding: frozenset[ExternalGrounding] = frozenset({ExternalGrounding.PRIMARY_SOURCE}),
    protected: bool = False,
    rollback: bool = False,
) -> GovernancePolicyV2:
    return GovernancePolicyV2(
        required_claim_checks=("hash_matches",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=verification,
                permitted_grounding=grounding,
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=protected,
                rollback_required=rollback,
            ),
        ),
    )


def _actor(actor_id: str, kind: ActorKind) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=kind, created_at=NOW)
