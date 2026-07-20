from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from super_scientist.domain.behavioral_rules.models import SemanticVersion
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.models import AssessmentOutcome, AssessmentProvenance
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class TransformationKind(StrEnum):
    INTRA_SPACE_TRANSFORMATION = "INTRA_SPACE_TRANSFORMATION"
    GENERATIVE_REPRESENTATION_PROPOSAL = "GENERATIVE_REPRESENTATION_PROPOSAL"


class PrimitiveStatus(StrEnum):
    PROPOSED = "PROPOSED"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    UNDER_DEFINITION = "UNDER_DEFINITION"
    EXPERIMENTAL = "EXPERIMENTAL"
    LOCALLY_USEFUL = "LOCALLY_USEFUL"
    REPLICATED = "REPLICATED"
    STABILIZED = "STABILIZED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


class PrimitiveUse(StrEnum):
    CANONICAL_CLAIM_SCHEMA = "CANONICAL_CLAIM_SCHEMA"
    GOVERNANCE = "GOVERNANCE"
    ACTIVE_EVALUATOR = "ACTIVE_EVALUATOR"
    ADAPTER_DATA = "ADAPTER_DATA"
    PUBLIC_CONCLUSION = "PUBLIC_CONCLUSION"


class AcceptedPrimitiveReceiptRef(_StrictFrozenModel):
    proposal_id: StableIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: StableIdentifier
    audit_event_hash: Sha256Hex


class PrimitiveVersionReceiptRef(AcceptedPrimitiveReceiptRef):
    receipt_type: Literal["PRIMITIVE_VERSION"] = "PRIMITIVE_VERSION"


class PrimitiveEvaluationReceiptRef(AcceptedPrimitiveReceiptRef):
    receipt_type: Literal["PRIMITIVE_EVALUATION"] = "PRIMITIVE_EVALUATION"


class EvaluatorAuditReceiptRef(AcceptedPrimitiveReceiptRef):
    receipt_type: Literal["EVALUATOR_AUDIT"] = "EVALUATOR_AUDIT"


class SelfImprovementMeasurementReceiptRef(AcceptedPrimitiveReceiptRef):
    receipt_type: Literal["SELF_IMPROVEMENT_MEASUREMENT"] = "SELF_IMPROVEMENT_MEASUREMENT"


type PrimitiveReceiptRef = Annotated[
    PrimitiveVersionReceiptRef
    | PrimitiveEvaluationReceiptRef
    | EvaluatorAuditReceiptRef
    | SelfImprovementMeasurementReceiptRef,
    Field(discriminator="receipt_type"),
]


class ConceptOverlap(StrEnum):
    DISTINCT = "DISTINCT"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"


class SemanticVersionChange(StrEnum):
    CLARIFICATION = "CLARIFICATION"
    COMPATIBLE_EXPANSION = "COMPATIBLE_EXPANSION"
    MEANING_INCOMPATIBLE = "MEANING_INCOMPATIBLE"


class SemanticVersionDecision(_StrictFrozenModel):
    accepted: bool
    code: NonBlankText


