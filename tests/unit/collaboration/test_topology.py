from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from super_scientist.domain.collaboration import (
    CollaborationSession,
    TopologyEvent,
    TopologyOperation,
    TopologySnapshot,
    apply_topology_event,
    initial_collaboration_state,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def _event(
    session: CollaborationSession,
    before: TopologySnapshot,
    after: TopologySnapshot,
    operation: TopologyOperation,
    *,
    peer_id: str | None = None,
    edge: tuple[str, str] | None = None,
    sequence: int = 1,
) -> TopologyEvent:
    return TopologyEvent.build(
        event_id=f"event-{sequence}",
        session_id=session.session_id,
        sequence=sequence,
        before_topology_hash=before.content_hash,
        operation=operation,
        peer_id=peer_id,
        edge=edge,
        reason_code="LOAD_BALANCE",
        after_topology_hash=after.content_hash,
    )


def test_topology_event_changes_only_one_declared_edge(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b")
    state = initial_collaboration_state(session)
    after = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    updated = apply_topology_event(
        session,
        state,
        _event(
            session,
            state.topology,
            after,
            TopologyOperation.DISABLE_EDGE,
            edge=("peer-a", "peer-b"),
        ),
    )
    assert updated.topology == after
    assert updated.scheduling_position == 1
    assert len(updated.topology_events) == 1
    assert state.topology.enabled_edges == session.declared_edges


@pytest.mark.parametrize(
    ("operation", "peer_id", "edge", "message"),
    (
        (TopologyOperation.DEACTIVATE_PEER, "peer-z", None, "declared peer"),
        (TopologyOperation.DISABLE_EDGE, None, ("peer-a", "peer-z"), "declared edge"),
        (TopologyOperation.DISABLE_EDGE, "peer-a", ("peer-a", "peer-b"), "exactly one"),
    ),
)
def test_topology_rejects_undeclared_or_ambiguous_changes(
    session_factory: Callable[..., CollaborationSession],
    operation: TopologyOperation,
    peer_id: str | None,
    edge: tuple[str, str] | None,
    message: str,
) -> None:
    session = session_factory("peer-a", "peer-b")
    state = initial_collaboration_state(session)
    alternate = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    with pytest.raises((ValueError, ValidationError), match=message):
        event = _event(session, state.topology, alternate, operation, peer_id=peer_id, edge=edge)
        apply_topology_event(session, state, event)


def test_topology_rejects_false_after_hash(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b")
    state = initial_collaboration_state(session)
    wrong = TopologySnapshot.build(active_peer_ids=("peer-a",), enabled_edges=())
    event = _event(
        session, state.topology, wrong, TopologyOperation.DISABLE_EDGE, edge=("peer-a", "peer-b")
    )
    with pytest.raises(ValueError, match="after topology hash"):
        apply_topology_event(session, state, event)


def test_direct_parsing_rejects_topology_hash_tampering() -> None:
    topology = TopologySnapshot.build(
        active_peer_ids=("peer-a", "peer-b"), enabled_edges=(("peer-a", "peer-b"),)
    )
    payload = topology.model_dump(mode="json")
    payload["content_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="content_hash"):
        TopologySnapshot.model_validate_json(__import__("json").dumps(payload))


def test_rehashed_semantically_false_topology_event_is_rejected(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b")
    state = initial_collaboration_state(session)
    after = TopologySnapshot.build(
        active_peer_ids=state.topology.active_peer_ids,
        enabled_edges=(("peer-b", "peer-a"),),
    )
    event = _event(
        session, state.topology, after, TopologyOperation.DISABLE_EDGE, edge=("peer-a", "peer-b")
    )
    payload = event.model_dump(mode="json")
    payload["operation"] = TopologyOperation.ENABLE_EDGE
    unhashed = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = sha256_hex(canonical_json_bytes(unhashed))
    with pytest.raises(ValueError, match="already enabled"):
        parsed = TopologyEvent.model_validate_json(__import__("json").dumps(payload))
        apply_topology_event(session, state, parsed)
