from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: str = "sqlite:///scientist-harness.db"
    artifact_root: Path = Path("artifacts")
    policy_path: Path = Path("governance-policy.json")


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    required_claim_checks: list[str] = Field(min_length=1)
    human_approval_for: set[str] = Field(
        default_factory=lambda: {"governance_change", "adapter_promotion"}
    )


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_hash: str
    policy: GovernancePolicy
