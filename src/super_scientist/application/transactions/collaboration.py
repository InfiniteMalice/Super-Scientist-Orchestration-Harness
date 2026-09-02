from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Connection

from super_scientist.application.collaboration.service import (
    AppendPeerContributionHandler,
    AppendPeerRequestHandler,
    AppendTopologyEventHandler,
    CollaborationHistoryRecord,
    RecordCollaborationSessionHandler,
    RecordCollaborationTerminationHandler,
)
from super_scientist.application.transactions.contracts import ProposalHandler
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.cognition import CohortPlan
from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationTermination,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
)
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    TransactionDecision,
    parse_untrusted_proposal_json,
)
from super_scientist.providers.storage.cognitive_records import (
    CohortPlanRepository,
    CollaborationSessionRepository,
    CollaborationTerminationRepository,
    PeerContributionRepository,
    PeerRequestRepository,
    TopologyEventRepository,
)
from super_scientist.providers.storage.repositories import (
    AuditRepository,
    StorageIntegrityError,
    StoredTransaction,
    TransactionRepository,
)

type FixedCollaborationHandler = ProposalHandler[BaseModel, BaseModel]
type CollaborationProposal = (
    RecordCollaborationSession
    | AppendPeerRequest
    | AppendPeerContribution
    | AppendTopologyEvent
    | RecordCollaborationTermination
)


@dataclass(frozen=True, slots=True)
class _AcceptedCollaborationHistoryReader:
    governing_policy_hash: str
    transactions: TransactionRepository
    audit: AuditRepository
    requests: PeerRequestRepository
    contributions: PeerContributionRepository
    topology_events: TopologyEventRepository

    def list_for_session(self, session_id: str) -> tuple[CollaborationHistoryRecord, ...]:
        request_by_id = {
            item.request_id: item for item in self.requests.list_for_session(session_id)
        }
        contribution_by_id = {
            item.contribution_id: item for item in self.contributions.list_for_session(session_id)
        }
        event_by_id = {
            item.event_id: item for item in self.topology_events.list_for_session(session_id)
        }
        history: list[CollaborationHistoryRecord] = []
        accepted_transactions = {
            transaction.proposal.proposal_id: transaction
            for transaction in self.transactions.list_all()
            if transaction.decision.accepted
            and isinstance(
                transaction.proposal,
                (AppendPeerRequest, AppendPeerContribution, AppendTopologyEvent),
            )
            and _collaboration_session_id(transaction.proposal) == session_id
        }
        seen_transaction_ids: set[str] = set()
        for event in self.audit.list_all():
            transaction = _transaction_for_audit_event(
                event,
                accepted_transactions,
                self.governing_policy_hash,
            )
            if transaction is None:
                continue
            if transaction.proposal.proposal_id in seen_transaction_ids:
                raise StorageIntegrityError(
                    "storage integrity error: collaboration transaction has duplicate audit"
                )
            seen_transaction_ids.add(transaction.proposal.proposal_id)
            proposal = transaction.proposal
            if (
                isinstance(proposal, AppendPeerRequest)
                and proposal.request.session_id == session_id
            ):
                retained = request_by_id.pop(proposal.request.request_id, None)
                if retained != proposal.request:
                    raise StorageIntegrityError(
                        "storage integrity error: accepted peer request history mismatch"
                    )
                history.append(retained)
            elif (
                isinstance(proposal, AppendPeerContribution)
                and proposal.contribution.session_id == session_id
            ):
                retained_contribution = contribution_by_id.pop(
                    proposal.contribution.contribution_id,
                    None,
                )
                if retained_contribution != proposal.contribution:
                    raise StorageIntegrityError(
                        "storage integrity error: accepted peer contribution history mismatch"
                    )
                history.append(proposal)
            elif (
                isinstance(proposal, AppendTopologyEvent)
                and proposal.event.session_id == session_id
            ):
                retained_event = event_by_id.pop(proposal.event.event_id, None)
                if retained_event != proposal.event:
                    raise StorageIntegrityError(
                        "storage integrity error: accepted topology event history mismatch"
                    )
                history.append(retained_event)
        if request_by_id or contribution_by_id or event_by_id:
            raise StorageIntegrityError(
                "storage integrity error: collaboration history lacks accepted provenance"
            )
        if seen_transaction_ids != set(accepted_transactions):
            raise StorageIntegrityError(
                "storage integrity error: collaboration transaction lacks exact audit"
            )
        return tuple(history)


