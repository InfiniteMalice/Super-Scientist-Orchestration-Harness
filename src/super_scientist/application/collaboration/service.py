from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.application.cognition.service import (
    governed_cognitive_authority_rejection,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.cognition import CohortPlan
from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationState,
    CollaborationTermination,
    PeerRequest,
    TopologyEvent,
    advance_collaboration,
    apply_topology_event,
    evaluate_termination,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.collaboration.models import exact_usage_within_budget
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RejectionCode,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )

type CollaborationHistoryRecord = PeerRequest | AppendPeerContribution | TopologyEvent


class CollaborationSessionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_cohort_plan(self, cohort_plan_id: str) -> CohortPlan | None: ...

    def get_session(self, session_id: str) -> CollaborationSession | None: ...


class CollaborationHistoryReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_session(self, session_id: str) -> CollaborationSession | None: ...

    def list_history(self, session_id: str) -> tuple[CollaborationHistoryRecord, ...]: ...

    def get_termination(self, session_id: str) -> CollaborationTermination | None: ...


class _CollaborationSessionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    retained_cohort: CohortPlan | None
    existing_session: CollaborationSession | None


class _CollaborationHistoryContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    session: CollaborationSession | None
    state: CollaborationState | None
    pending_request: PeerRequest | None
    request_ids: frozenset[str]
    contribution_ids: frozenset[str]
    topology_event_ids: frozenset[str]
    existing_termination: CollaborationTermination | None


@dataclass(frozen=True, slots=True)
class _RebuiltCollaboration:
    state: CollaborationState
    pending_request: PeerRequest | None
    request_ids: frozenset[str]
    contribution_ids: frozenset[str]
    topology_event_ids: frozenset[str]


class RecordCollaborationSessionHandler:
    proposal_type = "record_collaboration_session"

    def build_context(
        self,
        proposal: RecordCollaborationSession,
        reads: HandlerReadCapability,
    ) -> _CollaborationSessionContext:
        capability = cast(CollaborationSessionReadCapability, reads)
        session = proposal.session
        return _CollaborationSessionContext(
            active_policy=capability.policy_snapshot(),
            retained_cohort=capability.get_cohort_plan(session.cohort_plan.cohort_plan_id),
            existing_session=capability.get_session(session.session_id),
        )

    def decide(
        self,
        proposal: RecordCollaborationSession,
        context: _CollaborationSessionContext,
    ) -> TransactionDecision:
        authority = governed_cognitive_authority_rejection(proposal, context.active_policy)
        if authority is not None:
            return authority
        session = proposal.session
        if session.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "collaboration session")
        if context.retained_cohort is None or context.retained_cohort != session.cohort_plan:
            return _stale_reference(proposal.proposal_id, "collaboration cohort plan")
        if not _session_is_canonical(session):
            return _derivation_mismatch(
                proposal.proposal_id,
                "collaboration session does not match its canonical declared envelope",
            )
        if context.existing_session is not None:
            return _already_exists(proposal.proposal_id, "collaboration session")
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: RecordCollaborationSession,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.session)


class _HistoryHandlerBase:
    def _build_history_context(
        self,
        session_id: str,
        reads: HandlerReadCapability,
    ) -> _CollaborationHistoryContext:
        capability = cast(CollaborationHistoryReadCapability, reads)
        session = capability.get_session(session_id)
        rebuilt = (
            None
            if session is None
            else rebuild_collaboration_state(session, capability.list_history(session_id))
        )
        return _CollaborationHistoryContext(
            active_policy=capability.policy_snapshot(),
            session=session,
            state=None if rebuilt is None else rebuilt.state,
            pending_request=None if rebuilt is None else rebuilt.pending_request,
            request_ids=frozenset() if rebuilt is None else rebuilt.request_ids,
            contribution_ids=frozenset() if rebuilt is None else rebuilt.contribution_ids,
            topology_event_ids=frozenset() if rebuilt is None else rebuilt.topology_event_ids,
            existing_termination=capability.get_termination(session_id),
        )

    @staticmethod
    def _common_rejection(
        proposal: AppendPeerRequest
        | AppendPeerContribution
        | AppendTopologyEvent
        | RecordCollaborationTermination,
        context: _CollaborationHistoryContext,
    ) -> TransactionDecision | None:
        authority = governed_cognitive_authority_rejection(proposal, context.active_policy)
        if authority is not None:
            return authority
        if context.session is None:
            return _stale_reference(proposal.proposal_id, "collaboration session")
        if context.session.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_rejection(proposal.proposal_id, "collaboration history")
        if context.state is None:
            return _stale_reference(proposal.proposal_id, "collaboration history")
        return None