class PrimitiveVersion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    primitive_version_id: StableIdentifier
    primitive_id: StableIdentifier
    semantic_version: SemanticVersion
    transformation_kind: TransformationKind
    definition: NonBlankText
    motivation: NonBlankText
    parent_vocabulary: tuple[StableIdentifier, ...]
    contrasts: tuple[NonBlankText, ...]
    examples: tuple[NonBlankText, ...]
    counterexamples: tuple[NonBlankText, ...]
    construction_method: NonBlankText
    expected_uses: tuple[NonBlankText, ...]
    predecessor_primitive_version_ids: tuple[StableIdentifier, ...]
    dependency_primitive_version_ids: tuple[StableIdentifier, ...]
    measurement_ids: tuple[StableIdentifier, ...]
    falsification_tests: tuple[StableIdentifier, ...] = Field(min_length=1)
    ambiguity: tuple[NonBlankText, ...]
    proposer: ActorIdentity
    status: PrimitiveStatus
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("semantic_version")
    @classmethod
    def require_canonical_semantic_version(cls, value: str) -> str:
        _parse_semantic_version(value)
        return value

    @field_validator(
        "parent_vocabulary",
        "predecessor_primitive_version_ids",
        "dependency_primitive_version_ids",
        "measurement_ids",
        "falsification_tests",
    )
    @classmethod
    def require_unique_identifiers(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError(f"{getattr(info, 'field_name', 'identifiers')} must be unique")
        return value

    @model_validator(mode="after")
    def reject_self_references(self) -> Self:
        if self.primitive_version_id in {
            *self.predecessor_primitive_version_ids,
            *self.dependency_primitive_version_ids,
        }:
            raise ValueError("primitive version cannot depend on or precede itself")
        return self


class OldFrameEvaluation(_StrictFrozenModel):
    frame: Literal["OLD_FRAME"] = "OLD_FRAME"
    preserved_constraints: tuple[NonBlankText, ...] = Field(min_length=1)
    established_test_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    regression_findings: tuple[NonBlankText, ...] = Field(min_length=1)


class NewFrameEvaluation(_StrictFrozenModel):
    frame: Literal["NEW_FRAME"] = "NEW_FRAME"
    novel_predictions: tuple[NonBlankText, ...] = Field(min_length=1)
    independent_operationalization: NonBlankText
    non_circular_test_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    later_reuse_evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)


type FrameEvaluation = Annotated[
    OldFrameEvaluation | NewFrameEvaluation,
    Field(discriminator="frame"),
]


