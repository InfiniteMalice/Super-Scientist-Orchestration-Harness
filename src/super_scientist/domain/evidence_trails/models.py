from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.improvement.models import AssessmentProvenance
from super_scientist.domain.primitives import (
    NonBlankText,
    Sha256Hex,
    StableIdentifier,
    UtcTimestamp,
)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class RelationType(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    PRECEDES = "PRECEDES"
    FOLLOWS = "FOLLOWS"
    CAUSES_CANDIDATE = "CAUSES_CANDIDATE"
    ENABLES = "ENABLES"
    PREVENTS = "PREVENTS"
    QUALIFIES = "QUALIFIES"
    EXPLAINS = "EXPLAINS"
    SAME_ENTITY = "SAME_ENTITY"
    SAME_EVENT = "SAME_EVENT"
    DEPENDS_ON = "DEPENDS_ON"
    ALTERNATIVE_EXPLANATION = "ALTERNATIVE_EXPLANATION"


class TrailOutcome(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIALLY_SUPPORTING = "PARTIALLY_SUPPORTING"
    CONFLICTED = "CONFLICTED"
    INSUFFICIENT = "INSUFFICIENT"
    UNANSWERABLE = "UNANSWERABLE"
    INVALID_TRAIL = "INVALID_TRAIL"


class StructuralLocationKind(StrEnum):
    SECTION = "SECTION"
    SUBSECTION = "SUBSECTION"
    PAGE = "PAGE"
    PARAGRAPH = "PARAGRAPH"
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    FOOTNOTE = "FOOTNOTE"
    TIMESTAMP = "TIMESTAMP"
    SPEAKER = "SPEAKER"
    EVENT_SEQUENCE = "EVENT_SEQUENCE"
    APPENDIX = "APPENDIX"
    REFERENCE_TARGET = "REFERENCE_TARGET"


class AssessmentCategory(StrEnum):
    NECESSITY = "NECESSITY"
    GROUNDEDNESS = "GROUNDEDNESS"
    RELATION_FIDELITY = "RELATION_FIDELITY"
    COUNTEREVIDENCE = "COUNTEREVIDENCE"
    CAUSAL_OVERCLAIM_RISK = "CAUSAL_OVERCLAIM_RISK"
    RUBRIC_FIDELITY = "RUBRIC_FIDELITY"
    CONTAMINATION = "CONTAMINATION"
    ANSWERABILITY = "ANSWERABILITY"


class TrailCheckCategory(StrEnum):
    SOURCE_EXISTENCE = "SOURCE_EXISTENCE"
    EVIDENCE_EXISTENCE = "EVIDENCE_EXISTENCE"
    ARTIFACT_FIDELITY = "ARTIFACT_FIDELITY"
    EXACT_SPAN_FIDELITY = "EXACT_SPAN_FIDELITY"
    STRUCTURAL_BOUNDS = "STRUCTURAL_BOUNDS"
    GRAPH_MEMBERSHIP = "GRAPH_MEMBERSHIP"
    RELATION_SCHEMA = "RELATION_SCHEMA"
    ORDERING = "ORDERING"
    TEMPORAL_ORDER = "TEMPORAL_ORDER"
    MODALITY = "MODALITY"
    NECESSITY = "NECESSITY"
    GROUNDEDNESS = "GROUNDEDNESS"
    COUNTEREVIDENCE = "COUNTEREVIDENCE"
    ASSESSMENT_AUTHORITY = "ASSESSMENT_AUTHORITY"


class TrailNodeRole(StrEnum):
    REQUIRED = "REQUIRED"
    SUPPORTING = "SUPPORTING"
    OPPOSING = "OPPOSING"
    REDUNDANT = "REDUNDANT"


class ClaimModality(StrEnum):
    ASSERTED = "ASSERTED"
    QUALIFIED = "QUALIFIED"
    UNCERTAIN = "UNCERTAIN"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    ABSTAINED = "ABSTAINED"


class TrailGeometry(StrEnum):
    LINEAR = "LINEAR"
    BRANCHED = "BRANCHED"
    CONVERGENT = "CONVERGENT"
    DIVERGENT = "DIVERGENT"
    NETWORK = "NETWORK"


class ConstructionMethod(StrEnum):
    SOURCE_FIRST = "SOURCE_FIRST"


class ExactSourceSpan(_StrictFrozenModel):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def validate_bounds(self) -> ExactSourceSpan:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if self.end - self.start != len(self.text):
            raise ValueError("span offsets must match exact text length")
        return self


class StructuralLocation(_StrictFrozenModel):
    kind: StructuralLocationKind
    locator: NonBlankText
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> StructuralLocation:
        if self.end <= self.start:
            raise ValueError("structural location end must be greater than start")
        return self


class TrailOrderingConstraint(_StrictFrozenModel):
    constraint_id: StableIdentifier
    before_node_id: StableIdentifier
    after_node_id: StableIdentifier

    @model_validator(mode="after")
    def reject_self_ordering(self) -> TrailOrderingConstraint:
        if self.before_node_id == self.after_node_id:
            raise ValueError("ordering constraint endpoints must be distinct")
        return self


class EvidenceTrailVersion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    trail_version_id: StableIdentifier
    trail_id: StableIdentifier
    claim_version_id: StableIdentifier
    version: StrictInt = Field(ge=1)
    parent_trail_version_id: StableIdentifier | None = None
    source_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    required_node_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    supporting_node_ids: tuple[StableIdentifier, ...]
    opposing_node_ids: tuple[StableIdentifier, ...]
    redundant_node_ids: tuple[StableIdentifier, ...]
    ordering_constraints: tuple[TrailOrderingConstraint, ...]
    geometry: TrailGeometry
    status: TrailOutcome
    construction_method: ConstructionMethod
    check_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    assessment_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    constructed_by: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_local_lineage(self) -> EvidenceTrailVersion:
        if (self.version == 1) != (self.parent_trail_version_id is None):
            raise ValueError("only version 1 may omit parent_trail_version_id")
        return self


class EvidenceTrailNode(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    node_id: StableIdentifier
    trail_version_id: StableIdentifier
    source_id: StableIdentifier
    evidence_id: StableIdentifier
    exact_span: ExactSourceSpan
    structural_location: StructuralLocation
    content_hash: Sha256Hex
    role: TrailNodeRole
    temporal_position: StrictInt | None = Field(default=None, ge=0)
    causal_position: StrictInt | None = Field(default=None, ge=0)
    confidence: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    necessity: bool

    @field_validator("confidence", mode="before")
    @classmethod
    def require_float_confidence(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a float")
        return value


class EvidenceTrailRelation(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    relation_id: StableIdentifier
    trail_version_id: StableIdentifier
    source_node_id: StableIdentifier
    target_node_id: StableIdentifier
    relation_type: RelationType
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    modality: ClaimModality
    causal_support: tuple[StableIdentifier, ...] = ()


class TrailCheckResult(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    check_id: StableIdentifier
    trail_version_id: StableIdentifier
    category: TrailCheckCategory
    passed: bool
    finding_codes: tuple[StableIdentifier, ...]
    node_ids: tuple[StableIdentifier, ...]
    relation_ids: tuple[StableIdentifier, ...]
    evidence_ids: tuple[StableIdentifier, ...]
    checker_id: StableIdentifier
    checker_version: StableIdentifier
    checked_at: UtcTimestamp


class TrailAssessment(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    assessment_id: StableIdentifier
    trail_version_id: StableIdentifier
    category: AssessmentCategory
    provenance: AssessmentProvenance
    node_ids: tuple[StableIdentifier, ...]
    relation_ids: tuple[StableIdentifier, ...]
    evidence_ids: tuple[StableIdentifier, ...]
    finding_codes: tuple[StableIdentifier, ...]


class ReportSourceSpan(_StrictFrozenModel):
    node_id: StableIdentifier
    source_id: StableIdentifier
    evidence_id: StableIdentifier
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    text: str
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_bounds(self) -> ReportSourceSpan:
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("report source span bounds must match exact text length")
        return self


class ReportSentenceBinding(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    binding_id: StableIdentifier
    trail_version_id: StableIdentifier
    claim_version_id: StableIdentifier
    sentence: NonBlankText
    outcome: TrailOutcome
    source_node_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    source_spans: tuple[ReportSourceSpan, ...] = Field(min_length=1)
    contradiction_node_ids: tuple[StableIdentifier, ...]
    opposing_node_ids: tuple[StableIdentifier, ...]
    uncertainty: NonBlankText
    modality: ClaimModality
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex


class EvidenceTrailSnapshot(_StrictFrozenModel):
    version: EvidenceTrailVersion
    nodes: tuple[EvidenceTrailNode, ...] = Field(min_length=1)
    relations: tuple[EvidenceTrailRelation, ...]
    checks: tuple[TrailCheckResult, ...] = Field(min_length=1)
    assessments: tuple[TrailAssessment, ...] = Field(min_length=1)


class RetainedEvidenceSource(_StrictFrozenModel):
    source_id: StableIdentifier
    evidence: EvidenceRecord
    artifact_bytes: bytes


class TrailValidationInputs(_StrictFrozenModel):
    claim: AtomicClaim
    sources: tuple[RetainedEvidenceSource, ...] = Field(min_length=1)


class TrailValidationResult(_StrictFrozenModel):
    trail_version_id: StableIdentifier
    outcome: TrailOutcome
    finding_codes: tuple[StableIdentifier, ...]
    required_node_ids: tuple[StableIdentifier, ...]
    opposing_node_ids: tuple[StableIdentifier, ...]
    assessment_ids: tuple[StableIdentifier, ...]
