from datetime import UTC, datetime

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


def test_same_model_and_adapter_are_not_independent() -> None:
    left = ActorIdentity.model("a", "provider", "model", "adapter", datetime.now(UTC))
    right = ActorIdentity.model("b", "provider", "model", "adapter", datetime.now(UTC))
    assert not are_independent(left, right)
