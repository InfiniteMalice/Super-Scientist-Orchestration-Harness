from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent


def test_actor_timestamp_must_be_utc() -> None:
    with pytest.raises(ValidationError):
        ActorIdentity(
            actor_id="actor-1",
            kind=ActorKind.HUMAN,
            created_at=datetime(2026, 7, 11),
        )


def test_zero_offset_timestamp_is_normalized_to_canonical_utc() -> None:
    timestamp = datetime(2026, 7, 11, tzinfo=timezone(timedelta(0), name="zero-offset"))

    actor = ActorIdentity(
        actor_id="actor-1",
        kind=ActorKind.HUMAN,
        created_at=timestamp,
    )

    assert actor.created_at.tzinfo is UTC


def test_actor_timestamp_with_nonzero_offset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ActorIdentity(
            actor_id="actor-1",
            kind=ActorKind.HUMAN,
            created_at=datetime(2026, 7, 11, tzinfo=timezone(timedelta(hours=1))),
        )


def test_model_actor_requires_provider_and_model_ids() -> None:
    with pytest.raises(ValidationError):
        ActorIdentity(
            actor_id="actor-1",
            kind=ActorKind.MODEL,
            created_at=datetime.now(UTC),
        )


def test_same_model_and_adapter_without_configuration_hash_are_not_independent() -> None:
    left = ActorIdentity.model("a", "provider", "model", "adapter", datetime.now(UTC))
    right = ActorIdentity.model("b", "provider", "model", "adapter", datetime.now(UTC))
    assert not are_independent(left, right)


def test_missing_configuration_hash_on_either_model_is_not_independent() -> None:
    complete = ActorIdentity(
        actor_id="complete",
        kind=ActorKind.MODEL,
        provider_id="provider",
        model_id="model",
        adapter_id="adapter",
        configuration_hash="hash",
        created_at=datetime.now(UTC),
    )
    missing_left = complete.model_copy(
        update={"actor_id": "missing-left", "configuration_hash": None}
    )
    missing_right = complete.model_copy(
        update={"actor_id": "missing-right", "configuration_hash": None}
    )

    assert not are_independent(missing_left, complete)
    assert not are_independent(complete, missing_right)


def test_same_actor_is_not_independent_even_with_different_configuration() -> None:
    left = ActorIdentity(
        actor_id="same",
        kind=ActorKind.MODEL,
        provider_id="provider-a",
        model_id="model-a",
        adapter_id="adapter-a",
        configuration_hash="hash-a",
        created_at=datetime.now(UTC),
    )
    right = left.model_copy(
        update={
            "provider_id": "provider-b",
            "model_id": "model-b",
            "adapter_id": "adapter-b",
            "configuration_hash": "hash-b",
        }
    )

    assert not are_independent(left, right)


def test_exact_same_model_fingerprint_is_not_independent() -> None:
    left = ActorIdentity(
        actor_id="left",
        kind=ActorKind.MODEL,
        provider_id="provider",
        model_id="model",
        adapter_id="adapter",
        configuration_hash="hash",
        created_at=datetime.now(UTC),
    )
    right = left.model_copy(update={"actor_id": "right"})

    assert not are_independent(left, right)
