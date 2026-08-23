from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationState,
    CollaborationTerminationReason,
    CollaborationTransition,
    CollaborationTransitionKind,
    PeerContribution,
    PeerRequest,
    TopologyEvent,
    TopologyOperation,
    TopologySnapshot,
    advance_collaboration,
    apply_topology_event,
    evaluate_termination,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex

from .conftest import artifact, unit_usage


def _advance(
    session: CollaborationSession, state: CollaborationState, sequence: int
) -> CollaborationState:
    peer_id = next_peer(session, state)
    assert peer_id is not None
    parent = None if sequence == 1 else f"contribution-{sequence - 1}"
    request = PeerRequest.build(
        request_id=f"request-{sequence}",
        session_id=session.session_id,
        sequence=sequence,
        sender_id=None if sequence == 1 else state.contributions[-1].peer_id,
        recipient_id=peer_id,
        requested_capability_id="analysis",
        question="Assess.",
        artifact_refs=(artifact(),),
        parent_contribution_id=parent,
        tool_ids=("tool-a",),
        remaining_budget=session.remaining_resources(state.usage),
    )
    contribution = PeerContribution.build(
        contribution_id=f"contribution-{sequence}",
        session_id=session.session_id,
        request_id=request.request_id,
        peer_id=peer_id,
        parent_contribution_ids=() if parent is None else (parent,),
        contribution_kind="analysis",
        rationale_summary="Evidence.",
        candidate_content='{"finding":"supported"}',
        artifact_refs=(artifact(),),
        tool_ids=("tool-a",),
    )
    return advance_collaboration(session, state, request, contribution, unit_usage())


@pytest.mark.parametrize(
    ("session_kwargs", "steps", "reason"),
    (
        (
            {"max_hops": 1, "completion_count": 8},
            1,
            CollaborationTerminationReason.MAX_HOPS_REACHED,
        ),
        (
            {"max_contributions": 1, "completion_count": 8},
            1,
            CollaborationTerminationReason.MAX_CONTRIBUTIONS_REACHED,
        ),
        (
            {"max_per_peer": 1, "completion_count": 8},
            1,
            CollaborationTerminationReason.PER_PEER_LIMIT_REACHED,
        ),
        (
            {"max_share": 0.4, "completion_count": 8},
            2,
            CollaborationTerminationReason.CONTRIBUTION_MONOPOLY,
        ),
        ({"completion_count": 1}, 1, CollaborationTerminationReason.COMPLETED),
    ),
)
def test_contribution_termination_reasons_are_exact(
    session_factory: Callable[..., CollaborationSession],
    session_kwargs: dict[str, object],
    steps: int,
    reason: CollaborationTerminationReason,
) -> None:
    session = session_factory("peer-a", "peer-b", **session_kwargs)
    state = initial_collaboration_state(session)
    for sequence in range(1, steps + 1):
        state = _advance(session, state, sequence)
    assert evaluate_termination(state).reason is reason


