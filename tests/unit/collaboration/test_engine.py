from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from super_scientist.domain.collaboration import (
    CollaborationSession,
    PeerContribution,
    PeerRequest,
    advance_collaboration,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.improvement.models import ResourceUsage

from .conftest import artifact, unit_usage


def _request(
    session: CollaborationSession, recipient: str, *, sequence: int = 1, **changes: object
) -> PeerRequest:
    values: dict[str, object] = {
        "request_id": f"request-{sequence}",
        "session_id": session.session_id,
        "sequence": sequence,
        "sender_id": None if sequence == 1 else "peer-a",
        "recipient_id": recipient,
        "requested_capability_id": "analysis",
        "question": "Assess the evidence.",
        "artifact_refs": (artifact(),),
        "parent_contribution_id": None if sequence == 1 else f"contribution-{sequence - 1}",
        "tool_ids": ("tool-a",),
        "remaining_budget": session.budget.resources,
    }
    values.update(changes)
    return PeerRequest.build(**values)


def _contribution(
    session: CollaborationSession, expected_peer_id: str, *, sequence: int = 1, **changes: object
) -> PeerContribution:
    values: dict[str, object] = {
        "contribution_id": f"contribution-{sequence}",
        "session_id": session.session_id,
        "request_id": f"request-{sequence}",
        "peer_id": expected_peer_id,
        "parent_contribution_ids": () if sequence == 1 else (f"contribution-{sequence - 1}",),
        "contribution_kind": "analysis",
        "rationale_summary": "Public evidence summary.",
        "candidate_content": '{"finding":"supported"}',
        "artifact_refs": (artifact(),),
        "tool_ids": ("tool-a",),
    }
    values.update(changes)
    return PeerContribution.build(**values)


def test_next_peer_uses_canonical_eligible_actor_order(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-c", "peer-a", "peer-b")
    assert next_peer(session, initial_collaboration_state(session)) == "peer-a"


def test_advance_accepts_exactly_one_checked_transition(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b")
    state = initial_collaboration_state(session)
    updated = advance_collaboration(
        session, state, _request(session, "peer-a"), _contribution(session, "peer-a"), unit_usage()
    )
    assert updated.hop_count == 1
    assert len(updated.requests) == len(updated.contributions) == len(updated.usage_history) == 1
    assert updated.scheduling_position == 1
    assert updated.usage.tokens == 10
    assert updated.state_hash != state.state_hash


@pytest.mark.parametrize(
    ("request_change", "contribution_change", "message"),
    (
        ({"recipient_id": "peer-z"}, {}, "expected peer"),
        ({}, {"peer_id": "peer-b"}, "request recipient"),
        ({"tool_ids": ("tool-z",)}, {}, "tool"),
        ({}, {"tool_ids": ("tool-z",)}, "tool"),
        ({"artifact_refs": (artifact("evil", "b" * 64),)}, {}, "artifact"),
        ({}, {"artifact_refs": (artifact("evil", "b" * 64),)}, "artifact"),
        ({"parent_contribution_id": "missing"}, {}, "parent"),
        ({}, {"parent_contribution_ids": ("missing",)}, "parent"),
    ),
)
def test_advance_rejects_undeclared_authority(
    session_factory: Callable[..., CollaborationSession],
    request_change: dict[str, object],
    contribution_change: dict[str, object],
    message: str,
) -> None:
    session = session_factory("peer-a", "peer-b")
    with pytest.raises(ValueError, match=message):
        advance_collaboration(
            session,
            initial_collaboration_state(session),
            _request(session, "peer-a", **request_change),
            _contribution(session, "peer-a", **contribution_change),
            unit_usage(),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("cost_usd", 101.0),
        ("compute_units", 101.0),
        ("tokens", 1001),
        ("elapsed_seconds", 101.0),
        ("tool_calls", 101),
        ("human_interventions", 1),
    ),
)
def test_advance_rejects_every_resource_budget_overrun(
    session_factory: Callable[..., CollaborationSession],
    field_name: str,
    value: float | int,
) -> None:
    session = session_factory("peer-a", "peer-b")
    usage_values: dict[str, float | int] = {
        "cost_usd": 1.0,
        "compute_units": 1.0,
        "tokens": 10,
        "elapsed_seconds": 1.0,
        "tool_calls": 1,
        "human_interventions": 0,
    }
    usage_values[field_name] = value
    excessive = ResourceUsage.model_validate(usage_values)
    with pytest.raises(ValueError, match="resource budget"):
        advance_collaboration(
            session,
            initial_collaboration_state(session),
            _request(session, "peer-a"),
            _contribution(session, "peer-a"),
            excessive,
        )


def test_recursive_parent_depth_is_bounded(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", max_parent_depth=0)
    state = advance_collaboration(
        session,
        initial_collaboration_state(session),
        _request(session, "peer-a"),
        _contribution(session, "peer-a"),
        unit_usage(),
    )
    request = _request(
        session,
        "peer-b",
        sequence=2,
        sender_id="peer-a",
        parent_contribution_id="contribution-1",
        remaining_budget=session.remaining_resources(state.usage),
    )
    contribution = _contribution(
        session, "peer-b", sequence=2, parent_contribution_ids=("contribution-1",)
    )
    with pytest.raises(ValueError, match="parent depth"):
        advance_collaboration(session, state, request, contribution, unit_usage())


def test_direct_parsing_rejects_request_hash_tampering(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    payload = _request(session_factory("peer-a"), "peer-a").model_dump(mode="json")
    payload["recipient_id"] = "peer-z"
    with pytest.raises(ValidationError, match="content_hash"):
        PeerRequest.model_validate_json(__import__("json").dumps(payload))


def test_candidate_content_is_canonical_public_json(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    with pytest.raises(ValidationError, match="canonical JSON object"):
        _contribution(session, "peer-a", candidate_content='{"b": 1, "a": 2}')
    with pytest.raises(ValidationError):
        _contribution(session, "peer-a", provider_reasoning={"secret": "chain"})
    with pytest.raises(ValidationError):
        _contribution(session, "peer-a", delegated_request_ids=("request-z",))


def test_collaboration_evidence_has_no_promotion_authority(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    contribution = _contribution(session_factory("peer-a"), "peer-a")
    assert contribution.authority == "EVIDENCE_ONLY"
    assert not hasattr(contribution, "claim_status")
    assert not hasattr(contribution, "authorize_promotion")
