from __future__ import annotations

from collections import Counter

from super_scientist.domain.collaboration.models import (
    CollaborationSession,
    CollaborationState,
    CollaborationTermination,
    CollaborationTransition,
    CollaborationTransitionKind,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
    TopologySnapshot,
    _apply_topology_operation,
    _completion_satisfied,
    collaboration_cycle_projection_hash,
    collaboration_semantic_state_hash,
    collaboration_termination_reason,
    eligible_peer_ids,
    sum_usage,
    usage_matches,
)
from super_scientist.domain.improvement.models import ResourceUsage, usage_within_budget


def initial_collaboration_state(session: CollaborationSession) -> CollaborationState:
    active = set(session.initial_active_peer_ids)
    topology = TopologySnapshot.build(
        active_peer_ids=session.initial_active_peer_ids,
        enabled_edges=tuple(
            edge for edge in session.declared_edges if edge[0] in active and edge[1] in active
        ),
    )
    zero = ResourceUsage(
        cost_usd=0.0,
        compute_units=0.0,
        tokens=0,
        elapsed_seconds=0.0,
        tool_calls=0,
        human_interventions=0,
    )
    semantic_hash = collaboration_semantic_state_hash(
        session=session,
        topology=topology,
        prior_topology_hash=None,
        topology_event_count=0,
        topology_churn_count=0,
        peer_contribution_counts=Counter(),
        contribution_kind_counts=Counter(),
        last_peer_id=None,
        usage=zero,
        request_ids=(),
        contribution_depths=(),
        completed=False,
    )
    cycle_projection = collaboration_cycle_projection_hash(
        session=session,
        topology=topology,
        peer_contribution_counts=Counter(),
        contribution_kind_counts=Counter(),
        last_peer_id=None,
        usage=zero,
        completed=False,
    )
    return CollaborationState.build(
        session=session,
        topology=topology,
        topology_history=(topology,),
        topology_events=(),
        requests=(),
        contributions=(),
        usage_history=(),
        usage=zero,
        hop_count=0,
        scheduling_position=0,
        transitions=(),
        observed_state_hashes=(semantic_hash,),
        cycle_projection_hashes=(cycle_projection,),
        completed=False,
    )


def _require_matching_session(session: CollaborationSession, state: CollaborationState) -> None:
    if state.session != session:
        raise ValueError("collaboration state must bind the exact session snapshot")


def _eligible_peer_ids(session: CollaborationSession, state: CollaborationState) -> tuple[str, ...]:
    return eligible_peer_ids(session, state.topology, state.contributions)


def next_peer(session: CollaborationSession, state: CollaborationState) -> str | None:
    _require_matching_session(session, state)
    if evaluate_termination(state).terminated:
        return None
    eligible = _eligible_peer_ids(session, state)
    return eligible[0] if eligible else None


def evaluate_termination(state: CollaborationState) -> CollaborationTermination:
    return CollaborationTermination(
        reason=collaboration_termination_reason(
            session=state.session,
            topology=state.topology,
            topology_history=state.topology_history,
            topology_events=state.topology_events,
            contributions=state.contributions,
            cycle_projection_hashes=state.cycle_projection_hashes,
            completed=state.completed,
        )
    )


def _require_artifacts_and_tools(
    session: CollaborationSession,
    request: PeerRequest,
    contribution: PeerContribution,
) -> None:
    allowed_artifacts = set(session.allowed_artifacts)
    if not set(request.artifact_refs).issubset(allowed_artifacts):
        raise ValueError("peer request contains an undeclared artifact")
    if not set(contribution.artifact_refs).issubset(allowed_artifacts):
        raise ValueError("peer contribution contains an undeclared artifact")
    allowed_tools = set(session.budget.allowed_tool_ids)
    if not set(request.tool_ids).issubset(allowed_tools):
        raise ValueError("peer request contains an undeclared tool")
    if not set(contribution.tool_ids).issubset(allowed_tools):
        raise ValueError("peer contribution contains an undeclared tool")


