from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.primitives import (
    GitObjectId,
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class SourceBinding(_StrictFrozenModel):
    """A human-authored claim that a behavior is implemented at one source symbol."""

    repository_commit: GitObjectId
    relative_path: NonBlankText
    symbol: NonBlankText
    source_hash: Sha256Hex


class BehaviorEntry(_StrictFrozenModel):
    """Human-authored behavior truth; source syntax never fills these fields."""

    behavior_id: StableIdentifier
    summary: NonBlankText
    contracts: tuple[NonBlankText, ...] = Field(min_length=1)
    inputs: tuple[NonBlankText, ...]
    outputs: tuple[NonBlankText, ...]
    preconditions: tuple[NonBlankText, ...]
    postconditions: tuple[NonBlankText, ...]
    failure_modes: tuple[NonBlankText, ...]
    state_read: tuple[NonBlankText, ...]
    state_written: tuple[NonBlankText, ...]
    tools: tuple[NonBlankText, ...]
    permissions: tuple[NonBlankText, ...]
    dependencies: tuple[StableIdentifier, ...]
    governing_rule_version_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    source_bindings: tuple[SourceBinding, ...] = Field(min_length=1)
    test_paths: tuple[NonBlankText, ...] = Field(min_length=1)
    related_behaviors: tuple[StableIdentifier, ...]

    @field_validator(
        "contracts",
        "inputs",
        "outputs",
        "preconditions",
        "postconditions",
        "failure_modes",
        "state_read",
        "state_written",
        "tools",
        "permissions",
        "dependencies",
        "governing_rule_version_ids",
        "test_paths",
        "related_behaviors",
    )
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        field_name = str(getattr(info, "field_name", "values"))
        if len(set(value)) != len(value):
            raise ValueError(f"{field_name} must contain unique values")
        return value

    @field_validator("source_bindings")
    @classmethod
    def require_unique_source_bindings(
        cls,
        value: tuple[SourceBinding, ...],
    ) -> tuple[SourceBinding, ...]:
        identities = tuple((item.relative_path, item.symbol) for item in value)
        if len(set(identities)) != len(identities):
            raise ValueError("source_bindings must contain unique path and symbol pairs")
        return value


class BehaviorManifest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    repository: NonBlankText
    repository_commit: GitObjectId
    behaviors: tuple[BehaviorEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_coherent_manifest(self) -> Self:
        behavior_ids = tuple(item.behavior_id for item in self.behaviors)
        if len(set(behavior_ids)) != len(behavior_ids):
            raise ValueError("behaviors must contain unique behavior_id values")
        known_behaviors = set(behavior_ids)
        for behavior in self.behaviors:
            if any(
                binding.repository_commit != self.repository_commit
                for binding in behavior.source_bindings
            ):
                raise ValueError("every source binding must name the manifest repository_commit")
            references = set(behavior.dependencies) | set(behavior.related_behaviors)
            unknown = references - known_behaviors
            if unknown:
                raise ValueError("behavior references must name behaviors in the same manifest")
            if behavior.behavior_id in references:
                raise ValueError("a behavior cannot depend on or relate to itself")
        return self


class SourceSymbolKind(StrEnum):
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    ASYNC_FUNCTION = "ASYNC_FUNCTION"
    METHOD = "METHOD"
    ASYNC_METHOD = "ASYNC_METHOD"


class SourceLocation(_StrictFrozenModel):
    behavior_id: StableIdentifier
    repository_commit: GitObjectId
    relative_path: NonBlankText
    module: NonBlankText
    symbol: NonBlankText
    kind: SourceSymbolKind
    start_line: int = Field(strict=True, ge=1)
    end_line: int = Field(strict=True, ge=1)
    relationship: Literal["IMPLEMENTS"] = "IMPLEMENTS"
    verification_method: Literal["PYTHON_AST_SYMBOL_AND_SHA256"] = "PYTHON_AST_SYMBOL_AND_SHA256"
    source_hash: Sha256Hex
    symbol_source_hash: Sha256Hex

    @model_validator(mode="after")
    def require_ordered_line_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("source location end_line must not precede start_line")
        return self


class SourceBehaviorLink(_StrictFrozenModel):
    relative_path: NonBlankText
    symbol: NonBlankText
    behavior_ids: tuple[StableIdentifier, ...] = Field(min_length=1)


class RuleBehaviorLink(_StrictFrozenModel):
    rule_version_id: StableIdentifier
    behavior_ids: tuple[StableIdentifier, ...] = Field(min_length=1)


class HandbookBuildResult(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    repository_commit: GitObjectId
    manifest_hash: Sha256Hex
    source_tree_hash: Sha256Hex
    source_hashes: tuple[Sha256Hex, ...] = Field(min_length=1)
    source_locations: tuple[SourceLocation, ...] = Field(min_length=1)
    source_to_behaviors: tuple[SourceBehaviorLink, ...] = Field(min_length=1)
    rule_to_behaviors: tuple[RuleBehaviorLink, ...] = Field(min_length=1)
    json_bytes: bytes
    markdown_bytes: bytes
    generated_artifact_hash: Sha256Hex


class HandbookFindingCode(StrEnum):
    REPOSITORY_COMMIT_MISMATCH = "REPOSITORY_COMMIT_MISMATCH"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_NOT_REGULAR_FILE = "SOURCE_NOT_REGULAR_FILE"
    SOURCE_NOT_PYTHON = "SOURCE_NOT_PYTHON"
    SOURCE_ENCODING_ERROR = "SOURCE_ENCODING_ERROR"
    SOURCE_SYNTAX_ERROR = "SOURCE_SYNTAX_ERROR"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    TEST_NOT_FOUND = "TEST_NOT_FOUND"
    TEST_NOT_REGULAR_FILE = "TEST_NOT_REGULAR_FILE"
    GENERATED_ARTIFACT_MISMATCH = "GENERATED_ARTIFACT_MISMATCH"


class HandbookFinding(_StrictFrozenModel):
    code: HandbookFindingCode
    message: NonBlankText
    behavior_id: StableIdentifier | None
    location: NonBlankText | None


class HandbookVerificationResult(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    valid: bool
    repository_commit: GitObjectId
    manifest_hash: Sha256Hex
    expected_source_tree_hash: Sha256Hex
    actual_source_tree_hash: Sha256Hex
    source_hashes: tuple[Sha256Hex, ...] = Field(min_length=1)
    generated_artifact_hash: Sha256Hex
    findings: tuple[HandbookFinding, ...]
    finding_codes: tuple[HandbookFindingCode, ...]
    stale_locations: tuple[NonBlankText, ...]
    missing_symbols: tuple[NonBlankText, ...]
    affected_behavior_ids: tuple[StableIdentifier, ...]
    affected_rule_version_ids: tuple[StableIdentifier, ...]

    @model_validator(mode="after")
    def require_result_consistency(self) -> Self:
        codes = tuple(finding.code for finding in self.findings)
        if codes != self.finding_codes:
            raise ValueError("finding_codes must exactly project findings")
        if self.valid != (not self.findings):
            raise ValueError("valid must exactly reflect whether findings are absent")
        return self


class PathContainmentError(ValueError):
    """Raised when a declared path is outside the static repository namespace."""


class HandbookBuildError(ValueError):
    """Raised when a handbook cannot be built from fully verified declarations."""

    def __init__(self, finding_codes: tuple[HandbookFindingCode, ...]) -> None:
        self.finding_codes = finding_codes
        rendered = ", ".join(code.value for code in finding_codes)
        super().__init__(f"handbook verification failed: {rendered}")