class AppendPeerRequestHandler(_HistoryHandlerBase):
    proposal_type = "append_peer_request"

    def build_context(
        self,
        proposal: AppendPeerRequest,
        reads: HandlerReadCapability,
    ) -> _CollaborationHistoryContext:
        return self._build_history_context(proposal.request.session_id, reads)

    def decide(
        self,
        proposal: AppendPeerRequest,
        context: _CollaborationHistoryContext,
    ) -> TransactionDecision:
        common = self._common_rejection(proposal, context)
        if common is not None:
            return common
        request = proposal.request
        session = cast(CollaborationSession, context.session)
        state = cast(CollaborationState, context.state)
        if request.request_id in context.request_ids:
            return _already_exists(proposal.proposal_id, "peer request")
        if context.pending_request is not None:
            return _derivation_mismatch(
                proposal.proposal_id,
                "peer request cannot bypass the current unanswered request",
            )
        termination = evaluate_termination(state)
        if termination.terminated:
            return _bound_rejection(proposal.proposal_id)
        expected_peer = next_peer(session, state)
        expected_sender = state.contributions[-1].peer_id if state.contributions else None
        allowed_capabilities = {
            requirement.capability_id
            for requirement in (
                session.cohort_plan.request_snapshot.required_capabilities
                + session.cohort_plan.request_snapshot.preferred_capabilities
            )
        }
        known_contributions = {item.contribution_id for item in state.contributions}
        expected_remaining = session.remaining_resources(state.usage_history)
        valid = (
            request.session_id == session.session_id
            and request.sequence == len(state.contributions) + 1
            and request.recipient_id == expected_peer
            and request.sender_id == expected_sender
            and request.requested_capability_id in allowed_capabilities
            and set(request.tool_ids).issubset(session.budget.allowed_tool_ids)
            and set(request.artifact_refs).issubset(session.allowed_artifacts)
            and (
                request.parent_contribution_id is None
                or request.parent_contribution_id in known_contributions
            )
            and request.remaining_budget == expected_remaining
        )
        if not valid:
            return _derivation_mismatch(
                proposal.proposal_id,
                "peer request does not match the recomputed collaboration state",
            )
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: AppendPeerRequest,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.request)


class AppendPeerContributionHandler(_HistoryHandlerBase):
    proposal_type = "append_peer_contribution"

    def build_context(
        self,
        proposal: AppendPeerContribution,
        reads: HandlerReadCapability,
    ) -> _CollaborationHistoryContext:
        return self._build_history_context(proposal.contribution.session_id, reads)

    def decide(
        self,
        proposal: AppendPeerContribution,
        context: _CollaborationHistoryContext,
    ) -> TransactionDecision:
        common = self._common_rejection(proposal, context)
        if common is not None:
            return common
        contribution = proposal.contribution
        if evaluate_termination(cast(CollaborationState, context.state)).terminated:
            return _bound_rejection(proposal.proposal_id)
        if contribution.contribution_id in context.contribution_ids:
            return _already_exists(proposal.proposal_id, "peer contribution")
        request = context.pending_request
        if request is None or request.request_id != contribution.request_id:
            return _stale_reference(proposal.proposal_id, "peer request")
        try:
            advance_collaboration(
                cast(CollaborationSession, context.session),
                cast(CollaborationState, context.state),
                request,
                contribution,
                proposal.usage,
            )
        except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
            state = cast(CollaborationState, context.state)
            session = cast(CollaborationSession, context.session)
            if not exact_usage_within_budget(
                (*state.usage_history, proposal.usage),
                session.budget.resources,
            ):
                return _bound_rejection(proposal.proposal_id)
            return _derivation_mismatch(
                proposal.proposal_id,
                "peer contribution does not match the recomputed collaboration transition",
            )
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: AppendPeerContribution,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        # The contribution repository retains the projected record; accepted
        # transaction history retains the enclosing request for exact replay.
        writes.append_authoritative(proposal.contribution)


