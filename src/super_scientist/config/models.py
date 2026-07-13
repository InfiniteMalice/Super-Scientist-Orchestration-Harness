from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from super_scientist.domain.primitives import NonBlankText, Sha256Hex, StableIdentifier


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: NonBlankText = "sqlite:///scientist-harness.db"
    artifact_root: Path = Path("artifacts")
    policy_path: Path = Path("governance-policy.json")


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    required_claim_checks: tuple[StableIdentifier, ...] = Field(min_length=1)
    human_approval_for: frozenset[StableIdentifier] = Field(
        default_factory=lambda: frozenset({"governance_change", "adapter_promotion"})
    )

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_schema_version_one(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value

    @field_validator("required_claim_checks", mode="before")
    @classmethod
    def normalize_required_claim_checks(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("required_claim_checks must be a list or tuple of strings")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("required_claim_checks must contain strings")
            stripped = item.strip()
            if not stripped:
                raise ValueError("required_claim_checks entries must be nonblank")
            normalized.append(stripped)
        if not normalized:
            raise ValueError("required_claim_checks must not be empty")
        return tuple(normalized)


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_hash: Sha256Hex
    policy: GovernancePolicy