def _require_parents(
    session: CollaborationSession,
    state: CollaborationState,
    request: PeerRequest,
    contribution: PeerContribution,
) -> int:
    known = {item.contribution_id: item for item in state.contributions}
    if request.parent_contribution_id is not None and request.parent_contribution_id not in known:
        raise ValueError("peer request parent must be a declared prior contribution")
    if not set(contribution.parent_contribution_ids).issubset(known):
        raise ValueError("peer contribution parent must be a declared prior contribution")
    if (
        request.parent_contribution_id is not None
        and request.parent_contribution_id not in contribution.parent_contribution_ids
    ):
        raise ValueError("peer contribution must retain its request parent")
    depths: dict[str, int] = {}
    for item in state.contributions:
        depths[item.contribution_id] = (
            0
            if not item.parent_contribution_ids
            else 1 + max(depths[parent] for parent in item.parent_contribution_ids)
        )
    depth = (
        0
        if not contribution.parent_contribution_ids
        else 1 + max(depths[parent] for parent in contribution.parent_contribution_ids)
    )
    if depth > session.budget.max_parent_depth:
        raise ValueError("peer contribution parent depth exceeds collaboration budget")
    return depth


def _topology_churn_count(history: tuple[TopologySnapshot, ...]) -> int:
    return sum(
        history[index].content_hash == history[index - 2].content_hash
        for index in range(2, len(history))
    )


def _contribution_depths(
    contributions: tuple[PeerContribution, ...],
) -> tuple[tuple[str, int], ...]:
    depths: dict[str, int] = {}
    for contribution in contributions:
        depths[contribution.contribution_id] = (
            0
            if not contribution.parent_contribution_ids
            else 1 + max(depths[parent] for parent in contribution.parent_contribution_ids)
        )
    return tuple(sorted(depths.items()))


def advance_collaboration(
    session: CollaborationSession,
    state: CollaborationState,
    request: PeerRequest,
    contribution: PeerContribution,
    usage: ResourceUsage,
) -> CollaborationState:
    _require_matching_session(session, state)
    termination = evaluate_termination(state)
    if termination.terminated:
        raise ValueError(f"collaboration is terminated: {termination.reason}")
    expected_peer = next_peer(session, state)
    if request.recipient_id != expected_peer:
        raise ValueError("peer request recipient is not the expected peer")
    expected_sequence = len(state.requests) + 1
    if request.session_id != session.session_id or request.sequence != expected_sequence:
        raise ValueError("peer request must bind the session and next sequence")
    expected_sender = state.contributions[-1].peer_id if state.contributions else None
    if request.sender_id != expected_sender:
        raise ValueError("peer request sender must match the prior contributing peer")
    if contribution.session_id != session.session_id:
        raise ValueError("peer contribution must bind the collaboration session")
    if contribution.request_id != request.request_id:
        raise ValueError("peer contribution must bind the peer request")
    if contribution.peer_id != request.recipient_id:
        raise ValueError("peer contribution peer must match request recipient")
    if contribution.contribution_id in {item.contribution_id for item in state.contributions}:
        raise ValueError("peer contribution identifier must be unique")
    capability_ids = {
        assessment.requirement.capability_id
        for member in session.cohort_plan.members
        if member.actor_id == request.recipient_id
        for assessment in member.assessments
    }
    if request.requested_capability_id not in capability_ids:
        raise ValueError("peer request capability is not declared for the expected peer")
    if contribution.contribution_kind not in session.allowed_contribution_kinds:
        raise ValueError("peer contribution kind is not allowed")
    _require_artifacts_and_tools(session, request, contribution)
    _require_parents(session, state, request, contribution)
    expected_remaining = session.remaining_resources(state.usage)
    if not usage_matches(request.remaining_budget, expected_remaining):
        raise ValueError("peer request remaining budget does not match current usage")
    updated_usage = sum_usage((*state.usage_history, usage))
    if not usage_within_budget(updated_usage, session.budget.resources):
        raise ValueError("peer transition exceeds the collaboration resource budget")
    contributions = (*state.contributions, contribution)
    completed = _completion_satisfied(session, contributions)
    semantic_hash = collaboration_semantic_state_hash(
        session=session,
        topology=state.topology,
        prior_topology_hash=(
            state.topology_history[-2].content_hash
            if len(state.topology_history) >= 2
            else None
        ),
        topology_event_count=len(state.topology_events),
        topology_churn_count=_topology_churn_count(state.topology_history),
        peer_contribution_counts=Counter(item.peer_id for item in contributions),
        contribution_kind_counts=Counter(
            item.contribution_kind for item in contributions
        ),
        last_peer_id=contribution.peer_id,
        usage=updated_usage,
        request_ids=tuple(sorted(item.request_id for item in (*state.requests, request))),
        contribution_depths=_contribution_depths(contributions),
        completed=completed,
    )
    cycle_projection = collaboration_cycle_projection_hash(
        session=session,
        topology=state.topology,
        peer_contribution_counts=Counter(item.peer_id for item in contributions),
        contribution_kind_counts=Counter(
            item.contribution_kind for item in contributions
        ),
        last_peer_id=contribution.peer_id,
        usage=updated_usage,
        completed=completed,
    )
    return CollaborationState.build(
        session=session,
        topology=state.topology,
        topology_history=state.topology_history,
        topology_events=state.topology_events,
        requests=(*state.requests, request),
        contributions=contributions,
        usage_history=(*state.usage_history, usage),
        usage=updated_usage,
        hop_count=state.hop_count + 1,
        scheduling_position=state.scheduling_position + 1,
        transitions=(
            *state.transitions,
            CollaborationTransition(
                position=state.scheduling_position + 1,
                kind=CollaborationTransitionKind.PEER_EXCHANGE,
                request_id=request.request_id,
                contribution_id=contribution.contribution_id,
                topology_event_id=None,
            ),
        ),
        observed_state_hashes=(*state.observed_state_hashes, semantic_hash),
        cycle_projection_hashes=(
            *state.cycle_projection_hashes,
            cycle_projection,
        ),
        completed=completed,
    )


