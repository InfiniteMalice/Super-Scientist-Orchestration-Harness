from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from pydantic import ValidationError

from super_scientist.domain.collaboration import (
    CollaborationSession,
    CollaborationState,
    CollaborationTerminationReason,
    PeerContribution,
    PeerRequest,
    advance_collaboration,
    evaluate_termination,
    initial_collaboration_state,
    next_peer,
)
from super_scientist.domain.improvement.models import ResourceUsage
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex

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


def test_collaboration_session_round_trips_through_strict_json(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b")

    parsed = CollaborationSession.model_validate_json(
        __import__("json").dumps(session.model_dump(mode="json")),
        strict=True,
    )

    assert parsed == session


def test_single_peer_requires_declared_edge_after_initial_exchange(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", completion_count=8)
    state = initial_collaboration_state(session)
    assert next_peer(session, state) == "peer-a"
    assert _request(session, "peer-a").sender_id is None

    state = advance_collaboration(
        session,
        state,
        _request(session, "peer-a"),
        _contribution(session, "peer-a"),
        unit_usage(),
    )

    assert next_peer(session, state) is None
    assert (
        evaluate_termination(state).reason
        is CollaborationTerminationReason.NO_ELIGIBLE_PEER
    )
    with pytest.raises(ValueError, match=r"terminated.*NO_ELIGIBLE_PEER"):
        advance_collaboration(
            session,
            state,
            _request(
                session,
                "peer-a",
                sequence=2,
                remaining_budget=session.remaining_resources(state.usage),
            ),
            _contribution(session, "peer-a", sequence=2),
            unit_usage(),
        )


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
    assert len(updated.observed_state_hashes) == 2
    assert updated.observed_state_hashes[0] != updated.observed_state_hashes[1]


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
    with pytest.raises(ValidationError, match="closed public candidate"):
        _contribution(session, "peer-a", candidate_content='{"b": 1, "a": 2}')
    with pytest.raises(ValidationError):
        _contribution(session, "peer-a", provider_reasoning={"secret": "chain"})
    with pytest.raises(ValidationError):
        _contribution(session, "peer-a", delegated_request_ids=("request-z",))


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "chain_of_thought",
        "scratchpad",
        "provider_payload",
        "secret",
        "protected_answer",
        "command",
    ),
)
def test_candidate_content_rejects_private_or_executable_keys_at_any_depth(
    session_factory: Callable[..., CollaborationSession],
    forbidden_key: str,
) -> None:
    session = session_factory("peer-a")
    content = f'{{"outer":[{{"{forbidden_key}":"not-public"}}]}}'

    with pytest.raises(ValidationError, match="closed public candidate"):
        _contribution(session, "peer-a", candidate_content=content)


