from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from super_scientist.domain.primitives import Sha256Hex, StableIdentifier, UtcTimestamp


class ActorKind(StrEnum):
    HUMAN = "human"
    MODEL = "model"
    TOOL = "tool"
    SERVICE = "service"


class ActorIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: StableIdentifier
    kind: ActorKind
    created_at: UtcTimestamp
    provider_id: StableIdentifier | None = None
    model_id: StableIdentifier | None = None
    adapter_id: StableIdentifier | None = None
    configuration_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def require_model_identity(self) -> ActorIdentity:
        if self.kind is ActorKind.MODEL and (self.provider_id is None or self.model_id is None):
            raise ValueError("model actors require provider_id and model_id")
        return self

    @classmethod
    def model(
        cls,
        actor_id: str,
        provider_id: str,
        model_id: str,
        adapter_id: str | None,
        created_at: UtcTimestamp,
    ) -> ActorIdentity:
        return cls(
            actor_id=actor_id,
            kind=ActorKind.MODEL,
            provider_id=provider_id,
            model_id=model_id,
            adapter_id=adapter_id,
            created_at=created_at,
        )


def are_independent(left: ActorIdentity, right: ActorIdentity) -> bool:
    """Fail closed when actor IDs or any declared operational identity correlate."""

    if left.actor_id == right.actor_id:
        return False
    correlated_fields = (
        (left.provider_id, right.provider_id),
        (left.model_id, right.model_id),
        (left.adapter_id, right.adapter_id),
        (left.configuration_hash, right.configuration_hash),
    )
    if any(
        left_value is not None and right_value is not None and left_value == right_value
        for left_value, right_value in correlated_fields
    ):
        return False
    if left.kind is ActorKind.MODEL and right.kind is ActorKind.MODEL:
        return left.configuration_hash is not None and right.configuration_hash is not None
    return True
