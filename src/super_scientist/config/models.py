from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from super_scientist.domain.primitives import NonBlankText, Sha256Hex, StableIdentifier


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: NonBlankText = "sqlite:///scientist-harness.db"
    artifact_root: Path = Path("artifacts")
    policy_path: Path = Path("governance-policy.json")


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    required_claim_checks: tuple[StableIdentifier, ...] = Field(min_length=1)
    human_approval_for: frozenset[StableIdentifier] = Field(
        default_factory=lambda: frozenset({"governance_change", "adapter_promotion"})
    )

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
    model_config = ConfigDict(frozen=True)

    policy_hash: Sha256Hex
    policy: GovernancePolicy
