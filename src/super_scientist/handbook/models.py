from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.primitives import (
    GitObjectId,
    Sha256Hex,
)


def _require_handbook_text(value: str) -> str:
    if value != value.strip() or any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise ValueError("handbook text must be single-line and control-free")
    return value


HandbookText = Annotated[
    str,
    Field(strict=True, min_length=1),
    AfterValidator(_require_handbook_text),
]
HandbookIdentifier = HandbookText


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class SourceBinding(_StrictFrozenModel):
    """A human-authored claim that a behavior is implemented at one source symbol."""

    repository_commit: GitObjectId
    relative_path: HandbookText
    symbol: HandbookText
    source_hash: Sha256Hex


class BehaviorEntry(_StrictFrozenModel):
    """Human-authored behavior truth; source syntax never fills these fields."""

    behavior_id: HandbookIdentifier
    summary: HandbookText
    contracts: tuple[HandbookText, ...] = Field(min_length=1)
    inputs: tuple[HandbookText, ...]
    outputs: tuple[HandbookText, ...]
    preconditions: tuple[HandbookText, ...]
    postconditions: tuple[HandbookText, ...]
    failure_modes: tuple[HandbookText, ...]
    state_read: tuple[HandbookText, ...]
    state_written: tuple[HandbookText, ...]
    tools: tuple[HandbookText, ...]
    permissions: tuple[HandbookText, ...]
    dependencies: tuple[HandbookIdentifier, ...]
    governing_rule_version_ids: tuple[HandbookIdentifier, ...] = Field(min_length=1)
    source_bindings: tuple[SourceBinding, ...] = Field(min_length=1)
    test_paths: tuple[HandbookText, ...] = Field(min_length=1)
    related_behaviors: tuple[HandbookIdentifier, ...]

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
    repository: HandbookText
    repository_commit: GitObjectId
    behaviors: tuple[BehaviorEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_coherent_manifest(self) -> Self:
        behavior_ids = tuple(item.behavior_id for item in self.behaviors)
        if len(set(behavior_ids)) != len(behavior_ids):
            raise ValueError("behaviors must contain unique behavior_id values")
        known_behaviors = set(behavior_ids)
        source_hashes_by_path: dict[str, str] = {}
        for behavior in self.behaviors:
            if any(
                binding.repository_commit != self.repository_commit
                for binding in behavior.source_bindings
            ):
                raise ValueError("every source binding must name the manifest repository_commit")
            for binding in behavior.source_bindings:
                prior_hash = source_hashes_by_path.setdefault(
                    binding.relative_path,
                    binding.source_hash,
                )
                if prior_hash != binding.source_hash:
                    raise ValueError("every binding for one source path must declare the same hash")
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
    behavior_id: HandbookIdentifier
    repository_commit: GitObjectId
    relative_path: HandbookText
    module: HandbookText
    symbol: HandbookText
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
    relative_path: HandbookText
    symbol: HandbookText
    behavior_ids: tuple[HandbookIdentifier, ...] = Field(min_length=1)


class RuleBehaviorLink(_StrictFrozenModel):
    rule_version_id: HandbookIdentifier
    behavior_ids: tuple[HandbookIdentifier, ...] = Field(min_length=1)


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
    REPOSITORY_COMMIT_NOT_FOUND = "REPOSITORY_COMMIT_NOT_FOUND"
    REPOSITORY_OBJECT_NOT_COMMIT = "REPOSITORY_OBJECT_NOT_COMMIT"
    COMMIT_SOURCE_MISMATCH = "COMMIT_SOURCE_MISMATCH"
    CHECKOUT_SOURCE_STALE = "CHECKOUT_SOURCE_STALE"
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
    message: HandbookText
    behavior_id: HandbookIdentifier | None
    location: HandbookText | None


class HandbookVerificationResult(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    valid: bool
    provenance_verified: bool
    repository_commit: GitObjectId
    manifest_hash: Sha256Hex
    expected_source_tree_hash: Sha256Hex
    actual_source_tree_hash: Sha256Hex
    source_hashes: tuple[Sha256Hex, ...] = Field(min_length=1)
    generated_artifact_hash: Sha256Hex
    findings: tuple[HandbookFinding, ...]
    finding_codes: tuple[HandbookFindingCode, ...]
    stale_locations: tuple[HandbookText, ...]
    missing_symbols: tuple[HandbookText, ...]
    affected_behavior_ids: tuple[HandbookIdentifier, ...]
    affected_rule_version_ids: tuple[HandbookIdentifier, ...]

    @model_validator(mode="after")
    def require_result_consistency(self) -> Self:
        codes = tuple(finding.code for finding in self.findings)
        if codes != self.finding_codes:
            raise ValueError("finding_codes must exactly project findings")
        if self.valid != (not self.findings):
            raise ValueError("valid must exactly reflect whether findings are absent")
        if self.valid and not self.provenance_verified:
            raise ValueError("valid verification requires verified provenance")
        return self


class PathContainmentError(ValueError):
    """Raised when a declared path is outside the static repository namespace."""


class HandbookBuildError(ValueError):
    """Raised when a handbook cannot be built from fully verified declarations."""

    def __init__(self, finding_codes: tuple[HandbookFindingCode, ...]) -> None:
        self.finding_codes = finding_codes
        rendered = ", ".join(code.value for code in finding_codes)
        super().__init__(f"handbook verification failed: {rendered}")
