from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)

GENESIS_HASH = "0" * 64
AUDIT_SCHEMA_VERSION: Literal[1] = 1

type JsonScalar = bool | int | float | str | None
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
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
        raise ValueError("audit payload collections must be mappings or sequences")
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


def _to_json_value(value: FrozenJsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _to_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_json_value(item) for item in value]
    return value


def json_compatible_payload(value: FrozenJsonMapping) -> dict[str, Any]:
    return {key: _to_json_value(item) for key, item in value.items()}


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(strict=True, ge=1)
    event_id: StableIdentifier
    event_type: StableIdentifier
    schema_version: Literal[1]
    occurred_at: UtcTimestamp
    payload: FrozenJsonMapping
    payload_hash: Sha256Hex
    previous_hash: Sha256Hex
    event_hash: Sha256Hex

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_schema_version_one(cls, value: object) -> object:
        if type(value) is not int or value != AUDIT_SCHEMA_VERSION:
            raise ValueError("audit schema_version must be integer 1")
        return value

    @field_validator("payload", mode="after")
    @classmethod
    def freeze_payload(cls, value: object) -> FrozenJsonMapping:
        if not isinstance(value, Mapping):
            raise ValueError("audit payload must be a mapping")
        return freeze_json_mapping(value)

    @field_serializer("payload", when_used="json")
    def serialize_payload(self, value: FrozenJsonMapping) -> dict[str, Any]:
        return json_compatible_payload(value)


class AuditVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    checked_events: int = Field(strict=True, ge=0)
    first_invalid_sequence: int | None = Field(default=None, strict=True, ge=1)
    reason: NonBlankText | None = None