def test_no_eligible_peer_terminates_fail_closed(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    state = initial_collaboration_state(session)
    inactive = TopologySnapshot.build(active_peer_ids=(), enabled_edges=())
    event = TopologyEvent.build(
        event_id="event-1",
        session_id=session.session_id,
        sequence=1,
        before_topology_hash=state.topology.content_hash,
        operation=TopologyOperation.DEACTIVATE_PEER,
        peer_id="peer-a",
        edge=None,
        reason_code="UNAVAILABLE",
        after_topology_hash=inactive.content_hash,
    )
    state = apply_topology_event(session, state, event)
    assert evaluate_termination(state).reason is CollaborationTerminationReason.NO_ELIGIBLE_PEER


def test_topology_change_limit_is_bounded(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", max_topology_changes=1)
    state = initial_collaboration_state(session)
    after = TopologySnapshot.build(
        active_peer_ids=("peer-a", "peer-b"), enabled_edges=(("peer-b", "peer-a"),)
    )
    event = TopologyEvent.build(
        event_id="event-1",
        session_id=session.session_id,
        sequence=1,
        before_topology_hash=state.topology.content_hash,
        operation=TopologyOperation.DISABLE_EDGE,
        peer_id=None,
        edge=("peer-a", "peer-b"),
        reason_code="LOAD_BALANCE",
        after_topology_hash=after.content_hash,
    )
    state = apply_topology_event(session, state, event)
    reason = CollaborationTerminationReason.TOPOLOGY_CHANGE_LIMIT_REACHED
    assert evaluate_termination(state).reason is reason


def test_alternating_topology_hashes_trigger_churn(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", max_topology_churn=1)
    state = initial_collaboration_state(session)
    reduced = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    disable = TopologyEvent.build(
        event_id="event-1",
        session_id=session.session_id,
        sequence=1,
        before_topology_hash=state.topology.content_hash,
        operation=TopologyOperation.DISABLE_EDGE,
        peer_id=None,
        edge=("peer-a", "peer-b"),
        reason_code="LOAD_BALANCE",
        after_topology_hash=reduced.content_hash,
    )
    state = apply_topology_event(session, state, disable)
    restored = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=session.declared_edges,
    )
    enable = TopologyEvent.build(
        event_id="event-2",
        session_id=session.session_id,
        sequence=2,
        before_topology_hash=state.topology.content_hash,
        operation=TopologyOperation.ENABLE_EDGE,
        peer_id=None,
        edge=("peer-a", "peer-b"),
        reason_code="LOAD_BALANCE",
        after_topology_hash=restored.content_hash,
    )
    state = apply_topology_event(session, state, enable)
    assert evaluate_termination(state).reason is CollaborationTerminationReason.TOPOLOGY_CHURN


def test_direct_parser_rejects_state_beyond_hop_budget(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = initial_collaboration_state(session)
    state = _advance(session, state, 1)
    state = _advance(session, state, 2)
    payload = state.model_dump(mode="json")
    payload["session"]["budget"]["max_hops"] = 1
    session_unhashed = {
        key: value for key, value in payload["session"].items() if key != "content_hash"
    }
    payload["session"]["content_hash"] = sha256_hex(canonical_json_bytes(session_unhashed))
    state_unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(state_unhashed))

    with pytest.raises(ValidationError, match="hop budget"):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))


def test_repeated_state_loop_is_detected_from_bounded_observations(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    state = initial_collaboration_state(session_factory("peer-a"))
    payload = state.model_dump(mode="json")
    payload["observed_state_hashes"] = [state.state_hash, state.state_hash]
    unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(unhashed))
    repeated = CollaborationState.model_validate_json(__import__("json").dumps(payload))
    reason = CollaborationTerminationReason.REPEATED_STATE_LOOP
    assert evaluate_termination(repeated).reason is reason


def test_direct_parsing_rejects_state_hash_and_aggregate_tampering(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = _advance(session, initial_collaboration_state(session), 1)
    hash_payload = state.model_dump(mode="json")
    hash_payload["observed_state_hashes"].append("0" * 64)
    with pytest.raises(ValidationError, match="state_hash"):
        CollaborationState.model_validate_json(__import__("json").dumps(hash_payload))

    aggregate_payload = state.model_dump(mode="json")
    aggregate_payload["usage"]["tokens"] = 11
    unhashed = {key: value for key, value in aggregate_payload.items() if key != "state_hash"}
    aggregate_payload["state_hash"] = sha256_hex(canonical_json_bytes(unhashed))
    with pytest.raises(ValidationError, match="aggregate usage"):
        CollaborationState.model_validate_json(__import__("json").dumps(aggregate_payload))


def test_direct_parser_rejects_rehashed_historical_routing_fabrication(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = _advance(session, initial_collaboration_state(session), 1)
    state = _advance(session, state, 2)
    payload = state.model_dump(mode="json")
    payload["requests"][1]["recipient_id"] = "peer-a"
    request_unhashed = {
        key: value for key, value in payload["requests"][1].items() if key != "content_hash"
    }
    payload["requests"][1]["content_hash"] = sha256_hex(canonical_json_bytes(request_unhashed))
    payload["contributions"][1]["peer_id"] = "peer-a"
    contribution_unhashed = {
        key: value for key, value in payload["contributions"][1].items() if key != "content_hash"
    }
    payload["contributions"][1]["content_hash"] = sha256_hex(
        canonical_json_bytes(contribution_unhashed)
    )
    state_unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(state_unhashed))

    with pytest.raises(ValidationError, match="expected peer"):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))


def _rehash_embedded_session_and_state(payload: dict[str, object]) -> None:
    session_payload = payload["session"]
    assert isinstance(session_payload, dict)
    session_unhashed = {
        key: value for key, value in session_payload.items() if key != "content_hash"
    }
    session_payload["content_hash"] = sha256_hex(canonical_json_bytes(session_unhashed))
    state_unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(state_unhashed))


