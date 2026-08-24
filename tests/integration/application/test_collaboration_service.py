from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from super_scientist.application.collaboration.service import (
    AppendPeerContributionHandler,
    AppendPeerRequestHandler,
    RecordCollaborationSessionHandler,
)
from super_scientist.application.transactions.collaboration import (
    collaboration_capabilities,
    fixed_collaboration_handlers,
)
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.cognition import CohortPlan
from super_scientist.domain.collaboration import (
    CollaborationSession,
    PeerContribution,
    PeerRequest,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AppendPeerContribution,
    AppendPeerRequest,
    Approval,
    RecordCollaborationSession,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.cognitive_records import (
    PeerContributionRepository,
)
from super_scientist.providers.storage.database import (
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import RepositorySet
from tests.unit.collaboration.conftest import artifact
from tests.unit.collaboration.conftest import session_factory as session_factory_fixture

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64


@pytest.fixture
def v2_policy_snapshot() -> PolicySnapshot:
    return PolicySnapshot(
        policy_hash=POLICY_HASH,
        policy=GovernancePolicyV2(
            required_claim_checks=("source_exists",),
            human_approval_for=frozenset(),
            adaptation_requirements=(
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
        ),
    )


def _model_actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity.model(actor_id, "provider", actor_id, "adapter", NOW)


def _approval() -> Approval:
    return Approval(
        approver=ActorIdentity(actor_id="approver", kind=ActorKind.HUMAN, created_at=NOW),
        approved_at=NOW,
    )


def _request(session: CollaborationSession, recipient: str = "peer-a") -> PeerRequest:
    return PeerRequest.build(
        request_id="request-1",
        session_id=session.session_id,
        sequence=1,
        sender_id=None,
        recipient_id=recipient,
        requested_capability_id="analysis",
        question="Assess the evidence.",
        artifact_refs=(artifact(),),
        parent_contribution_id=None,
        tool_ids=("tool-a",),
        remaining_budget=session.budget.resources,
    )


def _contribution(session: CollaborationSession, peer_id: str = "peer-a") -> PeerContribution:
    return PeerContribution.build(
        contribution_id="contribution-1",
        session_id=session.session_id,
        request_id="request-1",
        peer_id=peer_id,
        parent_contribution_ids=(),
        contribution_kind="analysis",
        rationale_summary="Public evidence summary.",
        candidate_content='{"finding":"supported"}',
        artifact_refs=(artifact(),),
        tool_ids=("tool-a",),
    )


@dataclass
class _SessionReads:
    active_policy: PolicySnapshot
    cohort: CohortPlan | None
    existing: CollaborationSession | None = None

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None:
        del cohort_plan_id
        return self.cohort

    def get_session(self, session_id: str) -> CollaborationSession | None:
        del session_id
        return self.existing


@dataclass
class _HistoryReads:
    active_policy: PolicySnapshot
    session: CollaborationSession
    history: tuple[PeerRequest | PeerContribution, ...] = ()

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_session(self, session_id: str) -> CollaborationSession | None:
        return self.session if session_id == self.session.session_id else None

    def list_history(self, session_id: str) -> tuple[PeerRequest | PeerContribution, ...]:
        return self.history if session_id == self.session.session_id else ()

    def get_termination(self, session_id: str) -> None:
        del session_id
        return None


@dataclass
class _Writes:
    records: list[BaseModel] = field(default_factory=list)

    def append_authoritative(self, record: BaseModel) -> None:
        self.records.append(record)

    def update_projection(self, record: BaseModel) -> None:
        raise AssertionError(f"unexpected mutable projection: {record!r}")


def _session() -> CollaborationSession:
    factory = session_factory_fixture.__wrapped__()
    return factory("peer-a", "peer-b")


def test_session_handler_requires_exact_current_cohort(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    proposal = RecordCollaborationSession(
        proposal_id="record-session",
        idempotency_key="record-session",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        session=session,
    )
    handler = RecordCollaborationSessionHandler()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})
    context = handler.build_context(proposal, _SessionReads(policy, None))

    decision = handler.decide(proposal, context)

    assert decision.reasons[0].code is RejectionCode.STALE_REFERENCE


def test_peer_request_and_contribution_recompute_current_history(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})
    request = _request(session)
    request_proposal = AppendPeerRequest(
        proposal_id="append-request",
        idempotency_key="append-request",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        request=request,
    )
    request_handler = AppendPeerRequestHandler()
    request_decision = request_handler.decide(
        request_proposal,
        request_handler.build_context(request_proposal, _HistoryReads(policy, session)),
    )
    contribution = _contribution(session)
    contribution_proposal = AppendPeerContribution(
        proposal_id="append-contribution",
        idempotency_key="append-contribution",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        contribution=contribution,
    )
    contribution_handler = AppendPeerContributionHandler()
    contribution_decision = contribution_handler.decide(
        contribution_proposal,
        contribution_handler.build_context(
            contribution_proposal,
            _HistoryReads(policy, session, (request,)),
        ),
    )

    assert request_decision.accepted is True
    assert contribution_decision.accepted is True


def test_contribution_rejects_request_or_peer_mismatch(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})
    contribution = _contribution(session, peer_id="peer-b")
    proposal = AppendPeerContribution(
        proposal_id="append-contribution",
        idempotency_key="append-contribution",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        contribution=contribution,
    )
    handler = AppendPeerContributionHandler()

    decision = handler.decide(
        proposal,
        handler.build_context(proposal, _HistoryReads(policy, session, (_request(session),))),
    )

    assert decision.reasons[0].code is RejectionCode.DERIVATION_MISMATCH


