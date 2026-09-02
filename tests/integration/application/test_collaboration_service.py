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
    AppendTopologyEventHandler,
    RecordCollaborationSessionHandler,
    RecordCollaborationTerminationHandler,
    rebuild_collaboration_state,
)
from super_scientist.application.transactions.collaboration import (
    _AcceptedCollaborationHistoryReader,
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
    CollaborationTermination,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
    TopologyOperation,
    TopologySnapshot,
    evaluate_termination,
    initial_collaboration_state,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import ResourceBudget, ResourceUsage
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    Approval,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RejectionCode,
    RejectionReason,
    TransactionDecision,
)
from super_scientist.providers.storage.cognitive_records import (
    PeerContributionRepository,
)
from super_scientist.providers.storage.database import (
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import (
    RepositorySet,
    StorageIntegrityError,
    StoredTransaction,
)
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


def _usage(**updates: float | int) -> ResourceUsage:
    values: dict[str, float | int] = {
        "cost_usd": 1.0,
        "compute_units": 2.0,
        "tokens": 10,
        "elapsed_seconds": 3.0,
        "tool_calls": 1,
        "human_interventions": 0,
    }
    values.update(updates)
    return ResourceUsage.model_validate(values, strict=True)


def _contribution_proposal(
    session: CollaborationSession,
    *,
    proposal_id: str = "append-contribution",
    contribution: PeerContribution | None = None,
    usage: ResourceUsage | None = None,
    approval: Approval | None = None,
) -> AppendPeerContribution:
    return AppendPeerContribution(
        proposal_id=proposal_id,
        idempotency_key=proposal_id,
        proposer=_model_actor("proposer"),
        approval=_approval() if approval is None else approval,
        contribution=_contribution(session) if contribution is None else contribution,
        usage=_usage() if usage is None else usage,
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
    history: tuple[PeerRequest | AppendPeerContribution | TopologyEvent, ...] = ()
    termination: CollaborationTermination | None = None

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_session(self, session_id: str) -> CollaborationSession | None:
        return self.session if session_id == self.session.session_id else None

    def list_history(
        self, session_id: str
    ) -> tuple[PeerRequest | AppendPeerContribution | TopologyEvent, ...]:
        return self.history if session_id == self.session.session_id else ()

    def get_termination(self, session_id: str) -> CollaborationTermination | None:
        del session_id
        return self.termination


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


def _second_request(
    session: CollaborationSession,
    *,
    remaining_budget: ResourceBudget | None = None,
) -> PeerRequest:
    return PeerRequest.build(
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
        remaining_budget=(
            session.budget.resources if remaining_budget is None else remaining_budget
        ),
    )


def _topology_event(
    session: CollaborationSession,
    *,
    sequence: int = 1,
    before: TopologySnapshot | None = None,
) -> TopologyEvent:
    initial = initial_collaboration_state(session).topology
    prior = initial if before is None else before
    after = TopologySnapshot.build(
        active_peer_ids=initial.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    return TopologyEvent.build(
        event_id=f"event-{sequence}",
        session_id=session.session_id,
        sequence=sequence,
        before_topology_hash=prior.content_hash,
        operation=TopologyOperation.DISABLE_EDGE,
        peer_id=None,
        edge=("peer-a", "peer-b"),
        reason_code="LOAD_BALANCE",
        after_topology_hash=after.content_hash,
    )


@dataclass
class _AllRecords:
    records: tuple[object, ...]

    def list_all(self) -> tuple[object, ...]:
        return self.records


@dataclass
class _SessionRecords:
    records: tuple[object, ...]

    def list_for_session(self, session_id: str) -> tuple[object, ...]:
        del session_id
        return self.records


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
    contribution_proposal = _contribution_proposal(
        session,
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


def test_nonzero_usage_is_replayed_into_the_next_remaining_budget(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    first_request = _request(session)
    first_contribution = _contribution_proposal(session, usage=_usage())
    remaining = session.budget.resources.model_copy(
        update={
            "cost_usd": 99.0,
            "compute_units": 98.0,
            "tokens": 990,
            "elapsed_seconds": 97.0,
            "tool_calls": 99,
        }
    )
    next_request = _second_request(session, remaining_budget=remaining)
    proposal = AppendPeerRequest(
        proposal_id="append-request-2",
        idempotency_key="append-request-2",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        request=next_request,
    )
    handler = AppendPeerRequestHandler()

    decision = handler.decide(
        proposal,
        handler.build_context(
            proposal,
            _HistoryReads(
                v2_policy_snapshot,
                session,
                (first_request, first_contribution),
            ),
        ),
    )

    assert decision.accepted is True
    rebuilt = rebuild_collaboration_state(
        session,
        (first_request, first_contribution),
    )
    assert rebuilt is not None
    assert rebuilt.state.usage_history == (first_contribution.usage,)
    assert rebuilt.state.usage == first_contribution.usage


@pytest.mark.parametrize(
    "field_name",
    (
        "cost_usd",
        "compute_units",
        "tokens",
        "elapsed_seconds",
        "tool_calls",
        "human_interventions",
    ),
)
def test_contribution_accepts_exact_resource_maximum_and_rejects_maximum_plus_one(
    v2_policy_snapshot: PolicySnapshot,
    field_name: str,
) -> None:
    session = _session()
    request = _request(session)
    exact_values = session.budget.resources.model_dump(mode="python")
    exact_usage = ResourceUsage.model_validate(exact_values, strict=True)
    over_values = dict(exact_values)
    over_values[field_name] += 1
    over_usage = ResourceUsage.model_validate(over_values, strict=True)
    handler = AppendPeerContributionHandler()

    exact_proposal = _contribution_proposal(
        session,
        proposal_id=f"exact-{field_name}",
        usage=exact_usage,
    )
    exact_decision = handler.decide(
        exact_proposal,
        handler.build_context(
            exact_proposal,
            _HistoryReads(v2_policy_snapshot, session, (request,)),
        ),
    )
    over_proposal = _contribution_proposal(
        session,
        proposal_id=f"over-{field_name}",
        usage=over_usage,
    )
    over_decision = handler.decide(
        over_proposal,
        handler.build_context(
            over_proposal,
            _HistoryReads(v2_policy_snapshot, session, (request,)),
        ),
    )

    assert exact_decision.accepted is True
    assert over_decision.reasons[0].code is RejectionCode.COLLABORATION_BOUND_EXCEEDED


def test_accepted_collaboration_history_replays_audit_order_and_excludes_rejections() -> None:
    session = _session()
    request = _request(session)
    accepted_request = AppendPeerRequest(
        proposal_id="accepted-request",
        idempotency_key="accepted-request",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        request=request,
    )
    rejected_request = accepted_request.model_copy(
        update={"proposal_id": "rejected-request", "idempotency_key": "rejected-request"}
    )
    accepted_contribution = _contribution_proposal(
        session,
        proposal_id="accepted-contribution",
        usage=_usage(tokens=37),
    )

    def stored(proposal, *, accepted: bool) -> StoredTransaction:
        decision = TransactionDecision(
            proposal_id=proposal.proposal_id,
            accepted=accepted,
            reasons=(
                ()
                if accepted
                else (
                    RejectionReason(
                        code=RejectionCode.DERIVATION_MISMATCH,
                        message="rejected fixture",
                    ),
                )
            ),
        )
        return StoredTransaction(
            proposal=proposal,
            proposal_hash=sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json"))),
            decision=decision,
            created_at=NOW,
        )

    transactions = (
        stored(accepted_contribution, accepted=True),
        stored(rejected_request, accepted=False),
        stored(accepted_request, accepted=True),
    )
    previous = None
    audits = []
    for transaction in (transactions[2], transactions[1], transactions[0]):
        previous = append_event(
            previous,
            "transaction_decision",
            {
                "proposal": transaction.proposal.model_dump(mode="json"),
                "decision": transaction.decision.model_dump(mode="json"),
                "policy_hash": POLICY_HASH,
                "stored_policy_hash": POLICY_HASH,
                "transaction_persisted": True,
            },
            NOW,
        )
        audits.append(previous)
    reader = _AcceptedCollaborationHistoryReader(
        governing_policy_hash=POLICY_HASH,
        transactions=_AllRecords(transactions),
        audit=_AllRecords(tuple(audits)),
        requests=_SessionRecords((request,)),
        contributions=_SessionRecords((accepted_contribution.contribution,)),
        topology_events=_SessionRecords(()),
    )

    history = reader.list_for_session(session.session_id)

    assert history == (request, accepted_contribution)


@pytest.mark.parametrize("proposal_kind", ("request", "contribution", "topology"))
@pytest.mark.parametrize(
    "audited_policy_fields",
    (
        {"policy_hash": "e" * 64, "stored_policy_hash": "e" * 64},
        {"stored_policy_hash": POLICY_HASH},
        {"policy_hash": "", "stored_policy_hash": POLICY_HASH},
        {"policy_hash": POLICY_HASH},
        {"policy_hash": POLICY_HASH, "stored_policy_hash": ""},
        {"policy_hash": POLICY_HASH, "stored_policy_hash": "e" * 64},
    ),
    ids=(
        "wrong-existing-policy",
        "missing-policy-hash",
        "empty-policy-hash",
        "missing-stored-policy-hash",
        "empty-stored-policy-hash",
        "divergent-stored-policy-hash",
    ),
)
def test_collaboration_history_with_unbound_policy_fails_closed_before_replay(
    proposal_kind: str,
    audited_policy_fields: dict[str, object],
) -> None:
    session = _session()
    if proposal_kind == "request":
        request = _request(session)
        proposal = AppendPeerRequest(
            proposal_id="accepted-request",
            idempotency_key="accepted-request",
            proposer=_model_actor("proposer"),
            approval=_approval(),
            request=request,
        )
        requests = (request,)
        contributions = ()
        topology_events = ()
    elif proposal_kind == "contribution":
        proposal = _contribution_proposal(session, usage=_usage(tokens=37))
        requests = ()
        contributions = (proposal.contribution,)
        topology_events = ()
    else:
        event = _topology_event(session)
        proposal = AppendTopologyEvent(
            proposal_id="accepted-topology",
            idempotency_key="accepted-topology",
            proposer=_model_actor("proposer"),
            approval=_approval(),
            event=event,
        )
        requests = ()
        contributions = ()
        topology_events = (event,)
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    transaction = StoredTransaction(
        proposal=proposal,
        proposal_hash=sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json"))),
        decision=decision,
        created_at=NOW,
    )
    audit_payload = {
        "proposal": proposal.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "transaction_persisted": True,
        **audited_policy_fields,
    }
    audit = append_event(None, "transaction_decision", audit_payload, NOW)
    reader = _AcceptedCollaborationHistoryReader(
        governing_policy_hash=POLICY_HASH,
        transactions=_AllRecords((transaction,)),
        audit=_AllRecords((audit,)),
        requests=_SessionRecords(requests),
        contributions=_SessionRecords(contributions),
        topology_events=_SessionRecords(topology_events),
    )

    with pytest.raises(
        StorageIntegrityError,
        match="collaboration history lacks accepted provenance",
    ):
        reader.list_for_session(session.session_id)


def test_topology_handler_accepts_current_event_and_rejects_stale_topology(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    first = _topology_event(session)
    first_proposal = AppendTopologyEvent(
        proposal_id="topology-1",
        idempotency_key="topology-1",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        event=first,
    )
    handler = AppendTopologyEventHandler()
    accepted = handler.decide(
        first_proposal,
        handler.build_context(first_proposal, _HistoryReads(v2_policy_snapshot, session)),
    )
    stale = _topology_event(session, sequence=2)
    stale_proposal = AppendTopologyEvent(
        proposal_id="topology-2",
        idempotency_key="topology-2",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        event=stale,
    )
    rejected = handler.decide(
        stale_proposal,
        handler.build_context(
            stale_proposal,
            _HistoryReads(v2_policy_snapshot, session, (first,)),
        ),
    )

    assert accepted.accepted is True
    assert rejected.reasons[0].code is RejectionCode.DERIVATION_MISMATCH


def test_termination_handler_recomputes_current_completed_state_and_rejects_duplicate(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    factory = session_factory_fixture.__wrapped__()
    session = factory("peer-a", "peer-b", completion_count=1)
    request = _request(session)
    contribution = _contribution_proposal(session)
    rebuilt = rebuild_collaboration_state(session, (request, contribution))
    assert rebuilt is not None
    termination = evaluate_termination(rebuilt.state)
    proposal = RecordCollaborationTermination(
        proposal_id="terminate-session",
        idempotency_key="terminate-session",
        proposer=_model_actor("proposer"),
        approval=_approval(),
        session_id=session.session_id,
        termination=termination,
    )
    handler = RecordCollaborationTerminationHandler()
    accepted = handler.decide(
        proposal,
        handler.build_context(
            proposal,
            _HistoryReads(v2_policy_snapshot, session, (request, contribution)),
        ),
    )
    duplicate = handler.decide(
        proposal,
        handler.build_context(
            proposal,
            _HistoryReads(
                v2_policy_snapshot,
                session,
                (request, contribution),
                termination,
            ),
        ),
    )

    assert accepted.accepted is True
    assert duplicate.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS


def test_collaboration_handlers_reject_missing_or_forged_approval_and_policy_drift(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    proposer = _model_actor("proposer")
    request = _request(session)
    handler = AppendPeerRequestHandler()
    missing = AppendPeerRequest(
        proposal_id="missing-approval",
        idempotency_key="missing-approval",
        proposer=proposer,
        request=request,
    )
    forged = AppendPeerRequest(
        proposal_id="forged-approval",
        idempotency_key="forged-approval",
        proposer=proposer,
        approval=Approval(approver=proposer, approved_at=NOW),
        request=request,
    )
    valid = AppendPeerRequest(
        proposal_id="policy-drift",
        idempotency_key="policy-drift",
        proposer=proposer,
        approval=_approval(),
        request=request,
    )
    drifted_policy = v2_policy_snapshot.model_copy(update={"policy_hash": "e" * 64})

    missing_decision = handler.decide(
        missing,
        handler.build_context(missing, _HistoryReads(v2_policy_snapshot, session)),
    )
    forged_decision = handler.decide(
        forged,
        handler.build_context(forged, _HistoryReads(v2_policy_snapshot, session)),
    )
    drifted_decision = handler.decide(
        valid,
        handler.build_context(valid, _HistoryReads(drifted_policy, session)),
    )

    assert missing_decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    assert forged_decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    assert drifted_decision.reasons[0].code is RejectionCode.POLICY_HASH_MISMATCH


def test_duplicate_contribution_id_is_rejected_and_rejected_decision_cannot_project(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    first_request = _request(session)
    first = _contribution_proposal(session, proposal_id="first-contribution")
    second_request = _second_request(
        session,
        remaining_budget=session.remaining_resources((first.usage,)),
    )
    duplicate_record = PeerContribution.build(
        **(
            _contribution(session, peer_id="peer-b").model_dump(
                mode="python", exclude={"content_hash"}
            )
            | {
                "request_id": second_request.request_id,
                "parent_contribution_ids": ("contribution-1",),
            }
        )
    )
    duplicate = _contribution_proposal(
        session,
        proposal_id="duplicate-contribution",
        contribution=duplicate_record,
    )
    handler = AppendPeerContributionHandler()
    decision = handler.decide(
        duplicate,
        handler.build_context(
            duplicate,
            _HistoryReads(
                v2_policy_snapshot,
                session,
                (first_request, first, second_request),
            ),
        ),
    )
    writes = _Writes()

    with pytest.raises(ValueError, match="rejected proposals cannot be projected"):
        handler.project(duplicate, decision, writes)

    assert decision.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS
    assert writes.records == []


def test_contribution_rejects_request_or_peer_mismatch(
    v2_policy_snapshot: PolicySnapshot,
) -> None:
    session = _session()
    policy = v2_policy_snapshot.model_copy(update={"policy_hash": POLICY_HASH})
    contribution = _contribution(session, peer_id="peer-b")
    proposal = _contribution_proposal(session, contribution=contribution)
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
    first_proposal = _contribution_proposal(
        session,
        proposal_id="append-contribution-1",
        contribution=first_contribution,
    )
    proposal = _contribution_proposal(
        session,
        proposal_id="append-contribution-2",
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
                (first_request, first_proposal, second_request),
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
    proposal = _contribution_proposal(
        session,
        proposal_id="append-contribution-rollback",
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
