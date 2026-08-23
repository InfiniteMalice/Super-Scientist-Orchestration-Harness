from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import (
    Sha256Hex,
    StableIdentifier,
    canonical_json_bytes,
    sha256_hex,
)

MAX_COGNITION_ITEMS = 64
MAX_IDENTIFIER_LENGTH = 200
MAX_TEXT_LENGTH = 2_000


def _strip_text(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


BoundedIdentifier = Annotated[
    StableIdentifier,
    Field(max_length=MAX_IDENTIFIER_LENGTH),
]
BoundedText = Annotated[
    str,
    BeforeValidator(_strip_text),
    Field(strict=True, min_length=1, max_length=MAX_TEXT_LENGTH),
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


def _require_canonical_unique(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")
    if values != tuple(sorted(values)):
        raise ValueError(f"{field_name} must be sorted in canonical order")
    return values


def _content_hash(model: BaseModel, *, exclude: str = "content_hash") -> str:
    payload = model.model_dump(mode="json", exclude={exclude})
    return sha256_hex(canonical_json_bytes(payload))


class CapabilityEvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SELF_REPORTED = "SELF_REPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class CapabilityDisposition(StrEnum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    UNKNOWN = "UNKNOWN"


class DiversityAxisStatus(StrEnum):
    DIFFERENT = "DIFFERENT"
    SAME = "SAME"
    UNKNOWN = "UNKNOWN"


class ErrorCorrelationStatus(StrEnum):
    KNOWN = "KNOWN"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class CapabilityAssertion(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    assertion_id: BoundedIdentifier
    capability_id: BoundedIdentifier
    task_family_id: BoundedIdentifier
    status: CapabilityEvidenceStatus
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    validator_id: BoundedIdentifier | None
    validator_version: BoundedIdentifier | None
    evidence_snapshot_hash: Sha256Hex

    @field_validator("evidence_ids")
    @classmethod
    def require_canonical_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="evidence_ids")

    @model_validator(mode="after")
    def require_grounded_verification(self) -> Self:
        validator_fields = (self.validator_id, self.validator_version)
        if self.status is CapabilityEvidenceStatus.VERIFIED and (
            not self.evidence_ids or any(value is None for value in validator_fields)
        ):
            raise ValueError(
                "verified assertions require evidence IDs and validator identity/version"
            )
        if self.status is not CapabilityEvidenceStatus.VERIFIED and any(
            value is not None for value in validator_fields
        ):
            raise ValueError("only verified assertions may declare validator identity/version")
        return self


class DiversityFingerprint(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    fingerprint_id: BoundedIdentifier
    model_family: BoundedIdentifier | None
    model_version: BoundedIdentifier | None
    scale_class: BoundedIdentifier | None
    provider: BoundedIdentifier | None
    adapter_hash: Sha256Hex | None
    configuration_hash: Sha256Hex | None
    prompt_strategy: BoundedIdentifier | None
    methodological_prior: BoundedIdentifier | None
    tools: tuple[BoundedIdentifier, ...] | None = Field(max_length=MAX_COGNITION_ITEMS)
    evidence_partitions: tuple[BoundedIdentifier, ...] | None = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    modalities: tuple[BoundedIdentifier, ...] | None = Field(max_length=MAX_COGNITION_ITEMS)
    previous_error_clusters: tuple[BoundedIdentifier, ...] | None = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    prior_task_specializations: tuple[BoundedIdentifier, ...] | None = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    assigned_role: BoundedIdentifier | None = None
    procedure_family: BoundedIdentifier | None = None

    @field_validator(
        "tools",
        "evidence_partitions",
        "modalities",
        "previous_error_clusters",
        "prior_task_specializations",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...] | None,
        info: Any,
    ) -> tuple[str, ...] | None:
        if values is None:
            return None
        return _require_canonical_unique(values, field_name=info.field_name)


class _CapabilityProfilePayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    profile_id: BoundedIdentifier
    actor: ActorIdentity
    diversity_fingerprint: DiversityFingerprint
    allowed_tools: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    modalities: tuple[BoundedIdentifier, ...] = Field(default=(), max_length=MAX_COGNITION_ITEMS)
    supported_schemas: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    execution_constraints: tuple[BoundedText, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    known_failure_categories: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    assertions: tuple[CapabilityAssertion, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    governing_policy_hash: Sha256Hex

    @field_validator(
        "allowed_tools",
        "modalities",
        "supported_schemas",
        "execution_constraints",
        "known_failure_categories",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)

    @field_validator("assertions")
    @classmethod
    def require_sorted_assertions(
        cls,
        assertions: tuple[CapabilityAssertion, ...],
    ) -> tuple[CapabilityAssertion, ...]:
        if len({item.assertion_id for item in assertions}) != len(assertions):
            raise ValueError("assertion IDs must be unique")
        expected = tuple(
            sorted(
                assertions,
                key=lambda item: (item.capability_id, item.task_family_id, item.assertion_id),
            )
        )
        if assertions != expected:
            raise ValueError("assertions must be sorted in canonical order")
        return assertions

    @model_validator(mode="after")
    def require_fingerprint_identity_alignment(self) -> Self:
        fingerprint = self.diversity_fingerprint
        actor = self.actor
        if fingerprint.provider is not None and fingerprint.provider != actor.provider_id:
            raise ValueError("fingerprint provider must match actor provider identity")
        if (
            fingerprint.configuration_hash is not None
            and fingerprint.configuration_hash != actor.configuration_hash
        ):
            raise ValueError("fingerprint configuration hash must match actor identity")
        return self


class CapabilityProfile(_CapabilityProfilePayload):
    content_hash: Sha256Hex

    @property
    def actor_id(self) -> str:
        return self.actor.actor_id

    @property
    def actor_kind(self) -> ActorKind:
        return self.actor.kind

    @property
    def model_identity(self) -> str | None:
        return self.actor.model_id

    @property
    def provider_identity(self) -> str | None:
        return self.actor.provider_id

    @property
    def adapter_identity(self) -> str | None:
        return self.actor.adapter_id

    @property
    def configuration_hash(self) -> str | None:
        return self.actor.configuration_hash

    @classmethod
    def build(cls, **values: Any) -> CapabilityProfile:
        payload = _CapabilityProfilePayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the capability profile")
        return self


class CapabilityRequirement(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    requirement_id: BoundedIdentifier
    capability_id: BoundedIdentifier
    task_family_id: BoundedIdentifier
    evidence_snapshot_hash: Sha256Hex
    required_tools: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    required_modalities: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    required_schema_ids: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    required_execution_constraints: tuple[BoundedText, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )
    disqualifying_failure_categories: tuple[BoundedIdentifier, ...] = Field(
        default=(), max_length=MAX_COGNITION_ITEMS
    )

    @field_validator(
        "required_tools",
        "required_modalities",
        "required_schema_ids",
        "required_execution_constraints",
        "disqualifying_failure_categories",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)


class CapabilityAssessment(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    profile_id: BoundedIdentifier
    actor_id: BoundedIdentifier
    requirement: CapabilityRequirement
    matched_assertion_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    verified_assertion_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    disposition: CapabilityDisposition
    evidence_status: CapabilityEvidenceStatus
    missing_dimensions: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    failed_dimensions: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)

    @field_validator(
        "matched_assertion_ids",
        "verified_assertion_ids",
        "missing_dimensions",
        "failed_dimensions",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)

    @classmethod
    def from_matches(
        cls,
        profile: CapabilityProfile,
        requirement: CapabilityRequirement,
        matching: tuple[CapabilityAssertion, ...],
        verified: tuple[CapabilityAssertion, ...],
    ) -> CapabilityAssessment:
        matched_ids = tuple(sorted(item.assertion_id for item in matching))
        verified_ids = tuple(sorted(item.assertion_id for item in verified))
        if not matching:
            return cls(
                profile_id=profile.profile_id,
                actor_id=profile.actor_id,
                requirement=requirement,
                matched_assertion_ids=(),
                verified_assertion_ids=(),
                disposition=CapabilityDisposition.UNKNOWN,
                evidence_status=CapabilityEvidenceStatus.UNKNOWN,
                missing_dimensions=("capability_evidence",),
                failed_dimensions=(),
            )

        statuses = {item.status for item in matching}
        evidence_status = next(
            status
            for status in (
                CapabilityEvidenceStatus.VERIFIED,
                CapabilityEvidenceStatus.UNSUPPORTED,
                CapabilityEvidenceStatus.SELF_REPORTED,
                CapabilityEvidenceStatus.UNKNOWN,
            )
            if status in statuses
        )
        missing: list[str] = []
        failed: list[str] = []
        dimensions = (
            ("required_tools", requirement.required_tools, profile.allowed_tools),
            ("required_modalities", requirement.required_modalities, profile.modalities),
            ("required_schema_ids", requirement.required_schema_ids, profile.supported_schemas),
            (
                "required_execution_constraints",
                requirement.required_execution_constraints,
                profile.execution_constraints,
            ),
        )
        missing.extend(
            name
            for name, required, available in dimensions
            if not set(required).issubset(available)
        )
        if set(requirement.disqualifying_failure_categories) & set(
            profile.known_failure_categories
        ):
            failed.append("known_failure_categories")

        if verified:
            if failed:
                disposition = CapabilityDisposition.UNSATISFIED
            elif missing:
                disposition = CapabilityDisposition.UNKNOWN
            else:
                disposition = CapabilityDisposition.SATISFIED
        elif CapabilityEvidenceStatus.UNSUPPORTED in statuses:
            disposition = CapabilityDisposition.UNSATISFIED
            failed.append("capability_support")
        else:
            disposition = CapabilityDisposition.UNKNOWN
            if CapabilityEvidenceStatus.VERIFIED in statuses:
                failed.append("evidence_snapshot_hash")
            else:
                missing.append("verified_evidence")

        return cls(
            profile_id=profile.profile_id,
            actor_id=profile.actor_id,
            requirement=requirement,
            matched_assertion_ids=matched_ids,
            verified_assertion_ids=verified_ids,
            disposition=disposition,
            evidence_status=evidence_status,
            missing_dimensions=tuple(sorted(set(missing))),
            failed_dimensions=tuple(sorted(set(failed))),
        )


class CohortRequest(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    request_id: BoundedIdentifier
    task_id: BoundedIdentifier
    required_capabilities: tuple[CapabilityRequirement, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    preferred_capabilities: tuple[CapabilityRequirement, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    min_members: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)
    max_members: int = Field(strict=True, ge=1, le=MAX_COGNITION_ITEMS)
    candidate_actor_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    prohibited_combinations: tuple[
        tuple[BoundedIdentifier, BoundedIdentifier], ...
    ] = Field(max_length=MAX_COGNITION_ITEMS)
    tie_break_policy: Literal["ACTOR_ID_ASC"] = "ACTOR_ID_ASC"
    governing_policy_hash: Sha256Hex

    @field_validator("required_capabilities", "preferred_capabilities")
    @classmethod
    def require_sorted_requirements(
        cls,
        requirements: tuple[CapabilityRequirement, ...],
        info: Any,
    ) -> tuple[CapabilityRequirement, ...]:
        ids = tuple(item.requirement_id for item in requirements)
        _require_canonical_unique(ids, field_name=info.field_name)
        return requirements

    @field_validator("candidate_actor_ids")
    @classmethod
    def require_canonical_candidates(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="candidate_actor_ids")

    @field_validator("prohibited_combinations")
    @classmethod
    def require_canonical_prohibited_pairs(
        cls,
        pairs: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if any(left >= right for left, right in pairs):
            raise ValueError("prohibited combinations must use canonical distinct actor order")
        if len(set(pairs)) != len(pairs) or pairs != tuple(sorted(pairs)):
            raise ValueError("prohibited combinations must be unique and canonically sorted")
        return pairs

    @model_validator(mode="after")
    def require_coherent_bounds_and_requirements(self) -> Self:
        if self.min_members > self.max_members:
            raise ValueError("min_members must not exceed max_members")
        required_ids = {item.requirement_id for item in self.required_capabilities}
        preferred_ids = {item.requirement_id for item in self.preferred_capabilities}
        if required_ids & preferred_ids:
            raise ValueError("required and preferred requirement IDs must be disjoint")
        return self


class CohortMember(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    actor_id: BoundedIdentifier
    profile_id: BoundedIdentifier
    profile_content_hash: Sha256Hex
    required_satisfied: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)
    preferred_satisfied: int = Field(strict=True, ge=0, le=MAX_COGNITION_ITEMS)
    assessments: tuple[CapabilityAssessment, ...] = Field(max_length=MAX_COGNITION_ITEMS)


class CapabilityCoverage(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    requirement: CapabilityRequirement
    satisfying_actor_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)

    @field_validator("satisfying_actor_ids")
    @classmethod
    def require_canonical_actors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="satisfying_actor_ids")


class _CohortPlanPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    cohort_plan_id: BoundedIdentifier
    request_id: BoundedIdentifier
    task_id: BoundedIdentifier
    members: tuple[CohortMember, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    excluded_actor_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    coverage: tuple[CapabilityCoverage, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    unresolved_requirement_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    unresolved_candidate_actor_ids: tuple[BoundedIdentifier, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    tie_sets: tuple[tuple[BoundedIdentifier, ...], ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    evidence_snapshot_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    profile_content_hashes: tuple[Sha256Hex, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    minimum_size_met: bool
    governing_policy_hash: Sha256Hex

    @field_validator(
        "excluded_actor_ids",
        "unresolved_requirement_ids",
        "unresolved_candidate_actor_ids",
        "evidence_snapshot_hashes",
        "profile_content_hashes",
    )
    @classmethod
    def require_canonical_collections(
        cls,
        values: tuple[str, ...],
        info: Any,
    ) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name=info.field_name)

    @field_validator("tie_sets")
    @classmethod
    def require_complete_canonical_tie_sets(
        cls,
        tie_sets: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        for tie_set in tie_sets:
            if len(tie_set) < 2:
                raise ValueError("tie sets must contain at least two actors")
            _require_canonical_unique(tie_set, field_name="tie set")
        actors = tuple(actor_id for tie_set in tie_sets for actor_id in tie_set)
        if len(set(actors)) != len(actors):
            raise ValueError("an actor may appear in only one ranked tie set")
        return tie_sets


class CohortPlan(_CohortPlanPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> CohortPlan:
        payload = _CohortPlanPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the cohort plan")
        return self


class DiversityAxes(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    model_family: DiversityAxisStatus
    model_version: DiversityAxisStatus
    scale_class: DiversityAxisStatus
    provider: DiversityAxisStatus
    adapter_hash: DiversityAxisStatus
    configuration_hash: DiversityAxisStatus
    prompt_strategy: DiversityAxisStatus
    methodological_prior: DiversityAxisStatus
    tools: DiversityAxisStatus
    evidence_partitions: DiversityAxisStatus
    modalities: DiversityAxisStatus
    previous_error_clusters: DiversityAxisStatus
    prior_task_specializations: DiversityAxisStatus
    assigned_role: DiversityAxisStatus
    procedure_family: DiversityAxisStatus

    _AXES: ClassVar[tuple[str, ...]] = (
        "model_family",
        "model_version",
        "scale_class",
        "provider",
        "adapter_hash",
        "configuration_hash",
        "prompt_strategy",
        "methodological_prior",
        "tools",
        "evidence_partitions",
        "modalities",
        "previous_error_clusters",
        "prior_task_specializations",
        "assigned_role",
        "procedure_family",
    )

    def __getitem__(self, axis: str) -> DiversityAxisStatus:
        if axis not in self._AXES:
            raise KeyError(axis)
        value = getattr(self, axis)
        if not isinstance(value, DiversityAxisStatus):
            raise TypeError("diversity axis has an invalid status")
        return value

    def as_mapping(self) -> dict[str, DiversityAxisStatus]:
        return {axis: self[axis] for axis in self._AXES}


class ErrorCorrelationRecord(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    correlation_id: BoundedIdentifier
    left_actor_id: BoundedIdentifier
    right_actor_id: BoundedIdentifier
    evaluation_set_id: BoundedIdentifier
    sample_count: int = Field(strict=True, ge=0)
    method: BoundedIdentifier
    status: ErrorCorrelationStatus
    value: float | None = Field(default=None, ge=-1.0, le=1.0, allow_inf_nan=False)
    governing_policy_hash: Sha256Hex

    @field_validator("value", mode="before")
    @classmethod
    def require_strict_finite_float(cls, value: object) -> object:
        if value is not None and (type(value) is not float or not math.isfinite(value)):
            raise ValueError("correlation value must be a finite strict float")
        return value

    @model_validator(mode="after")
    def require_status_consistent_value(self) -> Self:
        if self.left_actor_id >= self.right_actor_id:
            raise ValueError("correlation actor IDs must use canonical distinct order")
        if self.status is ErrorCorrelationStatus.KNOWN and self.value is None:
            raise ValueError("KNOWN error correlation requires a measured coefficient")
        if self.status is ErrorCorrelationStatus.KNOWN and self.sample_count == 0:
            raise ValueError("KNOWN error correlation requires an observed sample")
        if self.status is not ErrorCorrelationStatus.KNOWN and self.value is not None:
            raise ValueError("unknown error correlation must not store an invented coefficient")
        return self


class _DiversityAssessmentPayload(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    diversity_assessment_id: BoundedIdentifier
    cohort_plan_id: BoundedIdentifier
    member_actor_ids: tuple[BoundedIdentifier, ...] = Field(max_length=MAX_COGNITION_ITEMS)
    axes: DiversityAxes
    error_correlations: tuple[ErrorCorrelationRecord, ...] = Field(
        max_length=MAX_COGNITION_ITEMS
    )
    governing_policy_hash: Sha256Hex

    @field_validator("member_actor_ids")
    @classmethod
    def require_canonical_members(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_canonical_unique(values, field_name="member_actor_ids")

    @field_validator("error_correlations")
    @classmethod
    def require_canonical_correlations(
        cls,
        records: tuple[ErrorCorrelationRecord, ...],
    ) -> tuple[ErrorCorrelationRecord, ...]:
        ids = tuple(record.correlation_id for record in records)
        _require_canonical_unique(ids, field_name="error_correlations")
        return records


class DiversityAssessment(_DiversityAssessmentPayload):
    content_hash: Sha256Hex

    @classmethod
    def build(cls, **values: Any) -> DiversityAssessment:
        payload = _DiversityAssessmentPayload(**values)
        digest = sha256_hex(canonical_json_bytes(payload.model_dump(mode="json")))
        return cls(**payload.model_dump(mode="python"), content_hash=digest)

    @model_validator(mode="after")
    def require_canonical_content_hash(self) -> Self:
        if self.content_hash != _content_hash(self):
            raise ValueError("content_hash must canonically address the diversity assessment")
        return self


class AcceptedCognitiveReceiptRef(_StrictFrozenModel):
    schema_version: Literal[1] = 1
    proposal_id: BoundedIdentifier
    proposal_hash: Sha256Hex
    audit_event_id: BoundedIdentifier
    audit_event_hash: Sha256Hex


class CapabilityProfileReceiptRef(AcceptedCognitiveReceiptRef):
    receipt_type: Literal["CAPABILITY_PROFILE"] = "CAPABILITY_PROFILE"


class CohortPlanReceiptRef(AcceptedCognitiveReceiptRef):
    receipt_type: Literal["COHORT_PLAN"] = "COHORT_PLAN"


__all__ = [
    "AcceptedCognitiveReceiptRef",
    "CapabilityAssertion",
    "CapabilityAssessment",
    "CapabilityCoverage",
    "CapabilityDisposition",
    "CapabilityEvidenceStatus",
    "CapabilityProfile",
    "CapabilityProfileReceiptRef",
    "CapabilityRequirement",
    "CohortMember",
    "CohortPlan",
    "CohortPlanReceiptRef",
    "CohortRequest",
    "DiversityAssessment",
    "DiversityAxes",
    "DiversityAxisStatus",
    "DiversityFingerprint",
    "ErrorCorrelationRecord",
    "ErrorCorrelationStatus",
]
