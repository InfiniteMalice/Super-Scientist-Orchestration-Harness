from __future__ import annotations

import math
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class VerificationState(StrEnum):
    UNVERIFIED = "unverified"
    HASH_VERIFIED = "hash_verified"
    UNAVAILABLE = "unavailable"


type JsonScalar = None | bool | int | float | str
type FrozenJsonValue = (
    JsonScalar
    | tuple[FrozenJsonValue, ...]
    | frozenset[FrozenJsonValue]
    | Mapping[str, FrozenJsonValue]
)
type FrozenJsonMapping = Mapping[str, FrozenJsonValue]


def _freeze_json_value(value: object, field_name: str) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            frozen[key] = _freeze_json_value(item, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, field_name) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_json_value(item, field_name) for item in value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"unsupported {field_name} value: {type(value).__name__}")


def _freeze_provenance(value: Mapping[str, str]) -> Mapping[str, str]:
    frozen: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("provenance keys must be strings")
        if not isinstance(item, str):
            raise ValueError(f"unsupported provenance value: {type(item).__name__}")
        frozen[key] = item
    return MappingProxyType(frozen)


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    media_type: str = Field(
        description="Normalized descriptive metadata; it is not part of the SHA-256 byte address."
    )
    relative_path: NonBlankText

    @field_validator("media_type")
    @classmethod
    def normalize_media_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("media_type must be nonblank")
        return normalized


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def validate_bounds(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if self.end - self.start != len(self.text):
            raise ValueError("span offsets must match extracted text length")
        return self


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: StableIdentifier
    evidence_type: StableIdentifier
    source_locator: NonBlankText
    retrieved_at: UtcTimestamp
    artifact: ArtifactRef
    extracted_span: EvidenceSpan | None = None
    structured_observation: Mapping[str, object] | None = None
    provenance: Mapping[StableIdentifier, NonBlankText]
    license: NonBlankText | None = None
    ingestion_actor_id: StableIdentifier
    verification_state: VerificationState = VerificationState.UNVERIFIED

    @field_validator("structured_observation", mode="after")
    @classmethod
    def freeze_structured_observation(
        cls, value: Mapping[str, object] | None
    ) -> FrozenJsonMapping | None:
        if value is None:
            return None
        frozen = _freeze_json_value(value, "structured observation")
        if not isinstance(frozen, Mapping):
            raise ValueError("structured observation must be a mapping")
        return frozen

    @field_validator("provenance", mode="after")
    @classmethod
    def freeze_provenance(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return _freeze_provenance(value)

    @property
    def content_hash(self) -> str:
        return self.artifact.sha256