def apply_topology_event(
    session: CollaborationSession,
    state: CollaborationState,
    event: TopologyEvent,
) -> CollaborationState:
    _require_matching_session(session, state)
    termination = evaluate_termination(state)
    if termination.terminated:
        raise ValueError(f"collaboration is terminated: {termination.reason}")
    if len(state.topology_events) >= session.budget.max_topology_changes:
        raise ValueError("collaboration topology change limit reached")
    if event.session_id != session.session_id or event.sequence != len(state.topology_events) + 1:
        raise ValueError("topology event must bind the session and next event sequence")
    if event.before_topology_hash != state.topology.content_hash:
        raise ValueError("topology event before topology hash does not match current topology")
    after = _apply_topology_operation(session, state.topology, event)
    if event.after_topology_hash != after.content_hash:
        raise ValueError("topology event after topology hash does not match its operation")
    semantic_hash = collaboration_semantic_state_hash(
        session=session,
        topology=after,
        prior_topology_hash=state.topology.content_hash,
        topology_event_count=len(state.topology_events) + 1,
        topology_churn_count=(
            _topology_churn_count(state.topology_history)
            + int(
                len(state.topology_history) >= 2
                and after.content_hash == state.topology_history[-2].content_hash
            )
        ),
        peer_contribution_counts=Counter(
            item.peer_id for item in state.contributions
        ),
        contribution_kind_counts=Counter(
            item.contribution_kind for item in state.contributions
        ),
        last_peer_id=(
            state.contributions[-1].peer_id if state.contributions else None
        ),
        usage=state.usage,
        request_ids=tuple(sorted(item.request_id for item in state.requests)),
        contribution_depths=_contribution_depths(state.contributions),
        completed=state.completed,
    )
    cycle_projection = collaboration_cycle_projection_hash(
        session=session,
        topology=after,
        peer_contribution_counts=Counter(
            item.peer_id for item in state.contributions
        ),
        contribution_kind_counts=Counter(
            item.contribution_kind for item in state.contributions
        ),
        last_peer_id=(
            state.contributions[-1].peer_id if state.contributions else None
        ),
        usage=state.usage,
        completed=state.completed,
    )
    return CollaborationState.build(
        session=session,
        topology=after,
        topology_history=(*state.topology_history, after),
        topology_events=(*state.topology_events, event),
        requests=state.requests,
        contributions=state.contributions,
        usage_history=state.usage_history,
        usage=state.usage,
        hop_count=state.hop_count,
        scheduling_position=state.scheduling_position + 1,
        transitions=(
            *state.transitions,
            CollaborationTransition(
                position=state.scheduling_position + 1,
                kind=CollaborationTransitionKind.TOPOLOGY_EVENT,
                request_id=None,
                contribution_id=None,
                topology_event_id=event.event_id,
            ),
        ),
        observed_state_hashes=(*state.observed_state_hashes, semantic_hash),
        cycle_projection_hashes=(
            *state.cycle_projection_hashes,
            cycle_projection,
        ),
        completed=state.completed,
    )


__all__ = [
    "advance_collaboration",
    "apply_topology_event",
    "evaluate_termination",
    "initial_collaboration_state",
    "next_peer",
]