def test_direct_parser_rejects_exchange_after_completion(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = _advance(session, initial_collaboration_state(session), 1)
    state = _advance(session, state, 2)
    payload = state.model_dump(mode="json")
    session_payload = payload["session"]
    assert isinstance(session_payload, dict)
    predicate = session_payload["completion_predicate"]
    assert isinstance(predicate, dict)
    predicate["min_contributions"] = 1
    payload["completed"] = True
    _rehash_embedded_session_and_state(payload)

    with pytest.raises(ValidationError, match=r"continues after.*COMPLETED"):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))


def test_direct_parser_rejects_exchange_after_topology_limit(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory(
        "peer-a", "peer-b", max_topology_changes=2, completion_count=8
    )
    state = initial_collaboration_state(session)
    reduced = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    event = TopologyEvent.build(
        event_id="event-1",
        session_id=session.session_id,
        sequence=1,
        before_topology_hash=state.topology.content_hash,
        operation=TopologyOperation.DISABLE_EDGE,
        peer_id=None,
        edge=("peer-a", "peer-b"),
        reason_code="LOAD_BALANCE",
        after_topology_hash=reduced.content_hash,
    )
    state = apply_topology_event(session, state, event)
    state = _advance(session, state, 1)
    payload = state.model_dump(mode="json")
    session_payload = payload["session"]
    assert isinstance(session_payload, dict)
    budget = session_payload["budget"]
    assert isinstance(budget, dict)
    budget["max_topology_changes"] = 1
    _rehash_embedded_session_and_state(payload)

    with pytest.raises(
        ValidationError, match=r"continues after.*TOPOLOGY_CHANGE_LIMIT_REACHED"
    ):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))


def test_direct_parser_rejects_exchange_after_repeated_state_loop(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = initial_collaboration_state(session)
    state = _advance(session, state, 1)
    state = _advance(session, state, 2)
    state = _advance(session, state, 3)
    payload = state.model_dump(mode="json")
    payload["observed_state_hashes"][1] = payload["observed_state_hashes"][0]
    state_unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(state_unhashed))

    with pytest.raises(
        ValidationError, match=r"continues after.*REPEATED_STATE_LOOP"
    ):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))


def test_direct_replay_rejects_single_peer_without_declared_edge(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", completion_count=8)
    state = _advance(session, initial_collaboration_state(session), 1)
    request = PeerRequest.build(
        request_id="request-2",
        session_id=session.session_id,
        sequence=2,
        sender_id="peer-a",
        recipient_id="peer-a",
        requested_capability_id="analysis",
        question="Assess.",
        artifact_refs=(artifact(),),
        parent_contribution_id="contribution-1",
        tool_ids=("tool-a",),
        remaining_budget=session.remaining_resources(state.usage),
    )
    contribution = PeerContribution.build(
        contribution_id="contribution-2",
        session_id=session.session_id,
        request_id=request.request_id,
        peer_id="peer-a",
        parent_contribution_ids=("contribution-1",),
        contribution_kind="analysis",
        rationale_summary="Evidence.",
        candidate_content='{"finding":"supported"}',
        artifact_refs=(artifact(),),
        tool_ids=("tool-a",),
    )
    payload = state.model_dump(mode="json")
    payload["requests"].append(request.model_dump(mode="json"))
    payload["contributions"].append(contribution.model_dump(mode="json"))
    payload["usage_history"].append(unit_usage().model_dump(mode="json"))
    payload["usage"] = {
        "cost_usd": 2.0,
        "compute_units": 2.0,
        "tokens": 20,
        "elapsed_seconds": 2.0,
        "tool_calls": 2,
        "human_interventions": 0,
    }
    payload["hop_count"] = 2
    payload["scheduling_position"] = 2
    payload["transitions"].append(
        CollaborationTransition(
            position=2,
            kind=CollaborationTransitionKind.PEER_EXCHANGE,
            request_id=request.request_id,
            contribution_id=contribution.contribution_id,
            topology_event_id=None,
        ).model_dump(mode="json")
    )
    payload["observed_state_hashes"].append(state.state_hash)
    state_unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(state_unhashed))

    with pytest.raises(ValidationError, match=r"NO_ELIGIBLE_PEER|expected peer"):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))
