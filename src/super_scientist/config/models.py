from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_serializer,
    field_validator,
    model_validator,
)

from super_scientist.domain.identity import ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.primitives import NonBlankText, Sha256Hex, StableIdentifier


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: NonBlankText = "sqlite:///scientist-harness.db"
    artifact_root: Path = Path("artifacts")
    policy_path: Path = Path("governance-policy.json")


class GovernancePolicyV1(BaseModel):
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


GovernancePolicy = GovernancePolicyV1


class AdaptationRequirement(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    change_target: ChangeTarget
    persistence: PersistenceScope
    minimum_verification: VerificationLevel
    permitted_grounding: frozenset[ExternalGrounding] = Field(min_length=1)
    required_approver_kind: ActorKind
    protected_evaluation_required: bool
    rollback_required: bool

    @field_serializer("permitted_grounding", when_used="json")
    def serialize_permitted_grounding(
        self,
        permitted_grounding: frozenset[ExternalGrounding],
    ) -> list[str]:
        return sorted(grounding.value for grounding in permitted_grounding)


class GovernancePolicyV2(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[2] = 2
    required_claim_checks: tuple[StableIdentifier, ...] = Field(min_length=1)
    human_approval_for: frozenset[StableIdentifier]
    adaptation_requirements: tuple[AdaptationRequirement, ...] = Field(min_length=1)

    @field_serializer("human_approval_for", when_used="json")
    def serialize_human_approval_for(
        self,
        human_approval_for: frozenset[str],
    ) -> list[str]:
        return sorted(human_approval_for)

    @model_validator(mode="after")
    def require_unique_adaptation_requirement_keys(self) -> Self:
        keys = tuple(
            (requirement.change_target, requirement.persistence)
            for requirement in self.adaptation_requirements
        )
        if len(keys) != len(set(keys)):
            raise ValueError(
                "duplicate adaptation requirement change_target/persistence key"
            )
        return self


def _policy_schema_version(value: object) -> object:
    if isinstance(value, dict):
        return value.get("schema_version", 1)
    return getattr(value, "schema_version", None)


type PolicyDocument = Annotated[
    Annotated[GovernancePolicyV1, Tag(1)] | Annotated[GovernancePolicyV2, Tag(2)],
    Discriminator(_policy_schema_version),
]


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    policy_hash: Sha256Hex
    policy: PolicyDocument
