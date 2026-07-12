from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from super_scientist.domain.primitives import UtcTimestamp

GENESIS_HASH = "0" * 64

type JsonScalar = None | bool | int | float | str
type FrozenJsonValue = (
    JsonScalar
    | tuple[FrozenJsonValue, ...]
    | frozenset[FrozenJsonValue]
    | Mapping[str, FrozenJsonValue]
)
type FrozenJsonMapping = Mapping[str, FrozenJsonValue]


def _freeze_json_value(value: object) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("audit payload keys must be strings")
            frozen[key] = _freeze_json_value(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"unsupported audit payload value: {type(value).__name__}")


def freeze_json_mapping(value: Mapping[str, Any]) -> FrozenJsonMapping:
    frozen = _freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise ValueError("audit payload must be a mapping")
    return frozen


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    event_id: str
    event_type: str
    schema_version: int = 1
    occurred_at: UtcTimestamp
    payload: FrozenJsonMapping
    payload_hash: str
    previous_hash: str
    event_hash: str

    @field_validator("payload", mode="after")
    @classmethod
    def freeze_payload(cls, value: object) -> FrozenJsonMapping:
        if not isinstance(value, Mapping):
            raise ValueError("audit payload must be a mapping")
        return freeze_json_mapping(value)


class AuditVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    checked_events: int = Field(ge=0)
    first_invalid_sequence: int | None = Field(default=None, ge=1)
    reason: str | None = None
