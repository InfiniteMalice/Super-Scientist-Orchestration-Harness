from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

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


def _strip_semantic_version(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


SemanticVersion = Annotated[
    str,
    BeforeValidator(_strip_semantic_version),
    Field(
        strict=True,
        min_length=5,
        max_length=32,
        pattern=(
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        ),
    ),
]


class RuleIncidentKind(StrEnum):
    ACCEPTED_HUMAN_REVIEW = "ACCEPTED_HUMAN_REVIEW"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    REPRODUCED_BUG = "REPRODUCED_BUG"
    FAILED_SCIENTIFIC_WORKFLOW = "FAILED_SCIENTIFIC_WORKFLOW"
    SECURITY_INCIDENT = "SECURITY_INCIDENT"
    QUALITY_GATE_FAILURE = "QUALITY_GATE_FAILURE"
    REPEATED_MISTAKE = "REPEATED_MISTAKE"
    VALIDATED_COUNTEREXAMPLE = "VALIDATED_COUNTEREXAMPLE"


class RuleStatus(StrEnum):
    PROPOSED = "PROPOSED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACTIVE = "ACTIVE"
    EXPERIMENTAL = "EXPERIMENTAL"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"
    DEPRECATED = "DEPRECATED"
    REJECTED = "REJECTED"


class RuleAuthority(StrEnum):
    CONSTITUTIONAL = "CONSTITUTIONAL"
    GOVERNANCE = "GOVERNANCE"
    PROJECT = "PROJECT"
    DOMAIN = "DOMAIN"
    COMPONENT = "COMPONENT"
    TASK = "TASK"
    RUN_LOCAL = "RUN_LOCAL"


class ReviewerRole(StrEnum):
    SEMANTIC = "SEMANTIC"
    CONFLICT = "CONFLICT"
    ABSTRACTION = "ABSTRACTION"
    ADVERSARIAL = "ADVERSARIAL"
    VERIFICATION = "VERIFICATION"


class ConflictClassification(StrEnum):
    TRUE_LOGICAL_CONTRADICTION = "TRUE_LOGICAL_CONTRADICTION"
    OVERLAPPING_SCOPE = "OVERLAPPING_SCOPE"
    MISSING_PRECONDITION_OR_EXCEPTION = "MISSING_PRECONDITION_OR_EXCEPTION"
    PRECEDENCE = "PRECEDENCE"
    TEMPORAL_VERSION = "TEMPORAL_VERSION"
    ENVIRONMENT_OR_MODEL_DEPENDENCE = "ENVIRONMENT_OR_MODEL_DEPENDENCE"
    COMPETING_FAILURE_MODES = "COMPETING_FAILURE_MODES"
    INVALID_OR_OUTDATED_RULES = "INVALID_OR_OUTDATED_RULES"
    MEASUREMENT_CONFLICT = "MEASUREMENT_CONFLICT"


class OverlapClassification(StrEnum):
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
    NARROWER_INSTANCE = "NARROWER_INSTANCE"
    BROADER_REFORMULATION = "BROADER_REFORMULATION"
    PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
    SAME_TRIGGER_DIFFERENT_ACTION = "SAME_TRIGGER_DIFFERENT_ACTION"
    DIFFERENT_TRIGGER_SAME_ACTION = "DIFFERENT_TRIGGER_SAME_ACTION"
    NON_REDUNDANT = "NON_REDUNDANT"


class RuleAction(StrEnum):
    ACCEPT = "ACCEPT"
    ACCEPT_WITH_REVISION = "ACCEPT_WITH_REVISION"
    MERGE_WITH_EXISTING = "MERGE_WITH_EXISTING"
    SPLIT = "SPLIT"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"


class RuleIncident(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    incident_id: StableIdentifier
    incident_kind: RuleIncidentKind
    summary: NonBlankText
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    observed_at: UtcTimestamp
    reported_by: ActorIdentity
    recorded_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _require_unique(value, "evidence_ids")


class BehavioralRuleVersion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    rule_version_id: StableIdentifier
    rule_id: StableIdentifier
    semantic_version: SemanticVersion
    title: NonBlankText
    canonical_statement: NonBlankText
    rationale: NonBlankText
    authority: RuleAuthority
    scope: tuple[NonBlankText, ...] = Field(min_length=1)
    triggers: tuple[NonBlankText, ...] = Field(min_length=1)
    required_behavior: tuple[NonBlankText, ...] = Field(min_length=1)
    prohibited_behavior: tuple[NonBlankText, ...]
    exceptions: tuple[NonBlankText, ...]
    decision_boundary: NonBlankText
    precedence_rule_ids: tuple[StableIdentifier, ...]
    source_incident_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    evidence_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    counterexamples: tuple[NonBlankText, ...]
    regression_test_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    retrieval_terms: tuple[NonBlankText, ...] = Field(min_length=1)
    aliases: tuple[NonBlankText, ...]
    related_rule_ids: tuple[StableIdentifier, ...]
    conflict_rule_ids: tuple[StableIdentifier, ...]
    supersedes_rule_version_ids: tuple[StableIdentifier, ...]
    status: RuleStatus
    creator: ActorIdentity
    approver: ActorIdentity | None
    created_at: UtcTimestamp
    approved_at: UtcTimestamp | None
    governing_policy_hash: Sha256Hex

    @field_validator(
        "precedence_rule_ids",
        "source_incident_ids",
        "evidence_ids",
        "regression_test_ids",
        "related_rule_ids",
        "conflict_rule_ids",
        "supersedes_rule_version_ids",
    )
    @classmethod
    def require_unique_identifier_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "references")
        return _require_unique(value, str(field_name))


class ReviewerAssessment(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    assessment_id: StableIdentifier
    role: ReviewerRole
    provenance: AssessmentProvenance
    proposal_id: StableIdentifier
    rule_version_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    incident_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    overlap: OverlapClassification | None
    conflict: ConflictClassification | None
    findings: tuple[NonBlankText, ...] = Field(min_length=1)
    candidate_statement: NonBlankText | None
    scope: tuple[NonBlankText, ...]
    triggers: tuple[NonBlankText, ...]
    exceptions: tuple[NonBlankText, ...]
    counterexamples: tuple[NonBlankText, ...]
    regression_test_ids: tuple[StableIdentifier, ...]
    recommended_action: RuleAction
    uncertainty: tuple[NonBlankText, ...]

    @field_validator("rule_version_ids", "incident_ids", "regression_test_ids")
    @classmethod
    def require_unique_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "references")
        return _require_unique(value, str(field_name))


class RuleConsolidationDecision(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    consolidation_decision_id: StableIdentifier
    proposal_id: StableIdentifier
    consumed_assessment_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    consumed_incident_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    resulting_rule_version_id: StableIdentifier | None
    action: RuleAction
    rationale: NonBlankText
    separating_variable: NonBlankText | None
    decision_boundary: NonBlankText | None
    accepted_recommendations: tuple[NonBlankText, ...]
    rejected_recommendations: tuple[NonBlankText, ...]
    preserved_dissent: tuple[NonBlankText, ...]
    decided_by: ActorIdentity
    decided_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("consumed_assessment_ids", "consumed_incident_ids")
    @classmethod
    def require_unique_consumed_references(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        field_name = getattr(info, "field_name", "references")
        return _require_unique(value, str(field_name))

    @model_validator(mode="after")
    def require_result_for_rule_producing_action(self) -> Self:
        result_is_required = self.action not in {
            RuleAction.REJECT,
            RuleAction.ESCALATE_TO_HUMAN,
        }
        if result_is_required and self.resulting_rule_version_id is None:
            raise ValueError("resulting_rule_version_id is required for a rule-producing action")
        if not result_is_required and self.resulting_rule_version_id is not None:
            raise ValueError("resulting_rule_version_id must be null for a non-producing action")
        return self


class RuleRegressionCase(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    regression_case_id: StableIdentifier
    rule_version_id: StableIdentifier
    incident_ids: tuple[StableIdentifier, ...] = Field(min_length=1)
    test_id: StableIdentifier
    scenario: NonBlankText
    expected_behavior: NonBlankText
    created_by: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: Sha256Hex

    @field_validator("incident_ids")
    @classmethod
    def require_unique_incident_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _require_unique(value, "incident_ids")


def _require_unique(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must contain unique identifiers")
    return value
