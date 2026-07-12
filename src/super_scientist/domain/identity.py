from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from super_scientist.domain.primitives import UtcTimestamp


class ActorKind(StrEnum):
    HUMAN = "human"
    MODEL = "model"
    TOOL = "tool"
    SERVICE = "service"


class ActorIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_id: str
    kind: ActorKind
    created_at: UtcTimestamp
    provider_id: str | None = None
    model_id: str | None = None
    adapter_id: str | None = None
    configuration_hash: str | None = None

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
    if left.actor_id == right.actor_id:
        return False
    if left.kind is ActorKind.MODEL and right.kind is ActorKind.MODEL:
        return (
            left.provider_id,
            left.model_id,
            left.adapter_id,
            left.configuration_hash,
        ) != (
            right.provider_id,
            right.model_id,
            right.adapter_id,
            right.configuration_hash,
        )
    return True