class AppendTopologyEventHandler(_HistoryHandlerBase):
    proposal_type = "append_topology_event"

    def build_context(
        self,
        proposal: AppendTopologyEvent,
        reads: HandlerReadCapability,
    ) -> _CollaborationHistoryContext:
        return self._build_history_context(proposal.event.session_id, reads)

    def decide(
        self,
        proposal: AppendTopologyEvent,
        context: _CollaborationHistoryContext,
    ) -> TransactionDecision:
        common = self._common_rejection(proposal, context)
        if common is not None:
            return common
        if proposal.event.event_id in context.topology_event_ids:
            return _already_exists(proposal.proposal_id, "topology event")
        try:
            apply_topology_event(
                cast(CollaborationSession, context.session),
                cast(CollaborationState, context.state),
                proposal.event,
            )
        except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
            state = cast(CollaborationState, context.state)
            if (
                evaluate_termination(state).terminated
                or len(state.topology_events)
                >= cast(CollaborationSession, context.session).budget.max_topology_changes
            ):
                return _bound_rejection(proposal.proposal_id)
            return _derivation_mismatch(
                proposal.proposal_id,
                "topology event does not match the recomputed collaboration transition",
            )
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: AppendTopologyEvent,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.event)


class RecordCollaborationTerminationHandler(_HistoryHandlerBase):
    proposal_type = "record_collaboration_termination"

    def build_context(
        self,
        proposal: RecordCollaborationTermination,
        reads: HandlerReadCapability,
    ) -> _CollaborationHistoryContext:
        return self._build_history_context(proposal.session_id, reads)

    def decide(
        self,
        proposal: RecordCollaborationTermination,
        context: _CollaborationHistoryContext,
    ) -> TransactionDecision:
        common = self._common_rejection(proposal, context)
        if common is not None:
            return common
        if context.existing_termination is not None:
            return _already_exists(proposal.proposal_id, "collaboration termination")
        expected = evaluate_termination(cast(CollaborationState, context.state))
        if not expected.terminated or expected != proposal.termination:
            return _derivation_mismatch(
                proposal.proposal_id,
                "collaboration termination does not match recomputed current state",
            )
        return _accepted(proposal.proposal_id)

    def project(
        self,
        proposal: RecordCollaborationTermination,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.termination)


def rebuild_collaboration_state(
    session: CollaborationSession,
    history: tuple[CollaborationHistoryRecord, ...],
) -> _RebuiltCollaboration | None:
    state = initial_collaboration_state(session)
    pending: PeerRequest | None = None
    request_ids: set[str] = set()
    contribution_ids: set[str] = set()
    topology_event_ids: set[str] = set()
    try:
        for record in history:
            if isinstance(record, PeerRequest):
                if pending is not None or record.request_id in request_ids:
                    return None
                pending = record
                request_ids.add(record.request_id)
                continue
            if isinstance(record, AppendPeerContribution):
                contribution = record.contribution
                if (
                    pending is None
                    or pending.request_id != contribution.request_id
                    or contribution.contribution_id in contribution_ids
                ):
                    return None
                state = advance_collaboration(
                    session,
                    state,
                    pending,
                    contribution,
                    record.usage,
                )
                contribution_ids.add(contribution.contribution_id)
                pending = None
                continue
            if not isinstance(record, TopologyEvent) or record.event_id in topology_event_ids:
                return None
            state = apply_topology_event(session, state, record)
            topology_event_ids.add(record.event_id)
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return None
    return _RebuiltCollaboration(
        state=state,
        pending_request=pending,
        request_ids=frozenset(request_ids),
        contribution_ids=frozenset(contribution_ids),
        topology_event_ids=frozenset(topology_event_ids),
    )


def _session_is_canonical(session: CollaborationSession) -> bool:
    try:
        rebuilt = CollaborationSession.build(
            **session.model_dump(mode="python", exclude={"content_hash"}, warnings=False)
        )
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return False
    return rebuilt == session


def _accepted(proposal_id: str) -> TransactionDecision:
    return TransactionDecision(proposal_id=proposal_id, accepted=True)


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)


def _already_exists(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.ENTITY_ALREADY_EXISTS,
        f"{label} already exists",
    )


def _stale_reference(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.STALE_REFERENCE,
        f"{label} does not resolve to exact current accepted state",
    )


def _policy_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.POLICY_HASH_MISMATCH,
        f"{label} must name the exact active governance policy",
    )


def _derivation_mismatch(proposal_id: str, message: str) -> TransactionDecision:
    return _rejected(proposal_id, RejectionCode.DERIVATION_MISMATCH, message)


def _bound_rejection(proposal_id: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.COLLABORATION_BOUND_EXCEEDED,
        "collaboration cannot accept another transition under its declared bounds",
    )


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")


__all__ = [
    "AppendPeerContributionHandler",
    "AppendPeerRequestHandler",
    "AppendTopologyEventHandler",
    "CollaborationHistoryReadCapability",
    "CollaborationHistoryRecord",
    "CollaborationSessionReadCapability",
    "RecordCollaborationSessionHandler",
    "RecordCollaborationTerminationHandler",
    "rebuild_collaboration_state",
]
