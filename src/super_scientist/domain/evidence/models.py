from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.primitives import UtcTimestamp


class VerificationState(StrEnum):
    UNVERIFIED = "unverified"
    HASH_VERIFIED = "hash_verified"
    UNAVAILABLE = "unavailable"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    relative_path: str


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

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
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    evidence_type: str
    source_locator: str
    retrieved_at: UtcTimestamp
    artifact: ArtifactRef
    extracted_span: EvidenceSpan | None = None
    structured_observation: Mapping[str, Any] | None = None
    provenance: Mapping[str, str]
    license: str | None = None
    ingestion_actor_id: str
    verification_state: VerificationState = VerificationState.HASH_VERIFIED

    @field_validator("structured_observation", "provenance", mode="after")
    @classmethod
    def freeze_collections(cls, value: Any) -> Any:
        return _freeze(value)

    @property
    def content_hash(self) -> str:
        return self.artifact.sha256
