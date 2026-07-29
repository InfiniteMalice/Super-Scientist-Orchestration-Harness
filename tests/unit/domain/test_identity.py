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


@pytest.mark.parametrize("configuration_hash", ["hash", "A" * 64, "a" * 63])
def test_model_configuration_hash_must_be_canonical_sha256(configuration_hash: str) -> None:
    with pytest.raises(ValidationError, match="configuration_hash"):
        ActorIdentity(
            actor_id="actor-1",
            kind=ActorKind.MODEL,
            provider_id="provider",
            model_id="model",
            configuration_hash=configuration_hash,
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
        provider_id="provider-complete",
        model_id="model-complete",
        adapter_id="adapter-complete",
        configuration_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    missing_left = ActorIdentity(
        actor_id="missing-left",
        kind=ActorKind.MODEL,
        provider_id="provider-left",
        model_id="model-left",
        adapter_id="adapter-left",
        configuration_hash=None,
        created_at=datetime.now(UTC),
    )
    missing_right = ActorIdentity(
        actor_id="missing-right",
        kind=ActorKind.MODEL,
        provider_id="provider-right",
        model_id="model-right",
        adapter_id="adapter-right",
        configuration_hash=None,
        created_at=datetime.now(UTC),
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
        configuration_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    right = left.model_copy(
        update={
            "provider_id": "provider-b",
            "model_id": "model-b",
            "adapter_id": "adapter-b",
            "configuration_hash": "b" * 64,
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
        configuration_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    right = left.model_copy(update={"actor_id": "right"})

    assert not are_independent(left, right)


def test_same_weights_with_different_adapters_are_not_independent() -> None:
    left = ActorIdentity(
        actor_id="left",
        kind=ActorKind.MODEL,
        provider_id="provider",
        model_id="model",
        adapter_id="adapter",
        configuration_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    right = left.model_copy(
        update={
            "actor_id": "right",
            "adapter_id": "other-adapter",
            "configuration_hash": "b" * 64,
        }
    )

    assert not are_independent(left, right)


@pytest.mark.parametrize(
    ("left_kind", "right_kind"),
    tuple((left_kind, right_kind) for left_kind in ActorKind for right_kind in ActorKind),
)
def test_shared_configuration_rejects_aliases_across_all_actor_kinds(
    left_kind: ActorKind,
    right_kind: ActorKind,
) -> None:
    def correlated_actor(actor_id: str, kind: ActorKind) -> ActorIdentity:
        model_identity = (
            {"provider_id": actor_id, "model_id": actor_id} if kind is ActorKind.MODEL else {}
        )
        return ActorIdentity(
            actor_id=actor_id,
            kind=kind,
            created_at=datetime.now(UTC),
            configuration_hash="a" * 64,
            **model_identity,
        )

    assert not are_independent(
        correlated_actor("left", left_kind),
        correlated_actor("right", right_kind),
    )


def test_distinct_operational_identities_are_independent() -> None:
    left = ActorIdentity(
        actor_id="left",
        kind=ActorKind.MODEL,
        provider_id="provider-left",
        model_id="model-left",
        adapter_id="adapter-left",
        configuration_hash="a" * 64,
        created_at=datetime.now(UTC),
    )
    right = ActorIdentity(
        actor_id="right",
        kind=ActorKind.MODEL,
        provider_id="provider-right",
        model_id="model-right",
        adapter_id="adapter-right",
        configuration_hash="b" * 64,
        created_at=datetime.now(UTC),
    )

    assert are_independent(left, right)