@dataclass(frozen=True, slots=True)
class CollaborationCapabilities:
    active_policy: PolicySnapshot
    proposal: CollaborationProposal
    cohorts: CohortPlanRepository
    sessions: CollaborationSessionRepository
    requests: PeerRequestRepository
    contributions: PeerContributionRepository
    topology_events: TopologyEventRepository
    terminations: CollaborationTerminationRepository
    history: _AcceptedCollaborationHistoryReader
    created_at: UtcTimestamp

    def policy_snapshot(self) -> PolicySnapshot:
        return self.active_policy

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None:
        return self.cohorts.get(cohort_plan_id)

    def get_session(self, session_id: str) -> CollaborationSession | None:
        return self.sessions.get(session_id)

    def list_history(self, session_id: str) -> tuple[CollaborationHistoryRecord, ...]:
        return self.history.list_for_session(session_id)

    def get_termination(self, session_id: str) -> CollaborationTermination | None:
        return self.terminations.get(session_id)

    def append_authoritative(self, record: BaseModel) -> None:
        proposal = self.proposal
        if (
            isinstance(proposal, RecordCollaborationSession)
            and type(record) is CollaborationSession
            and record == proposal.session
        ):
            self.sessions.add_from_proposal(
                proposal,
                created_at=self.created_at,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=self.active_policy.policy_hash,
            )
            return
        if (
            isinstance(proposal, AppendPeerRequest)
            and type(record) is PeerRequest
            and record == proposal.request
        ):
            self.requests.add_from_proposal(
                proposal,
                created_at=self.created_at,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=self.active_policy.policy_hash,
            )
            return
        if (
            isinstance(proposal, AppendPeerContribution)
            and type(record) is PeerContribution
            and record == proposal.contribution
        ):
            self.contributions.add_from_proposal(
                proposal,
                created_at=self.created_at,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=self.active_policy.policy_hash,
            )
            return
        if (
            isinstance(proposal, AppendTopologyEvent)
            and type(record) is TopologyEvent
            and record == proposal.event
        ):
            self.topology_events.add_from_proposal(
                proposal,
                created_at=self.created_at,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=self.active_policy.policy_hash,
            )
            return
        if (
            isinstance(proposal, RecordCollaborationTermination)
            and type(record) is CollaborationTermination
            and record == proposal.termination
        ):
            self.terminations.add_from_proposal(
                proposal,
                created_at=self.created_at,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=self.active_policy.policy_hash,
            )
            return
        raise TypeError(f"unsupported collaboration record: {type(record)!r}")

    def update_projection(self, record: BaseModel) -> None:
        del record
        raise RuntimeError("collaboration records have no mutable projection")


def fixed_collaboration_handlers() -> tuple[FixedCollaborationHandler, ...]:
    return (  # type: ignore[return-value]
        RecordCollaborationSessionHandler(),
        AppendPeerRequestHandler(),
        AppendPeerContributionHandler(),
        AppendTopologyEventHandler(),
        RecordCollaborationTerminationHandler(),
    )


def collaboration_capabilities(
    proposal: BaseModel,
    connection: Connection,
    active_policy: PolicySnapshot,
    *,
    current_transaction_created_at: UtcTimestamp,
) -> CollaborationCapabilities:
    if not isinstance(
        proposal,
        (
            RecordCollaborationSession,
            AppendPeerRequest,
            AppendPeerContribution,
            AppendTopologyEvent,
            RecordCollaborationTermination,
        ),
    ):
        raise TypeError(f"no fixed collaboration capability for proposal: {type(proposal)!r}")
    requests = PeerRequestRepository(connection)
    contributions = PeerContributionRepository(connection)
    topology_events = TopologyEventRepository(connection)
    return CollaborationCapabilities(
        active_policy=active_policy,
        proposal=proposal,
        cohorts=CohortPlanRepository(connection),
        sessions=CollaborationSessionRepository(connection),
        requests=requests,
        contributions=contributions,
        topology_events=topology_events,
        terminations=CollaborationTerminationRepository(connection),
        history=_AcceptedCollaborationHistoryReader(
            governing_policy_hash=active_policy.policy_hash,
            transactions=TransactionRepository(connection),
            audit=AuditRepository(connection),
            requests=requests,
            contributions=contributions,
            topology_events=topology_events,
        ),
        created_at=current_transaction_created_at,
    )


def _collaboration_session_id(
    proposal: AppendPeerRequest | AppendPeerContribution | AppendTopologyEvent,
) -> str:
    if isinstance(proposal, AppendPeerRequest):
        return proposal.request.session_id
    if isinstance(proposal, AppendPeerContribution):
        return proposal.contribution.session_id
    return proposal.event.session_id


def _transaction_for_audit_event(
    event: AuditEvent,
    transactions: dict[str, StoredTransaction],
    governing_policy_hash: str,
) -> StoredTransaction | None:
    if getattr(event, "event_type", None) != "transaction_decision":
        return None
    try:
        payload = json_compatible_payload(event.payload)
        if (
            payload.get("transaction_persisted") is not True
            or payload.get("policy_hash") != governing_policy_hash
            or payload.get("stored_policy_hash") != governing_policy_hash
        ):
            return None
        proposal = parse_untrusted_proposal_json(canonical_json_bytes(payload["proposal"]))
        decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(payload["decision"]),
            strict=True,
        )
        transaction = transactions.get(proposal.proposal_id)
    except (KeyError, MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        return None
    if transaction is None or transaction.proposal != proposal or transaction.decision != decision:
        return None
    return transaction


__all__ = [
    "CollaborationCapabilities",
    "collaboration_capabilities",
    "fixed_collaboration_handlers",
]