class PrimitiveEvaluation(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    primitive_evaluation_id: StableIdentifier
    primitive_version_id: StableIdentifier
    frame_evaluation: FrameEvaluation
    verification_result_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    check_actors: tuple[ActorIdentity, ...] = Field(min_length=1)
    provenance: AssessmentProvenance
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    outcome: AssessmentOutcome
    evaluated_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("verification_result_ids", "evidence_ids")
    @classmethod
    def require_unique_identifiers(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError(f"{getattr(info, 'field_name', 'identifiers')} must be unique")
        return value

    @model_validator(mode="after")
    def require_exact_assessment_bindings(self) -> Self:
        if len(self.check_actors) != len(self.verification_result_ids):
            raise ValueError("each verification result requires one retained check actor")
        if len({actor.actor_id for actor in self.check_actors}) != len(self.check_actors):
            raise ValueError("check actors must be unique")
        if self.provenance.evidence_ids != self.evidence_ids:
            raise ValueError("assessment evidence must exactly bind evaluation evidence")
        if self.provenance.checks_run != self.verification_result_ids:
            raise ValueError("assessment checks must exactly bind verification results")
        if self.provenance.result is not self.outcome:
            raise ValueError("assessment and evaluation outcomes must match")
        if self.provenance.assessed_at != self.evaluated_at:
            raise ValueError("assessment and evaluation timestamps must match")
        if self.provenance.governing_policy_hash != self.governing_policy_hash:
            raise ValueError("assessment and evaluation policy hashes must match")
        if isinstance(self.frame_evaluation, NewFrameEvaluation) and not set(
            self.frame_evaluation.later_reuse_evidence_ids
        ).issubset(self.evidence_ids):
            raise ValueError("later reuse must bind retained evaluation evidence")
        return self


_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_CONCEPT_TOKEN = re.compile(r"[a-z0-9]+")
_SEMANTIC_STOP_WORDS = frozenset({"a", "an", "the", "in", "has", "is", "of"})


def validate_semantic_version_change(
    prior: str,
    current: str,
    *,
    change: SemanticVersionChange,
) -> SemanticVersionDecision:
    prior_parsed = _parse_semantic_version(prior)
    current_parsed = _parse_semantic_version(current)
    if _semver_precedence(current_parsed) <= _semver_precedence(prior_parsed):
        return SemanticVersionDecision(accepted=False, code="SEMANTIC_VERSION_NOT_MONOTONIC")
    prior_major, prior_minor, prior_patch = prior_parsed[:3]
    current_major, current_minor, current_patch = current_parsed[:3]
    if change is SemanticVersionChange.CLARIFICATION and not (
        current_major == prior_major
        and current_minor == prior_minor
        and current_patch > prior_patch
    ):
        return SemanticVersionDecision(
            accepted=False,
            code="CLARIFICATION_REQUIRES_PATCH",
        )
    if change is SemanticVersionChange.COMPATIBLE_EXPANSION and not (
        current_major == prior_major and current_minor > prior_minor
    ):
        return SemanticVersionDecision(
            accepted=False,
            code="COMPATIBLE_EXPANSION_REQUIRES_MINOR",
        )
    if change is SemanticVersionChange.MEANING_INCOMPATIBLE and current_major <= prior_major:
        return SemanticVersionDecision(
            accepted=False,
            code="INCOMPATIBLE_MEANING_REQUIRES_MAJOR",
        )
    return SemanticVersionDecision(accepted=True, code="SEMANTIC_VERSION_ACCEPTED")


def classify_concept_overlap(
    candidate: PrimitiveVersion,
    retained: tuple[PrimitiveVersion, ...],
) -> ConceptOverlap:
    other_primitives = tuple(
        item for item in retained if item.primitive_id != candidate.primitive_id
    )
    exact = _exact_concept_key(candidate)
    if any(_exact_concept_key(item) == exact for item in other_primitives):
        return ConceptOverlap.EXACT_DUPLICATE
    semantic = _semantic_concept_key(candidate)
    if any(_semantic_concept_key(item) == semantic for item in other_primitives):
        return ConceptOverlap.SEMANTIC_DUPLICATE
    return ConceptOverlap.DISTINCT


def semantic_change_between(
    prior: PrimitiveVersion,
    current: PrimitiveVersion,
) -> SemanticVersionChange:
    if _meaning_key(prior) != _meaning_key(current):
        return SemanticVersionChange.MEANING_INCOMPATIBLE
    operationalization_fields = (
        "examples",
        "counterexamples",
        "construction_method",
        "expected_uses",
        "dependency_primitive_version_ids",
        "falsification_tests",
    )
    if any(getattr(prior, field) != getattr(current, field) for field in operationalization_fields):
        return SemanticVersionChange.COMPATIBLE_EXPANSION
    return SemanticVersionChange.CLARIFICATION


def _parse_semantic_version(value: str) -> tuple[int, int, int, tuple[str, ...] | None, str | None]:
    match = _SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("invalid semantic version")
    prerelease = match.group("prerelease")
    prerelease_identifiers = None if prerelease is None else tuple(prerelease.split("."))
    if prerelease_identifiers is not None and any(
        identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        for identifier in prerelease_identifiers
    ):
        raise ValueError("numeric prerelease identifiers cannot contain leading zeroes")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        prerelease_identifiers,
        match.group("build"),
    )


def _semver_precedence(
    parsed: tuple[int, int, int, tuple[str, ...] | None, str | None],
) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]]:
    major, minor, patch, prerelease, _build = parsed
    if prerelease is None:
        prerelease_key: tuple[tuple[int, int | str], ...] = ((2, ""),)
    else:
        prerelease_key = tuple(
            (0, int(identifier)) if identifier.isdigit() else (1, identifier)
            for identifier in prerelease
        )
    return major, minor, patch, prerelease_key


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _exact_concept_key(value: PrimitiveVersion) -> tuple[object, ...]:
    return (
        _normalize_text(value.definition),
        tuple(_normalize_text(item) for item in value.parent_vocabulary),
        tuple(_normalize_text(item) for item in value.contrasts),
    )


def _semantic_concept_key(value: PrimitiveVersion) -> tuple[object, ...]:
    tokens = frozenset(
        token
        for token in _CONCEPT_TOKEN.findall(value.definition.casefold())
        if token not in _SEMANTIC_STOP_WORDS
    )
    return (
        tokens,
        frozenset(_normalize_text(item) for item in value.parent_vocabulary),
        frozenset(_normalize_text(item) for item in value.contrasts),
    )


def _meaning_key(value: PrimitiveVersion) -> tuple[object, ...]:
    return (
        value.transformation_kind,
        _normalize_text(value.definition),
        tuple(_normalize_text(item) for item in value.parent_vocabulary),
        tuple(_normalize_text(item) for item in value.contrasts),
    )