def test_candidate_content_rejects_excessive_depth_and_collection_width(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    deeply_nested = '{"a":' * 20 + "null" + "}" * 20
    too_wide = '{"items":[' + ",".join("0" for _ in range(300)) + "]}"

    with pytest.raises(ValidationError, match="candidate_content"):
        _contribution(session, "peer-a", candidate_content=deeply_nested)
    with pytest.raises(ValidationError, match="candidate_content"):
        _contribution(session, "peer-a", candidate_content=too_wide)


def test_resource_replay_rejects_tiny_numeric_drift(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = advance_collaboration(
        session,
        initial_collaboration_state(session),
        _request(session, "peer-a"),
        _contribution(session, "peer-a"),
        unit_usage(),
    )
    payload = state.model_dump(mode="json")
    payload["usage"]["cost_usd"] += 5e-13
    unhashed = {key: value for key, value in payload.items() if key != "state_hash"}
    payload["state_hash"] = sha256_hex(canonical_json_bytes(unhashed))

    with pytest.raises(ValidationError, match="aggregate usage"):
        CollaborationState.model_validate_json(__import__("json").dumps(payload))


def test_resource_replay_accumulates_original_entries_without_float_round_trip(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a", "peer-b", completion_count=8)
    state = initial_collaboration_state(session)
    values = (1e-16, 1.234567890123, 1e-12)
    recipients = ("peer-a", "peer-b", "peer-a")
    senders: tuple[str | None, ...] = (None, "peer-a", "peer-b")
    for sequence, (value, recipient, sender) in enumerate(
        zip(values, recipients, senders, strict=True), start=1
    ):
        usage = ResourceUsage(
            cost_usd=value,
            compute_units=value,
            tokens=0,
            elapsed_seconds=value,
            tool_calls=0,
            human_interventions=0,
        )
        state = advance_collaboration(
            session,
            state,
            _request(
                session,
                recipient,
                sequence=sequence,
                sender_id=sender,
                remaining_budget=session.remaining_resources(state.usage),
            ),
            _contribution(session, recipient, sequence=sequence),
            usage,
        )

    expected = float(sum((Decimal(str(value)) for value in values), Decimal("0")))
    assert state.usage.cost_usd == expected
    assert CollaborationState.model_validate_json(state.model_dump_json()) == state


def test_candidate_content_parser_exhaustion_is_a_closed_validation_failure(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    parser_exhaustion = '{"a":' * 1_100 + "null" + "}" * 1_100

    with pytest.raises(ValidationError, match="candidate_content"):
        _contribution(session, "peer-a", candidate_content=parser_exhaustion)


def test_collaboration_rejects_unbounded_artifact_and_resource_scalars(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    oversized_artifact = artifact("x" * 10_000)

    with pytest.raises(ValidationError, match="artifact"):
        _request(session, "peer-a", artifact_refs=(oversized_artifact,))
    with pytest.raises(ValidationError, match="remaining_budget"):
        _request(
            session,
            "peer-a",
            remaining_budget=session.budget.resources.model_copy(
                update={"tokens": 10**30}
            ),
        )


def test_collaboration_rejects_coercive_nested_artifact_scalars(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    coercive_artifact = {
        "sha256": "a" * 64,
        "size_bytes": "4",
        "media_type": "text/plain",
        "relative_path": "input.txt",
    }

    with pytest.raises(ValidationError, match="artifact reference"):
        _request(session, "peer-a", artifact_refs=(coercive_artifact,))


def test_preconstructed_nested_dtos_are_strictly_revalidated(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    malformed_actor = session.peers[0].model_copy(update={"actor_id": 7})
    session_values = session.model_dump(mode="python", exclude={"content_hash"})
    session_values["peers"] = (malformed_actor,)

    with pytest.raises(ValidationError, match="actor identity"):
        CollaborationSession.build(**session_values)
    with pytest.raises(ValidationError, match="artifact reference"):
        _request(
            session,
            "peer-a",
            artifact_refs=(artifact().model_copy(update={"size_bytes": "4"}),),
        )
    with pytest.raises(ValidationError, match="resources"):
        _request(
            session,
            "peer-a",
            remaining_budget=session.budget.resources.model_copy(
                update={"tokens": "1000"}
            ),
        )


def test_collaboration_sequence_and_artifact_size_counters_are_bounded(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    oversized_artifact = artifact().model_copy(update={"size_bytes": 10**30})

    with pytest.raises(ValidationError, match="sequence"):
        _request(session, "peer-a", sequence=257)
    with pytest.raises(ValidationError, match="artifact reference"):
        _request(session, "peer-a", artifact_refs=(oversized_artifact,))


@pytest.mark.parametrize(
    "candidate_content",
    (
        '{"' + "k" * 201 + '":true}',
        '{"value":"' + "v" * 4_001 + '"}',
        '{"value":10000000000000000000}',
    ),
)
def test_candidate_content_rejects_bounded_key_and_scalar_violations(
    session_factory: Callable[..., CollaborationSession],
    candidate_content: str,
) -> None:
    with pytest.raises(ValidationError, match="candidate_content"):
        _contribution(
            session_factory("peer-a"),
            "peer-a",
            candidate_content=candidate_content,
        )


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "chainOfThought",
        "CHAIN-OF-THOUGHT",
        "scratch_pad",
        "providerPayload",
        "protected-answer",
        "\uff43\uff48\uff41\uff49\uff4e\uff2f\uff46\uff34\uff48\uff4f\uff55\uff47\uff48\uff54",
        "provider\u2011payload",
    ),
)
def test_candidate_content_normalizes_forbidden_key_spellings(
    session_factory: Callable[..., CollaborationSession],
    forbidden_key: str,
) -> None:
    with pytest.raises(ValidationError, match="closed public candidate"):
        _contribution(
            session_factory("peer-a"),
            "peer-a",
            candidate_content=f'{{"outer":{{"{forbidden_key}":"private"}}}}',
        )


def test_candidate_validation_errors_never_echo_secret_input(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    marker = "PRIVATE-MARKER-9f7e31"
    content = f'{{"chainOfThought":"{marker}"}}'

    with pytest.raises(ValidationError) as captured:
        _contribution(
            session_factory("peer-a"),
            "peer-a",
            candidate_content=content,
        )

    assert marker not in str(captured.value)
    assert marker not in repr(captured.value)
    assert marker not in repr(captured.value.errors())
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("candidate_content", "marker"),
    (
        ('{"api_key":"sk-private-marker","finding":"supported"}', "sk-private-marker"),
        (
            '{"finding":"supported","private_reasoning":"hidden scratch work"}',
            "hidden scratch work",
        ),
        ('{"apiKey":"sk-private-alias","finding":"supported"}', "sk-private-alias"),
        ('{"finding":"PRIVATE-VALUE-MARKER"}', "PRIVATE-VALUE-MARKER"),
        (
            '{"evidence_ids":["sk-private-evidence"],"finding":"supported"}',
            "sk-private-evidence",
        ),
        (
            '{"evidence_ids":["evidence-api-key-sk-private-marker"],"finding":"supported"}',
            "sk-private-marker",
        ),
        (
            '{"evidence_ids":["evidence-sha256:' + "a" * 63 + '"],"finding":"supported"}',
            "evidence-sha256",
        ),
        (
            '{"evidence_ids":["evidence-sha256:' + "A" * 64 + '"],"finding":"supported"}',
            "evidence-sha256",
        ),
    ),
)
def test_candidate_content_rejects_private_keys_aliases_and_secret_values(
    session_factory: Callable[..., CollaborationSession],
    candidate_content: str,
    marker: str,
) -> None:
    with pytest.raises(ValidationError) as captured:
        _contribution(
            session_factory("peer-a"),
            "peer-a",
            candidate_content=candidate_content,
        )

    assert marker not in str(captured.value)
    assert marker not in repr(captured.value)
    assert marker not in repr(captured.value.errors())
    assert captured.value.__cause__ is None


def test_candidate_content_accepts_closed_public_finding_and_evidence_ids(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    contribution = _contribution(
        session_factory("peer-a"),
        "peer-a",
        candidate_content=(
            '{"evidence_ids":["evidence-sha256:' + "a" * 64 + '"]'
            ',"finding":"supported"}'
        ),
    )

    assert contribution.candidate_content == (
        '{"evidence_ids":["evidence-sha256:' + "a" * 64 + '"]'
        ',"finding":"supported"}'
    )


def test_collaboration_evidence_has_no_promotion_authority(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    contribution = _contribution(session_factory("peer-a"), "peer-a")
    assert contribution.authority == "EVIDENCE_ONLY"
    assert not hasattr(contribution, "claim_status")
    assert not hasattr(contribution, "authorize_promotion")
