from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from super_scientist.application.trails.receipts import (
    AcceptedProposalReceipt,
    AcceptedProposalReceiptReader,
)
from super_scientist.application.trails.service import (
    FIXED_TRAIL_CLASSIFICATION,
    EvidenceTrailDraft,
    EvidenceTrailVersionBuilder,
    trail_authority_rejection,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV1,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.evidence.models import EvidenceSpan, VerificationState
from super_scientist.domain.evidence_trails.authority import (
    build_source_first_provenance,
    canonical_evidence_ids,
    canonical_node_set_hash,
    derive_geometry_from_graph,
    required_assessment_scope,
    trusted_assessment_id,
    trusted_check_id,
)
from super_scientist.domain.evidence_trails.models import (
    AssessmentCategory,
    EvidenceTrailNode,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelation,
    ExactSourceSpan,
    RelationType,
    SourceFirstProvenance,
    StructuralLocation,
    StructuralLocationKind,
    TrailCheckCategory,
    TrailNodeRole,
    TrailOutcome,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    BindReportSentence,
    ProposeClaim,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    RecordEvidenceTrailVersion,
    RejectionCode,
    TransactionDecision,
    TransitionClaim,
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


class AdvancingClock:
    def __init__(self) -> None:
        self._next = NOW

    def now(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        return value


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


def _accepted_receipt(
    runtime: TrailRuntime,
    proposal_id: str,
) -> AcceptedProposalReceipt:
    with runtime.engine.connect() as connection:
        receipt = AcceptedProposalReceiptReader(connection).get(proposal_id)
    assert receipt is not None
    return receipt


def _append_accepted_without_handler(
    runtime: TrailRuntime,
    proposal: ProposeEvidenceTrailNodes | ProposeEvidenceTrailRelations,
    *,
    governing_policy: PolicySnapshot | None = None,
) -> None:
    policy = runtime.policy if governing_policy is None else governing_policy
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    with DatabaseUnitOfWork(runtime.engine) as unit_of_work:
        repositories = unit_of_work.repositories()
        occurred_at = NOW + timedelta(minutes=10)
        repositories.transactions.add(proposal, decision, occurred_at)
        event = append_event(
            repositories.audit.last(),
            "transaction_decision",
            {
                "proposal": proposal.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
                "policy_hash": policy.policy_hash,
                "stored_policy_hash": policy.policy_hash,
                "configured_policy_hash": policy.policy_hash,
                "transaction_persisted": True,
            },
            occurred_at + timedelta(seconds=1),
        )
        repositories.audit.add(event)


def _node_stage_proposal(
    runtime: TrailRuntime,
    *,
    proposal_id: str = "proposal-node-stage-1",
    approver: ActorIdentity | None = None,
    nodes: tuple[EvidenceTrailNode, ...] | None = None,
    trail_version_id: str | None = None,
) -> ProposeEvidenceTrailNodes:
    source_receipt = _accepted_receipt(runtime, "proposal-source")
    exact_nodes = runtime.fixture.snapshot.nodes if nodes is None else nodes
    return ProposeEvidenceTrailNodes(
        proposal_id=proposal_id,
        idempotency_key=f"intent-{proposal_id}",
        proposer=runtime.proposer,
        approval=Approval(
            approver=runtime.approver if approver is None else approver,
            approved_at=NOW,
        ),
        trail_id=runtime.fixture.snapshot.version.trail_id,
        trail_version_id=(
            runtime.fixture.snapshot.version.trail_version_id
            if trail_version_id is None
            else trail_version_id
        ),
        classification=FIXED_TRAIL_CLASSIFICATION,
        source_receipts=(source_receipt.reference,),
        nodes=exact_nodes,
    )


def _relation_stage_proposal(
    runtime: TrailRuntime,
    *,
    node_stage_id: str = "proposal-node-stage-1",
    proposal_id: str = "proposal-relation-stage-1",
    approver: ActorIdentity | None = None,
    relations: tuple[EvidenceTrailRelation, ...] | None = None,
) -> ProposeEvidenceTrailRelations:
    node_receipt = _accepted_receipt(runtime, node_stage_id)
    node_stage = node_receipt.proposal
    assert isinstance(node_stage, ProposeEvidenceTrailNodes)
    nodes = node_stage.nodes
    exact_relations = (
        runtime.fixture.snapshot.relations if relations is None else relations
    )
    return ProposeEvidenceTrailRelations(
        proposal_id=proposal_id,
        idempotency_key=f"intent-{proposal_id}",
        proposer=runtime.proposer,
        approval=Approval(
            approver=runtime.approver if approver is None else approver,
            approved_at=NOW,
        ),
        trail_id=node_stage.trail_id,
        trail_version_id=node_stage.trail_version_id,
        classification=FIXED_TRAIL_CLASSIFICATION,
        node_stage_receipt=node_receipt.reference,
        node_ids=tuple(node.node_id for node in nodes),
        nodes_hash=canonical_node_set_hash(nodes),
        relations=exact_relations,
    )


def _source_first_receipts(
    runtime: TrailRuntime,
    *,
    node_stage_id: str,
    relation_stage_id: str,
    claim_stage_id: str,
) -> SourceFirstProvenance:
    return build_source_first_provenance(
        source_receipts=(_accepted_receipt(runtime, "proposal-source").reference,),
        node_stage_receipt=_accepted_receipt(runtime, node_stage_id).reference,
        relation_stage_receipt=_accepted_receipt(runtime, relation_stage_id).reference,
        claim_stage_receipt=_accepted_receipt(runtime, claim_stage_id).reference,
    )


def _finalize_draft(
    draft: EvidenceTrailDraft,
    template: RecordEvidenceTrailVersion,
    *,
    claim: AtomicClaim,
    source_first_provenance: SourceFirstProvenance,
    checked_at: datetime,
    assessed_at: datetime,
    assessment_results: dict[AssessmentCategory, AssessmentOutcome] | None = None,
) -> RecordEvidenceTrailVersion:
    node_ids = tuple(node.node_id for node in draft.nodes)
    relation_ids = tuple(relation.relation_id for relation in draft.relations)
    evidence_ids = canonical_evidence_ids(draft.nodes)
    checks = tuple(
        prior.model_copy(
            update={
                "check_id": trusted_check_id(draft.trail_version_id, category),
                "trail_version_id": draft.trail_version_id,
                "claim_version_id": f"{claim.claim_id}:{claim.version}",
                "governing_policy_hash": draft.governing_policy_hash,
                "category": category,
                "node_ids": node_ids,
                "relation_ids": relation_ids,
                "evidence_ids": evidence_ids,
                "checked_at": checked_at,
            }
        )
        for category, prior in zip(TrailCheckCategory, template.checks, strict=True)
    )
    check_ids = tuple(check.check_id for check in checks)
    assessments = tuple(
        prior.model_copy(
            update={
                "assessment_id": trusted_assessment_id(
                    draft.trail_version_id,
                    category,
                ),
                "trail_version_id": draft.trail_version_id,
                "claim_version_id": f"{claim.claim_id}:{claim.version}",
                "governing_policy_hash": draft.governing_policy_hash,
                "category": category,
                "node_ids": required_assessment_scope(
                    category,
                    draft.nodes,
                    draft.relations,
                ).node_ids,
                "relation_ids": required_assessment_scope(
                    category,
                    draft.nodes,
                    draft.relations,
                ).relation_ids,
                "evidence_ids": required_assessment_scope(
                    category,
                    draft.nodes,
                    draft.relations,
                ).evidence_ids,
                "provenance": prior.provenance.model_copy(
                    update={
                        "evidence_ids": required_assessment_scope(
                            category,
                            draft.nodes,
                            draft.relations,
                        ).evidence_ids,
                        "checks_run": check_ids,
                        "assessed_at": assessed_at,
                        "governing_policy_hash": draft.governing_policy_hash,
                        "result": (
                            prior.provenance.result
                            if assessment_results is None
                            else assessment_results.get(
                                category,
                                prior.provenance.result,
                            )
                        ),
                    }
                ),
            }
        )
        for category, prior in zip(
            AssessmentCategory,
            template.assessments,
            strict=True,
        )
    )
    return EvidenceTrailVersionBuilder.finalize(
        draft=draft,
        claim=claim,
        checks=checks,
        assessments=assessments,
        source_first_provenance=source_first_provenance,
    )


def _next_claim(current: AtomicClaim) -> AtomicClaim:
    evidence_links = current.evidence_links
    status = ClaimStatus.TESTABLE
    if current.status is ClaimStatus.PROPOSED:
        status = ClaimStatus.EVIDENCE_LINKED
        evidence_links = (
            EvidenceLink(
                evidence_id="evidence-1",
                supporting_span="Cause happened",
            ),
        )
    return current.model_copy(
        update={
            "version": current.version + 1,
            "status": status,
            "evidence_links": evidence_links,
            "parent_version_id": f"{current.claim_id}:{current.version}",
            "created_at": current.created_at + timedelta(minutes=1),
        }
    )


def _accept_successor(
    runtime: TrailRuntime,
    *,
    draft: EvidenceTrailDraft,
    template: RecordEvidenceTrailVersion,
    current_claim: AtomicClaim,
    checked_at: datetime,
    assessed_at: datetime,
    assessment_results: dict[AssessmentCategory, AssessmentOutcome] | None = None,
    submit_final: bool = True,
) -> tuple[RecordEvidenceTrailVersion, AtomicClaim]:
    suffix = str(draft.version)
    node_stage_id = f"proposal-node-stage-{suffix}"
    relation_stage_id = f"proposal-relation-stage-{suffix}"
    claim_stage_id = f"proposal-claim-{suffix}"
    node_stage = _node_stage_proposal(
        runtime,
        proposal_id=node_stage_id,
        nodes=draft.nodes,
        trail_version_id=draft.trail_version_id,
    )
    assert runtime.coordinator.submit(node_stage).accepted
    relation_stage = _relation_stage_proposal(
        runtime,
        node_stage_id=node_stage_id,
        proposal_id=relation_stage_id,
        relations=draft.relations,
    )
    assert runtime.coordinator.submit(relation_stage).accepted
    claim = _next_claim(current_claim)
    transition = TransitionClaim(
        proposal_id=claim_stage_id,
        idempotency_key=f"intent-{claim_stage_id}",
        proposer=_actor(claim.created_by, ActorKind.HUMAN),
        next_claim=claim,
    )
    assert runtime.coordinator.submit(transition).accepted
    provenance = _source_first_receipts(
        runtime,
        node_stage_id=node_stage_id,
        relation_stage_id=relation_stage_id,
        claim_stage_id=claim_stage_id,
    )
    proposal = _finalize_draft(
        draft,
        template,
        claim=claim,
        source_first_provenance=provenance,
        checked_at=checked_at,
        assessed_at=assessed_at,
        assessment_results=assessment_results,
    )
    if submit_final:
        assert runtime.coordinator.submit(proposal).accepted
    return proposal, claim

@pytest.fixture
def v2_runtime(tmp_path: Path) -> Iterator[TrailRuntime]:
    yield from _runtime(tmp_path, _v2_policy(), bootstrap_stages=True)


@pytest.fixture
def v2_stage_runtime(tmp_path: Path) -> Iterator[TrailRuntime]:
    yield from _runtime(tmp_path, _v2_policy(), include_claim=False)


@pytest.fixture
def v1_runtime(tmp_path: Path) -> Iterator[TrailRuntime]:
    yield from _runtime(
        tmp_path,
        GovernancePolicyV1(required_claim_checks=("hash_matches",)),
    )


@pytest.mark.integration
def test_router_registers_all_four_fixed_trail_handlers(v2_runtime: TrailRuntime) -> None:
    assert tuple(
        v2_runtime.coordinator.router.resolve(name).proposal_type
        for name in (
            "propose_evidence_trail_nodes",
            "propose_evidence_trail_relations",
            "record_evidence_trail_version",
            "bind_report_sentence",
        )
    ) == (
        "propose_evidence_trail_nodes",
        "propose_evidence_trail_relations",
        "record_evidence_trail_version",
        "bind_report_sentence",
    )


@pytest.mark.integration
def test_shared_trail_authority_owns_exact_fixed_stage_classification(
    v2_stage_runtime: TrailRuntime,
) -> None:
    proposal = _node_stage_proposal(v2_stage_runtime).model_copy(
        update={
            "classification": FIXED_TRAIL_CLASSIFICATION.model_copy(
                update={
                    "signal": ImprovementSignal.INTRINSIC_EVALUATIVE_FEEDBACK
                }
            )
        }
    )

    rejection = trail_authority_rejection(proposal, v2_stage_runtime.policy)

    assert rejection is not None
    assert rejection.reasons[0].code is RejectionCode.PERMISSION_DENIED


@pytest.mark.integration
def test_accepted_node_stage_is_durable_audited_and_projects_no_trail_rows(
    v2_stage_runtime: TrailRuntime,
) -> None:
    from super_scientist.application.trails.receipts import (
        AcceptedProposalReceiptReader,
    )
    from super_scientist.application.trails.service import FIXED_TRAIL_CLASSIFICATION
    from super_scientist.kernel.transactions.models import ProposeEvidenceTrailNodes

    with v2_stage_runtime.engine.connect() as connection:
        source_receipt = AcceptedProposalReceiptReader(connection).get("proposal-source")
    assert source_receipt is not None
    proposal = ProposeEvidenceTrailNodes(
        proposal_id="proposal-node-stage-1",
        idempotency_key="intent-node-stage-1",
        proposer=v2_stage_runtime.proposer,
        approval=Approval(approver=v2_stage_runtime.approver, approved_at=NOW),
        trail_id=v2_stage_runtime.fixture.snapshot.version.trail_id,
        trail_version_id=v2_stage_runtime.fixture.snapshot.version.trail_version_id,
        classification=FIXED_TRAIL_CLASSIFICATION,
        source_receipts=(source_receipt.reference,),
        nodes=v2_stage_runtime.fixture.snapshot.nodes,
    )

    decision = v2_stage_runtime.coordinator.submit(proposal)

    assert decision.accepted
    with v2_stage_runtime.engine.connect() as connection:
        reader = AcceptedProposalReceiptReader(connection)
        stage_receipt = reader.get(proposal.proposal_id)
        repositories = RepositorySet(connection)
        assert stage_receipt is not None
        assert stage_receipt.proposal == proposal
        assert len(repositories.transactions.list_all()) == 2
        assert len(repositories.audit.list_all()) == 2
        assert EvidenceTrailVersionRepository(connection).list_all() == ()
        assert EvidenceTrailNodeRepository(connection).list_all() == ()
        assert EvidenceTrailRelationRepository(connection).list_all() == ()


@pytest.mark.integration
def test_accepted_relation_stage_is_durable_audited_and_projects_no_trail_rows(
    v2_stage_runtime: TrailRuntime,
) -> None:
    node_stage = _node_stage_proposal(v2_stage_runtime)
    assert v2_stage_runtime.coordinator.submit(node_stage).accepted
    relation_stage = _relation_stage_proposal(v2_stage_runtime)

    decision = v2_stage_runtime.coordinator.submit(relation_stage)

    assert decision.accepted
    with v2_stage_runtime.engine.connect() as connection:
        receipt = AcceptedProposalReceiptReader(connection).get(relation_stage.proposal_id)
        repositories = RepositorySet(connection)
        assert receipt is not None
        assert receipt.proposal == relation_stage
        assert len(repositories.transactions.list_all()) == 3
        assert len(repositories.audit.list_all()) == 3
        assert EvidenceTrailVersionRepository(connection).list_all() == ()
        assert EvidenceTrailNodeRepository(connection).list_all() == ()
        assert EvidenceTrailRelationRepository(connection).list_all() == ()


@pytest.mark.integration
def test_node_stage_rejects_duplicate_exact_nodes(v2_stage_runtime: TrailRuntime) -> None:
    proposal = _node_stage_proposal(v2_stage_runtime)
    duplicate = proposal.model_copy(update={"nodes": (*proposal.nodes, proposal.nodes[0])})

    decision = v2_stage_runtime.coordinator.submit(duplicate)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL


@pytest.mark.integration
def test_relation_stage_rejects_duplicate_exact_relations(
    v2_stage_runtime: TrailRuntime,
) -> None:
    assert v2_stage_runtime.coordinator.submit(
        _node_stage_proposal(v2_stage_runtime)
    ).accepted
    proposal = _relation_stage_proposal(v2_stage_runtime)
    duplicate = proposal.model_copy(
        update={"relations": (*proposal.relations, proposal.relations[0])}
    )

    decision = v2_stage_runtime.coordinator.submit(duplicate)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL


@pytest.mark.integration
def test_relation_stage_approver_must_be_independent_of_source_ingestor(
    v2_stage_runtime: TrailRuntime,
) -> None:
    assert v2_stage_runtime.coordinator.submit(
        _node_stage_proposal(v2_stage_runtime)
    ).accepted
    proposal = _relation_stage_proposal(
        v2_stage_runtime,
        approver=_actor("ingestor-1", ActorKind.HUMAN),
    )

    decision = v2_stage_runtime.coordinator.submit(proposal)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
def test_wrong_source_receipt_hash_cannot_authorize_node_stage(
    v2_stage_runtime: TrailRuntime,
) -> None:
    proposal = _node_stage_proposal(v2_stage_runtime)
    forged_reference = proposal.source_receipts[0].model_copy(
        update={"proposal_hash": "f" * 64}
    )
    forged = proposal.model_copy(update={"source_receipts": (forged_reference,)})

    decision = v2_stage_runtime.coordinator.submit(forged)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.MISSING_EVIDENCE


@pytest.mark.integration
def test_missing_audit_cannot_resolve_an_accepted_stage_receipt(
    v2_stage_runtime: TrailRuntime,
) -> None:
    proposal = _node_stage_proposal(v2_stage_runtime)
    assert v2_stage_runtime.coordinator.submit(proposal).accepted
    with v2_stage_runtime.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER audit_events_no_delete"))
        connection.execute(text("DELETE FROM audit_events WHERE sequence = 2"))
    with v2_stage_runtime.engine.connect() as connection:
        assert AcceptedProposalReceiptReader(connection).get(proposal.proposal_id) is None


@pytest.mark.integration
def test_v1_both_stage_proposals_fail_closed_durably_and_audited(
    v1_runtime: TrailRuntime,
) -> None:
    node_stage = _node_stage_proposal(v1_runtime)
    missing_node_ref = EvidenceTrailNodeStageReceiptRef(
        proposal_id=node_stage.proposal_id,
        proposal_hash="f" * 64,
        audit_event_id="missing-node-audit",
        audit_event_hash="e" * 64,
    )
    nodes = v1_runtime.fixture.snapshot.nodes
    relation_stage = ProposeEvidenceTrailRelations(
        proposal_id="proposal-relation-stage-v1",
        idempotency_key="intent-relation-stage-v1",
        proposer=v1_runtime.proposer,
        approval=Approval(approver=v1_runtime.approver, approved_at=NOW),
        trail_id=v1_runtime.fixture.snapshot.version.trail_id,
        trail_version_id=v1_runtime.fixture.snapshot.version.trail_version_id,
        classification=FIXED_TRAIL_CLASSIFICATION,
        node_stage_receipt=missing_node_ref,
        node_ids=tuple(node.node_id for node in nodes),
        nodes_hash=canonical_node_set_hash(nodes),
        relations=v1_runtime.fixture.snapshot.relations,
    )

    decisions = (
        v1_runtime.coordinator.submit(node_stage),
        v1_runtime.coordinator.submit(relation_stage),
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
        assert len(RepositorySet(connection).transactions.list_all()) == 5
        assert len(RepositorySet(connection).audit.list_all()) == 5


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
        assert len(RepositorySet(connection).transactions.list_all()) == 4
        assert len(RepositorySet(connection).audit.list_all()) == 4


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
@pytest.mark.parametrize("alias_kind", ("claim_author", "source_ingestor", "source_config"))
def test_human_approver_must_be_independent_of_every_trail_authority(
    v2_runtime: TrailRuntime,
    alias_kind: str,
) -> None:
    proposal = v2_runtime.record_proposal()
    source_receipt = _accepted_receipt(v2_runtime, "proposal-source")
    assert isinstance(source_receipt.proposal, AddEvidence)
    source_actor = source_receipt.proposal.proposer
    if alias_kind == "claim_author":
        approver = _actor(v2_runtime.fixture.inputs.claim.created_by, ActorKind.HUMAN)
    elif alias_kind == "source_ingestor":
        approver = _actor(
            v2_runtime.fixture.inputs.sources[0].evidence.ingestion_actor_id,
            ActorKind.HUMAN,
        )
    else:
        approver = source_actor.model_copy(
            update={"actor_id": "human-source-config-alias", "kind": ActorKind.HUMAN}
        )
    proposal = proposal.model_copy(
        update={"approval": Approval(approver=approver, approved_at=NOW)}
    )

    decision = v2_runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


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
            start=0,
            end=len(SOURCE_TEXT),
        ),
        content_hash=sha256_hex(alternative_text.encode("utf-8")),
        role=TrailNodeRole.REDUNDANT,
        temporal_position=2,
        causal_position=None,
        confidence=0.7,
        necessity=False,
    )
    second_draft = EvidenceTrailVersionBuilder.add_node(
        current_head=initial.snapshot(),
        node=node,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-2",
        idempotency_key="intent-trail-2",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW + timedelta(minutes=3),
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    second, second_claim = _accept_successor(
        v2_runtime,
        draft=second_draft,
        template=initial,
        current_claim=v2_runtime.fixture.inputs.claim,
        checked_at=NOW + timedelta(minutes=1),
        assessed_at=NOW + timedelta(minutes=2),
    )
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
    third_draft = EvidenceTrailVersionBuilder.add_relation(
        current_head=second_snapshot,
        relation=relation,
        trail_version_id="trail-version-3",
        proposal_id="proposal-trail-3",
        idempotency_key="intent-trail-3",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW + timedelta(minutes=6),
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    third, third_claim = _accept_successor(
        v2_runtime,
        draft=third_draft,
        template=second,
        current_claim=second_claim,
        checked_at=NOW + timedelta(minutes=4),
        assessed_at=NOW + timedelta(minutes=5),
    )
    assert tuple(
        item.trail_version.claim_version_id for item in (initial, second, third)
    ) == ("claim-1:1", "claim-1:2", "claim-1:3")
    assert third_claim.version == 3

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
@pytest.mark.parametrize(
    ("category", "result", "expected"),
    (
        (
            AssessmentCategory.NECESSITY,
            AssessmentOutcome.FAILED,
            TrailOutcome.INSUFFICIENT,
        ),
        (
            AssessmentCategory.ANSWERABILITY,
            AssessmentOutcome.ABSTAINED,
            TrailOutcome.UNANSWERABLE,
        ),
    ),
)
def test_finalize_derives_successor_status_from_fresh_assessments(
    v2_runtime: TrailRuntime,
    category: AssessmentCategory,
    result: AssessmentOutcome,
    expected: TrailOutcome,
) -> None:
    initial = v2_runtime.record_proposal()
    assert v2_runtime.coordinator.submit(initial).accepted
    source, target = initial.nodes[:2]
    relation = EvidenceTrailRelation(
        relation_id="relation-status-v2",
        trail_version_id=initial.trail_version.trail_version_id,
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relation_type=RelationType.QUALIFIES,
        evidence_ids=(source.evidence_id,),
        modality=initial.relations[0].modality,
    )
    draft = EvidenceTrailVersionBuilder.add_relation(
        current_head=initial.snapshot(),
        relation=relation,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-status-2",
        idempotency_key="intent-trail-status-2",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW + timedelta(minutes=3),
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )

    successor, _ = _accept_successor(
        v2_runtime,
        draft=draft,
        template=initial,
        current_claim=v2_runtime.fixture.inputs.claim,
        checked_at=NOW + timedelta(minutes=1),
        assessed_at=NOW + timedelta(minutes=2),
        assessment_results={category: result},
        submit_final=False,
    )

    assert "status" not in EvidenceTrailDraft.model_fields
    assert successor.trail_version.status is expected


@pytest.mark.integration
def test_successor_derives_conflicted_from_fresh_contradictory_evidence(
    v2_runtime: TrailRuntime,
) -> None:
    initial = v2_runtime.record_proposal()
    assert v2_runtime.coordinator.submit(initial).accepted
    alternative_text = "Alternative explanation remains"
    start = SOURCE_TEXT.index(alternative_text)
    opposing = EvidenceTrailNode(
        node_id="node-opposing-v2",
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
            start=0,
            end=len(SOURCE_TEXT),
        ),
        content_hash=sha256_hex(alternative_text.encode()),
        role=TrailNodeRole.OPPOSING,
        temporal_position=2,
        causal_position=None,
        confidence=0.7,
        necessity=True,
    )
    draft = EvidenceTrailVersionBuilder.add_node(
        current_head=initial.snapshot(),
        node=opposing,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-conflicted-2",
        idempotency_key="intent-trail-conflicted-2",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW + timedelta(minutes=3),
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    required = draft.nodes[0]
    retained_opposing = draft.nodes[-1]
    contradiction = EvidenceTrailRelation(
        relation_id="relation-contradiction-v2",
        trail_version_id=draft.trail_version_id,
        source_node_id=retained_opposing.node_id,
        target_node_id=required.node_id,
        relation_type=RelationType.CONTRADICTS,
        evidence_ids=(retained_opposing.evidence_id,),
        modality=initial.relations[0].modality,
    )
    relations = (*draft.relations, contradiction)
    draft = draft.model_copy(
        update={
            "relations": relations,
            "geometry": derive_geometry_from_graph(draft.nodes, relations),
        }
    )

    successor, _ = _accept_successor(
        v2_runtime,
        draft=draft,
        template=initial,
        current_claim=v2_runtime.fixture.inputs.claim,
        checked_at=NOW + timedelta(minutes=1),
        assessed_at=NOW + timedelta(minutes=2),
    )

    assert successor.trail_version.status is TrailOutcome.CONFLICTED
    counterevidence = next(
        assessment
        for assessment in successor.assessments
        if assessment.category is AssessmentCategory.COUNTEREVIDENCE
    )
    assert counterevidence.provenance.result is AssessmentOutcome.PASSED


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
def test_workspace_integrity_replay_rejects_forged_source_first_provenance(
    v2_runtime: TrailRuntime,
) -> None:
    proposal = v2_runtime.record_proposal()
    assert v2_runtime.coordinator.submit(proposal).accepted is True
    provenance = proposal.trail_version.source_first_provenance
    forged = provenance.model_copy(
        update={
            "node_stage_receipt": provenance.node_stage_receipt.model_copy(
                update={"proposal_hash": "f" * 64}
            )
        }
    )
    version = proposal.trail_version.model_copy(
        update={"source_first_provenance": forged}
    )
    record_json = canonical_json_bytes(version.model_dump(mode="json")).decode("utf-8")
    with v2_runtime.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER evidence_trail_versions_no_update"))
        connection.execute(
            text(
                "UPDATE evidence_trail_versions "
                "SET record_json = :record_json, content_hash = :content_hash "
                "WHERE trail_version_id = :trail_version_id"
            ),
            {
                "trail_version_id": version.trail_version_id,
                "record_json": record_json,
                "content_hash": sha256_hex(record_json.encode("utf-8")),
            },
        )

    with v2_runtime.engine.connect() as connection:
        verification = verify_workspace(RepositorySet(connection), v2_runtime.artifact_store)
    assert verification.valid is False
    assert "trail" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_replay_rejects_backdated_claim_stage_transaction(
    v2_runtime: TrailRuntime,
) -> None:
    assert v2_runtime.coordinator.submit(v2_runtime.record_proposal()).accepted
    with v2_runtime.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER transactions_no_update"))
        connection.execute(
            text(
                "UPDATE transactions SET created_at = :created_at "
                "WHERE proposal_id = 'proposal-claim'"
            ),
            {"created_at": (NOW + timedelta(seconds=3)).isoformat()},
        )

    with v2_runtime.engine.connect() as connection:
        verification = verify_workspace(
            RepositorySet(connection),
            v2_runtime.artifact_store,
        )

    assert verification.valid is False
    assert "receipt" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_replay_rejects_registered_but_inactive_trail_policy(
    v2_stage_runtime: TrailRuntime,
) -> None:
    weak_policy = _v2_policy(verification=VerificationLevel.SELF_CRITIQUE)
    weak_snapshot = PolicySnapshot(
        policy_hash=policy_hash(weak_policy),
        policy=weak_policy,
    )
    with v2_stage_runtime.engine.begin() as connection:
        policies = PolicyRepository(connection)
        policies.add_and_activate(weak_snapshot, NOW + timedelta(minutes=1))
        policies.add_and_activate(v2_stage_runtime.policy, NOW + timedelta(minutes=2))
    _append_accepted_without_handler(
        v2_stage_runtime,
        _node_stage_proposal(v2_stage_runtime),
        governing_policy=weak_snapshot,
    )

    with v2_stage_runtime.engine.connect() as connection:
        verification = verify_workspace(
            RepositorySet(connection),
            v2_stage_runtime.artifact_store,
        )

    assert verification.valid is False
    assert "policy" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_replay_rejects_dependent_stage_human_approval(
    v2_stage_runtime: TrailRuntime,
) -> None:
    proposal = _node_stage_proposal(
        v2_stage_runtime,
        approver=_actor("ingestor-1", ActorKind.HUMAN),
    )
    _append_accepted_without_handler(v2_stage_runtime, proposal)

    with v2_stage_runtime.engine.connect() as connection:
        verification = verify_workspace(
            RepositorySet(connection),
            v2_stage_runtime.artifact_store,
        )

    assert verification.valid is False
    assert "authority" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_replay_rejects_wrong_durable_stage_classification(
    v2_stage_runtime: TrailRuntime,
) -> None:
    proposal = _node_stage_proposal(v2_stage_runtime).model_copy(
        update={
            "classification": FIXED_TRAIL_CLASSIFICATION.model_copy(
                update={
                    "signal": ImprovementSignal.INTRINSIC_EVALUATIVE_FEEDBACK
                }
            )
        }
    )
    _append_accepted_without_handler(v2_stage_runtime, proposal)

    with v2_stage_runtime.engine.connect() as connection:
        verification = verify_workspace(
            RepositorySet(connection),
            v2_stage_runtime.artifact_store,
        )

    assert verification.valid is False
    assert "stage" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_replay_rejects_non_primary_stage_source(
    tmp_path: Path,
) -> None:
    runtime_iterator = _runtime(
        tmp_path,
        _v2_policy(),
        include_claim=False,
        source_grounding=ExternalGrounding.INDEPENDENT_MODEL,
    )
    runtime = next(runtime_iterator)
    try:
        _append_accepted_without_handler(runtime, _node_stage_proposal(runtime))
        with runtime.engine.connect() as connection:
            verification = verify_workspace(
                RepositorySet(connection),
                runtime.artifact_store,
            )
        assert verification.valid is False
        assert "stage" in (verification.reason or "").lower()
    finally:
        runtime_iterator.close()


@pytest.mark.integration
@pytest.mark.parametrize(("protected", "rollback"), ((True, False), (False, True)))
def test_workspace_replay_rejects_unsupported_trail_policy_flags(
    tmp_path: Path,
    protected: bool,
    rollback: bool,
) -> None:
    runtime_iterator = _runtime(
        tmp_path,
        _v2_policy(protected=protected, rollback=rollback),
        include_claim=False,
    )
    runtime = next(runtime_iterator)
    try:
        _append_accepted_without_handler(runtime, _node_stage_proposal(runtime))
        with runtime.engine.connect() as connection:
            verification = verify_workspace(
                RepositorySet(connection),
                runtime.artifact_store,
            )
        assert verification.valid is False
        assert "authority" in (verification.reason or "").lower()
    finally:
        runtime_iterator.close()


@pytest.mark.integration
def test_workspace_replay_rejects_wrong_historical_stage_policy(
    v2_stage_runtime: TrailRuntime,
) -> None:
    weak_policy = _v2_policy(verification=VerificationLevel.SELF_CRITIQUE)
    weak_snapshot = PolicySnapshot(
        policy_hash=policy_hash(weak_policy),
        policy=weak_policy,
    )
    with v2_stage_runtime.engine.begin() as connection:
        policies = PolicyRepository(connection)
        policies.add_and_activate(weak_snapshot, NOW + timedelta(minutes=1))
        policies.add_and_activate(v2_stage_runtime.policy, NOW + timedelta(minutes=2))
    _append_accepted_without_handler(
        v2_stage_runtime,
        _node_stage_proposal(v2_stage_runtime),
        governing_policy=weak_snapshot,
    )

    with v2_stage_runtime.engine.connect() as connection:
        verification = verify_workspace(
            RepositorySet(connection),
            v2_stage_runtime.artifact_store,
        )

    assert verification.valid is False
    assert "policy" in (verification.reason or "").lower()


@pytest.mark.integration
def test_workspace_integrity_replay_rejects_reordered_report_semantics(
    v2_runtime: TrailRuntime,
) -> None:
    assert v2_runtime.coordinator.submit(v2_runtime.record_proposal()).accepted is True
    proposal = v2_runtime.binding_proposal()
    assert v2_runtime.coordinator.submit(proposal).accepted is True
    forged = proposal.binding.model_copy(
        update={
            "source_node_ids": tuple(reversed(proposal.binding.source_node_ids)),
            "source_spans": tuple(reversed(proposal.binding.source_spans)),
        }
    )
    record_json = canonical_json_bytes(forged.model_dump(mode="json")).decode("utf-8")
    with v2_runtime.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER report_sentence_bindings_no_update"))
        connection.execute(
            text(
                "UPDATE report_sentence_bindings "
                "SET record_json = :record_json, content_hash = :content_hash "
                "WHERE binding_id = :binding_id"
            ),
            {
                "binding_id": forged.binding_id,
                "record_json": record_json,
                "content_hash": sha256_hex(record_json.encode("utf-8")),
            },
        )

    with v2_runtime.engine.connect() as connection:
        verification = verify_workspace(RepositorySet(connection), v2_runtime.artifact_store)
    assert verification.valid is False
    assert "binding" in (verification.reason or "").lower()


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
    successor_draft = EvidenceTrailVersionBuilder.add_relation(
        current_head=initial.snapshot(),
        relation=relation,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-2",
        idempotency_key="intent-trail-2",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW + timedelta(minutes=3),
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    _successor, _ = _accept_successor(
        v2_runtime,
        draft=successor_draft,
        template=initial,
        current_claim=v2_runtime.fixture.inputs.claim,
        checked_at=NOW + timedelta(minutes=1),
        assessed_at=NOW + timedelta(minutes=2),
    )

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


@pytest.mark.integration
def test_graph_edit_is_unsubmittable_until_fresh_checks_and_assessments_are_supplied(
    v2_runtime: TrailRuntime,
) -> None:
    initial = v2_runtime.record_proposal()
    source, target = initial.nodes[:2]
    relation = EvidenceTrailRelation(
        relation_id="relation-draft-v2",
        trail_version_id=initial.trail_version.trail_version_id,
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relation_type=RelationType.QUALIFIES,
        evidence_ids=(source.evidence_id,),
        modality=initial.relations[0].modality,
    )

    draft = EvidenceTrailVersionBuilder.add_relation(
        current_head=initial.snapshot(),
        relation=relation,
        trail_version_id="trail-version-2",
        proposal_id="proposal-trail-draft",
        idempotency_key="intent-trail-draft",
        proposer=v2_runtime.proposer,
        approval=Approval(approver=v2_runtime.approver, approved_at=NOW),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )

    assert not isinstance(draft, RecordEvidenceTrailVersion)
    assert v2_runtime.coordinator.submit(draft).accepted is False
    finalize = EvidenceTrailVersionBuilder.finalize
    with pytest.raises(ValueError, match=r"fresh|receipt"):
        finalize(
            draft=draft,
            claim=_next_claim(v2_runtime.fixture.inputs.claim),
            checks=initial.checks,
            assessments=initial.assessments,
        )


def _runtime(
    tmp_path: Path,
    policy: GovernancePolicyV1 | GovernancePolicyV2,
    *,
    bootstrap_stages: bool = False,
    include_claim: bool = True,
    source_grounding: ExternalGrounding = ExternalGrounding.PRIMARY_SOURCE,
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
        update={
            "evidence": source.evidence.model_copy(
                update={
                    "artifact": artifact,
                    "extracted_span": EvidenceSpan(
                        start=0,
                        end=len(SOURCE_TEXT),
                        text=SOURCE_TEXT,
                    ),
                }
            )
        }
    )
    source = source.model_copy(
        update={
            "evidence": source.evidence.model_copy(
                update={
                    "provenance": {
                        **source.evidence.provenance,
                        "external_grounding": source_grounding.value,
                    }
                }
            )
        }
    )
    fixture = fixture.__class__(
        snapshot=fixture.snapshot,
        inputs=fixture.inputs.model_copy(update={"sources": (source,)}),
    )
    coordinator = TransactionCoordinator(
        lambda: DatabaseUnitOfWork(engine),
        snapshot,
        AdvancingClock(),
        artifact_store,
    )
    evidence_actor = _actor("ingestor-1", ActorKind.MODEL)
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
    runtime = TrailRuntime(
        engine=engine,
        artifact_store=artifact_store,
        coordinator=coordinator,
        policy=snapshot,
        fixture=fixture,
        proposer=fixture.snapshot.version.constructed_by,
        approver=_actor("trail-approver", ActorKind.HUMAN),
    )
    if bootstrap_stages:
        node_stage = _node_stage_proposal(runtime)
        assert coordinator.submit(node_stage).accepted
        relation_stage = _relation_stage_proposal(runtime)
        assert coordinator.submit(relation_stage).accepted
    if include_claim or bootstrap_stages:
        assert coordinator.submit(
            ProposeClaim(
                proposal_id="proposal-claim",
                idempotency_key="intent-claim",
                proposer=claim_actor,
                claim=fixture.inputs.claim,
            )
        ).accepted
    if bootstrap_stages:
        provenance = _source_first_receipts(
            runtime,
            node_stage_id="proposal-node-stage-1",
            relation_stage_id="proposal-relation-stage-1",
            claim_stage_id="proposal-claim",
        )
        rebound_version = fixture.snapshot.version.model_copy(
            update={"source_first_provenance": provenance}
        )
        fixture = fixture.__class__(
            snapshot=fixture.snapshot.model_copy(
                update={"version": rebound_version}
            ),
            inputs=fixture.inputs,
        )
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
    checks = tuple(
        check.model_copy(update={"governing_policy_hash": governing_policy_hash})
        for check in fixture.snapshot.checks
    )
    assessments = tuple(
        assessment.model_copy(
            update={
                "governing_policy_hash": governing_policy_hash,
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
        update={"version": version, "checks": checks, "assessments": assessments}
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
        required_claim_checks=("source_exists", "evidence_span_exists"),
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
    model_fields: dict[str, str] = {}
    if kind is ActorKind.MODEL:
        model_fields = {
            "provider_id": f"provider-{actor_id}",
            "model_id": f"model-{actor_id}",
            "configuration_hash": sha256_hex(f"config-{actor_id}".encode()),
        }
    return ActorIdentity(
        actor_id=actor_id,
        kind=kind,
        created_at=NOW,
        **model_fields,
    )