def test_contribution_rejects_after_recomputed_hop_bound(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    factory = session_factory_fixture.__wrapped__()
    session = factory("peer-a", "peer-b", max_hops=1, completion_count=8)
    first_request = _request(session)
    first_contribution = _contribution(session)
    second_request = PeerRequest.build(
        request_id="request-2",
        session_id=session.session_id,
        sequence=2,
        sender_id="peer-a",
        recipient_id="peer-b",
        requested_capability_id="analysis",
        question="Assess the next evidence.",
        artifact_refs=(artifact(),),
        parent_contribution_id="contribution-1",
        tool_ids=("tool-a",),
        remaining_budget=session.budget.resources,
    )
    second_contribution = PeerContribution.build(
        contribution_id="contribution-2",
        session_id=session.session_id,
        request_id=second_request.request_id,
        peer_id="peer-b",
        parent_contribution_ids=("contribution-1",),
        contribution_kind="analysis",
        rationale_summary="Second public evidence summary.",
        candidate_content='{"finding":"unsupported"}',
        artifact_refs=(artifact(),),
        tool_ids=("tool-a",),
    )
    proposal = AppendPeerContribution(
        proposal_id="append-contribution-2",
        idempotency_key="append-contribution-2",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        contribution=second_contribution,
    )
    handler = AppendPeerContributionHandler()

    decision = handler.decide(
        proposal,
        handler.build_context(
            proposal,
            _HistoryReads(
                v2_policy_snapshot,
                session,
                (first_request, first_contribution, second_request),
            ),
        ),
    )

    assert decision.reasons[0].code is RejectionCode.COLLABORATION_BOUND_EXCEEDED


def test_fixed_collaboration_handlers_have_one_source_controlled_route_each() -> None:
    assert tuple(handler.proposal_type for handler in fixed_collaboration_handlers()) == (
        "record_collaboration_session",
        "append_peer_request",
        "append_peer_contribution",
        "append_topology_event",
        "record_collaboration_termination",
    )


def test_contribution_storage_failure_rolls_back_record_transaction_and_audit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'rollback.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    session = _session()
    proposal = AppendPeerContribution(
        proposal_id="append-contribution-rollback",
        idempotency_key="append-contribution-rollback",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        contribution=_contribution(session),
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def raise_storage_failure(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise SQLAlchemyError("injected contribution failure")

    monkeypatch.setattr(PeerContributionRepository, "add_from_proposal", raise_storage_failure)
    try:
        with (
            pytest.raises(SQLAlchemyError, match="injected"),
            engine.connect() as connection,
            connection.begin(),
        ):
            connection.execute(
                text(
                    "INSERT INTO governance_policies "
                    "(policy_hash, policy_json, created_at) "
                    "VALUES (:policy_hash, :policy_json, :created_at)"
                ),
                {
                    "policy_hash": POLICY_HASH,
                    "policy_json": v2_policy_snapshot.policy.model_dump_json(),
                    "created_at": NOW.isoformat(),
                },
            )
            repositories = RepositorySet(connection)
            repositories.transactions.add(proposal, decision, NOW)
            audit = append_event(
                repositories.audit.last(),
                "transaction_decision",
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                    "policy_hash": POLICY_HASH,
                    "stored_policy_hash": POLICY_HASH,
                    "transaction_persisted": True,
                },
                NOW,
            )
            repositories.audit.add(audit)
            capabilities = collaboration_capabilities(
                proposal,
                connection,
                v2_policy_snapshot,
                current_transaction_created_at=NOW,
            )
            capabilities.append_authoritative(proposal.contribution)

        with engine.connect() as connection:
            repositories = RepositorySet(connection)
            assert PeerContributionRepository(connection).list_all() == ()
            assert repositories.transactions.list_all() == ()
            assert repositories.audit.list_all() == ()
    finally:
        engine.dispose()
